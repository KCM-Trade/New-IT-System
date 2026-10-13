"""Storage and business rules of the CS fund risk-control ledger.

SSOT: docs/features/fund-risk-ledger.md. Rows live in
``cs_ledger.fund_risk_incidents`` (PostgreSQL, ``risk_cases`` database); every
write also appends a row to ``cs_ledger.fund_risk_incidents_history`` in the
same transaction (``risk_cases_conn`` commits once at exit, rolls back on any
exception).

Two rules shape the control flow:

* The MySQL replica is never queried while a PG connection is borrowed (the
  pool is 8 per worker). Order lookups happen before the PG block (create) or
  after it has been released (reads).
* Optimistic locking is an atomic compare-and-swap on ``version``: the row is
  read ``FOR UPDATE`` and the UPDATE also carries ``AND version = %s``.

All SQL values are bound parameters; the only interpolated fragments are
constant table / column names defined in this module.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from psycopg2.extras import Json

from app.core.cs_ledger_pg import GROUP_SEQ, HISTORY_TABLE, TABLE
from app.core.risk_cases_pg import risk_cases_conn
from app.schemas.cs_fund_risk import (
    PROVINCES,
    Incident,
    IncidentCreate,
    IncidentFields,
    IncidentUpdate,
    OrderSnapshot,
)
from app.services.cs_ledger import order_lookup
from app.services.cs_ledger.order_lookup import OrderLookup, ReplicaUnavailable
from app.services.cs_ledger.text import mask_text

logger = logging.getLogger(__name__)

# Staff-entered columns, in contract order.
STAFF_FIELDS: tuple[str, ...] = tuple(IncidentFields.model_fields)
MASKED_FIELDS: frozenset[str] = frozenset({"event_text", "result_text", "freeze_note"})
PROVINCE_FIELDS: tuple[str, ...] = ("client_bank_province", "trader_bank_province", "id_province")
# Copied to the other rows of the group when ``apply_to_group`` is set.
GROUP_FIELDS: tuple[str, ...] = ("event_text", "result_text", "authority")

# Columns of the order snapshot stored at entry time.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "client_id", "login_sid", "venue", "direction", "txn_type", "psp_id",
    "psp_name", "channel", "amount_cny", "txn_status", "txn_created_at",
    "txn_processed_at", "is_test_account",
)

SORT_COLUMNS: dict[str, str] = {
    "reported_date": "t.reported_date",
    "txn_processed_at": "t.txn_processed_at",
    "id": "t.id",
}

EXPORT_MAX_ROWS = 50000

_GROUP_SIZE_SQL = (
    f"(SELECT count(*) FROM {TABLE} g "
    "WHERE g.incident_group_id = t.incident_group_id AND g.deleted_at IS NULL) AS group_size"
)
_SELECT_ROW = f"SELECT t.*, {_GROUP_SIZE_SQL} FROM {TABLE} t"


# --- errors -------------------------------------------------------------------

class IncidentNotFound(Exception):
    """No live ledger row with this id."""


class DuplicateTransaction(Exception):
    """The transaction id already has a live ledger row."""

    def __init__(self, transaction_id: int, existing_id: Optional[int]):
        super().__init__(f"transaction {transaction_id} is already recorded")
        self.existing_id = existing_id


class VersionConflict(Exception):
    """The row was changed by someone else since the caller read it."""

    def __init__(self, incident_id: int, current_version: int):
        super().__init__(f"incident {incident_id} was modified by someone else; please refresh")
        self.current_version = current_version


class GroupNotFound(Exception):
    """``incident_group_id`` does not belong to any live row."""


class OrderNotFound(Exception):
    """The transaction id does not exist in the CRM."""


class OrderNotRecordable(Exception):
    """The transaction exists but its type cannot be recorded."""


class InvalidProvince(ValueError):
    """A province field holds a value outside ``PROVINCES``."""


# --- value helpers --------------------------------------------------------------

def _utc_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _jsonb(row: Optional[Mapping[str, Any]]) -> Optional[Json]:
    if row is None:
        return None
    return Json(dict(row), dumps=lambda o: json.dumps(o, default=_json_default, ensure_ascii=False))


def plain_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """A JSON-friendly copy of a table row (for audit values)."""
    return json.loads(json.dumps(dict(row), default=_json_default, ensure_ascii=False))


def audit_target(row: Mapping[str, Any]) -> str:
    """``transaction:<id>``; imported legacy rows without one fall back to the row id."""
    if row.get("transaction_id") is not None:
        return f"transaction:{row['transaction_id']}"
    return f"incident:{row['id']}"


def normalize_fields(fields: IncidentFields) -> dict[str, Any]:
    """Staff fields as stored: blank strings become NULL, free text is masked."""
    out: dict[str, Any] = {}
    for name in STAFF_FIELDS:
        value = getattr(fields, name)
        if isinstance(value, str):
            value = value.strip() or None
            if name in MASKED_FIELDS:
                value = mask_text(value)
        out[name] = value
    return out


def _check_provinces(values: Mapping[str, Any], previous: Optional[Mapping[str, Any]] = None) -> None:
    """Province fields must be one of PROVINCES.

    On update only a CHANGED value is checked, so an imported row holding a
    legacy spelling can still be edited without first fixing that field.
    """
    for name in PROVINCE_FIELDS:
        value = values.get(name)
        if value is None or value in PROVINCES:
            continue
        if previous is not None and previous.get(name) == value:
            continue
        raise InvalidProvince(f"{name}: '{value}' is not a recognised province")


def _staff_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """Staff fields of a row as JSON-friendly values (for audit diffs)."""
    return {
        name: (row.get(name).isoformat() if isinstance(row.get(name), date) else row.get(name))
        for name in STAFF_FIELDS
    }


def snapshot_from_row(row: Mapping[str, Any]) -> Optional[OrderSnapshot]:
    """The stored order snapshot; ``None`` for legacy rows that never had one."""
    if row.get("transaction_id") is None or row.get("direction") not in ("deposit", "withdrawal"):
        return None
    amount = row.get("amount_cny")
    return OrderSnapshot(
        transaction_id=int(row["transaction_id"]),
        client_id=row.get("client_id"),
        client_name=None,                      # never persisted
        login_sid=row.get("login_sid"),
        venue=row.get("venue"),
        direction=row["direction"],
        txn_type=row.get("txn_type") or "",
        psp_id=row.get("psp_id"),
        psp_name=row.get("psp_name"),
        channel=row.get("channel") or "",
        amount_cny=float(amount) if amount is not None else None,
        txn_status=row.get("txn_status") or "",
        txn_created_at=_utc_iso(row.get("txn_created_at")),
        txn_processed_at=_utc_iso(row.get("txn_processed_at")),
        is_test_account=bool(row.get("is_test_account")),
    )


def to_incident(row: Mapping[str, Any], live: Optional[OrderLookup] = None) -> Incident:
    """Build the API model; ``live`` replaces the stored snapshot when given."""
    order = live.snapshot if live is not None else snapshot_from_row(row)
    return Incident(
        **{name: row.get(name) for name in STAFF_FIELDS},
        id=row["id"],
        incident_group_id=row["incident_group_id"],
        group_size=int(row.get("group_size") or 1),
        transaction_id=row.get("transaction_id"),
        order=order,
        order_is_live=live is not None,
        source=row.get("source") or "manual",
        version=row["version"],
        created_by=row["created_by"],
        created_at=_utc_iso(row["created_at"]),
        updated_by=row["updated_by"],
        updated_at=_utc_iso(row["updated_at"]),
    )


def hydrate(rows: Sequence[Mapping[str, Any]], settings=None) -> tuple[list[Incident], bool]:
    """Merge live order values from the replica into PG rows.

    Must be called with NO PG connection borrowed. Returns the incidents and
    whether the replica answered; when it did not, every row carries its stored
    snapshot and ``order_is_live`` is False.
    """
    ids = [int(r["transaction_id"]) for r in rows if r.get("transaction_id") is not None]
    lookups: dict[int, OrderLookup] = {}
    replica_live = True
    if ids:
        try:
            lookups = order_lookup.fetch_orders(ids, settings)
        except ReplicaUnavailable as exc:
            replica_live = False
            logger.warning("fund-risk ledger: replica unavailable, serving snapshots: %s", exc)
    return [to_incident(r, lookups.get(r.get("transaction_id"))) for r in rows], replica_live


# --- reads --------------------------------------------------------------------------

def find_existing_id(transaction_id: int) -> Optional[int]:
    """Id of the live ledger row holding this transaction, if any."""
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {TABLE} WHERE transaction_id = %s AND deleted_at IS NULL",
                (transaction_id,),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else None


def _select_one(cur, incident_id: int) -> Optional[dict[str, Any]]:
    cur.execute(f"{_SELECT_ROW} WHERE t.id = %s AND t.deleted_at IS NULL", (incident_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_row(incident_id: int) -> dict[str, Any]:
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            row = _select_one(cur, incident_id)
    if row is None:
        raise IncidentNotFound(incident_id)
    return row


@dataclass(frozen=True)
class ListFilters:
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    channel: Optional[str] = None
    direction: Optional[str] = None
    authority: Optional[str] = None
    frozen_scope: Optional[str] = None
    pending_result: Optional[bool] = None
    q: Optional[str] = None                 # digits only: transaction id or client id


def _where(filters: ListFilters) -> tuple[str, list[Any]]:
    clauses = ["t.deleted_at IS NULL"]
    params: list[Any] = []
    if filters.date_from is not None:
        clauses.append("t.reported_date >= %s")
        params.append(filters.date_from)
    if filters.date_to is not None:
        clauses.append("t.reported_date <= %s")
        params.append(filters.date_to)
    for column in ("channel", "direction", "authority", "frozen_scope"):
        value = getattr(filters, column)
        if value:
            clauses.append(f"t.{column} = %s")
            params.append(value)
    if filters.pending_result is True:
        clauses.append("(t.result_text IS NULL OR btrim(t.result_text) = '')")
    elif filters.pending_result is False:
        clauses.append("(t.result_text IS NOT NULL AND btrim(t.result_text) <> '')")
    if filters.q:
        number = int(filters.q)
        clauses.append("(t.transaction_id = %s OR t.client_id = %s)")
        params.extend([number, number])
    return " AND ".join(clauses), params


def _order_by(sort: str, order: str) -> str:
    column = SORT_COLUMNS.get(sort, SORT_COLUMNS["reported_date"])
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    if column == "t.id":
        return f"ORDER BY t.id {direction}"
    return f"ORDER BY {column} {direction} NULLS LAST, t.id {direction}"


def list_rows(
    filters: ListFilters, *, page: int, page_size: int, sort: str, order: str
) -> tuple[list[dict[str, Any]], int]:
    """One page of live rows plus the filtered total."""
    where, params = _where(filters)
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {TABLE} t WHERE {where}", params)
            total = int(cur.fetchone()["n"])
            cur.execute(
                f"{_SELECT_ROW} WHERE {where} {_order_by(sort, order)} LIMIT %s OFFSET %s",
                [*params, page_size, (page - 1) * page_size],
            )
            rows = [dict(r) for r in cur.fetchall()]
    return rows, total


def export_rows(filters: ListFilters, *, sort: str, order: str) -> list[dict[str, Any]]:
    """Every filtered row (snapshot values), for the xlsx export."""
    where, params = _where(filters)
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"{_SELECT_ROW} WHERE {where} {_order_by(sort, order)} LIMIT %s",
                [*params, EXPORT_MAX_ROWS],
            )
            return [dict(r) for r in cur.fetchall()]


def total_pages(total: int, page_size: int) -> int:
    return math.ceil(total / page_size) if total else 0


# --- writes --------------------------------------------------------------------------

def _insert_history(cur, incident_id: int, action: str, old_row, new_row, actor: str) -> None:
    cur.execute(
        f"INSERT INTO {HISTORY_TABLE} (incident_id, action, old_row, new_row, actor) "
        "VALUES (%s, %s, %s, %s, %s)",
        (incident_id, action, _jsonb(old_row), _jsonb(new_row), actor),
    )


def _history_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out.pop("group_size", None)
    return out


@dataclass(frozen=True)
class CreateResult:
    row: dict[str, Any]
    lookup: OrderLookup


def create_incident(body: IncidentCreate, actor: str, settings=None) -> CreateResult:
    """Record one order. The order snapshot is re-read from the replica here.

    Raises OrderNotFound / OrderNotRecordable / ReplicaUnavailable (before any
    PG work), InvalidProvince, GroupNotFound, DuplicateTransaction.
    """
    values = normalize_fields(body)
    _check_provinces(values)

    # Replica first: no PG connection is held during this call.
    lookup = order_lookup.fetch_order(body.transaction_id, settings)
    if lookup is None:
        raise OrderNotFound(f"transaction {body.transaction_id} not found")
    if not lookup.recordable:
        raise OrderNotRecordable(lookup.reject_reason or "transaction cannot be recorded")
    snap = lookup.snapshot.model_dump()

    columns = ["transaction_id", *SNAPSHOT_FIELDS, "snapshot_at", *STAFF_FIELDS,
               "source", "created_by", "updated_by"]
    params: list[Any] = [body.transaction_id, *[snap[name] for name in SNAPSHOT_FIELDS]]
    placeholders = ["%s"] * (1 + len(SNAPSHOT_FIELDS)) + ["now()"]
    params.extend(values[name] for name in STAFF_FIELDS)
    placeholders.extend(["%s"] * len(STAFF_FIELDS))
    params.extend(["manual", actor, actor])
    placeholders.extend(["%s", "%s", "%s"])
    if body.incident_group_id is not None:
        columns.append("incident_group_id")
        placeholders.append("%s")
        params.append(body.incident_group_id)

    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            if body.incident_group_id is not None:
                cur.execute(
                    f"SELECT 1 AS ok FROM {TABLE} WHERE incident_group_id = %s "
                    "AND deleted_at IS NULL LIMIT 1",
                    (body.incident_group_id,),
                )
                if cur.fetchone() is None:
                    raise GroupNotFound(f"incident group {body.incident_group_id} not found")
            # Guarded insert: a concurrent create of the same transaction makes
            # this a no-op (0 rows) instead of a unique-violation abort.
            cur.execute(
                f"INSERT INTO {TABLE} ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) "
                "ON CONFLICT (transaction_id) WHERE deleted_at IS NULL AND transaction_id IS NOT NULL "
                "DO NOTHING RETURNING id",
                params,
            )
            inserted = cur.fetchone()
            if inserted is None:
                cur.execute(
                    f"SELECT id FROM {TABLE} WHERE transaction_id = %s AND deleted_at IS NULL",
                    (body.transaction_id,),
                )
                existing = cur.fetchone()
                raise DuplicateTransaction(
                    body.transaction_id, int(existing["id"]) if existing else None
                )
            row = _select_one(cur, int(inserted["id"]))
            _insert_history(cur, row["id"], "create", None, _history_row(row), actor)
    return CreateResult(row=row, lookup=lookup)


@dataclass(frozen=True)
class RowChange:
    """One row changed by an update: its audit target and the staff-field values."""

    target: str
    old: dict[str, Any]
    new: dict[str, Any]


@dataclass(frozen=True)
class UpdateResult:
    row: dict[str, Any]
    changes: list[RowChange] = field(default_factory=list)   # empty = no-op


def _apply_update(cur, existing: Mapping[str, Any], new_values: Mapping[str, Any], actor: str):
    """Compare-and-swap one row; returns the new row or None when nothing differs."""
    changed = {k: v for k, v in new_values.items() if existing.get(k) != v}
    if not changed:
        return None
    assignments = ", ".join(f"{name} = %s" for name in changed)
    cur.execute(
        f"UPDATE {TABLE} SET {assignments}, version = version + 1, "
        "updated_by = %s, updated_at = now() "
        "WHERE id = %s AND version = %s AND deleted_at IS NULL RETURNING *",
        [*changed.values(), actor, existing["id"], existing["version"]],
    )
    updated = cur.fetchone()
    if updated is None:
        raise VersionConflict(int(existing["id"]), int(existing["version"]))
    updated = dict(updated)
    _insert_history(cur, updated["id"], "update", _history_row(existing), updated, actor)
    return updated


def update_incident(incident_id: int, body: IncidentUpdate, actor: str) -> UpdateResult:
    """Replace the staff fields of one row (PUT semantics).

    A request that changes nothing writes nothing: no version bump, no history
    row, ``changes`` empty. With ``apply_to_group`` the event text, result text
    and authority are also copied to the other live rows of the group, each
    getting its own version bump and history row.
    """
    values = normalize_fields(body)
    changes: list[RowChange] = []
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {TABLE} WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
                (incident_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                raise IncidentNotFound(incident_id)
            existing = dict(existing)
            if int(existing["version"]) != int(body.version):
                raise VersionConflict(incident_id, int(existing["version"]))
            _check_provinces(values, previous=existing)

            updated = _apply_update(cur, existing, values, actor)
            if updated is not None:
                changes.append(RowChange(audit_target(existing), _staff_view(existing), _staff_view(updated)))

            if body.apply_to_group:
                group_values = {name: values[name] for name in GROUP_FIELDS}
                cur.execute(
                    f"SELECT * FROM {TABLE} WHERE incident_group_id = %s AND id <> %s "
                    "AND deleted_at IS NULL ORDER BY id FOR UPDATE",
                    (existing["incident_group_id"], incident_id),
                )
                for sibling in [dict(r) for r in cur.fetchall()]:
                    sib_new = _apply_update(cur, sibling, group_values, actor)
                    if sib_new is not None:
                        changes.append(
                            RowChange(audit_target(sibling), _staff_view(sibling), _staff_view(sib_new))
                        )
            row = _select_one(cur, incident_id)
    return UpdateResult(row=row, changes=changes)


def soft_delete_incident(incident_id: int, actor: str) -> dict[str, Any]:
    """Mark one row deleted and return the row as it stood before. Raises IncidentNotFound."""
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {TABLE} WHERE id = %s AND deleted_at IS NULL FOR UPDATE",
                (incident_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                raise IncidentNotFound(incident_id)
            existing = dict(existing)
            cur.execute(
                f"UPDATE {TABLE} SET deleted_at = now(), deleted_by = %s "
                "WHERE id = %s AND deleted_at IS NULL RETURNING *",
                (actor, incident_id),
            )
            deleted = cur.fetchone()
            if deleted is None:
                raise IncidentNotFound(incident_id)
            _insert_history(cur, incident_id, "delete", existing, dict(deleted), actor)
    return existing


__all__ = [
    "CreateResult", "DuplicateTransaction", "GROUP_SEQ", "GroupNotFound", "IncidentNotFound",
    "InvalidProvince", "ListFilters", "OrderNotFound", "OrderNotRecordable", "RowChange",
    "UpdateResult", "VersionConflict", "create_incident", "export_rows", "find_existing_id",
    "get_row", "hydrate", "list_rows", "soft_delete_incident", "to_incident", "total_pages",
    "update_incident",
]
