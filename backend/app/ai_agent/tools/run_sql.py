"""Tool — ``run_sql``, the UNCERTIFIED escape hatch (docs/ai-agent/02-contracts.md §10).

The three certified tools encode the 口径; this one lets the model write a
single read-only SELECT when none of them can answer. Its result is marked
``certified: false`` end to end (envelope → ``tool_done`` → amber badge), the
SQL is echoed back verbatim so the analyst can see exactly what ran, and the
prompt forces the model to say "uncertified" and list the known pitfalls.

Seven gates (02 §10.1), every one hard:

  ① read-only ACCOUNTS — MySQL: the shared ``readonly`` replica account
     through ``core.mysql_readonly.connect_readonly`` (connect 5s /
     MAX_EXECUTION_TIME 15s / read 20s); PG: the ``ai_agent_ro`` role
     (SELECT-only, ``default_transaction_read_only``) plus ``statement_timeout``.
  ② ONE statement whose root is SELECT / UNION (sqlglot AST, dialect-aware);
     any DML / DDL / admin node ANYWHERE in the tree (``WITH d AS (DELETE …)``)
     is refused — see ``_FORBIDDEN_NODES``.
  ③ node BLACKLIST — ``exp.Command`` (FLUSH / LOCK TABLES / CALL parse to it),
     ``INTO OUTFILE`` / ``SELECT … INTO @v`` (``exp.Into``), ``FOR UPDATE`` /
     ``LOCK IN SHARE MODE`` (``exp.Lock``), and the functions in
     ``_FORBIDDEN_FUNCTIONS`` (SLEEP / BENCHMARK / LOAD_FILE / GET_LOCK / pg_sleep…).
  ④ ROWS capped: ``limit`` clamped to ``MAX_LIMIT`` and the statement is run as
     ``SELECT * FROM (<sql>) AS _q LIMIT n+1`` so truncation is detected, not
     guessed; cells cut at ``CELL_MAX_CHARS``.
  ⑤ TABLE whitelist per database, checked on every ``exp.Table`` node
     (CTE names excepted); system schemas are refused by construction because
     they are simply not in the list.
  ⑥ a RESTRICTED caller (``ctx.scope is not None``) never gets this tool: the
     harness does not register it, and this impl still answers ``scope_denied``
     if it is ever reached (belt and braces — free SQL cannot be cid-filtered).
  ⑦ AUDIT — the main API copies ``tool_use.input.sql`` into
     ``ai.query.submit.new_value.sql[]`` (routes/ai.py); the shared MySQL
     account cannot be attributed DB-side, so that row is the attribution.

Why the AST is the MAIN defence on MySQL (02 §10.1a): MySQL has no
session-level read-only transaction the way PG does, and the shared account
carries RELOAD/PROCESS — but those can only be exercised by non-SELECT
statements, which ② and ③ refuse before any connection is opened. pymysql is
never given ``CLIENT.MULTI_STATEMENTS`` (``connect_readonly`` does not pass
``client_flag``), so ``SELECT 1; FLUSH TABLES`` cannot ride through as one
string either — ``tests/test_ai_run_sql_guard.py`` pins both.

Sensitive columns (02 §13 last bullet) are masked at the OUTPUT layer: a result
column whose name is in ``SENSITIVE_COLUMNS`` comes back as ``"***"`` in every
row. Output-layer rather than AST-layer because the model may alias
(``SELECT email AS e``) — the alias is what we see, so the list is matched
against the RESULT column names and the caveat tells the model what was hidden.

Framework-free like the other tools: ``harness.build_tools`` wraps this into
the framework tool; ``validate_sql`` is pure and unit-tested without a DB.
"""

from __future__ import annotations

import decimal
from datetime import date, datetime
from typing import Any, Optional

import pymysql
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from app.core.config import Settings
from app.core.logging_config import get_logger
from app.core.mysql_readonly import connect_readonly
from app.core.pg_session import pg_session

from .common import CallerCtx, error_envelope, is_error, ok_envelope, run_sync_with_timeout, utc_now_iso

logger = get_logger(__name__)

TOOL_NAME = "run_sql"

DB_MYSQL = "fxbackoffice"
DB_PG = "risk_cases"
DATABASES = (DB_MYSQL, DB_PG)

MAX_LIMIT = 200
DEFAULT_LIMIT = 200
CELL_MAX_CHARS = 500
# Longer than any honest ad-hoc query; short enough that a pasted dump is refused.
MAX_SQL_CHARS = 4000
# Statement budget on both engines (02 §10.1 ③). connect_readonly enforces
# "strictly below read_timeout" for MySQL; PG gets the same number via SET.
STATEMENT_TIMEOUT_MS = 15_000

# ⑤ whitelists. MySQL: exactly these tables, all in fxbackoffice (an
# unqualified name resolves there; any other schema — information_schema,
# mysql, performance_schema, sys, mt5_live — is refused because it is not
# fxbackoffice, not because it is on a blacklist). PG: any table in these
# two schemas (an unqualified name resolves to public).
MYSQL_SCHEMA = "fxbackoffice"
MYSQL_TABLES = frozenset(
    {"mt4_trades", "mt4_users", "users", "transactions", "stats_ib_commissions", "user_tags", "tags"}
)
PG_SCHEMAS = frozenset({"public", "kcm"})
PG_DEFAULT_SCHEMA = "public"

# ② + ③: anything of these types anywhere in the tree is refused. Statement
# kinds sqlglot cannot parse at all (FLUSH … WITH READ LOCK, INTO OUTFILE,
# HANDLER, DO) raise ParseError and are refused one step earlier; the ones it
# parses into a generic Command (LOCK TABLES, CALL) are caught here.
_FORBIDDEN_NODES: tuple[type, ...] = (
    exp.Delete,
    exp.Insert,
    exp.Update,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Set,
    exp.Show,
    exp.Use,
    exp.Kill,
    exp.Describe,   # EXPLAIN / DESCRIBE — schema probing
    exp.Lock,       # FOR UPDATE / LOCK IN SHARE MODE
    exp.Into,       # SELECT … INTO @var / INTO OUTFILE (when it parses)
    exp.Commit,
    exp.Rollback,
    exp.Transaction,
    exp.Grant,
    exp.Copy,
    exp.Analyze,
    exp.Refresh,
    exp.Cache,
    exp.Uncache,
    exp.Pragma,
    exp.Execute,
    exp.Fetch,
    exp.Attach,
    exp.Detach,
    exp.Install,
)

# ③ functions that stall, lock or read files. Matched on the lower-cased
# function name for BOTH built-ins sqlglot knows (``sql_name()``) and the
# ``Anonymous`` fallback it uses for everything else.
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "sleep",
        "benchmark",
        "load_file",
        "get_lock",
        "release_lock",
        "release_all_locks",
        "is_free_lock",
        "is_used_lock",
        "master_pos_wait",
        "source_pos_wait",
        "wait_for_executed_gtid_set",
        "wait_until_sql_thread_after_gtids",
        # PostgreSQL
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_try_advisory_lock",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "set_config",
        "current_setting",
        "pg_notify",
    }
)

# 02 §13: no names / emails / phones / full IPs in tool output. Matched on the
# RESULT column name (lower-cased), so an alias hides nothing.
SENSITIVE_COLUMNS = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "password",
        "passwd",
        "name",
        "firstname",
        "lastname",
        "first_name",
        "last_name",
        "address",
        "ip",
        "last_ip",
        "ipaddress",
    }
)
MASK = "***"

UNCERTIFIED_SUMMARY = "Ad-hoc SQL written by the model; NOT a certified 口径"
FIXED_CAVEATS = [
    "CEN 未换算 — cent accounts (mt4_users.CURRENCY = 'CEN') and .cent/.kcmc symbols store money (and lots) ×100; nothing here divided them.",
    "demo/员工未排除 — demo/test groups and employee clients (users.isEmployee) are in the rows unless the SQL excluded them.",
    "sid=5 CMD 未归一化 — MT5 (sid 5) closed rows store the EXIT side in CMD; direction is inverted for those rows.",
    "日界按 SQL 原样 — closeDate/openDate are MT server days (UTC+3 summer / UTC+2 winter); *_TIME columns are MT wall clock, not UTC.",
    f"Sensitive result columns ({', '.join(sorted(SENSITIVE_COLUMNS))}) are masked as '{MASK}'.",
]

_DIALECT = {DB_MYSQL: "mysql", DB_PG: "postgres"}


# ── the pure guard (② ③ ⑤) ───────────────────────────────────────────────────


def _invalid(reason: str, sql: str) -> dict:
    return error_envelope("invalid_argument", f"SQL refused: {reason}", {"sql": sql[:MAX_SQL_CHARS]})


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name or "").lower()
    try:
        return str(node.sql_name() or "").lower()
    except Exception:  # noqa: BLE001 — a name we cannot read is not one we allow
        return type(node).__name__.lower()


def validate_sql(sql: Any, db: Any) -> Optional[dict]:
    """Gates ② ③ ⑤ as one pure function. ``None`` = allowed; otherwise an
    ``invalid_argument`` error envelope saying which gate refused it.

    Every refusal is a structured answer the model can read and fix (or
    stop), never an exception — the framework would otherwise hand the model
    an error string whose wording is not ours (02 §2.6).
    """
    if db not in DATABASES:
        return _invalid(f"db must be one of {list(DATABASES)}", str(sql or ""))
    if not isinstance(sql, str) or not sql.strip():
        return _invalid("sql is empty", "")
    text = sql.strip()
    if len(text) > MAX_SQL_CHARS:
        return _invalid(f"sql longer than {MAX_SQL_CHARS} characters", text)

    try:
        statements = [s for s in sqlglot.parse(text, read=_DIALECT[db]) if s is not None]
    except (ParseError, TokenError) as exc:
        # FLUSH … WITH READ LOCK, INTO OUTFILE, HANDLER, DO … all land here.
        return _invalid(f"could not parse as a single SELECT ({type(exc).__name__})", text)
    except Exception as exc:  # noqa: BLE001 — sqlglot internals; refuse, never raise
        return _invalid(f"could not parse ({type(exc).__name__})", text)

    if len(statements) != 1:
        return _invalid(f"exactly one statement is allowed, got {len(statements)}", text)
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        return _invalid(f"only SELECT / UNION statements are allowed, got {type(tree).__name__}", text)

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return _invalid(f"{type(node).__name__} is not allowed inside a read-only query", text)
        if isinstance(node, exp.Func):
            fname = _function_name(node)
            if fname in _FORBIDDEN_FUNCTIONS:
                return _invalid(f"function {fname.upper()}() is not allowed", text)

    cte_names = {str(cte.alias or "").lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = str(table.name or "").lower()
        schema = str(table.db or "").lower()
        catalog = str(table.catalog or "").lower()
        if not name:
            continue
        if not schema and not catalog and name in cte_names:
            continue
        if catalog:
            return _invalid(f"three-part table names are not allowed ({catalog}.{schema}.{name})", text)
        if db == DB_MYSQL:
            schema = schema or MYSQL_SCHEMA
            if schema != MYSQL_SCHEMA or name not in MYSQL_TABLES:
                return _invalid(
                    f"table {schema}.{name} is not in the whitelist "
                    f"({MYSQL_SCHEMA}.{{{', '.join(sorted(MYSQL_TABLES))}}})",
                    text,
                )
        else:
            schema = schema or PG_DEFAULT_SCHEMA
            if schema not in PG_SCHEMAS:
                return _invalid(f"schema {schema} is not in the whitelist ({', '.join(sorted(PG_SCHEMAS))}.*)", text)
    return None


# ── execution (① ③-timeouts ④) ───────────────────────────────────────────────


def clamp_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def wrap_with_limit(sql: str, limit: int) -> str:
    """``SELECT * FROM (<sql>) AS _q LIMIT n+1`` — one more than asked so a
    full page proves truncation instead of leaving it to guesswork."""
    inner = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({inner}) AS _q LIMIT {int(limit) + 1}"


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    text = str(value)
    if len(text) > CELL_MAX_CHARS:
        return text[:CELL_MAX_CHARS] + "…"
    return text


def shape_rows(columns: list[str], raw_rows: list[tuple], limit: int) -> dict:
    """Apply ④ (page + truncation flag, cell cap) and the output mask."""
    truncated = len(raw_rows) > limit
    rows_in = raw_rows[:limit]
    masked = [c.lower() in SENSITIVE_COLUMNS for c in columns]
    rows = [
        [MASK if masked[i] and value is not None else _cell(value) for i, value in enumerate(row)]
        for row in rows_in
    ]
    return {
        "columns": list(columns),
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "masked_columns": [c for c, m in zip(columns, masked) if m],
    }


def _timeout_envelope(engine: str) -> dict:
    return error_envelope(
        "upstream_timeout",
        f"The {engine} query was stopped by the {STATEMENT_TIMEOUT_MS // 1000}s statement limit. "
        "Narrow it (fewer rows, tighter WHERE, an indexed column) and retry once.",
    )


# pymysql error numbers that mean "the SERVER stopped the statement".
# 3024 = ER_QUERY_TIMEOUT (MAX_EXECUTION_TIME fired), 1317 = ER_QUERY_INTERRUPTED,
# 2013 = CR_SERVER_LOST (read_timeout abandoned the socket).
_MYSQL_TIMEOUT_CODES = frozenset({3024, 1317, 2013})


def execute_mysql(settings: Settings, sql: str, limit: int) -> dict:
    """① + ④ on the fxbackoffice replica. Returns a page dict or an error envelope."""
    wrapped = wrap_with_limit(sql, limit)
    try:
        conn = connect_readonly(settings, max_execution_ms=STATEMENT_TIMEOUT_MS)
    except pymysql.MySQLError as exc:
        logger.error("run_sql: MySQL connect failed: %s", type(exc).__name__)
        return error_envelope("internal", "Could not connect to the fxbackoffice replica.")
    try:
        # Plain (tuple) cursor: DictCursor would collapse duplicate column
        # names and lose the SELECT's column order.
        with conn.cursor(pymysql.cursors.Cursor) as cur:
            cur.execute(wrapped)
            columns = [d[0] for d in (cur.description or [])]
            raw = list(cur.fetchall())
    except pymysql.MySQLError as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        if code in _MYSQL_TIMEOUT_CODES:
            return _timeout_envelope("MySQL")
        # Syntax the parser accepted but MySQL did not, unknown column, etc.
        # Give the model the server's own words — it can fix the query.
        message = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
        return error_envelope("invalid_argument", f"MySQL refused the query: {message[:300]}", {"mysql_errno": code})
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return shape_rows(columns, raw, limit)


def execute_pg(settings: Settings, sql: str, limit: int) -> dict:
    """① + ④ on risk_cases (role ai_agent_ro). Returns a page dict or an error envelope."""
    import psycopg2
    import psycopg2.errors

    wrapped = wrap_with_limit(sql, limit)
    try:
        with pg_session(settings.risk_cases_pg_dsn()) as conn:
            with conn.cursor() as cur:
                # The role already defaults to read-only transactions; saying it
                # again costs nothing and survives a role change.
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute(wrapped)
                columns = [d[0] for d in (cur.description or [])]
                raw = list(cur.fetchall())
    except psycopg2.errors.QueryCanceled:
        return _timeout_envelope("PostgreSQL")
    except psycopg2.OperationalError as exc:
        logger.error("run_sql: PG connect/operational failure: %s", type(exc).__name__)
        return error_envelope("internal", "Could not query the risk_cases database.")
    except psycopg2.Error as exc:
        message = getattr(exc, "pgerror", None) or str(exc)
        return error_envelope("invalid_argument", f"PostgreSQL refused the query: {message.strip()[:300]}")
    return shape_rows(columns, raw, limit)


# ── the tool ─────────────────────────────────────────────────────────────────


async def run_sql(ctx: CallerCtx, db: Any, sql: Any, limit: Any = DEFAULT_LIMIT) -> dict:
    # ⑥ — structurally unreachable for restricted callers (harness does not
    # register the tool), kept here so a future harness change cannot open it.
    if ctx.scope is not None:
        return error_envelope(
            "scope_denied",
            "Ad-hoc SQL is not available to accounts with a restricted data scope: its rows cannot be filtered by country.",
        )
    refused = validate_sql(sql, db)
    if refused is not None:
        return refused
    text = str(sql).strip()
    page_size = clamp_limit(limit)

    executor = execute_mysql if db == DB_MYSQL else execute_pg
    result = await run_sync_with_timeout(executor, ctx.settings, text, page_size, ctx=ctx)
    if is_error(result):
        return result

    data = {
        "db": db,
        "sql": text,
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": result["row_count"],
        "truncated": result["truncated"],
        "limit": page_size,
    }
    caveats = list(FIXED_CAVEATS)
    if result["masked_columns"]:
        caveats.append(f"Masked in this result: {', '.join(result['masked_columns'])}.")
    if result["truncated"]:
        caveats.append(f"More than {page_size} rows matched; only the first {page_size} are shown. Aggregate or filter instead of paging.")
    definition = {
        "summary": UNCERTIFIED_SUMMARY,
        "day_basis": "as written in the SQL",
        "caveats": caveats,
    }
    source = {
        "service": "app.ai_agent.tools.run_sql",
        "function": "run_sql",
        "as_of": utc_now_iso(),
        "certified": False,
    }
    return ok_envelope(data, definition=definition, source=source, ctx=ctx, truncated=bool(result["truncated"]))
