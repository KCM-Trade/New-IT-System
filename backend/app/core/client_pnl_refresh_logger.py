from __future__ import annotations

import json
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.config import get_settings

# fresh grad note: keep logging helpers isolated for reuse across routers/services

LOG_SUBDIR = ("backend", "logs", "client_pnl_refresh")
LOG_PREFIX = "client_pnl_refresh_"
LOG_SUFFIX = ".log"
DEFAULT_KEEP_DAYS = 15


def _ensure_log_dir() -> Path:
    """Return log directory path and ensure it exists."""
    settings = get_settings()
    log_dir = settings.repo_root.joinpath(*LOG_SUBDIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _json_default(value: Any) -> str:
    """Fallback serializer for datetime/unknown types."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    return str(value)


def cleanup_old_logs(keep_days: int = DEFAULT_KEEP_DAYS) -> None:
    """Remove log files older than the retention window."""
    if keep_days <= 0:
        return

    log_dir = _ensure_log_dir()
    today = datetime.now(timezone.utc).date()
    threshold = today - timedelta(days=keep_days - 1)

    for path in log_dir.glob(f"{LOG_PREFIX}*{LOG_SUFFIX}"):
        date_part = path.stem.removeprefix(LOG_PREFIX)
        file_date = None
        with suppress(ValueError):
            file_date = datetime.strptime(date_part, "%Y-%m-%d").date()
        if file_date and file_date < threshold:
            with suppress(OSError):
                path.unlink()


def log_refresh_event(
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    keep_days: int = DEFAULT_KEEP_DAYS,
) -> None:
    """Append refresh event payload to the daily log and enforce retention."""
    log_dir = _ensure_log_dir()
    now = datetime.now(timezone.utc)
    log_file = log_dir / f"{LOG_PREFIX}{now.strftime('%Y-%m-%d')}{LOG_SUFFIX}"

    record = {
        "timestamp": now.isoformat(),
        "event_type": event_type,
        "payload": payload or {},
    }

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=_json_default))
        fh.write("\n")

    cleanup_old_logs(keep_days=keep_days)


