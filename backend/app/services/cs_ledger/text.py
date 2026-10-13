"""Text masking for the fund risk-control ledger (spec §6).

Free-text fields are masked before they reach PostgreSQL: bank card numbers
(runs of 16-19 digits) and mainland mobile numbers (11 digits starting with 1).
"""

from __future__ import annotations

import re
from typing import Optional

CARD_TOKEN = "[卡号]"
PHONE_TOKEN = "[手机号]"

_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1\d{10}(?!\d)")


def mask_text(s: Optional[str]) -> Optional[str]:
    """Mask card and phone numbers. ``None`` stays ``None``; masking is idempotent."""
    if s is None:
        return None
    # Cards first: a 16-19 digit run never matches the 11-digit phone pattern,
    # but ordering keeps the intent explicit.
    out = _CARD_RE.sub(CARD_TOKEN, s)
    return _PHONE_RE.sub(PHONE_TOKEN, out)
