"""Text masking for the fund risk-control ledger (spec §6).

Free-text fields are masked before they reach PostgreSQL: bank card numbers
(runs of 16-19 digits) and mainland mobile numbers (11 digits starting with 1).
"""

from __future__ import annotations

import re
from typing import Optional

CARD_TOKEN = "[卡号]"
PHONE_TOKEN = "[手机号]"

# A plain 16-19 digit run, or the same number typed in groups of four separated
# by a space or dash. Grouping is required for the separated form so a list of
# order ids ("700450 700484 705497") is not mistaken for a card number.
_CARD_RE = re.compile(r"(?<!\d)(?:\d{16,19}|\d{4}(?:[ -]\d{4}){3}(?:[ -]\d{1,3})?)(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[ -]?)?1\d{2}[ -]?\d{4}[ -]?\d{4}(?!\d)")
# Control characters openpyxl refuses to write (pasted from Word / Excel).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_IPV4_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
_IPV6_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){2,}[0-9A-Fa-f:]*")


def mask_text(s: Optional[str]) -> Optional[str]:
    """Mask card and phone numbers. ``None`` stays ``None``; masking is idempotent."""
    if s is None:
        return None
    # Cards first: a 16-19 digit run never matches the 11-digit phone pattern,
    # but ordering keeps the intent explicit.
    out = _CARD_RE.sub(CARD_TOKEN, s)
    return _PHONE_RE.sub(PHONE_TOKEN, out)


def strip_control(s: Optional[str]) -> Optional[str]:
    """Drop control characters that cannot be written to a spreadsheet."""
    if s is None:
        return None
    return _CONTROL_RE.sub("", s)


def strip_ip(s: Optional[str]) -> Optional[str]:
    """Remove IP addresses from a region label; only the region text is stored."""
    if s is None:
        return None
    return _IPV6_RE.sub("", _IPV4_RE.sub("", s)).strip() or None
