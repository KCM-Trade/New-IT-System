"""Tool — ``run_sql``, the UNCERTIFIED escape hatch (docs/ai-agent/02-contracts.md §10).

The three certified tools encode the 口径; this one lets the model write a
single read-only SELECT when none of them can answer. Its result is marked
``certified: false`` end to end (envelope → ``tool_done`` → amber badge), the
SQL is echoed back verbatim so the analyst can see exactly what ran, and the
prompt forces the model to say "uncertified" and list the known pitfalls.

Seven gates (02 §10.1), every one hard:

  ① read-only ACCOUNTS — MySQL: the shared ``readonly`` replica account
     through ``core.mysql_readonly.connect_readonly`` (connect 5s /
     MAX_EXECUTION_TIME 30s / read 40s); PG: the ``ai_agent_ro`` role
     (SELECT-only, ``default_transaction_read_only``) plus ``statement_timeout``.
  ② ONE statement whose root is SELECT / UNION (sqlglot AST, dialect-aware);
     any DML / DDL / admin node ANYWHERE in the tree (``WITH d AS (DELETE …)``)
     is refused — see ``_FORBIDDEN_NODES``. COMMENTS are refused before parsing
     (``/* */``, ``--``, ``#`` outside string literals): MySQL executes the body
     of ``/*!50000 … */`` while every parser treats it as a comment, so a comment
     is the one place where "what the AST saw" and "what the server runs" can
     differ. What is executed is the AST REGENERATED without comments
     (``tree.sql(comments=False)``), never the model's raw text.
  ③ node BLACKLIST — ``exp.Command`` (FLUSH / LOCK TABLES / CALL parse to it),
     ``INTO OUTFILE`` / ``SELECT … INTO @v`` (``exp.Into``), ``FOR UPDATE`` /
     ``LOCK IN SHARE MODE`` (``exp.Lock``), and the functions in
     ``_FORBIDDEN_FUNCTIONS`` (SLEEP / BENCHMARK / LOAD_FILE / GET_LOCK / pg_sleep…).
  ④ ROWS capped: ``limit`` clamped to ``MAX_LIMIT``; a SELECT gets ``LIMIT n+1``
     pushed into its own AST (``min(existing, n+1)`` when it already has one) so
     truncation is detected, not guessed, AND its ORDER BY keeps meaning — MySQL
     merges a derived table and is documented to drop the inner ORDER BY, so
     wrapping ``SELECT … ORDER BY`` in ``SELECT * FROM (…) LIMIT`` would hand
     back 200 arbitrary rows labelled "top 200". Only a UNION root (which
     cannot take a trailing LIMIT unambiguously) is wrapped. Cells cut at
     ``CELL_MAX_CHARS``.
  ⑤ TABLE whitelist per database, checked on every ``exp.Table`` node
     (CTE names excepted); system schemas are refused by construction because
     they are simply not in the list — plus, on PG, any relation named ``pg_*``
     (``pg_stat_activity``, ``pg_roles``, ``pg_settings`` … are reachable
     UNQUALIFIED because ``pg_catalog`` is implicitly on the search_path, so a
     schema whitelist alone would let them through). Server-identity /
     server-info functions (``USER()``, ``VERSION()``, ``@@datadir``,
     ``inet_server_addr()`` …) are refused on both engines.
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

Personal data (02 §13 last bullet) is refused at the AST layer: any column
named in ``PII_COLUMNS`` that belongs to (or may belong to) a ``PII_TABLES``
table is refused wherever it appears — select list, WHERE, function arguments,
CTEs — so ``CONCAT(email, '')``, ``SUBSTRING(phone, 1, 20)`` and ``WHERE email
LIKE …`` all stop before a connection is opened; and ``SELECT *`` / ``t.*`` over
``users`` / ``mt4_users`` is refused so the model must name columns. The same
names are ALSO masked at the output layer (``SENSITIVE_COLUMNS`` on the RESULT
column names) as belt and braces for anything the AST rule did not see.

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
#
# 30s since 2026-10-06 (user decision; 15s before — a 30s bump was tried and
# reverted on 2026-09-28). Measured that day: the whole-universe ranking over
# mt4_trades costs ~1.35s per closeDate day (14 days 19s, 30 days 40s), so 15s
# failed even the 14-day window the prompt recommends. The fxbackoffice replica
# is shared; a long scan there churns the buffer pool and queues behind MDL
# (the 08-09 / 08-15 incident shape, db-timeout-guard skill), so this is the
# one agent limit whose cost lands outside this app — do not raise it again to
# make a 30-day scan fit; that needs a pre-aggregate, not a longer budget. The
# client read_timeout is set explicitly from it — connect_readonly refuses a
# statement budget that is not strictly under its client-side read timeout.
STATEMENT_TIMEOUT_MS = 30_000
MYSQL_READ_TIMEOUT_S = STATEMENT_TIMEOUT_MS // 1000 + 10

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
# PG catalog objects are visible unqualified (pg_catalog is implicitly first on
# search_path), so the schema whitelist is not enough: refuse the name prefix.
PG_FORBIDDEN_TABLE_PREFIX = "pg_"
PG_FORBIDDEN_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})

# 02 §13: tables that hold personal data. A PII column reached through one of
# these — or unqualified in a query that touches one — is refused (see
# ``_check_pii``); ``SELECT *`` over them is refused too.
PII_TABLES = frozenset({"users", "mt4_users"})

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
    # @@datadir / @@hostname / @@version_comment … and @user variables: server
    # and session state, never client data.
    exp.SessionParameter,
    exp.Parameter,
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
        # server / connection identity — leaks the account, host, version
        "user",
        "current_user",
        "currentuser",
        "session_user",
        "sessionuser",
        "system_user",
        "version",
        "current_version",
        "currentversion",
        "database",
        "schema",
        "current_schema",
        "currentschema",
        "current_database",
        "currentdatabase",
        "connection_id",
        "inet_server_addr",
        "inet_server_port",
        "inet_client_addr",
        "inet_client_port",
        "pg_backend_pid",
        "pg_postmaster_start_time",
        "pg_conf_load_time",
    }
)
# Function-name PREFIXES refused as a family (file system, large objects,
# foreign connections, sleeping, backend control, advisory locks).
_FORBIDDEN_FUNCTION_PREFIXES = (
    "pg_ls_",
    "pg_read_",
    "pg_stat_file",
    "pg_sleep",
    "pg_terminate",
    "pg_cancel",
    "pg_advisory",
    "pg_try_advisory",
    "lo_",
    "dblink",
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
        "telephone",
        "passport",
        "id_number",
        "birthday",
        "dob",
    }
)
# AST-layer list (same names; kept as its own constant so the two layers can
# diverge deliberately later without one silently widening the other).
PII_COLUMNS = SENSITIVE_COLUMNS
MASK = "***"

UNCERTIFIED_SUMMARY = "Ad-hoc SQL written by the model; NOT a certified 口径"
FIXED_CAVEATS = [
    # Lots are ×100 because of the SYMBOL, not the account (OPT-0069 data check
    # 2026-09-30: CEN accounts trade only .cent/.kcmc symbols, so the two
    # coincide in practice; a non-CEN account holding a cent symbol exists).
    "CEN 未换算 — money is ×100 on CEN accounts (mt4_users.CURRENCY = 'CEN') and on .cent/.kcmc symbols; "
    "lots are ×100 on .cent/.kcmc symbols only (XAUUSD.c is not cent); nothing here divided them.",
    "demo/员工未排除 — demo/test groups and employee clients (users.isEmployee) are in the rows unless the SQL excluded them.",
    "sid=5 CMD 未归一化 — MT5 (sid 5) closed rows store the EXIT side in CMD; direction is inverted for those rows.",
    "日界按 SQL 原样 — closeDate/openDate are MT server days (UTC+3 summer / UTC+2 winter); *_TIME columns are MT wall clock, not UTC.",
    f"Personal-data columns ({', '.join(sorted(PII_COLUMNS))}) of {'/'.join(sorted(PII_TABLES))} cannot be queried, and "
    f"SELECT * over those tables is refused; any such result column is additionally masked as '{MASK}'.",
]

_DIALECT = {DB_MYSQL: "mysql", DB_PG: "postgres"}


# ── the pure guard (② ③ ⑤) ───────────────────────────────────────────────────


def _invalid(reason: str, sql: str) -> dict:
    return error_envelope("invalid_argument", f"SQL refused: {reason}", {"sql": sql[:MAX_SQL_CHARS]})


def _function_names(node: exp.Func) -> set[str]:
    """Every spelling sqlglot may give a function: the raw name for
    ``Anonymous``, ``sql_name()`` and the class name for typed nodes
    (``version()`` is ``CurrentVersion`` / ``CURRENT_VERSION``)."""
    names = {type(node).__name__.lower()}
    if isinstance(node, exp.Anonymous):
        names.add(str(node.name or "").lower())
    else:
        try:
            names.add(str(node.sql_name() or "").lower())
        except Exception:  # noqa: BLE001 — a name we cannot read is not one we allow
            pass
    return {n for n in names if n}


def _forbidden_function(node: exp.Func) -> Optional[str]:
    for name in _function_names(node):
        if name in _FORBIDDEN_FUNCTIONS or name.startswith(_FORBIDDEN_FUNCTION_PREFIXES):
            return name
    return None


_QUOTES = {"'", '"', "`"}


def find_comment(text: str) -> Optional[str]:
    """Return a description of the first SQL comment in ``text``, or None.

    ``/*`` and ``*/`` are refused ANYWHERE (even inside a literal — the model
    has no honest use for them and MySQL's ``/*!…*/`` is the attack); ``--``
    and ``#`` only outside single-, double- and back-quoted literals so that
    ``WHERE name = '#1'`` stays legal. Escapes: backslash and doubled quotes.
    """
    if "/*" in text or "*/" in text:
        return "block comment (/* */)"
    quote: Optional[str] = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\" and quote != "`":
                i += 2
                continue
            if ch == quote:
                if i + 1 < n and text[i + 1] == quote:  # doubled quote inside literal
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch == "#":
            return "line comment (#)"
        elif ch == "-" and i + 1 < n and text[i + 1] == "-":
            return "line comment (--)"
        i += 1
    return None


def _parse_single(text: str, db: str) -> exp.Expression | dict:
    """②: one statement, root SELECT / UNION. Returns the tree or a refusal."""
    try:
        statements = [st for st in sqlglot.parse(text, read=_DIALECT[db]) if st is not None]
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
    return tree


def _is_star_projection(expression: exp.Expression) -> bool:
    """``SELECT *`` or ``SELECT t.*`` (a Column whose name part is a Star).
    ``COUNT(*)`` is a Func and does not count."""
    if isinstance(expression, exp.Star):
        return True
    return isinstance(expression, exp.Column) and isinstance(expression.this, exp.Star)


def _check_pii(tree: exp.Expression, text: str) -> Optional[dict]:
    """02 §13 at the AST layer. Only tables in PII_TABLES carry personal data,
    so ``tags.name`` stays legal while ``users.name`` (qualified, aliased or
    unqualified in a query that touches users) is refused."""
    alias_to_table: dict[str, str] = {}
    referenced: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = str(table.name or "").lower()
        if not name:
            continue
        referenced.add(name)
        alias_to_table[name] = name
        if table.alias:
            alias_to_table[str(table.alias).lower()] = name
    touches_pii = bool(referenced & PII_TABLES)
    if not touches_pii:
        return None

    for select in tree.find_all(exp.Select):
        if any(_is_star_projection(e) for e in select.expressions):
            return _invalid(
                f"SELECT * is not allowed when {'/'.join(sorted(PII_TABLES))} is in the query — name the columns you need",
                text,
            )

    for column in tree.find_all(exp.Column):
        name = str(column.name or "").lower()
        if name not in PII_COLUMNS:
            continue
        qualifier = str(column.table or "").lower()
        if qualifier:
            resolved = alias_to_table.get(qualifier, qualifier)
            if resolved not in PII_TABLES:
                continue  # e.g. t.name where t is tags
        return _invalid(f"column {name} is personal data and cannot be queried", text)
    return None


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
    comment = find_comment(text)
    if comment is not None:
        return _invalid(f"comments are not allowed ({comment}); write the query without comments", text)

    tree = _parse_single(text, db)
    if isinstance(tree, dict):
        return tree

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return _invalid(f"{type(node).__name__} is not allowed inside a read-only query", text)
        if isinstance(node, exp.Func):
            fname = _forbidden_function(node)
            if fname is not None:
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
            if schema in PG_FORBIDDEN_SCHEMAS or name.startswith(PG_FORBIDDEN_TABLE_PREFIX):
                return _invalid(f"{schema + '.' if schema else ''}{name} is a system catalog object and is not allowed", text)
            schema = schema or PG_DEFAULT_SCHEMA
            if schema not in PG_SCHEMAS:
                return _invalid(f"schema {schema} is not in the whitelist ({', '.join(sorted(PG_SCHEMAS))}.*)", text)

    if db == DB_MYSQL:
        slow = _check_open_sentinel(tree, text)
        if slow is not None:
            return slow

    return _check_pii(tree, text)


# The open-order sentinel on mt4_trades is `closeDate = '1970-01-01'` (indexed).
# `CLOSE_TIME = '1970-01-01…'` means the same thing but is not indexed: a full
# scan of ~48M rows that always dies on the statement limit (2026-09-29, twice in one
# turn, "who holds the most XAUUSD"). Refusing it is exact — it is never the
# right way to write the query — and the message tells the model the fix.
_TIME_COLUMNS = frozenset({"close_time", "open_time"})
OPEN_SENTINEL_HINT = (
    "open (still-held) orders are `closeDate = '1970-01-01'` (indexed, sub-second); "
    "CLOSE_TIME / OPEN_TIME are not indexed and this scan cannot finish within the statement limit. "
    "Do not add an openDate range to an open-positions question. "
    "For current exposure by symbol use the certified rank_open_positions tool instead of SQL"
)


def _check_open_sentinel(tree: exp.Expression, text: str) -> Optional[dict]:
    for node in tree.find_all(exp.EQ):
        sides = (node.left, node.right)
        col = next((s for s in sides if isinstance(s, exp.Column)), None)
        lit = next((s for s in sides if isinstance(s, exp.Literal) and s.is_string), None)
        if col is None or lit is None:
            continue
        if str(col.name or "").lower() in _TIME_COLUMNS and str(lit.this).startswith("1970-01-01"):
            return _invalid(OPEN_SENTINEL_HINT, text)
    return None


# ── execution (① ③-timeouts ④) ───────────────────────────────────────────────


def clamp_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def wrap_with_limit(sql: str, limit: int) -> str:
    """``SELECT * FROM (<sql>) AS _q LIMIT n+1`` — used for UNION roots only
    (see ``prepare_sql``): one more than asked so a full page proves truncation
    instead of leaving it to guesswork."""
    inner = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({inner}) AS _q LIMIT {int(limit) + 1}"


def prepare_sql(sql: str, db: str, limit: int) -> str:
    """The text that is actually EXECUTED for an already-validated statement.

    * Regenerated from the AST with ``comments=False`` — never the raw text, so
      nothing a parser skipped can reach the server.
    * ``exp.Select`` root: ``LIMIT n+1`` pushed into the statement itself
      (``min(existing, n+1)`` if it already had one). ORDER BY stays at the top
      level, where MySQL honours it; wrapping would let MySQL merge the derived
      table and drop the ordering (documented behaviour), and ``SELECT u.*, mu.*``
      would die with 1060 duplicate column inside a derived table.
    * ``exp.Union`` root: wrapped, because a trailing LIMIT on a UNION is
      ambiguous across dialects and the wrap is unambiguous.
    """
    dialect = _DIALECT[db]
    tree = _parse_single(sql.strip(), db)
    if isinstance(tree, dict):  # validate_sql already ran; this is a programming error
        raise ValueError(tree["error"]["message"])
    page = int(limit) + 1
    if isinstance(tree, exp.Select):
        existing = tree.args.get("limit")
        if existing is not None:
            try:
                current = int(existing.expression.name)
            except (TypeError, ValueError, AttributeError):
                current = page
            page = min(current, page)
        return tree.limit(page).sql(dialect=dialect, comments=False)
    return wrap_with_limit(tree.sql(dialect=dialect, comments=False), int(limit))


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
        "Narrow it (fewer rows, tighter WHERE, an indexed column) and retry once. "
        "On mt4_trades the indexed filters are closeDate, openDate and loginSid "
        "(open orders: closeDate = '1970-01-01'); a narrower range on a NON-indexed "
        "column will time out again.",
    )


# pymysql error numbers that mean "the SERVER stopped the statement".
# 3024 = ER_QUERY_TIMEOUT (MAX_EXECUTION_TIME fired), 1317 = ER_QUERY_INTERRUPTED,
# 2013 = CR_SERVER_LOST (read_timeout abandoned the socket).
_MYSQL_TIMEOUT_CODES = frozenset({3024, 1317, 2013})


def execute_mysql(settings: Settings, sql: str, limit: int) -> dict:
    """① + ④ on the fxbackoffice replica. ``sql`` is the PREPARED text from
    ``prepare_sql`` (limit already inside). Returns a page dict or an error envelope."""
    wrapped = sql
    try:
        conn = connect_readonly(
            settings,
            max_execution_ms=STATEMENT_TIMEOUT_MS,
            read_timeout=MYSQL_READ_TIMEOUT_S,
        )
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
    """① + ④ on risk_cases (role ai_agent_ro). ``sql`` is the PREPARED text from
    ``prepare_sql``. Returns a page dict or an error envelope."""
    import psycopg2
    import psycopg2.errors

    wrapped = sql
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
    executed = prepare_sql(text, db, page_size)

    executor = execute_mysql if db == DB_MYSQL else execute_pg
    result = await run_sync_with_timeout(executor, ctx.settings, executed, page_size, ctx=ctx)
    if is_error(result):
        return result

    data = {
        "db": db,
        "sql": text,
        "sql_executed": executed,
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
