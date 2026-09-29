"""Price gap, money and time conversion for one fill (02 §2 / §3.1 / §4) — pure.

Money is never truncated here (01 D9): a better-than-request fill yields a
negative amount; netting vs positive-only is decided by the query core.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from app.services.rule_intraday_return_service import MT_SERVER_TZ

from .errors import ExecCompError
from .models import RawFill

FILETIME_EPOCH_OFFSET_S = 11644473600
FILETIME_TICKS_PER_S = 10**7
_PX_DECIMALS = 8          # prices have <= 5 digits; kill float subtraction noise
_OFFSET_STEP_S = 30 * 60  # server offsets are whole half-hours
_OFFSET_MIN_S, _OFFSET_MAX_S = 1 * 3600, 4 * 3600  # sane band for GMT+2/+3

ENTRY_KIND = {0: "open", 1: "close", 2: "inout", 3: "close_by"}


def side(fill: RawFill) -> str:
    """Direction from deals.Action — never orders.Type (close-by Type=8 would
    be read as a sell; 02 §4)."""
    if fill.action == 0:
        return "buy"
    if fill.action == 1:
        return "sell"
    raise ValueError(f"deal {fill.deal_id}: non-trade action {fill.action}")


def ref_price(fill: RawFill) -> Optional[float]:
    """Request price (02 §3.1): PriceOrder when > 0, else PriceCurrent."""
    if not fill.order_found:
        return None
    if fill.price_order and fill.price_order > 0:
        return fill.price_order
    return fill.price_current


def worse_px(fill: RawFill) -> Optional[float]:
    """> 0 = the client got a worse price than requested."""
    ref = ref_price(fill)
    if ref is None:
        return None
    diff = (fill.price - ref) if side(fill) == "buy" else (ref - fill.price)
    return round(diff, _PX_DECIMALS)


def outcome(wpx: Optional[float]) -> Optional[str]:
    if wpx is None:
        return None
    if wpx > 0:
        return "worse"
    if wpx < 0:
        return "better"
    return "same"


def to_usd(amount_acct: float, ccy: str) -> float:
    """CEN accounts are in cents; anything but CEN/USD fails closed (02 §4)."""
    if ccy == "CEN":
        return amount_acct / 100.0
    if ccy == "USD":
        return amount_acct
    raise ExecCompError(
        "UNKNOWN_CURRENCY", f"account currency {ccy!r} is not CEN/USD", status=500
    )


def comp_usd(fill: RawFill, ccy: str) -> Optional[float]:
    """worse_px x Volume/10000 x ContractSize x RateProfit, in USD."""
    wpx = worse_px(fill)
    if wpx is None:
        return None
    acct = wpx * fill.volume / 10000.0 * fill.contract_size * fill.rate_profit
    return to_usd(acct, ccy)


def delay_ms(fill: RawFill) -> Optional[int]:
    if fill.time_setup_msc is None:
        return None
    return int(round((fill.time_msc - fill.time_setup_msc).total_seconds() * 1000))


def filetime_to_utc(ft: int) -> dt.datetime:
    secs = ft / FILETIME_TICKS_PER_S - FILETIME_EPOCH_OFFSET_S
    return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=secs)


def utc_to_filetime(utc_naive: dt.datetime) -> int:
    secs = (utc_naive - dt.datetime(1970, 1, 1)).total_seconds()
    return int(round((secs + FILETIME_EPOCH_OFFSET_S) * FILETIME_TICKS_PER_S))


def srv_to_utc_calendar(srv: dt.datetime) -> dt.datetime:
    """MT wall clock -> naive UTC by the US-DST calendar (fallback path)."""
    return srv.replace(tzinfo=MT_SERVER_TZ).astimezone(dt.timezone.utc).replace(tzinfo=None)


def server_offset(fill: RawFill) -> dt.timedelta:
    """The row's own offset TimeMsc - Timestamp, rounded to 30 min (02 §2);
    falls back to the DST calendar when the row's Timestamp is implausible."""
    raw = (fill.time_msc - filetime_to_utc(fill.timestamp_ft)).total_seconds()
    step = round(raw / _OFFSET_STEP_S) * _OFFSET_STEP_S
    if _OFFSET_MIN_S <= step <= _OFFSET_MAX_S:
        return dt.timedelta(seconds=step)
    return fill.time_msc - srv_to_utc_calendar(fill.time_msc)


def iso_z(utc_naive: dt.datetime) -> str:
    return utc_naive.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_naive.microsecond // 1000:03d}Z"


def srv_str(srv: dt.datetime) -> str:
    return srv.strftime("%Y-%m-%d %H:%M:%S.") + f"{srv.microsecond // 1000:03d}"


def unit_check_ok(fill: RawFill) -> Optional[bool]:
    """02 §4.1 self-check on close fills: the formula reproduces deals.Profit.
    None = not applicable."""
    if fill.entry != 1 or not fill.price_position or fill.price_position <= 0:
        return None
    sign = 1 if side(fill) == "buy" else -1
    # A closing buy covers a short: profit = (open - close); closing sell: (close - open)
    pnl = (fill.price_position - fill.price) * sign
    expected = pnl * fill.volume / 10000.0 * fill.contract_size * fill.rate_profit
    return abs(expected - fill.profit) <= 0.02 + 1e-6 * abs(fill.profit)
