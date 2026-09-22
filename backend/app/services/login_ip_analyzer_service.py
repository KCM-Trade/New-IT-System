"""
Login IP Monitor — log analyzer service.

Takes one day's raw .log files (one per MT server) and produces the JSON
analysis artifacts + writes monitored-account login rows into login_ip.db.
Since 2026-07 the same single pass also extracts, per trading account, the
LAST client-initiated CLOSE order of the day that carried a client IPv4 —
persisted to the `last_trade_ip` table and `analysis_last_trade_ip.json`
(90-day retention).
Since 2026-09 (OPT-0063 Phase 1) the same pass ALSO extracts EVERY order
placement event carrying a client IPv4 (MT4 `order #N, ...` confirmations;
MT5 `order performed` / `order placed`) — persisted to the separate
`login_ip_orders.db` (`order_ip` table, 120-day retention) and
`analysis_order_ip.json`. Open/close is NOT judged here for MT5 (the journal
cannot tell them apart); the nightly reconciliation does that in Phase 2.

Called by
---------
- `scripts/backfill_login_ip.py`       — historical backfill
- `core/login_ip_scheduler.py`         — Phase 5 APScheduler 08:30 daily job
- `api/v1/routes/login_ip.py`          — Phase 6 admin "run-now" API

Parsing contract (DO NOT change — must match the legacy system 1:1 so the
downstream report service / email template keeps working):

    MT4 / MT4_Live2:  utf-8,      IP = parts[2], account = parts[3]
    MT5:              utf-16-le,  IP = parts[4], account = parts[5]

    Line filter (2-stage for speed):
      1) substring `login` present
      2) substring `': login` OR `:\tlogin` present (rejects "cannot login" etc.)

    Account extraction:  acc_part.split("':")[0].strip("'")  must isdigit()
    IP validation:       must contain '.' (IPv4) or ':' (IPv6)

Two-pass scan (§3 "re-scan" semantics):
    Pass 1  records every login + grabs raw lines for MONITORED accounts
    Pass 2  only runs if there are CORRELATED accounts (non-monitored, but
            sharing an IP with a monitored one) — grabs their raw lines too

Raw-log cap: MAX_RAW_LOGS_PER_ACCOUNT = 10 (matches legacy `MAX_LOGS_TO_STORE`).
Capping happens AFTER both passes so we always keep the most recent 10 lines,
not the first 10 seen.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.core import login_ip_db, login_ip_orders_db

logger = logging.getLogger(__name__)

# Output JSON filenames (identical to legacy project so the report service
# can read either source without branching).
IP_MAPPING_FILE = "analysis_ip_to_accounts.json"
ACCOUNT_LOGINS_FILE = "analysis_account_logins.json"
RAW_LOGINS_FILE = "analysis_raw_logins.json"
# Daily per-account "last trade order IP" snapshot (2026-07 requirement).
LAST_TRADE_IP_FILE = "analysis_last_trade_ip.json"
# Daily per-order open IP capture (OPT-0063 Phase 1, 2026-09).
ORDER_IP_FILE = "analysis_order_ip.json"

# --- last-trade-IP extraction rules ------------------------------------------
# Business definition (decided 2026-07-13, narrowed same day): for every
# trading account, keep the LAST client-initiated CLOSE order of the MT day
# that carries a valid client IPv4. Opens / pending orders / modifies /
# deletes do NOT count. Server-initiated lines (StopOut.*, dealer routing,
# SL/TP activation) have a module name or an empty IP column and never match.
#
# Demo / manager filtering happens HERE (at parse time, per business decision):
#   - MT5:            demo logins all start with '3' (verified against the
#                     `Group demo\\*` audit lines in the live journal; real
#                     client groups KCM*/KCMC* use '6'/'7' prefixes)
#   - MT4/MT4_Live2:  demo/test logins start with '7' (house convention,
#                     same as open_positions_service `LOGIN NOT LIKE '7%'`)
#   - all servers:    manager/dealer accounts are short numbers ('25', '114')
#                     → require at least 5 digits
_DEMO_PREFIX_BY_SERVER = {"MT5": "3", "MT4": "7", "MT4_Live2": "7"}
_MIN_CLIENT_LOGIN_LEN = 5

# Close-order detection, matched against the message after "'<acc>': ".
# Enumerated from real journal samples (20260710) — the two server families
# log client closes in completely different shapes:
#
#   MT4/MT4_Live2:  'close market order #N (...)'   close request
#                   'close order #N (...) completed' execution confirmation
#     Both start with 'close '. The only other 'close ' shape, 'close all
#     orders due stop out', is server-initiated (empty IP column) and is
#     already rejected by the IPv4 gate below.
#
#   MT5:            'market sell 0.02 X, close #N buy ...'  market close
#                   'close position #N ... by position #M'  close-by (hedge)
#     Server-side variants ('close stop-out position', StopOut.All module
#     lines, empty-IP SL/TP 'market ..., close #N' activations) never carry
#     a client IPv4 and fall to the same gate.


def _is_mt4_close(msg: str) -> bool:
    return msg.startswith("close ")


_MT5_MARKET_PREFIXES = ("market buy", "market sell")


def _is_mt5_close(msg: str) -> bool:
    if msg.startswith("close position #"):
        return True
    return msg.startswith(_MT5_MARKET_PREFIXES) and ", close #" in msg

_ORDER_REF_RE = re.compile(r"#(\d+)")

# --- order-event extraction rules (OPT-0063 Phase 1, 2026-09) ----------------
# Matched against the message after "'<acc>': " — the same anchor and the same
# upstream gates (client IPv4, >=5-digit non-demo account) as close detection.
# Samples below are from the 20260918 journals.
#
# MT4 / MT4_Live2 — the CONFIRMATION line carries the ticket; the request line
# ('order buy limit 0.11 XAUUSD at 4305.00000 sl: ...') has no '#N,' and never
# matches:
#   '8520962': order #23106530, buy 0.60 XAUUSD at 4344.62000
#   '8510072': order #23106533, buy limit 0.11 XAUUSD at 4305.00000
# Lookalikes that must NOT match:
#   '8509886': order #23105091 closed by API            (no comma after #N)
#   '100001333': order #23106618, sell 0.01 XAUUSD is opened at 4347.85000
#     (API echo account; empty IP in practice, and 'is opened at' breaks the
#      '<symbol> at <price>' shape, so it cannot match even with an IP)
_MT4_ORDER_RE = re.compile(
    r"^order #(\d+), (buy|sell)( (?:limit|stop))? ([\d.]+) (\S+) at [\d.]+"
)

# MT5 — the order ticket lives inside the trailing '[#N ...]' bracket.
# 'performed' (market fills AND pending-order activations; bracket tail is
# 'at market' or 'at <price>'):
#   '67043240': order performed buy 0.02 at 81169.06 [#40659198 buy 0.02 BTCUSD at market], time 324.37 ms
#   '67039488': order performed sell 0.01 at 4343.85 [#40497330 sell 0.01 XAUUSD.kcmc at 4342.37], time ...
# ⚠ open vs close is NOT decided here: a close confirmation looks identical to
# an open ('[#N buy 0.02 XAUUSD.cent at market]' with N = the CLOSE order
# ticket). The nightly reconciliation (Phase 2) judges via
# mt5_orders_history."Order" == PositionID. What IS excluded here:
#   '[#40497598 close by 0.3 XAUUSD.cent at 4308.26]'  (close-by hedge close —
#   the (buy|sell) verb anchor rejects it structurally)
_MT5_PERFORMED_RE = re.compile(
    r"^order performed .*\[#(\d+) (buy|sell)( (?:limit|stop))? ([\d.]+) (\S+) at (?:market|[\d.]+)\]"
)

# 'placed' — pending-order submission, bracket immediately after 'placed ':
#   '67043869': order placed [#40497321 buy limit 0.01 USDCAD.kcm at 1.39000], time 0.86 ms
# Pending verbs only (limit|stop) — no plain buy/sell exists on this shape
# (verified 20260918). Two lookalikes are excluded ON PURPOSE:
#   'order placed for execution [#N buy 0.2 XAUUSD.kcm at market]' (~300/day,
#     server-side LP routing leg of a market order; out of OPT-0063's spec —
#     the market fill itself is already captured as 'performed')
#   'order placed for '67038239' [#N buy limit ...]' (manager placing FOR a
#     client — the account column holds the manager's short id, so the >=5
#     digit gate drops it upstream; the anchored '\[#' is the second fence)
#   'order placed [#40556683 buy stop limit 0.8 XAUUSD at 4402.00 (4352.75)]'
#     (stop-limit: 'limit' lands where lots should be — ~1 line/day, accepted loss)
_MT5_PLACED_RE = re.compile(
    r"^order placed \[#(\d+) (buy|sell)( (?:limit|stop)) ([\d.]+) (\S+) at [\d.]+\]"
)


def _match_order_event(server_name: str, msg: str) -> dict | None:
    """Match an order-placement journal message; return the event or None.

    Pure matcher — caller supplies account/IP/time from the line columns.
    Returns a dict with order_ref (int ticket), event_kind, cmd, lots, symbol.
    MT5 events are collected WITHOUT an open/close verdict (see above).
    """
    # Cheap gate: every order shape on both server families starts with
    # 'order ' — keeps the regexes off the millions of unrelated lines.
    if not msg.startswith("order "):
        return None

    if server_name == "MT5":
        m = _MT5_PERFORMED_RE.match(msg)
        if m:
            ticket, verb, modifier, lots, symbol = m.groups()
            event_kind = "performed"
        else:
            m = _MT5_PLACED_RE.match(msg)
            if not m:
                return None
            ticket, verb, modifier, lots, symbol = m.groups()
            event_kind = "placed"
        return {
            "order_ref": int(ticket),
            "event_kind": event_kind,
            "cmd": verb + (modifier or ""),
            "lots": float(lots),
            "symbol": symbol,
        }

    m = _MT4_ORDER_RE.match(msg)
    if not m:
        return None
    ticket, verb, modifier, lots, symbol = m.groups()
    return {
        "order_ref": int(ticket),
        "event_kind": "order",
        "cmd": verb + (modifier or ""),
        "lots": float(lots),
        "symbol": symbol,
    }


# Per-account cap on captured raw log lines. Keeping this small keeps the JSON
# small enough to ship inside email attachments and render quickly in the UI.
MAX_RAW_LOGS_PER_ACCOUNT = 10

# Servers we currently process. Declared here rather than imported from the
# FTP service to avoid an otherwise-unnecessary cross-module coupling — the
# two services should be independently testable.
SUPPORTED_SERVERS: tuple[str, ...] = ("MT4", "MT5", "MT4_Live2")


# ---------------------------------------------------------------------------
# Single-file parser
# ---------------------------------------------------------------------------


def _parse_one_log(
    log_path: Path,
    server_name: str,
    monitored_ids: set[str],
) -> tuple[
    dict[str, set[int]],
    dict[str, Counter],
    dict[str, list[str]],
    dict[str, dict],
    list[dict],
    int,
]:
    """Parse a single-day .log for one server.

    Returns `(ip_to_accounts, account_ip_logins, raw_login_logs, last_trade,
    order_events, lines_scanned)`. Every key is a str so JSON serialization
    doesn't need any custom converters later. `last_trade` maps account_id →
    the LAST client close order of the day that carried a valid IPv4 (see
    `_is_mt4_close` / `_is_mt5_close` and the demo/manager filter above).
    `order_events` (OPT-0063) is one dict per order-placement line that
    carried a client IPv4 — see `_match_order_event`. `lines_scanned` feeds
    the `order_ip_parse_runs` audit table.

    See module docstring for the two-pass logic. `monitored_ids` is the set of
    monitored account-id STRINGS for this specific server — passing it in
    (rather than looking up by server inside this function) keeps the
    function pure-ish and easy to unit test.
    """
    if server_name == "MT5":
        encoding = "utf-16-le"
        ip_idx, acc_idx = 4, 5
        time_idx = 3
        is_close = _is_mt5_close
    else:
        encoding = "utf-8"
        ip_idx, acc_idx = 2, 3
        time_idx = 1
        is_close = _is_mt4_close

    demo_prefix = _DEMO_PREFIX_BY_SERVER.get(server_name)

    ip_to_accounts: dict[str, set[int]] = defaultdict(set)
    account_ip_logins: dict[str, Counter] = defaultdict(Counter)
    raw_login_logs: dict[str, list[str]] = defaultdict(list)
    # Journal lines are chronological, so "last close order" is simply the
    # latest matching line — overwrite wins, memory stays O(#accounts).
    last_trade: dict[str, dict] = {}
    # OPT-0063: every order-placement event with a client IPv4. ~120k rows on
    # a weekday (dominated by MT5 'order performed') — fine as a list, it is
    # drained into SQLite right after the parse.
    order_events: list[dict] = []

    lines_scanned = 0
    logins_matched = 0
    close_lines_matched = 0
    order_lines_matched = 0

    # ----- Pass 1 ---------------------------------------------------------
    with open(log_path, "r", encoding=encoding, errors="ignore") as fp:
        for line in fp:
            lines_scanned += 1
            is_login = "login" in line and (
                "': login" in line or ":\tlogin" in line
            )
            if not is_login:
                # --- last-trade-IP branch (cheap gate first) ---------------
                if "': " not in line:
                    continue
                parts = line.split("\t")
                if len(parts) <= max(ip_idx, acc_idx, time_idx):
                    continue
                ip_str = parts[ip_idx].strip()
                # Client IPv4 gate: rejects empty (server-initiated) and
                # module names like 'StopOut.All' / 'DealerLogic 776'.
                if ip_str.count(".") != 3 or not ip_str[:1].isdigit():
                    continue
                acc_part = parts[acc_idx].strip()
                if not acc_part.startswith("'") or "': " not in acc_part:
                    continue
                acc_id_str, _, msg = acc_part[1:].partition("': ")
                if not acc_id_str.isdigit():
                    continue
                # Demo / manager filter (business decision 2026-07-13).
                if len(acc_id_str) < _MIN_CLIENT_LOGIN_LEN:
                    continue
                if demo_prefix and acc_id_str.startswith(demo_prefix):
                    continue
                # --- order-placement branch (OPT-0063), BEFORE close -------
                # Runs on the same gated stream as close detection (client
                # IPv4 + real non-demo account already enforced above).
                # Rejected requests are not order events: 'no money' (MT4
                # rejection marker, same rule as close) and 'invalid'.
                if "no money" not in msg and "invalid" not in msg:
                    order_event = _match_order_event(server_name, msg)
                    if order_event is not None:
                        order_event["account_id"] = int(acc_id_str)
                        order_event["ip_address"] = ip_str
                        order_event["event_time_mt"] = parts[time_idx].strip()
                        order_events.append(order_event)
                        order_lines_matched += 1
                # MT4 rejections keep the verb ('... [no money]') — a
                # refused request is not a close order.
                if is_close(msg) and "no money" not in msg:
                    # First '#N' in the message is the order/position being
                    # closed, for both MT4 shapes and both MT5 shapes.
                    ref_match = _ORDER_REF_RE.search(msg)
                    last_trade[acc_id_str] = {
                        "ip": ip_str,
                        "event_time_mt": parts[time_idx].strip(),
                        "event_kind": "close",
                        "order_ref": f"#{ref_match.group(1)}" if ref_match else None,
                    }
                    close_lines_matched += 1
                continue

            parts = line.split("\t")
            if len(parts) <= max(ip_idx, acc_idx):
                continue

            ip_str = parts[ip_idx].strip()
            acc_part = parts[acc_idx].strip()

            if "." not in ip_str and ":" not in ip_str:
                continue
            if "':" not in acc_part:
                continue

            acc_id_str = acc_part.split("':")[0].strip("'")
            if not acc_id_str.isdigit():
                continue

            acc_id = int(acc_id_str)
            ip_to_accounts[ip_str].add(acc_id)
            account_ip_logins[acc_id_str][ip_str] += 1
            logins_matched += 1

            # Monitored accounts are captured in pass 1 because we already
            # know they're interesting — saves a second-pass substring match.
            if acc_id_str in monitored_ids:
                raw_login_logs[acc_id_str].append(line.strip())

    # ----- Identify correlated accounts -----------------------------------
    monitored_ips: set[str] = set()
    for acc_id_str in monitored_ids:
        if acc_id_str in account_ip_logins:
            monitored_ips.update(account_ip_logins[acc_id_str].keys())

    correlated_ids: set[str] = set()
    for ip in monitored_ips:
        for acc_id in ip_to_accounts.get(ip, set()):
            if str(acc_id) not in monitored_ids:
                correlated_ids.add(str(acc_id))

    # ----- Pass 2 (only if needed) ----------------------------------------
    # Cheaper to re-read the file than to keep every login line in memory
    # during pass 1 — daily MT5 logs can be 2-3 GB.
    if correlated_ids:
        logger.info(
            "[%s] re-scanning for raw logs of %d correlated account(s)",
            server_name,
            len(correlated_ids),
        )
        # Build the substring search tokens once rather than per-line, since
        # these loops iterate millions of times.
        tokens = [(acc_id_str, f"'{acc_id_str}': login") for acc_id_str in correlated_ids]
        with open(log_path, "r", encoding=encoding, errors="ignore") as fp:
            for line in fp:
                if "login" not in line:
                    continue
                for acc_id_str, token in tokens:
                    if token in line:
                        raw_login_logs[acc_id_str].append(line.strip())
                        break  # same line shouldn't match 2 accounts

    # ----- Trim raw logs to the per-account cap ---------------------------
    # Keep the LAST N (most recent), matching legacy `[-MAX_LOGS_TO_STORE:]`.
    for acc_id_str in list(raw_login_logs.keys()):
        raw_login_logs[acc_id_str] = raw_login_logs[acc_id_str][-MAX_RAW_LOGS_PER_ACCOUNT:]

    logger.info(
        "[%s] parsed: %d lines scanned, %d login events, %d unique IPs, %d unique accounts, "
        "%d raw-log accounts captured, %d close lines → %d last-trade-IP accounts, "
        "%d order events",
        server_name,
        lines_scanned,
        logins_matched,
        len(ip_to_accounts),
        len(account_ip_logins),
        len(raw_login_logs),
        close_lines_matched,
        len(last_trade),
        order_lines_matched,
    )
    return ip_to_accounts, account_ip_logins, raw_login_logs, last_trade, order_events, lines_scanned


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _json_default(obj: Any):
    """Make `set` / `Counter` / `defaultdict` JSON-serializable in one place."""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, (Counter, defaultdict)):
        return dict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _save_json(data: dict, out_path: Path, compact: bool = False) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        # compact=True for the ~120k-rows/day order_ip dump: indent=2 would
        # put every field on its own line and triple the file size.
        if compact:
            json.dump(data, fp, separators=(",", ":"), ensure_ascii=False, default=_json_default)
        else:
            json.dump(data, fp, indent=2, ensure_ascii=False, default=_json_default)
    size_kb = out_path.stat().st_size / 1024
    logger.info("saved %s (%.1f KB)", out_path.name, size_kb)


# ---------------------------------------------------------------------------
# Monitored-accounts helpers
# ---------------------------------------------------------------------------


def _load_monitored_from_db() -> dict[str, list[dict]]:
    """Wrap the DB call so tests / backfill can stub it out easily."""
    return login_ip_db.get_monitored_accounts()


def _monitored_ids_by_server(monitored: dict[str, list[dict]]) -> dict[str, set[str]]:
    """Flatten `{server: [{'account_id':..., ...}]}` into `{server: {"123","456"}}`.

    IDs are stored as STRINGS because log parsing always yields strings and
    set-membership checks on strings avoid an `int()` conversion per line.
    """
    result: dict[str, set[str]] = {}
    for server, accounts in monitored.items():
        result[server] = {str(info["account_id"]) for info in accounts}
    return result


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def analyze_date(
    target_date: str,
    log_dir: Path | str,
    out_dir: Path | str,
    monitored_accounts: dict[str, list[dict]] | None = None,
    write_to_db: bool = True,
) -> dict:
    """Parse all .log files for one date and persist the 3 JSON + login_history rows.

    Args:
        target_date: YYYYMMDD. Used to locate the `<log_dir>/<target_date>/`
            subdir and to tag login_history rows.
        log_dir:  Parent of dated raw-log subdirectories — typically the
            `tmp/` root `backend/data/login_ip/tmp`. We read `.log` files from
            `<log_dir>/<target_date>/`.
        out_dir:  Parent of dated JSON output subdirectories — typically
            `backend/data/login_ip`. We write JSON to `<out_dir>/<target_date>/`.
        monitored_accounts: Optional override (mainly for tests). None means
            "load from the login_ip.db watchlist".
        write_to_db: When True (default), every monitored account's
            (account_id, ip, date, server) tuple is pushed into
            `login_history` with INSERT OR IGNORE. Pass False only for
            tests / dry-runs.

    Returns a per-server summary dict:
        {
          'date': '20260421',
          'servers': {
            'MT4': {
              'unique_ips': 4068, 'unique_accounts': 1421,
              'total_logins': 81048, 'monitored_logins': 12,
              'correlated_accounts': 5, 'raw_logs_captured_accounts': 17,
              'last_trade_ip_accounts': 780, 'order_events': 9518,
            },
            ...
          },
          'login_history_inserted': 42,
          'last_trade_ip_upserted': 1488,
          'order_ip_upserted': 118448,
          'status': 'ok' | 'empty' | 'partial',
        }
    """
    log_day_dir = Path(log_dir) / target_date
    out_day_dir = Path(out_dir) / target_date

    summary: dict[str, Any] = {
        "date": target_date,
        "servers": {},
        "login_history_inserted": 0,
        "last_trade_ip_upserted": 0,
        "order_ip_upserted": 0,
        "status": "empty",
    }

    if not log_day_dir.is_dir():
        logger.warning("analyze_date: log dir missing, nothing to do: %s", log_day_dir)
        return summary

    log_files = sorted(log_day_dir.glob("*.log"))
    if not log_files:
        logger.warning("analyze_date: no .log files under %s", log_day_dir)
        return summary

    # Watchlist: load once for the whole date.
    if monitored_accounts is None:
        monitored_accounts = _load_monitored_from_db()
    monitored_ids_per_server = _monitored_ids_by_server(monitored_accounts)
    all_monitored_ids: set[int] = {
        int(info["account_id"])
        for accounts in monitored_accounts.values()
        for info in accounts
    }

    ip_all: dict[str, dict] = {}
    acc_all: dict[str, dict] = {}
    raw_all: dict[str, dict] = {}
    trade_all: dict[str, dict] = {}
    order_all: dict[str, list[dict]] = {}
    # Per-server line counts feed the order_ip_parse_runs audit table, so the
    # Phase 2 coverage endpoint can flag a truncated log weeks later.
    lines_scanned_by_server: dict[str, int] = {}

    # ----- Parse each server's log ---------------------------------------
    for log_path in log_files:
        # Filename convention: `YYYYMMDD_<server_name>.log`
        try:
            server_name = log_path.stem.split("_", 1)[1]
        except IndexError:
            logger.warning("unrecognized filename, skip: %s", log_path.name)
            continue

        logger.info("--- analyzing [%s] from %s ---", server_name, log_path.name)
        monitored_ids = monitored_ids_per_server.get(server_name, set())

        try:
            ip_map, acc_map, raw_map, trade_map, order_events, lines_scanned = (
                _parse_one_log(log_path, server_name, monitored_ids)
            )
        except Exception as exc:
            logger.exception("[%s] parse FAILED: %s", server_name, exc)
            continue

        ip_all[server_name] = ip_map
        acc_all[server_name] = acc_map
        if raw_map:
            raw_all[server_name] = raw_map
        if trade_map:
            trade_all[server_name] = trade_map
        order_all[server_name] = order_events
        lines_scanned_by_server[server_name] = lines_scanned

        # Per-server stats for the caller.
        monitored_login_count = sum(
            sum(counter.values())
            for aid, counter in acc_map.items()
            if aid in monitored_ids
        )
        total_logins = sum(sum(counter.values()) for counter in acc_map.values())
        summary["servers"][server_name] = {
            "unique_ips": len(ip_map),
            "unique_accounts": len(acc_map),
            "total_logins": total_logins,
            "monitored_logins": monitored_login_count,
            "correlated_accounts": sum(1 for aid in raw_map if aid not in monitored_ids),
            "raw_logs_captured_accounts": len(raw_map),
            "last_trade_ip_accounts": len(trade_map),
            "order_events": len(order_events),
        }

    if not acc_all:
        logger.warning("analyze_date: all server parses failed for %s", target_date)
        return summary

    # ----- Save JSONs (always written, even if one server is missing) -----
    _save_json(ip_all, out_day_dir / IP_MAPPING_FILE)
    _save_json(acc_all, out_day_dir / ACCOUNT_LOGINS_FILE)
    _save_json(raw_all, out_day_dir / RAW_LOGINS_FILE)
    _save_json(trade_all, out_day_dir / LAST_TRADE_IP_FILE)
    _save_json(order_all, out_day_dir / ORDER_IP_FILE, compact=True)

    # ----- Write per-order open-IP rows (OPT-0063) ------------------------
    if write_to_db:
        order_records = [
            (
                target_date,
                server_name,
                ev["account_id"],
                ev["order_ref"],
                ev["ip_address"],
                ev["event_time_mt"],
                ev["event_kind"],
                ev["cmd"],
                ev["lots"],
                ev["symbol"],
            )
            for server_name, events in order_all.items()
            for ev in events
        ]
        summary["order_ip_upserted"] = login_ip_orders_db.upsert_order_ips(order_records)
        # Parse audit: one row per (day, server) actually parsed, so coverage
        # can later tell "log was truncated" apart from "no orders that day".
        login_ip_orders_db.record_parse_runs(
            (
                target_date,
                server_name,
                lines_scanned_by_server[server_name],
                len(order_all[server_name]),
            )
            for server_name in order_all
        )

    # ----- Write per-account last-trade-IP rows ----------------------------
    if write_to_db and trade_all:
        trade_records = [
            (
                target_date,
                server_name,
                int(acc_id_str),
                info["ip"],
                info["event_time_mt"],
                info["event_kind"],
                info["order_ref"],
            )
            for server_name, trades in trade_all.items()
            for acc_id_str, info in trades.items()
        ]
        summary["last_trade_ip_upserted"] = login_ip_db.upsert_last_trade_ips(
            trade_records
        )

    # ----- Write monitored-account login_history rows ---------------------
    if write_to_db and all_monitored_ids:
        records = []
        for server_name, logins in acc_all.items():
            for acc_id_str, ip_counter in logins.items():
                try:
                    acc_id = int(acc_id_str)
                except ValueError:
                    continue
                if acc_id not in all_monitored_ids:
                    continue
                for ip in ip_counter.keys():
                    records.append((acc_id, ip, target_date, server_name))

        if records:
            # login_ip_db.add_login_history uses INSERT OR IGNORE, so re-running
            # backfill is safe — duplicates are absorbed by the UNIQUE constraint.
            # The DB layer already logs inserted-vs-requested, no need to log again here.
            summary["login_history_inserted"] = login_ip_db.add_login_history(records)

    summary["status"] = "ok" if len(summary["servers"]) == len(log_files) else "partial"
    return summary
