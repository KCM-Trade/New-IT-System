#!/usr/bin/env python3
"""One-off import of the hand-kept Excel ledger 「出入金风控情况」 into
cs_ledger.fund_risk_incidents (spec: docs/features/fund-risk-ledger.md §8).

Default mode is a DRY RUN: parse the sheet, split multi-order rows into one
candidate per order id, look every id up on the MySQL replica, normalise the
staff-entered columns and write a review file. Nothing is written to any
database unless ``--apply`` is given, and ``--apply`` refuses to run while any
candidate is still ``needs_fix``.

    cd backend
    PYTHONPATH=. .venv/bin/python scripts/import_fund_risk_ledger.py            # dry run
    PYTHONPATH=. .venv/bin/python scripts/import_fund_risk_ledger.py --apply    # after human review

Human corrections go into ``scripts/output/fund_risk_import/overrides.csv``
(the source CSV is never edited):

    sheet_row,raw_order,action,field,value
    106,85343-1,set_txn,,985343          # use / confirm this transaction id
    86,949400,skip,,                     # drop this candidate
    30,,import_without_txn,,             # legacy row with transaction_id NULL
    12,700450,set_field,freeze_days,15   # overwrite one normalised field

``raw_order`` is the ``order_token`` column of review.csv (the piece of the
order cell this candidate came from). Leave it empty (or ``*``) to address
every candidate of the sheet row. ``set_txn`` with the id already parsed is
how a human confirms a candidate flagged for an identity doubt (client id
mismatch, "-1" suffix, 或 alternatives, far-away order date).
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from app.core.cs_ledger_pg import DDL_STATEMENTS, GROUP_SEQ, HISTORY_TABLE, TABLE
from app.schemas.cs_fund_risk import PROVINCES, IncidentFields
from app.services.cs_ledger.order_lookup import OrderLookup, ReplicaUnavailable, fetch_orders
from app.services.cs_ledger.text import mask_text

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DEFAULT_SHEET = REPO / "temp_folder" / "ryan_transactions_analysis_20261012" / "出入金风控情况(Sheet1).csv"
OUT_DIR = BACKEND / "scripts" / "output" / "fund_risk_import"
REVIEW_CSV = OUT_DIR / "review.csv"
OVERRIDES_CSV = OUT_DIR / "overrides.csv"

IMPORT_BATCH = "ledger-20261012"
ACTOR = f"import:{IMPORT_BATCH}"

# Sheet columns (0-based). Two header cells contain newlines, so address by index.
C_CLIENT, C_BIRTH, C_ID_PROV, C_CLIENT_BANK, C_TRADER_BANK, C_VENUE, C_ACCT_TYPE = 0, 1, 2, 3, 4, 5, 6
C_DIRECTION, C_IP, C_PWA, C_ORDER_DATE, C_ORDER, C_AMOUNT, C_REPORTED, C_CHANNEL = 7, 8, 9, 10, 11, 12, 13, 14
C_TRANSFER, C_SCOPE, C_DURATION, C_AUTHORITY, C_SOURCE, C_EVENT, C_RESULT = 15, 16, 17, 18, 19, 20, 21
N_COLS = 22
# Never stored, not even inside import_raw: client name, birth date, IP address.
PII_COLS = {C_CLIENT, C_BIRTH, C_IP}

# A candidate whose order date is this far from the ledger's date is not trusted.
DATE_FAR_DAYS = 180
DATE_NOTE_DAYS = 7

ORDER_TOKEN_RE = re.compile(r"(?<!\d)(\d{5,8})(-\d+)?(?!\d)")
# Leftover long digit runs (optionally spaced) after masking = a card / phone the mask missed.
RESIDUAL_DIGITS_RE = re.compile(r"\d(?:[ \-]?\d){11,}")
IPV4_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")

# Reasons a human clears by confirming the id with a ``set_txn`` override.
IDENTITY_REASONS = (
    "ambiguous_candidates", "suffix_stripped", "client_id_mismatch",
    "client_cell_holds_order_id", "order_date_far",
)


# --------------------------------------------------------------------------- normalisers

def norm_date(s: str) -> Optional[dt.date]:
    """2025/5/14, 2025-10-06 and 15/5/2025 -> date; anything else -> None."""
    s = (s or "").strip()
    m = re.search(r"(20\d\d)[/-](\d{1,2})[/-](\d{1,2})", s)
    try:
        if m:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(\d{1,2})/(\d{1,2})/(20\d\d)", s)   # dd/mm/yyyy
        if m:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def norm_channel(s: str) -> str:
    """Ledger channel text -> channel label (only used to show mismatches)."""
    u = s.strip().upper().replace(" ", "")
    if not u:
        return ""
    if "MPAYCORP" in u:
        return "Mpay Corporate"
    if "MPAY" in u:
        return "Mpay"
    if "CHIP" in u:
        return "Chippay"
    if "NPAY" in u or "P2PAY" in u or "ALIPAY" in u or "支付宝" in u:
        return "Npay(支付宝)"
    if "LINK" in u:
        return "Exlink"
    if u == "CC":
        return "CC"
    return s.strip()


def norm_direction(s: str) -> str:
    s = s.strip()
    return {"入金": "deposit", "出金": "withdrawal"}.get(s, "")


def ip_region(s: str) -> Optional[str]:
    """Region text of the 操作IP地區 cell; the address itself is dropped."""
    s = (s or "").replace("\t", " ").replace("\n", " ").strip()
    s = re.sub(r"^[0-9a-fA-F:.]+\s*", "", s).strip()
    s = IPV4_RE.sub("", s).strip()
    if not s or re.fullmatch(r"[0-9a-fA-F:.\s]+", s):
        return None
    return s[:64]


def norm_transfer(cell: str, order_id: Optional[int]) -> tuple[str, Optional[str]]:
    """-> (yes / no / unknown, note)."""
    s = cell.strip()
    if s == "是":
        return "yes", None
    if s == "否":
        return "no", None
    if not s:
        return "unknown", None
    # Per-order form: "705497否,705482,705500是".
    if order_id is not None:
        for ids, verdict in re.findall(r"((?:\d{5,8}[,，、/\s]*)+)(是|否)", s):
            if str(order_id) in re.findall(r"\d{5,8}", ids):
                return ("yes" if verdict == "是" else "no"), "transfer_per_order_text"
    return "unknown", "transfer_text_unparsed"


def norm_authority(cell: str) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    """-> (authority, scope hint, text for freeze_note, note).

    Text about 非柜 / 只进不出 describes the restriction, not who imposed it.
    """
    s = cell.strip()
    if not s or s == "尚不知":
        return "unknown", None, None, None
    if "非柜" in s:
        return "unknown", "non_counter", s, "authority_cell_is_scope_text"
    if "只进不出" in s:
        return "unknown", None, s, "authority_cell_is_scope_text"
    if any(k in s for k in ("反诈", "公安", "警", "派出所")):
        exact = s in ("反诈", "公安", "国家反诈中心")
        note = "authority_ambiguous_bank_and_antifraud" if "银行" in s else None
        return "police_antifraud", None, (None if exact else s), note
    if "支付宝" in s:
        return "alipay", None, (None if s == "支付宝" else s), None
    if "银行" in s:
        return "bank", None, (None if s == "银行" else s), None
    return "unknown", None, s, "authority_unmapped"


_SCOPE_EXACT = {
    "其中一张": "one_card", "名下一张": "one_card",
    "名下所有卡都被封": "all_cards", "名下所有": "all_cards", "所有卡": "all_cards",
    "未封": "none", "无": "none", "非柜": "non_counter", "银行卡非柜": "non_counter",
}


def norm_scope(cell: str) -> tuple[str, Optional[str]]:
    """-> (frozen_scope, text to keep in freeze_note)."""
    s = re.sub(r"\s+", " ", cell.strip())
    if not s:
        return "unknown", None
    if s in _SCOPE_EXACT:
        return _SCOPE_EXACT[s], None
    if re.search(r"名下所有|所有.{0,3}卡|卡全部冻结|名下卡片都", s):
        return "all_cards", s
    if re.search(r"其中一张|名下一张|^一张", s):
        return "one_card", s
    if "非柜" in s:
        return "non_counter", s
    if re.search(r"未封|没有冻结", s):
        return "none", s
    return "unknown", s


def norm_duration(cell: str) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
    """-> (freeze_days, scope hint, text to keep in freeze_note, note).

    Only an explicit length becomes freeze_days; "尚不知" carries no information
    (freeze_days NULL already means unknown) and is not copied into the note.
    """
    s = re.sub(r"\s+", " ", cell.strip())
    if not s or s == "尚不知":
        return None, None, None, None
    m = re.fullmatch(r"(\d+)\s*天", s)
    if m:
        return int(m.group(1)), None, None, None
    if s == "半年":
        return 180, None, None, None
    m = re.fullmatch(r"(\d+)\s*年", s)
    if m:
        return int(m.group(1)) * 365, None, None, None
    if re.fullmatch(r"\d+", s):
        return int(s), None, None, "duration_bare_number_read_as_days"
    m = re.search(r"[冻封]\D{0,2}(\d+)\s*天", s)
    if m:
        hint = "all_cards" if re.search(r"名下所有|所有.{0,3}卡", s) else None
        return int(m.group(1)), hint, s, None
    if s in ("未封", "银行卡正常"):
        return None, "none", s, None
    if "非柜" in s:
        return None, "non_counter", s, None
    return None, None, s, ("duration_text_not_a_length" if re.search(r"\d", s) else None)


_T2S = str.maketrans("廣東龍慶爾濱滄漢陽內寧蘇貴陝肅臺灣門雲遼", "广东龙庆尔滨沧汉阳内宁苏贵陕肃台湾门云辽")
_PROV_SUFFIX_RE = re.compile(r"^(省|市|壮族自治区|维吾尔自治区|回族自治区|自治区|特别行政区)")
# City-only values that actually occur in the sheet (plus a few obvious neighbours).
CITY_TO_PROVINCE = {
    "广州": "广东", "深圳": "广东", "南京": "江苏", "苏州": "江苏", "郑州": "河南",
    "金华": "浙江", "杭州": "浙江", "成都": "四川", "武汉": "湖北", "哈尔滨": "黑龙江",
    "西安": "陕西", "长沙": "湖南",
}


def split_province(cell: str) -> tuple[Optional[str], Optional[str], bool]:
    """-> (province, city remainder, mapped?). Empty cell counts as mapped."""
    raw = re.sub(r"\s+", "", cell or "")
    if not raw:
        return None, None, True
    s = raw.translate(_T2S)
    for p in sorted(PROVINCES, key=len, reverse=True):
        if s.startswith(p):
            rest = _PROV_SUFFIX_RE.sub("", s[len(p):])
            return p, (rest or None), True
    for city, prov in CITY_TO_PROVINCE.items():
        if s.startswith(city):
            return prov, s, True
    for p in sorted(PROVINCES, key=len, reverse=True):
        i = s.find(p)
        if i >= 0:
            rest = (s[:i] + _PROV_SUFFIX_RE.sub("", s[i + len(p):])) or None
            return p, rest, True
    return None, s, False


def clean_text(cell: str) -> Optional[str]:
    s = mask_text((cell or "").strip())
    return s or None


# --------------------------------------------------------------------------- parsing

def read_sheet(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    rows = list(csv.reader(open(path, encoding="utf-8-sig", newline="")))
    header = [re.sub(r"\s+", " ", h).strip() for h in rows[0]]
    body = []
    for idx, r in enumerate(rows[1:], start=2):       # sheet_row = Excel row number
        if any(c.strip() for c in r):
            body.append((idx, r + [""] * max(0, N_COLS - len(r))))
    return header, body


def build_import_raw(header: list[str], cells: list[str], token: str, ledger_client_id: Optional[int]) -> dict:
    raw: dict[str, Any] = {}
    for i, cell in enumerate(cells):
        if i in PII_COLS or not cell.strip():
            continue
        key = header[i] if i < len(header) and header[i] else f"col_{i + 1}"
        raw[key] = mask_text(cell.strip())
    raw["_order_token"] = token
    if ledger_client_id is not None:
        raw["_ledger_client_id"] = ledger_client_id
    return raw


def parse_candidates(header: list[str], body: list[tuple[int, list[str]]]) -> list[dict]:
    cands: list[dict] = []
    for sheet_row, cells in body:
        c = [x for x in cells]
        order_cell = c[C_ORDER].strip()
        tokens = [(m.group(0), int(m.group(1)), m.group(2)) for m in ORDER_TOKEN_RE.finditer(order_cell)]
        ambiguous = "或" in order_cell and len(tokens) > 1
        if not tokens:
            tokens = [("", None, None)]
        cid_m = re.search(r"(?<!\d)\d{5,7}(?!\d)", c[C_CLIENT])
        ledger_client_id = int(cid_m.group(0)) if cid_m else None

        # --- fields shared by every order of the sheet row
        shared_notes: list[str] = []
        authority, auth_scope, auth_text, auth_note = norm_authority(c[C_AUTHORITY])
        scope, scope_text = norm_scope(c[C_SCOPE])
        days, dur_scope, dur_text, dur_note = norm_duration(c[C_DURATION])
        for n in (auth_note, dur_note):
            if n:
                shared_notes.append(n)
        if scope == "unknown":
            hinted = auth_scope or dur_scope
            if hinted:
                scope = hinted
                shared_notes.append("frozen_scope_from_other_column")
        note_parts = []
        if scope_text:
            note_parts.append(f"被封资料: {scope_text}")
        if dur_text:
            note_parts.append(f"时长: {dur_text}")
        if auth_text:
            note_parts.append(f"封卡单位: {auth_text}")
        freeze_note = clean_text("；".join(note_parts))
        if freeze_note and len(freeze_note) > 500:
            freeze_note = freeze_note[:499] + "…"
            shared_notes.append("freeze_note_truncated")

        prov: dict[str, Optional[str]] = {}
        for col, name in ((C_ID_PROV, "id"), (C_CLIENT_BANK, "client_bank"), (C_TRADER_BANK, "trader_bank")):
            p, city, mapped = split_province(c[col])
            prov[f"{name}_province"], prov[f"{name}_city"] = p, city
            if not mapped:
                shared_notes.append(f"{name}_province_unmapped")

        source_raw = c[C_SOURCE].strip()
        transfer_raw = c[C_TRANSFER].strip()
        if transfer_raw == "是否成功转账":            # a header cell pasted into a data row
            transfer_raw_eff = ""
            shared_notes.append("transfer_cell_is_header_text")
        else:
            transfer_raw_eff = transfer_raw
        event_text, result_text = clean_text(c[C_EVENT]), clean_text(c[C_RESULT])
        for label, text in (("event_text", event_text), ("result_text", result_text), ("freeze_note", freeze_note)):
            if text and IPV4_RE.search(text):
                shared_notes.append(f"ip_like_text_in_{label}")
        venue_ledger = c[C_VENUE].strip().upper()
        reported = norm_date(c[C_REPORTED])
        if c[C_REPORTED].strip() and reported is None:
            shared_notes.append("reported_date_unparsed")

        for token, txn_id, suffix in tokens:
            transfer, t_note = norm_transfer(transfer_raw_eff, txn_id)
            fields = {
                "reported_date": reported,
                "transfer_succeeded": transfer,
                "frozen_scope": scope,
                "freeze_days": days,
                "freeze_note": freeze_note,
                "authority": authority,
                "client_source": {"线上": "online", "线下": "offline"}.get(source_raw),
                "client_bank_province": prov["client_bank_province"],
                "client_bank_city": prov["client_bank_city"],
                "trader_bank_province": prov["trader_bank_province"],
                "trader_bank_city": prov["trader_bank_city"],
                "id_province": prov["id_province"],
                "id_city": prov["id_city"],
                "ip_region": ip_region(c[C_IP]),
                "pwa_domain": (c[C_PWA].strip()[:200] or None),
                "account_type": (c[C_ACCT_TYPE].strip() or None),
                "venue_override": None,             # decided after the lookup
                "event_text": event_text,
                "result_text": result_text,
            }
            cands.append({
                "sheet_row": sheet_row,
                "order_cell": order_cell,
                "token": token,
                "parsed_txn_id": txn_id,
                "txn_id": txn_id,
                "suffix": suffix,
                "ambiguous": ambiguous,
                "ledger_client_id": ledger_client_id,
                "ledger_direction": norm_direction(c[C_DIRECTION]),
                "ledger_channel": norm_channel(c[C_CHANNEL]),
                "ledger_order_date": norm_date(c[C_ORDER_DATE]),
                "ledger_venue": venue_ledger,
                "cells": c,
                "fields": fields,
                "import_raw": build_import_raw(header, c, token, ledger_client_id),
                "notes": list(shared_notes) + ([t_note] if t_note else []),
                "reasons": [],
                "overrides": [],
                "confirmed": False,       # set_txn override present
                "without_txn": False,     # import_without_txn override present
                "skip": False,
                "lookup": None,
                "status": "",
            })
    return cands


# --------------------------------------------------------------------------- overrides

OVERRIDE_ACTIONS = ("set_txn", "skip", "import_without_txn", "set_field")
SETTABLE_FIELDS = tuple(IncidentFields.model_fields.keys())


def load_overrides(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for n, r in enumerate(csv.DictReader(f), start=2):
            if not any((v or "").strip() for v in r.values()):
                continue
            out.append({
                "line": n,
                "sheet_row": (r.get("sheet_row") or "").strip(),
                "raw_order": (r.get("raw_order") or "").strip(),
                "action": (r.get("action") or "").strip(),
                "field": (r.get("field") or "").strip(),
                "value": (r.get("value") or "").strip(),
            })
    return out


def apply_overrides(cands: list[dict], overrides: list[dict]) -> list[str]:
    """Mutate candidates in place; return override-file errors (block --apply)."""
    errors: list[str] = []
    for ov in overrides:
        where = f"overrides.csv line {ov['line']}"
        if ov["action"] not in OVERRIDE_ACTIONS:
            errors.append(f"{where}: unknown action '{ov['action']}'")
            continue
        if not ov["sheet_row"].isdigit():
            errors.append(f"{where}: sheet_row must be a number")
            continue
        targets = [c for c in cands if c["sheet_row"] == int(ov["sheet_row"])
                   and (ov["raw_order"] in ("", "*") or c["token"] == ov["raw_order"])]
        if not targets:
            errors.append(f"{where}: no candidate matches sheet_row={ov['sheet_row']} raw_order='{ov['raw_order']}'")
            continue
        for c in targets:
            label = ov["action"]
            if ov["action"] == "set_txn":
                if not ov["value"].isdigit() or int(ov["value"]) <= 0:
                    errors.append(f"{where}: set_txn needs a positive integer value")
                    break
                c["txn_id"], c["confirmed"], c["without_txn"] = int(ov["value"]), True, False
                label += f"={ov['value']}"
            elif ov["action"] == "skip":
                c["skip"] = True
            elif ov["action"] == "import_without_txn":
                c["txn_id"], c["without_txn"] = None, True
            else:  # set_field
                if ov["field"] not in SETTABLE_FIELDS:
                    errors.append(f"{where}: unknown field '{ov['field']}'")
                    break
                value: Any = ov["value"] or None
                if value is not None and ov["field"] in ("event_text", "result_text", "freeze_note"):
                    value = mask_text(value)
                c["fields"][ov["field"]] = value
                c.setdefault("forced_fields", set()).add(ov["field"])
                label += f":{ov['field']}"
            c["overrides"].append(label)
    return errors


# --------------------------------------------------------------------------- evaluation

def _days_apart(ledger: Optional[dt.date], snap) -> Optional[int]:
    if ledger is None:
        return None
    gaps = []
    for iso in (snap.txn_created_at, snap.txn_processed_at):
        if iso:
            gaps.append(abs((dt.date.fromisoformat(iso[:10]) - ledger).days))
    return min(gaps) if gaps else None


def evaluate(cands: list[dict], lookups: dict[int, OrderLookup]) -> None:
    """Fill lookup / venue_override / reasons / status on every candidate."""
    for c in cands:
        reasons: list[str] = []
        c["lookup"] = lookups.get(c["txn_id"]) if c["txn_id"] is not None else None
        lk: Optional[OrderLookup] = c["lookup"]

        if c["txn_id"] is None:
            if not c["without_txn"]:
                reasons.append("no_order_id" if c["parsed_txn_id"] is None else "no_transaction_id")
        else:
            if c["ambiguous"]:
                reasons.append("ambiguous_candidates(或: pick one with set_txn, skip the rest)")
            if c["suffix"] and c["txn_id"] == c["parsed_txn_id"]:
                reasons.append(f"suffix_stripped('{c['suffix']}' removed for lookup)")
            if lk is None:
                reasons.append(f"not_found_in_db({c['txn_id']})")
            else:
                snap = lk.snapshot
                if not lk.recordable:
                    reasons.append(f"not_recordable({lk.reject_reason})")
                row_ids = {x["txn_id"] for x in cands if x["sheet_row"] == c["sheet_row"]}
                if c["ledger_client_id"] is not None and c["ledger_client_id"] in row_ids:
                    reasons.append(f"client_cell_holds_order_id(db client {snap.client_id})")
                elif c["ledger_client_id"] is not None and snap.client_id != c["ledger_client_id"]:
                    reasons.append(f"client_id_mismatch(ledger {c['ledger_client_id']} / db {snap.client_id})")
                elif c["ledger_client_id"] is None:
                    c["notes"].append("ledger_client_id_missing")
                ref_date = c["ledger_order_date"] or c["fields"]["reported_date"]
                gap = _days_apart(ref_date, snap)
                if gap is not None and gap > DATE_FAR_DAYS:
                    reasons.append(f"order_date_far(ledger {ref_date} / db {(snap.txn_created_at or '')[:10]}, {gap}d)")
                elif gap is not None and gap > DATE_NOTE_DAYS:
                    c["notes"].append(f"date_differs_{gap}d")
                if c["ledger_direction"] and c["ledger_direction"] != snap.direction:
                    c["notes"].append("direction_differs_from_db")
                if c["ledger_channel"] and c["ledger_channel"] != snap.channel:
                    c["notes"].append("channel_differs_from_db")
                c["notes"].extend(f"db:{w}" for w in lk.warnings)

        if c["confirmed"] or c["without_txn"]:
            reasons = [r for r in reasons if not r.startswith(IDENTITY_REASONS)]

        # Venue: keep the ledger's value only where it differs from the CRM tag.
        if "venue_override" not in c.get("forced_fields", ()):
            db_venue = lk.snapshot.venue if lk else None
            lv = c["ledger_venue"]
            c["fields"]["venue_override"] = lv if (lv and lv != (db_venue or "")) else None

        # Contract validation + a last look for digits the mask did not catch.
        try:
            IncidentFields(**c["fields"])
        except Exception as exc:  # pydantic.ValidationError
            for err in getattr(exc, "errors", lambda: [])():
                reasons.append(f"invalid_field({'.'.join(map(str, err['loc']))}: {err['msg']})")
        for name in ("client_bank_province", "trader_bank_province", "id_province"):
            if c["fields"][name] is not None and c["fields"][name] not in PROVINCES:
                reasons.append(f"invalid_field({name}: not one of the 34 provinces)")
        for name in ("event_text", "result_text", "freeze_note"):
            if c["fields"][name] and RESIDUAL_DIGITS_RE.search(c["fields"][name]):
                reasons.append(f"unmasked_digit_run({name})")
        c["reasons"] = reasons

    live = [c for c in cands if not c["skip"]]
    by_txn = collections.defaultdict(list)
    for c in live:
        if c["txn_id"] is not None:
            by_txn[c["txn_id"]].append(c)
    for txn, group in by_txn.items():
        if len(group) > 1:
            rows = ",".join(str(g["sheet_row"]) for g in group)
            for g in group:
                g["reasons"].append(f"duplicate_transaction_id({txn} in sheet rows {rows}: skip all but one)")
    no_txn = collections.Counter(c["sheet_row"] for c in live if c["txn_id"] is None and c["without_txn"])
    for c in live:
        if c["txn_id"] is None and c["without_txn"] and no_txn[c["sheet_row"]] > 1:
            c["reasons"].append("several_rows_without_txn_in_one_sheet_row(import index allows one)")

    for c in cands:
        c["status"] = "skip" if c["skip"] else ("needs_fix" if c["reasons"] else "ok")


# --------------------------------------------------------------------------- review file

RAW_FIELD_COLS = (
    ("transfer", C_TRANSFER, ("transfer_succeeded",)),
    ("frozen_scope", C_SCOPE, ("frozen_scope",)),
    ("freeze_duration", C_DURATION, ("freeze_days",)),
    ("authority", C_AUTHORITY, ("authority",)),
    ("client_source", C_SOURCE, ("client_source",)),
    ("id_region", C_ID_PROV, ("id_province", "id_city")),
    ("client_bank_region", C_CLIENT_BANK, ("client_bank_province", "client_bank_city")),
    ("trader_bank_region", C_TRADER_BANK, ("trader_bank_province", "trader_bank_city")),
    ("reported_date", C_REPORTED, ("reported_date",)),
    ("pwa_domain", C_PWA, ("pwa_domain",)),
    ("account_type", C_ACCT_TYPE, ("account_type",)),
    ("venue", C_VENUE, ("venue_override",)),
    ("event", C_EVENT, ("event_text",)),
    ("result", C_RESULT, ("result_text",)),
)


def review_header() -> list[str]:
    h = ["sheet_row", "order_cell_raw", "order_token", "transaction_id", "status", "reasons", "notes", "overrides_applied",
         "ledger_client_id", "db_client_id", "ledger_direction", "db_direction", "ledger_channel", "db_channel",
         "ledger_order_date", "db_processed_at", "db_created_at", "db_amount_cny", "ledger_amount_raw", "db_status",
         "db_txn_type", "db_psp", "db_venue", "db_login_sid", "db_is_test_account"]
    for label, _col, names in RAW_FIELD_COLS:
        h.append(f"{label}_raw")
        h.extend(f"norm_{n}" for n in names)
    h += ["norm_ip_region", "norm_freeze_note"]
    return h


def review_row(c: dict) -> list[Any]:
    snap = c["lookup"].snapshot if c["lookup"] else None
    g = (lambda attr: getattr(snap, attr) if snap is not None else "")
    row: list[Any] = [
        c["sheet_row"], c["order_cell"], c["token"], c["txn_id"] if c["txn_id"] is not None else "",
        c["status"], "; ".join(c["reasons"]), "; ".join(dict.fromkeys(c["notes"])), "; ".join(c["overrides"]),
        c["ledger_client_id"] or "", g("client_id"), c["ledger_direction"], g("direction"),
        c["ledger_channel"], g("channel"), c["ledger_order_date"] or "", g("txn_processed_at") or "",
        g("txn_created_at") or "", g("amount_cny") if snap and snap.amount_cny is not None else "",
        c["cells"][C_AMOUNT].strip(), g("txn_status"), g("txn_type"), g("psp_name") or "", g("venue") or "",
        g("login_sid") or "", g("is_test_account"),
    ]
    for _label, col, names in RAW_FIELD_COLS:
        # Raw cells are shown masked too: the review file leaves this machine by email.
        row.append(mask_text(c["cells"][col].strip()))
        for n in names:
            v = c["fields"][n]
            row.append("" if v is None else v)
    row += [c["fields"]["ip_region"] or "", c["fields"]["freeze_note"] or ""]
    return row


def write_review(cands: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(review_header())
        for c in cands:
            w.writerow(review_row(c))


def print_summary(cands: list[dict], n_sheet_rows: int, override_errors: list[str], n_overrides: int) -> None:
    status = collections.Counter(c["status"] for c in cands)
    print(f"sheet rows (non-blank): {n_sheet_rows}   candidates (one per order id): {len(cands)}")
    print(f"status: ok={status['ok']}  needs_fix={status['needs_fix']}  skip={status['skip']}")
    print(f"overrides applied: {n_overrides - len(override_errors)} of {n_overrides}")
    for e in override_errors:
        print(f"  OVERRIDE ERROR: {e}")

    by_reason: dict[str, list[int]] = collections.defaultdict(list)
    for c in cands:
        if c["status"] == "needs_fix":
            for r in c["reasons"]:
                by_reason[r.split("(")[0]].append(c["sheet_row"])
    print("\nneeds_fix reasons (candidates; sheet rows):")
    for reason, rows in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"  {reason}: {len(rows)}  rows {sorted(set(rows))}")
    nf_rows = sorted({c["sheet_row"] for c in cands if c["status"] == "needs_fix"})
    print(f"  sheet rows with at least one needs_fix: {len(nf_rows)}")

    notes = collections.Counter(re.sub(r"_\d+d$", "", n) for c in cands for n in dict.fromkeys(c["notes"]))
    print("\nnon-blocking notes (candidates):")
    for n, k in notes.most_common():
        print(f"  {n}: {k}")

    print("\nenum distributions (candidates):")
    for name in ("transfer_succeeded", "frozen_scope", "authority", "client_source"):
        print(f"  {name}: {dict(collections.Counter(str(c['fields'][name]) for c in cands).most_common())}")
    days = collections.Counter(c["fields"]["freeze_days"] for c in cands)
    print(f"  freeze_days: {dict(sorted(days.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)))}")
    print(f"  freeze_note filled: {sum(1 for c in cands if c['fields']['freeze_note'])}")
    print(f"  reported_date filled: {sum(1 for c in cands if c['fields']['reported_date'])}")
    print(f"  venue_override written: {sum(1 for c in cands if c['fields']['venue_override'])}")
    print(f"  ip_region filled: {sum(1 for c in cands if c['fields']['ip_region'])}")

    print("\nprovince cells (per sheet row):")
    seen: set[int] = set()
    stats = {k: [0, 0] for k in ("id", "client_bank", "trader_bank")}
    col_of = {"id": C_ID_PROV, "client_bank": C_CLIENT_BANK, "trader_bank": C_TRADER_BANK}
    unmapped = []
    for c in cands:
        if c["sheet_row"] in seen:
            continue
        seen.add(c["sheet_row"])
        for k, col in col_of.items():
            if c["cells"][col].strip():
                stats[k][1] += 1
                if c["fields"][f"{k}_province"]:
                    stats[k][0] += 1
                else:
                    unmapped.append((c["sheet_row"], c["cells"][col].strip()))
    for k, (ok, total) in stats.items():
        print(f"  {k}: {ok} of {total} non-empty cells mapped to a province")
    if unmapped:
        print(f"  unmapped: {unmapped}")

    found = [c for c in cands if c["lookup"]]
    print(f"\nDB lookup: {len(found)} candidates found, "
          f"{sum(1 for c in cands if c['txn_id'] is not None and not c['lookup'])} not found, "
          f"{sum(1 for c in cands if c['txn_id'] is None)} without id")
    print(f"  db direction: {dict(collections.Counter(c['lookup'].snapshot.direction for c in found))}")
    print(f"  db channel: {dict(collections.Counter(c['lookup'].snapshot.channel for c in found).most_common())}")
    print(f"  db status: {dict(collections.Counter(c['lookup'].snapshot.txn_status for c in found).most_common())}")


# --------------------------------------------------------------------------- apply

SNAPSHOT_COLUMNS = (
    "client_id", "login_sid", "venue", "direction", "txn_type", "psp_id", "psp_name", "channel",
    "amount_cny", "txn_status", "txn_created_at", "txn_processed_at", "is_test_account",
)
STAFF_COLUMNS = tuple(IncidentFields.model_fields.keys())
INSERT_COLUMNS = (
    ("incident_group_id", "transaction_id") + SNAPSHOT_COLUMNS + ("snapshot_at",) + STAFF_COLUMNS
    + ("source", "import_batch", "import_sheet_row", "import_raw", "created_by", "updated_by")
)
INSERT_SQL = (
    f"INSERT INTO {TABLE} ({', '.join(INSERT_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(INSERT_COLUMNS))}) "
    "ON CONFLICT (import_batch, import_sheet_row, COALESCE(transaction_id, 0)) "
    "WHERE import_batch IS NOT NULL DO NOTHING RETURNING id"
)
HISTORY_SQL = (
    f"INSERT INTO {HISTORY_TABLE} (incident_id, action, old_row, new_row, actor) "
    f"SELECT t.id, 'import', NULL, to_jsonb(t), %s FROM {TABLE} t WHERE t.id = %s"
)


def build_insert_params(c: dict, group_id: int, now: dt.datetime, as_json=None) -> tuple:
    """One INSERT parameter tuple, ordered as INSERT_COLUMNS. Touches no database."""
    as_json = as_json or (lambda d: json.dumps(d, ensure_ascii=False))
    snap = c["lookup"].snapshot if c["lookup"] is not None and c["txn_id"] is not None else None
    values: dict[str, Any] = {"incident_group_id": group_id, "transaction_id": c["txn_id"]}
    for col in SNAPSHOT_COLUMNS:
        values[col] = getattr(snap, col) if snap is not None else None
    if snap is None:
        values["is_test_account"] = False            # NOT NULL column
    values["snapshot_at"] = now if snap is not None else None
    for col in STAFF_COLUMNS:
        values[col] = c["fields"][col]
    values.update(
        source="import", import_batch=IMPORT_BATCH, import_sheet_row=c["sheet_row"],
        import_raw=as_json(c["import_raw"]), created_by=ACTOR, updated_by=ACTOR,
    )
    return tuple(values[col] for col in INSERT_COLUMNS)


def _ddl_columns() -> tuple[dict[str, str], dict[str, set[str]]]:
    """Column name -> definition, and CHECK ... IN (...) sets, parsed from the frozen DDL."""
    ddl = next(s for s in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS cs_ledger.fund_risk_incidents (" in s)
    cols: dict[str, str] = {}
    checks: dict[str, set[str]] = {}
    for line in ddl.splitlines():
        m = re.match(r"\s{8}([a-z_]+)\s+(BIGSERIAL|BIGINT|TEXT|INTEGER|NUMERIC|TIMESTAMPTZ|BOOLEAN|DATE|JSONB)\b(.*)", line)
        if m:
            cols[m.group(1)] = m.group(2) + m.group(3)
    for m in re.finditer(r"CHECK \(([a-z_]+) IN \(([^)]*)\)\)", ddl):
        checks[m.group(1)] = set(re.findall(r"'([^']*)'", m.group(2)))
    return cols, checks


def self_check(cands: list[dict]) -> int:
    """Verify the apply path's row building against the DDL without touching PG."""
    cols, checks = _ddl_columns()
    assert len(cols) == 47, f"DDL parse found {len(cols)} columns"
    assert len(set(INSERT_COLUMNS)) == len(INSERT_COLUMNS), "duplicate insert column"
    unknown = set(INSERT_COLUMNS) - set(cols)
    assert not unknown, f"insert columns missing from the DDL: {unknown}"
    left_out = set(cols) - set(INSERT_COLUMNS)
    assert left_out == {"id", "version", "created_at", "updated_at", "deleted_at", "deleted_by"}, left_out
    for name, definition in cols.items():       # every NOT NULL column is inserted or has a default
        if "NOT NULL" in definition and "DEFAULT" not in definition:
            assert name in INSERT_COLUMNS, f"NOT NULL column {name} is not inserted"
    assert INSERT_SQL.count("%s") == len(INSERT_COLUMNS)
    assert HISTORY_SQL.count("%s") == 2
    assert set(checks) == {"direction", "transfer_succeeded", "frozen_scope", "authority", "client_source", "source"}

    now = dt.datetime.now(dt.timezone.utc)
    idx = {name: i for i, name in enumerate(INSERT_COLUMNS)}
    built = 0
    for n, c in enumerate(cands):
        if c["skip"]:
            continue
        p = build_insert_params(c, 1000 + n, now)
        assert len(p) == len(INSERT_COLUMNS)
        for col, allowed in checks.items():
            v = p[idx[col]]
            nullable = "NOT NULL" not in cols[col]
            assert v in allowed or (v is None and nullable), f"row {c['sheet_row']}: {col}={v!r}"
        assert p[idx["created_by"]] == p[idx["updated_by"]] == ACTOR
        assert p[idx["import_batch"]] == IMPORT_BATCH and p[idx["import_sheet_row"]] == c["sheet_row"]
        assert isinstance(p[idx["is_test_account"]], bool)
        assert p[idx["freeze_days"]] is None or p[idx["freeze_days"]] >= 0
        assert p[idx["reported_date"]] is None or isinstance(p[idx["reported_date"]], dt.date)
        raw = json.loads(p[idx["import_raw"]])
        assert not {"客户", "出生日期", "操作IP地區"} & set(raw), "PII column leaked into import_raw"
        for v in raw.values():
            assert not (isinstance(v, str) and re.search(r"(?<!\d)\d{16,19}(?!\d)", v)), "card number in import_raw"
        if c["txn_id"] is None:
            assert p[idx["client_id"]] is None and p[idx["snapshot_at"]] is None
        elif c["lookup"] is not None:
            assert p[idx["transaction_id"]] == c["lookup"].snapshot.transaction_id
        built += 1
    return built


def apply(cands: list[dict]) -> None:
    """Insert every ``ok`` candidate in one transaction. Idempotent per import batch."""
    from psycopg2.extras import Json

    from app.core.risk_cases_pg import risk_cases_conn

    todo = [c for c in cands if c["status"] == "ok"]
    txn_ids = [c["txn_id"] for c in todo if c["txn_id"] is not None]
    inserted = already_imported = 0
    live_skipped: list[tuple[int, int]] = []
    with risk_cases_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT transaction_id, import_batch, import_sheet_row FROM {TABLE} "
                "WHERE deleted_at IS NULL AND transaction_id = ANY(%s)", (txn_ids,))
            live = {int(r["transaction_id"]): r for r in cur.fetchall()}
            cur.execute(
                f"SELECT import_sheet_row, MIN(incident_group_id) AS gid FROM {TABLE} "
                "WHERE import_batch = %s GROUP BY import_sheet_row", (IMPORT_BATCH,))
            groups = {int(r["import_sheet_row"]): int(r["gid"]) for r in cur.fetchall()}
            cur.execute("SELECT now() AS now")
            now = cur.fetchone()["now"]
            for c in todo:
                holder = live.get(c["txn_id"]) if c["txn_id"] is not None else None
                if holder is not None:
                    same = holder["import_batch"] == IMPORT_BATCH and holder["import_sheet_row"] == c["sheet_row"]
                    if same:
                        already_imported += 1
                    else:
                        live_skipped.append((c["sheet_row"], c["txn_id"]))
                    continue
                if c["sheet_row"] not in groups:      # one group per original sheet row
                    cur.execute(f"SELECT nextval('{GROUP_SEQ}') AS gid")
                    groups[c["sheet_row"]] = int(cur.fetchone()["gid"])
                cur.execute(INSERT_SQL, build_insert_params(c, groups[c["sheet_row"]], now, as_json=Json))
                row = cur.fetchone()
                if row is None:                        # import index conflict
                    already_imported += 1
                    continue
                cur.execute(HISTORY_SQL, (ACTOR, row["id"]))
                inserted += 1
    print(f"\nAPPLY done (batch {IMPORT_BATCH}): inserted={inserted}  "
          f"already_imported={already_imported}  skipped_live_elsewhere={len(live_skipped)}")
    for sheet_row, txn in live_skipped:
        print(f"  sheet row {sheet_row}: transaction {txn} is already a live ledger row, not imported")


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET, help="source CSV export of the ledger")
    ap.add_argument("--overrides", type=Path, default=OVERRIDES_CSV, help="human corrections (see module docstring)")
    ap.add_argument("--apply", action="store_true", help="write to PostgreSQL (after the review is clean)")
    args = ap.parse_args()

    header, body = read_sheet(args.sheet)
    cands = parse_candidates(header, body)
    overrides = load_overrides(args.overrides)
    override_errors = apply_overrides(cands, overrides)

    ids = sorted({c["txn_id"] for c in cands if c["txn_id"] is not None})
    try:
        lookups = fetch_orders(ids)
    except ReplicaUnavailable as exc:
        print(f"replica unavailable, nothing written: {exc}", file=sys.stderr)
        return 2
    evaluate(cands, lookups)
    write_review(cands)
    print_summary(cands, len(body), override_errors, len(overrides))
    built = self_check(cands)
    print(f"\nself-check: {built} INSERT parameter tuples built and checked against the DDL (no PG access)")
    print(f"review file: {REVIEW_CSV}")
    print(f"overrides file: {args.overrides} ({'present' if args.overrides.exists() else 'not present'})")

    if not args.apply:
        print("\nDRY RUN: no database was written. Re-run with --apply after the review is clean.")
        return 0
    blocked = sum(1 for c in cands if c["status"] == "needs_fix")
    if blocked or override_errors:
        print(f"\nREFUSING --apply: {blocked} candidate(s) still needs_fix, "
              f"{len(override_errors)} override error(s).", file=sys.stderr)
        return 1
    apply(cands)
    return 0


if __name__ == "__main__":
    sys.exit(main())
