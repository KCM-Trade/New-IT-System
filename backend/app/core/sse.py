"""Server-Sent Events plumbing shared by every stream in the app.

Three endpoints speak SSE — the Risk Monitor alert stream, the AI turn relay in
the main API and the ai-agent container's internal ``/v1/turn`` — and until
OPT-0064 each carried its own copy of the frame serialiser, the keepalive
interval and the "do not buffer this" header set. Two of the three serialisers
disagreed on ``ensure_ascii``, so Chinese text was escaped to ``\\uXXXX`` on the
internal hop and decoded again one hop later. One module, one answer.

Framing rules (https://html.spec.whatwg.org/multipage/server-sent-events.html):
a frame is ``event:`` + ``data:`` lines terminated by a blank line; a line that
starts with ``:`` is a comment the client ignores, which is what makes
``SSE_PING`` a keepalive rather than a message.
"""

from __future__ import annotations

import json
from typing import Any

# Cloudflare Tunnel / nginx drop idle long connections near 60s; 15s keeps a
# wide margin. Same figure the risk-monitor stream has used since OPT-0013.
SSE_KEEPALIVE_SECONDS = 15.0

SSE_PING = b": ping\n\n"

# `X-Accel-Buffering: no` is what tells nginx to pass events through as they
# arrive instead of holding them until its buffer fills.
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse(event: str, data: Any) -> bytes:
    """Serialise one named event.

    ``ensure_ascii=False`` because the payload is UTF-8 on the wire anyway and
    most of it is Chinese; ``default=str`` so a stray datetime in a tool summary
    cannot kill a stream mid-turn.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
