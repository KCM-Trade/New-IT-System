"""DDL for the CS fund risk-control ledger (schema ``cs_ledger``).

The schema lives in the ``risk_cases`` database next to ``public`` (case layer)
and ``kcm`` (pipeline). It was created once by the admin role:

    CREATE SCHEMA cs_ledger AUTHORIZATION risk_app;

and is deliberately NOT granted to the read-only roles (the AI agent's
``ai_agent_ro`` among them): the table holds hand-entered incident notes.

Same posture as ``risk_cases_pg``: idempotent statements run at startup,
fail-open. dev and prod share this database, so every statement here must stay
additive (CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS) — never DROP or
ALTER TYPE.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.config import Settings
from app.core.risk_cases_pg import RiskCasesUnavailable, risk_cases_conn

logger = logging.getLogger(__name__)

SCHEMA = "cs_ledger"
TABLE = "cs_ledger.fund_risk_incidents"
HISTORY_TABLE = "cs_ledger.fund_risk_incidents_history"
GROUP_SEQ = "cs_ledger.fund_risk_group_seq"

DDL_STATEMENTS: tuple[str, ...] = (
    "CREATE SEQUENCE IF NOT EXISTS cs_ledger.fund_risk_group_seq",
    """
    CREATE TABLE IF NOT EXISTS cs_ledger.fund_risk_incidents (
        id                   BIGSERIAL PRIMARY KEY,
        incident_group_id    BIGINT      NOT NULL DEFAULT nextval('cs_ledger.fund_risk_group_seq'),
        transaction_id       BIGINT,                 -- CRM transactions.id; NULL only on imported legacy rows
        -- order snapshot taken from the replica at entry time (never hand-typed)
        client_id            BIGINT,
        login_sid            TEXT,
        venue                TEXT,
        direction            TEXT CHECK (direction IN ('deposit', 'withdrawal')),
        txn_type             TEXT,
        psp_id               INTEGER,
        psp_name             TEXT,
        channel              TEXT,
        amount_cny           NUMERIC(18, 2),
        txn_status           TEXT,
        txn_created_at       TIMESTAMPTZ,
        txn_processed_at     TIMESTAMPTZ,
        is_test_account      BOOLEAN     NOT NULL DEFAULT false,
        snapshot_at          TIMESTAMPTZ,
        -- staff-entered
        reported_date        DATE,
        transfer_succeeded   TEXT        NOT NULL DEFAULT 'unknown'
                             CHECK (transfer_succeeded IN ('yes', 'no', 'unknown')),
        frozen_scope         TEXT        NOT NULL DEFAULT 'unknown'
                             CHECK (frozen_scope IN ('none', 'one_card', 'all_cards', 'non_counter', 'unknown')),
        freeze_days          INTEGER     CHECK (freeze_days >= 0),
        freeze_note          TEXT,
        authority            TEXT        NOT NULL DEFAULT 'unknown'
                             CHECK (authority IN ('bank', 'police_antifraud', 'alipay', 'unknown')),
        client_source        TEXT        CHECK (client_source IN ('online', 'offline')),
        client_bank_province TEXT,
        client_bank_city     TEXT,
        trader_bank_province TEXT,
        trader_bank_city     TEXT,
        id_province          TEXT,
        id_city              TEXT,
        ip_region            TEXT,
        pwa_domain           TEXT,
        account_type         TEXT,
        venue_override       TEXT,
        event_text           TEXT,
        result_text          TEXT,
        -- provenance / lifecycle
        source               TEXT        NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'import')),
        import_batch         TEXT,
        import_sheet_row     INTEGER,
        import_raw           JSONB,
        version              INTEGER     NOT NULL DEFAULT 1,
        created_by           TEXT        NOT NULL,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_by           TEXT        NOT NULL,
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at           TIMESTAMPTZ,
        deleted_by           TEXT
    )
    """,
    # A soft-deleted transaction can be entered again; legacy rows may have no id.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_fund_risk_txn
        ON cs_ledger.fund_risk_incidents (transaction_id)
        WHERE deleted_at IS NULL AND transaction_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_fund_risk_import
        ON cs_ledger.fund_risk_incidents (import_batch, import_sheet_row, COALESCE(transaction_id, 0))
        WHERE import_batch IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fund_risk_reported
        ON cs_ledger.fund_risk_incidents (reported_date DESC, id DESC) WHERE deleted_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fund_risk_processed
        ON cs_ledger.fund_risk_incidents (txn_processed_at DESC) WHERE deleted_at IS NULL
    """,
    "CREATE INDEX IF NOT EXISTS ix_fund_risk_group ON cs_ledger.fund_risk_incidents (incident_group_id)",
    "CREATE INDEX IF NOT EXISTS ix_fund_risk_client ON cs_ledger.fund_risk_incidents (client_id)",
    """
    CREATE TABLE IF NOT EXISTS cs_ledger.fund_risk_incidents_history (
        id          BIGSERIAL PRIMARY KEY,
        incident_id BIGINT      NOT NULL,
        action      TEXT        NOT NULL,   -- create / update / delete / import
        old_row     JSONB,
        new_row     JSONB,
        actor       TEXT        NOT NULL,
        at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fund_risk_hist
        ON cs_ledger.fund_risk_incidents_history (incident_id, at DESC)
    """,
)


def init_cs_ledger_pg(settings: Optional[Settings] = None) -> bool:
    """Ensure the ledger tables exist (idempotent, fail-open)."""
    try:
        with risk_cases_conn(settings) as conn:
            with conn.cursor() as cur:
                for stmt in DDL_STATEMENTS:
                    cur.execute(stmt)
        logger.info("cs_ledger PG schema ensured (2 tables + indexes)")
        return True
    except RiskCasesUnavailable as exc:
        logger.warning("cs_ledger PG init skipped (fail-open): %s", exc)
        return False
    except Exception:
        # Most likely the schema itself is missing (it needs the admin role once).
        logger.error("cs_ledger PG init failed (fail-open)", exc_info=True)
        return False
