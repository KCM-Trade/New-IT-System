"""OPT-0063 Phase 1 — per-order open-IP extraction from MT journals.

Two layers under test:

1. `_match_order_event` — the pure message matcher. Positive cases are the
   real journal shapes sampled 2026-09-18; negative cases are the lookalikes
   that must stay out (API echoes, request lines without a ticket, close-by,
   manager-placed, server LP-routing legs).
2. `_parse_one_log` — the gates around the matcher: client-IPv4-only,
   >=5-digit non-demo accounts, rejected requests ('no money'/'invalid'),
   and coexistence with the older last-close-IP extraction.
"""

from __future__ import annotations

import pytest

from app.services.login_ip_analyzer_service import _match_order_event, _parse_one_log


# ---------------------------------------------------------------------------
# _match_order_event — MT4 / MT4_Live2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("server", ["MT4", "MT4_Live2"])
@pytest.mark.parametrize(
    "msg, expected",
    [
        # Market order confirmation — the canonical shape.
        (
            "order #23106530, buy 0.60 XAUUSD at 4344.62000",
            {"order_ref": 23106530, "event_kind": "order", "cmd": "buy",
             "lots": 0.60, "symbol": "XAUUSD"},
        ),
        # Pending-order confirmations (limit / stop).
        (
            "order #23106533, buy limit 0.11 XAUUSD at 4305.00000",
            {"order_ref": 23106533, "event_kind": "order", "cmd": "buy limit",
             "lots": 0.11, "symbol": "XAUUSD"},
        ),
        (
            "order #23106529, sell stop 0.01 XAUUSD.c at 4237.85000",
            {"order_ref": 23106529, "event_kind": "order", "cmd": "sell stop",
             "lots": 0.01, "symbol": "XAUUSD.c"},
        ),
        # Exotic symbols must pass through verbatim.
        (
            "order #23106600, buy 5.00 #INTC at 198.12000",
            {"order_ref": 23106600, "event_kind": "order", "cmd": "buy",
             "lots": 5.0, "symbol": "#INTC"},
        ),
        (
            "order #23106601, sell 1.15 XAU-CNH at 29.12000",
            {"order_ref": 23106601, "event_kind": "order", "cmd": "sell",
             "lots": 1.15, "symbol": "XAU-CNH"},
        ),
    ],
)
def test_mt4_order_confirmation_shapes(server, msg, expected):
    assert _match_order_event(server, msg) == expected


@pytest.mark.parametrize("server", ["MT4", "MT4_Live2"])
@pytest.mark.parametrize(
    "msg",
    [
        # Server-side close notification: same 'order #N' prefix, no comma.
        "order #23105091 closed by API",
        "order #23105091 already closed",
        "order #23105091 was deleted - not enough money",
        # Request line: no ticket, never matches.
        "order buy limit 0.11 XAUUSD at 4305.00000 sl: 0.00000 tp: 0.00000 exp: never",
        # API echo of an open: 'is opened at' breaks the '<symbol> at <price>'
        # shape (and these accounts carry no client IP in practice).
        "order #23106618, sell 0.01 XAUUSD is opened at 4347.85000",
        # Close-order lines belong to the close branch, not here.
        "close market order #23106530",
    ],
)
def test_mt4_lookalikes_do_not_match(server, msg):
    assert _match_order_event(server, msg) is None


# ---------------------------------------------------------------------------
# _match_order_event — MT5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg, expected",
    [
        # Market fill, 'at market' tail.
        (
            "order performed buy 0.02 at 81169.06 [#40659198 buy 0.02 BTCUSD at market], time 324.37 ms",
            {"order_ref": 40659198, "event_kind": "performed", "cmd": "buy",
             "lots": 0.02, "symbol": "BTCUSD"},
        ),
        # Fill with an explicit price tail.
        (
            "order performed sell 0.01 at 4343.85 [#40497330 sell 0.01 XAUUSD.kcmc at 4342.37], time 271.70 ms",
            {"order_ref": 40497330, "event_kind": "performed", "cmd": "sell",
             "lots": 0.01, "symbol": "XAUUSD.kcmc"},
        ),
        # Pending-order ACTIVATION: collected as 'performed' too (open/close
        # is resolved by the nightly reconcile, not here). Usually has no
        # client IP, so the IPv4 gate drops it upstream — the matcher still
        # recognizes the shape.
        (
            "order performed buy 0.01 at 4344.37 [#40495529 buy stop 0.01 XAUUSD at 4343.53], time 436.26 ms",
            {"order_ref": 40495529, "event_kind": "performed", "cmd": "buy stop",
             "lots": 0.01, "symbol": "XAUUSD"},
        ),
        # Pending-order submission.
        (
            "order placed [#40497321 buy limit 0.01 USDCAD.kcm at 1.39000], time 0.86 ms",
            {"order_ref": 40497321, "event_kind": "placed", "cmd": "buy limit",
             "lots": 0.01, "symbol": "USDCAD.kcm"},
        ),
        (
            "order placed [#40497323 sell limit 0.01 XAUUSD.kcmc at 4381.32], time 0.61 ms",
            {"order_ref": 40497323, "event_kind": "placed", "cmd": "sell limit",
             "lots": 0.01, "symbol": "XAUUSD.kcmc"},
        ),
    ],
)
def test_mt5_order_shapes(msg, expected):
    assert _match_order_event("MT5", msg) == expected


@pytest.mark.parametrize(
    "msg",
    [
        # Close-by hedge close: explicitly out of scope (its IP is the
        # client's, but the ticket is a close-by pair, not an open).
        "order performed [#40497598 close by 0.3 XAUUSD.cent at 4308.26], time 0.90 ms",
        # Server-side LP routing leg of a market order (~300/day) — out of
        # the OPT-0063 'placed' spec; the fill is captured as 'performed'.
        "order placed for execution [#40497669 buy 0.2 XAUUSD.kcm at market], time 1.20 ms",
        # Manager placing FOR a client (account column holds the manager id).
        "order placed for '67038239' [#40497452 buy limit 0.01 XAUUSD.kcm at 4335.00], time 0.80 ms",
        # Stop-limit pending: 'limit' occupies the lots position — known
        # accepted loss (~1 line/day).
        "order placed [#40556683 buy stop limit 0.8 XAUUSD at 4402.00 (4352.75)], time 0.66 ms",
        # Modify / cancel / expire are not placements.
        "order modified [#40497371 buy limit 0.01 XAUUSD at 4330.66], time 0.59 ms",
        "order canceled [#40495524 sell stop 0.01 XAUUSD at 4340.19], time 0.59 ms",
        # Request lines and close lines belong elsewhere.
        "market buy 0.07 BTCUSD (81106.36 / 81121.36)",
        "market sell 0.02 XAUUSD.cent, close #40498093",
        "close position #40498093 buy 0.02 XAUUSD.cent at market",
    ],
)
def test_mt5_lookalikes_do_not_match(msg):
    assert _match_order_event("MT5", msg) is None


def test_match_order_event_ignores_non_order_messages():
    assert _match_order_event("MT4", "login") is None
    assert _match_order_event("MT5", "") is None
    assert _match_order_event("MT4", "deal performed #123") is None


# ---------------------------------------------------------------------------
# _parse_one_log — gates around the matcher
# ---------------------------------------------------------------------------

# MT4: shortcode \t time \t IP \t 'ACC': msg   (utf-8)
# MT5: XX \t 0 \t 6 \t time \t IP \t 'ACC': msg  (utf-16-le)

MT4_LINES = [
    # kept: plain market confirmation
    "1\t01:01:16.424\t39.144.59.59\t'8520962': order #23106530, buy 0.60 XAUUSD at 4344.62000",
    # kept: pending confirmation
    "1\t01:03:31.714\t49.93.23.191\t'8510072': order #23106533, buy limit 0.11 XAUUSD at 4305.00000",
    # dropped: empty IP (server-initiated)
    "1\t01:35:15.198\t\t'100001199': order #23106610, buy 0.01 XAUUSD is opened at 4348.31000",
    # dropped: module name in the IP column
    "1\t02:00:00.000\tStopOut.All\t'8520962': order #23106611, sell 0.10 XAUUSD at 4340.00000",
    # dropped: demo account (MT4 '7' prefix)
    "1\t03:00:00.000\t1.2.3.4\t'7021083': order #23106529, sell stop 0.01 XAUUSD.c at 4237.85000",
    # dropped: manager account (<5 digits)
    "1\t04:00:00.000\t1.2.3.4\t'114': order #23106540, buy 1.00 XAUUSD at 4340.00000",
    # dropped: rejected request
    "1\t05:00:00.000\t1.2.3.4\t'8520962': order #23106541, buy 1.00 XAUUSD at 4340.00000 [no money]",
    # not an order line at all (close branch picks this one up instead)
    "1\t06:00:00.000\t1.2.3.4\t'8520962': close order #23106530 completed",
]

MT5_LINES = [
    # kept: performed at market
    "HR\t0\t6\t00:05:09.218\t58.10.224.247\t'67043240': order performed buy 0.02 at 81169.06 [#40659198 buy 0.02 BTCUSD at market], time 324.37 ms",
    # kept: placed pending
    "ND\t0\t6\t00:51:50.340\t105.120.13.186\t'67043869': order placed [#40497321 buy limit 0.01 USDCAD.kcm at 1.39000], time 0.86 ms",
    # dropped: close-by
    "HD\t0\t6\t01:11:09.209\t1.47.157.145\t'67035825': order performed [#40497598 close by 0.3 XAUUSD.cent at 4308.26], time 0.90 ms",
    # dropped: empty IP
    "JO\t0\t6\t00:08:50.786\t\t'60002140': order performed buy 0.2 at 4344.16 [#40497331 buy 0.2 XAUUSD.kcmc at 4344.02], time 271.70 ms",
    # dropped: demo account (MT5 '3' prefix)
    "QF\t0\t6\t01:01:26.817\t58.10.178.98\t'39076925': order performed buy 1 at 4343.51 [#40497425 buy 1 Gold.demo at market], time 403.84 ms",
    # dropped: placed-for-execution routing leg
    "HI\t0\t6\t01:14:18.743\t147.50.181.90\t'67040168': order placed for execution [#40497669 buy 0.2 XAUUSD.kcm at market], time 1.20 ms",
    # dropped: rejected request
    "IE\t0\t6\t01:01:00.598\t58.10.224.247\t'67041475': order performed sell 0.01 at 4343.85 [#40497334 sell 0.01 XAUUSD.kcmc at 4342.90] invalid volume",
]


def _write_log(tmp_path, name, lines, encoding):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding=encoding)
    return path


def test_parse_one_log_mt4_order_events(tmp_path):
    log = _write_log(tmp_path, "20260918_MT4.log", MT4_LINES, "utf-8")
    _, _, _, last_trade, order_events, lines_scanned = _parse_one_log(log, "MT4", set())

    assert lines_scanned == len(MT4_LINES)
    assert [(e["order_ref"], e["cmd"]) for e in order_events] == [
        (23106530, "buy"),
        (23106533, "buy limit"),
    ]
    first = order_events[0]
    assert first["account_id"] == 8520962
    assert first["ip_address"] == "39.144.59.59"
    assert first["event_time_mt"] == "01:01:16.424"
    assert first["event_kind"] == "order"
    assert first["lots"] == 0.60
    assert first["symbol"] == "XAUUSD"
    # The close line still lands in the legacy last-close extraction.
    assert last_trade["8520962"]["order_ref"] == "#23106530"


def test_parse_one_log_mt5_order_events(tmp_path):
    log = _write_log(tmp_path, "20260918_MT5.log", MT5_LINES, "utf-16-le")
    _, _, _, _, order_events, lines_scanned = _parse_one_log(log, "MT5", set())

    assert lines_scanned == len(MT5_LINES)
    assert [(e["order_ref"], e["event_kind"]) for e in order_events] == [
        (40659198, "performed"),
        (40497321, "placed"),
    ]
    assert order_events[0]["account_id"] == 67043240
    assert order_events[0]["ip_address"] == "58.10.224.247"
    assert order_events[1]["cmd"] == "buy limit"
