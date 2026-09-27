"""
APScheduler integration for scheduled IB Financial reports.

The scheduler reads report_config from SQLite and runs the daily
email job at the configured HKT time. When the config is updated
via the API, call `reschedule()` to apply the new time immediately.
"""

from __future__ import annotations

import logging
import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

JOB_ID = "ib_financial_daily_report"
DIGEST_JOB_ID = "alert_mail_digest_dispatch"
# OPT-0047 risk-V2 case layer jobs
CASE_BASELINE_JOB_ID = "risk_cases_daily_baseline"
USER_ID_REPAIR_JOB_ID = "alert_events_user_id_repair"
RETENTION_JOB_ID = "users_db_retention_sweep"
AI_SESSIONS_RETENTION_JOB_ID = "ai_sessions_retention_sweep"
ECON_CALENDAR_JOB_ID = "econ_calendar_refresh"
ECON_CALENDAR_BOOTSTRAP_JOB_ID = "econ_calendar_bootstrap"
HKT = ZoneInfo("Asia/Hong_Kong")

# Module-level singleton; initialised by start_scheduler()
_scheduler: BackgroundScheduler | None = None


def _send_daily_report() -> None:
    """Job function: query data + send email using current config."""
    from ..core.config import get_settings
    from ..services import ib_financial_service as svc
    from ..services.email_service import send_email

    try:
        cfg = svc.get_report_config()
        if not cfg.get("is_enabled") or not cfg.get("mail_to"):
            logger.info("Scheduled report skipped: disabled or no recipients")
            return

        settings = get_settings()
        date_str, records = svc.query_financial_data(settings)

        # Reuse the HTML builder from the route module
        from ..api.v1.routes.ib_financial import _build_report_html
        html = _build_report_html(date_str, records, is_scheduled=True)

        send_email(
            subject=f"CS Report - IB Financial - {date_str}",
            body=html,
            to=cfg["mail_to"],
            cc=cfg.get("mail_cc"),
        )
        logger.info(f"Scheduled report sent for {date_str}")
    except Exception:
        logger.error("Scheduled report failed", exc_info=True)


def _dispatch_digest_mails_job() -> None:
    """Job function: compose due digest-mode alert mail subscriptions.

    OPT-0043: digest subscriptions send once daily at their per-subscription
    `digest_time` (HKT). Times are user-editable at runtime, so instead of
    one cron job per subscription this single minutely job asks the
    dispatcher which subscriptions are due — a no-op SQLite read on idle
    minutes. Fully fenced: a mail problem must never kill the scheduler.
    """
    try:
        from ..services.alert_mail_dispatcher import dispatch_digest_mails
        dispatch_digest_mails()
    except Exception:
        logger.error("Alert mail digest dispatch failed (non-fatal)", exc_info=True)


def _case_baseline_job() -> None:
    """Job function (OPT-0047): daily long-window metric snapshots.

    Order inside one run matters:
      1. case sync catch-up — enroll any signals the 10-min tick could not
         push (PG outage backlog) so today's baseline covers them too;
      2. daily baseline — one case_metrics_daily row per enrolled client
         (idempotent upsert; Δ1 needs two consecutive days, Δ30 thirty);
      3. NULL user_id observability count (repair itself runs earlier, see
         _user_id_repair_job).
    Fully fenced — a case-layer problem must never kill the scheduler.
    """
    try:
        from ..services.case_engine_service import (
            log_null_user_id_count,
            sync_cases_from_alert_events,
        )
        from ..services.case_metrics_service import run_daily_baseline

        sync_cases_from_alert_events()
        result = run_daily_baseline()
        logger.info("Case baseline job finished: %s", result)
        log_null_user_id_count()
    except Exception:
        logger.error("Case baseline job failed (non-fatal)", exc_info=True)


def _user_id_repair_job() -> None:
    """Job function (OPT-0047 deliverable 6 / OPT-0045 F2): fix NULL
    user_id alert rows daily so MySQL-outage alerts don't age out of the
    30-day window invisible to GROUP BY user_id."""
    try:
        from ..services.alert_user_id_repair_service import repair_null_user_ids
        repair_null_user_ids()
    except Exception:
        logger.error("user_id repair job failed (non-fatal)", exc_info=True)


def _users_db_retention_job() -> None:
    """Job function: enforce users.db retention on the three growing tables.

    Retention used to be applied only in the lifespan startup block, which
    means it was only ever applied on redeploy — and prod containers here stay
    up for weeks. A 90-day window that nobody restarts past is a window that
    never closes, so the stated retention has to be a recurring sweep to be
    true. `auth_events` matters most of the three: unauthenticated callers can
    write to it (throttled) through the /auth/callback failure paths.

    Each table is fenced on its own rather than sharing one try: a failure on
    one of them must not cost the other two a whole day of growth, and the
    log line has to say which one broke.
    """
    try:
        from ..services.auth_service import (
            purge_expired_sessions,
            purge_old_audit_log,
            purge_old_auth_events,
        )
    except Exception:
        logger.error(
            "users.db retention sweep could not load auth_service (non-fatal)",
            exc_info=True,
        )
        return

    removed: dict[str, object] = {}
    for table, purge in (
        ("sessions", purge_expired_sessions),
        ("auth_events", purge_old_auth_events),
        ("audit_log", purge_old_audit_log),
    ):
        try:
            removed[table] = purge()
        except Exception:
            removed[table] = "failed"
            logger.error(
                "users.db retention sweep failed on %s (non-fatal)",
                table,
                exc_info=True,
            )
    logger.info("users.db retention sweep removed: %s", removed)


def _ai_sessions_retention_job() -> None:
    """Job function: hard-delete AI conversations soft-deleted past retention.

    OPT-0065 §8.2. Same shape as the users.db sweep above: the recurring job
    is the primary mechanism and the lifespan startup pass is its complement
    for a box that was off across the window. Only rows the user already
    deleted are eligible (`deleted_at IS NOT NULL`); 0 days = keep forever.
    """
    try:
        from ..core.ai_usage_db import purge_ai_sessions
        from ..core.config import get_settings

        removed = purge_ai_sessions(get_settings().AI_SESSION_RETENTION_DAYS)
    except Exception:
        logger.error("ai_sessions retention sweep failed (non-fatal)", exc_info=True)
        return
    logger.info("ai_sessions retention sweep removed %d session(s)", removed)


def _econ_calendar_refresh_job() -> None:
    """Job function: re-fetch the US economic calendar into ai_agent.db.

    OPT-0065 §12. The agent container reads the cache through a read-only
    mount and has no network tool, so the main API is the only writer. A
    source that fails keeps its previous rows and records the failure; the
    tool reports `stale_since` from that, so this job never needs to alarm.
    """
    try:
        from ..core.config import get_settings
        from ..services.econ_calendar_service import refresh_calendar

        refresh_calendar(get_settings())
    except Exception:
        logger.error("econ calendar refresh failed (non-fatal)", exc_info=True)


def start_scheduler() -> None:
    """Start the background scheduler using report_config from SQLite.

    Controlled by SCHEDULER_ENABLED env var (default: "true").
    Set to "false" in dev to avoid duplicate emails since dev/prod share SQLite.
    """
    global _scheduler
    if _scheduler is not None:
        return

    if os.getenv("SCHEDULER_ENABLED", "true").lower() == "false":
        logger.info("Scheduler disabled by SCHEDULER_ENABLED=false")
        return

    _scheduler = BackgroundScheduler(timezone=HKT)

    from ..services.ib_financial_service import get_report_config
    cfg = get_report_config()

    hour, minute = _parse_time(cfg.get("schedule_time", "17:00"))
    _scheduler.add_job(
        _send_daily_report,
        CronTrigger(hour=hour, minute=minute, timezone=HKT),
        id=JOB_ID,
        replace_existing=True,
    )
    # OPT-0043: minutely due-check for digest-mode alert mail subscriptions
    # (see _dispatch_digest_mails_job). Guarded by the same SCHEDULER_ENABLED
    # switch above, so dev (which shares prod's SQLite) never double-sends.
    _scheduler.add_job(
        _dispatch_digest_mails_job,
        CronTrigger(minute="*", timezone=HKT),
        id=DIGEST_JOB_ID,
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=120,
    )
    # OPT-0047 risk-V2 case layer. Same SCHEDULER_ENABLED guard as everything
    # above (dev shares prod's SQLite → only one owner runs jobs). Opt-out env
    # mirrors the other risk jobs. Timing: MT day rolls at 05:00 HKT; the
    # repair runs first (06:50) so the baseline (07:10) and the day's case
    # syncs see reconciled user_ids.
    if os.getenv("CASE_ENGINE_JOBS_ENABLED", "true").lower() != "false":
        _scheduler.add_job(
            _user_id_repair_job,
            CronTrigger(hour=6, minute=50, timezone=HKT),
            id=USER_ID_REPAIR_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=600,
            max_instances=1,
        )
        _scheduler.add_job(
            _case_baseline_job,
            CronTrigger(hour=7, minute=10, timezone=HKT),
            id=CASE_BASELINE_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=600,
            max_instances=1,
        )
        logger.info(
            "Case-engine jobs scheduled: user_id repair 06:50 HKT, "
            "daily baseline 07:10 HKT"
        )
    # users.db retention sweep. 04:00 HKT is the quiet hour for this box: HK
    # staff are asleep so the DELETEs take users.db's write lock while almost
    # nothing is logging in, and it sits clear of the MT 05:00 day roll and of
    # the 06:50/07:10 risk jobs. Own kill switch because this is the only
    # scheduled job that DELETEs from users.db, and backend/data is a bind
    # mount shared with prod — an off switch here beats stopping the whole
    # scheduler. Same SCHEDULER_ENABLED guard as everything above.
    if os.getenv("AUTH_RETENTION_JOB_ENABLED", "true").lower() != "false":
        _scheduler.add_job(
            _users_db_retention_job,
            CronTrigger(hour=4, minute=0, timezone=HKT),
            id=RETENTION_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
            max_instances=1,
        )
        logger.info("users.db retention sweep scheduled: 04:00 HKT daily")
    # OPT-0065: AI conversation retention. Ten minutes after the users.db sweep
    # so the two never contend, in the same quiet hour, for the same reasons.
    # No separate kill switch: AI_SESSION_RETENTION_DAYS=0 already means "never
    # delete anything", which is the only thing a switch would add.
    _scheduler.add_job(
        _ai_sessions_retention_job,
        CronTrigger(hour=4, minute=10, timezone=HKT),
        id=AI_SESSIONS_RETENTION_JOB_ID,
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )
    logger.info("ai_sessions retention sweep scheduled: 04:10 HKT daily")
    # OPT-0065 §12: US economic calendar, 06:00 HKT = after the US close and
    # the FOMC/FRED pages have settled for the day, before HK staff arrive.
    _scheduler.add_job(
        _econ_calendar_refresh_job,
        CronTrigger(hour=6, minute=0, timezone=HKT),
        id=ECON_CALENDAR_JOB_ID,
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
        max_instances=1,
    )
    # First boot (or a wiped cache): fetch once shortly after start instead of
    # waiting for 06:00. A one-shot `date` job, not an inline call, so a slow
    # or blocked upstream cannot hold the lifespan for 30s.
    try:
        from ..core.ai_usage_db import calendar_is_empty

        if calendar_is_empty():
            from datetime import datetime, timedelta

            _scheduler.add_job(
                _econ_calendar_refresh_job,
                "date",
                run_date=datetime.now(HKT) + timedelta(seconds=20),
                id=ECON_CALENDAR_BOOTSTRAP_JOB_ID,
                replace_existing=True,
            )
            logger.info("econ calendar cache is empty: bootstrap refresh scheduled in 20s")
    except Exception:
        logger.warning("econ calendar bootstrap check failed (non-fatal)", exc_info=True)
    logger.info("econ calendar refresh scheduled: 06:00 HKT daily")
    _scheduler.start()
    logger.info(f"Scheduler started: daily report at {hour:02d}:{minute:02d} HKT")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


def reschedule() -> None:
    """Re-read config and update the job trigger. Call after config changes."""
    if not _scheduler:
        return

    from ..services.ib_financial_service import get_report_config
    cfg = get_report_config()
    hour, minute = _parse_time(cfg.get("schedule_time", "17:00"))

    _scheduler.reschedule_job(
        JOB_ID,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=HKT),
    )
    logger.info(f"Scheduler rescheduled: daily report at {hour:02d}:{minute:02d} HKT")


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' string into (hour, minute)."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
