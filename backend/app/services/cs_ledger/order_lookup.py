"""Read CRM orders from the MySQL replica for the fund risk-control ledger.

SSOT: docs/features/fund-risk-ledger.md §5. One primary-key query per batch
(``WHERE t.id IN (...)``, chunked), through ``connect_readonly`` so all three
timeouts apply. Nothing here touches PostgreSQL: callers must not hold a PG
connection while calling into this module (the PG pool is 8 per worker).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

import pymysql

from app.core.config import get_settings
from app.core.mysql_readonly import connect_readonly
from app.schemas.cs_fund_risk import OrderSnapshot
from app.services.cs_ledger.channels import RECORDABLE_TYPES, channel_for

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500

# CRM stores naive datetimes at a fixed UTC+3 (no DST) on these columns.
CRM_UTC_OFFSET = timedelta(hours=3)

_TEST_MARKERS = ("demo", "test")

_SELECT = """
SELECT t.id, t.type, t.status, t.pspId, p.displayName AS psp_name, p.cid AS psp_cid,
       COALESCE(t.isFee,0) AS is_fee,
       t.requestedAmount, t.requestedCurrency, t.vendorAmount, t.vendorCurrency,
       t.createdAt, t.processedAt, t.fromUserId, t.fromLoginSid,
       u.firstName, u.middleName, u.lastName,
       COALESCE(u.isEmployee,0) AS is_employee,
       m.`GROUP` AS mt_group, m.NAME AS mt_name,
       (SELECT GROUP_CONCAT(tg.tag ORDER BY tg.id SEPARATOR ',')
          FROM fxbackoffice.user_tags ut
          JOIN fxbackoffice.tags tg ON tg.id = ut.tagId AND tg.categoryId = 6
         WHERE ut.userId = t.fromUserId) AS venue_tags
FROM fxbackoffice.transactions t
LEFT JOIN fxbackoffice.psps p      ON p.id = t.pspId
LEFT JOIN fxbackoffice.users u     ON u.id = t.fromUserId
LEFT JOIN fxbackoffice.mt4_users m ON m.loginSid = t.fromLoginSid
WHERE t.id IN ({placeholders})
"""


class ReplicaUnavailable(RuntimeError):
    """The MySQL replica could not be reached or the query timed out."""


@dataclass(frozen=True)
class OrderLookup:
    snapshot: OrderSnapshot            # client_name filled (live)
    warnings: list[str]                # OrderWarning values
    recordable: bool                   # False when type not recordable or isFee=1
    reject_reason: Optional[str]       # set when not recordable


def crm_time_to_utc_iso(value: Any) -> Optional[str]:
    """Naive UTC+3 datetime (or its string form) -> UTC ISO8601 ``...Z``."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith("0000-00-00"):
            return None
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (value - CRM_UTC_OFFSET).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_cny(currency: Any) -> bool:
    return isinstance(currency, str) and currency.strip().lower() == "cny"


def _has_test_marker(*values: Any) -> bool:
    for v in values:
        if v and any(marker in str(v).lower() for marker in _TEST_MARKERS):
            return True
    return False


def _client_name(row: Mapping[str, Any]) -> Optional[str]:
    parts = [str(row.get(k)).strip() for k in ("firstName", "middleName", "lastName") if row.get(k)]
    name = " ".join(p for p in parts if p)
    return name or None


def derive_order(row: Mapping[str, Any]) -> OrderLookup:
    """Apply the spec §5 derivation rules to one replica row."""
    txn_type = str(row.get("type") or "")
    is_fee = bool(int(row.get("is_fee") or 0))
    type_ok = txn_type in RECORDABLE_TYPES
    if type_ok:
        direction = RECORDABLE_TYPES[txn_type]
    else:
        direction = "deposit" if "deposit" in txn_type.lower() else "withdrawal"

    reject_reason: Optional[str] = None
    if not type_ok:
        reject_reason = (
            f"transaction type '{txn_type}' cannot be recorded "
            f"(allowed: {', '.join(RECORDABLE_TYPES)})"
        )
    elif is_fee:
        reject_reason = "fee transactions (isFee=1) cannot be recorded"

    amount: Optional[float] = None
    if direction == "deposit":
        if _is_cny(row.get("requestedCurrency")) and row.get("requestedAmount") is not None:
            amount = float(row["requestedAmount"])
    else:
        if _is_cny(row.get("vendorCurrency")) and row.get("vendorAmount") is not None:
            amount = abs(float(row["vendorAmount"]))
    if amount is not None:
        amount = round(amount, 2)

    psp_id = int(row["pspId"]) if row.get("pspId") is not None else None
    psp_name = row.get("psp_name")
    channel, mapped = channel_for(psp_id, psp_name)

    venue: Optional[str] = None
    tags = row.get("venue_tags")
    if tags:
        first = str(tags).split(",")[0].strip()
        venue = first.upper() or None

    client_name = _client_name(row)
    is_test = bool(int(row.get("is_employee") or 0)) or _has_test_marker(
        client_name, row.get("mt_name"), row.get("mt_group")
    )
    status = str(row.get("status") or "")

    warnings: list[str] = []
    if not mapped:
        warnings.append("psp_unmapped")
    psp_cid = row.get("psp_cid")
    if psp_cid is not None and int(psp_cid) != 0:
        warnings.append("non_cn_psp")
    if is_test:
        warnings.append("test_account")
    if status.lower() != "approved":
        warnings.append("not_approved")
    if amount is None:
        warnings.append("amount_not_cny")

    login_sid = row.get("fromLoginSid")
    snapshot = OrderSnapshot(
        transaction_id=int(row["id"]),
        client_id=int(row["fromUserId"]) if row.get("fromUserId") is not None else None,
        client_name=client_name,
        login_sid=str(login_sid) if login_sid not in (None, "") else None,
        venue=venue,
        direction=direction,
        txn_type=txn_type,
        psp_id=psp_id,
        psp_name=str(psp_name) if psp_name is not None else None,
        channel=channel,
        amount_cny=amount,
        txn_status=status,
        txn_created_at=crm_time_to_utc_iso(row.get("createdAt")),
        txn_processed_at=crm_time_to_utc_iso(row.get("processedAt")),
        is_test_account=is_test,
    )
    return OrderLookup(
        snapshot=snapshot,
        warnings=warnings,
        recordable=reject_reason is None,
        reject_reason=reject_reason,
    )


def fetch_orders(transaction_ids: Sequence[int], settings=None) -> dict[int, OrderLookup]:
    """Look up many orders. Ids missing from the replica are absent from the result.

    Raises ReplicaUnavailable on connection / timeout errors.
    """
    ids = sorted({int(i) for i in transaction_ids if i is not None})
    if not ids:
        return {}
    settings = settings or get_settings()
    out: dict[int, OrderLookup] = {}
    try:
        conn = connect_readonly(settings)
        try:
            with conn.cursor() as cur:
                for start in range(0, len(ids), CHUNK_SIZE):
                    chunk = ids[start:start + CHUNK_SIZE]
                    sql = _SELECT.format(placeholders=", ".join(["%s"] * len(chunk)))
                    cur.execute(sql, chunk)
                    for row in cur.fetchall():
                        # mt4_users.loginSid is expected unique; keep the first row if not.
                        out.setdefault(int(row["id"]), derive_order(row))
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except (pymysql.MySQLError, OSError) as exc:
        raise ReplicaUnavailable(f"MySQL replica unavailable: {type(exc).__name__}") from exc
    return out


def fetch_order(transaction_id: int, settings=None) -> Optional[OrderLookup]:
    return fetch_orders([transaction_id], settings).get(int(transaction_id))
