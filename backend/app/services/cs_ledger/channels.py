"""PSP -> channel mapping for the CS fund risk-control ledger.

Single source for both the ledger page and the amount-band analysis. Calibrated
against the CS monthly statistics sheet (2026-10-12): P2Pay (87) and Exlink CNY
Alipay (108) are Alipay rails and belong to Npay; CC USDT to CNY (33) is CC.
Unionpay Withdraw(JPAY) (6) joined Jpay on 2026-10-13 (user decision).

A PSP missing from this map is still recordable: the channel label becomes
``其他: <psp display name>`` and the preview carries a ``psp_unmapped`` warning.
"""

from __future__ import annotations

from typing import Optional

CHANNEL_MAP: dict[int, str] = {
    97: "Mpay", 99: "Mpay", 129: "Mpay", 110: "Mpay", 174: "Mpay", 98: "Mpay",
    107: "Mpay Corporate",
    114: "Chippay", 116: "Chippay", 157: "Chippay",
    105: "Exlink", 109: "Exlink", 106: "Exlink",
    137: "Npay(支付宝)", 171: "Npay(支付宝)", 170: "Npay(支付宝)",
    87: "Npay(支付宝)",    # P2Pay
    108: "Npay(支付宝)",   # Exlink CNY Alipay
    22: "CC", 33: "CC",   # 33 = CC USDT to CNY
    132: "Jpay", 15: "Jpay", 31: "Jpay", 6: "Jpay",
}

CHANNEL_ORDER: tuple[str, ...] = (
    "Mpay", "Mpay Corporate", "Chippay", "Npay(支付宝)", "Exlink", "CC", "Jpay",
)

OTHER_PREFIX = "其他: "

# transactions.type values that can be recorded, mapped to a direction.
RECORDABLE_TYPES: dict[str, str] = {
    "deposit": "deposit",
    "withdrawal": "withdrawal",
    "ib withdrawal": "withdrawal",
}


def channel_for(psp_id: Optional[int], psp_name: Optional[str]) -> tuple[str, bool]:
    """Return (channel label, mapped?)."""
    if psp_id is not None and psp_id in CHANNEL_MAP:
        return CHANNEL_MAP[psp_id], True
    return f"{OTHER_PREFIX}{psp_name or psp_id or '?'}", False
