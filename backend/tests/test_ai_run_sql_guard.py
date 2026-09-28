"""The run_sql escape hatch's gates (docs/ai-agent/02-contracts.md §10.1 / §10.1a).

Every row of §10.1a is pinned here, without a database:

  * single statement            — `SELECT 1; SELECT 2` refused; pymysql never gets MULTI_STATEMENTS
  * AST root type               — DML / DDL / admin roots refused, CTE bodies included
  * node blacklist              — Command (FLUSH / LOCK TABLES / CALL), INTO, Lock, SLEEP/BENCHMARK/LOAD_FILE
  * table whitelist             — per node, system schemas and foreign schemas refused, CTE names allowed
  * timeout                     — MySQL 3024 / PG QueryCanceled map to upstream_timeout
  * attribution                 — routes/ai.py copies the SQL into the audit row (tests/test_ai_route.py)

plus gate ④ (limit clamp + truncation detection), gate ⑥ (restricted caller →
scope_denied and the tool not registered), and the output-layer mask.
"""

from __future__ import annotations

import inspect
from typing import Any

import pymysql
import pytest

from app.ai_agent.tools import run_sql as rs
from app.ai_agent.tools.common import ctx_from_request

MYSQL = rs.DB_MYSQL
PG = rs.DB_PG


@pytest.fixture
def anyio_backend():
    # asyncio only: trio is not installed in either venv.
    return "asyncio"


def _ctx(scope: Any = None):
    return ctx_from_request({"user_id": 7, "email": "x@kohleservices.com", "role": "user", "allowed_modules": ["ai"]}, scope, "trace-1")


def _code(env: dict | None) -> str | None:
    return None if env is None else env["error"]["code"]


# ── ② ③ ⑤ : the pure guard ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT 1;",
        "SELECT COUNT(*) FROM mt4_users",
        "SELECT * FROM fxbackoffice.tags",
        "SELECT * FROM `fxbackoffice`.`mt4_trades` WHERE closeDate = '2026-09-01'",
        "SELECT LOGIN FROM mt4_users UNION SELECT id FROM users",
        "WITH c AS (SELECT userId AS u FROM mt4_users) SELECT u FROM c",
        "SELECT u.id, m.LOGIN FROM users u JOIN mt4_users m ON m.userId = u.id",
        "SELECT * FROM tags WHERE name = '#1'",          # '#' inside a literal is not a comment
        "SELECT * FROM tags WHERE name = 'it''s -- x'",   # doubled quote + '--' inside a literal
        "SELECT t.name FROM tags t JOIN user_tags ut ON ut.tagId = t.id",  # tags.name is not PII
        "SELECT * FROM (SELECT 1 AS x) AS t",
        "SELECT t.SYMBOL, SUM(t.lots) FROM mt4_trades t GROUP BY t.SYMBOL ORDER BY 2 DESC LIMIT 5",
    ],
)
def test_allowed_mysql_selects_pass(sql):
    assert rs.validate_sql(sql, MYSQL) is None


@pytest.mark.parametrize(
    "sql",
    [
        # DML / DDL roots and a DML hidden in a CTE (the MCP Toolbox bypass)
        "DELETE FROM users",
        "WITH d AS (DELETE FROM users) SELECT 1",
        "INSERT INTO tags (name) VALUES ('x')",
        "UPDATE users SET isEmployee = 1",
        "DROP TABLE tags",
        "CREATE TABLE t (x INT)",
        "ALTER TABLE tags ADD COLUMN x INT",
        "TRUNCATE TABLE tags",
        # multiple statements
        "SELECT 1; SELECT 2",
        "SELECT 1 -- comment\n; DROP TABLE tags",
        # admin / lock statements (parse to Command or their own node, or fail to parse)
        "FLUSH TABLES WITH READ LOCK",
        "FLUSH TABLES",
        "LOCK TABLES users READ",
        "UNLOCK TABLES",
        "KILL 1",
        "SHOW TABLES",
        "SHOW PROCESSLIST",
        "SET @a = 1",
        "SET SESSION MAX_EXECUTION_TIME = 0",
        "CALL p()",
        "USE mysql",
        "EXPLAIN SELECT 1",
        "DESCRIBE users",
        "HANDLER users OPEN",
        "DO SLEEP(5)",
        # file / variable sinks
        "SELECT * FROM users INTO OUTFILE '/tmp/x'",
        "SELECT * FROM users INTO DUMPFILE '/tmp/x'",
        "SELECT 1 INTO @v",
        "SELECT LOAD_FILE('/etc/passwd')",
        # stall / lock functions
        "SELECT SLEEP(20)",
        "SELECT sleep(20)",
        "SELECT BENCHMARK(1000000000, MD5('x'))",
        "SELECT GET_LOCK('a', 10)",
        "SELECT * FROM mt4_trades WHERE 1 = SLEEP(1)",
        # explicit row locks
        "SELECT * FROM mt4_trades FOR UPDATE",
        "SELECT * FROM mt4_trades LOCK IN SHARE MODE",
        "SELECT * FROM mt4_trades FOR SHARE",
        # schema probing / foreign schemas
        "SELECT * FROM information_schema.tables",
        "SELECT (SELECT COUNT(*) FROM information_schema.tables)",
        "SELECT * FROM mysql.user",
        "SELECT * FROM performance_schema.threads",
        "SELECT * FROM sys.session",
        "SELECT * FROM other_db.mt4_trades",
        "SELECT * FROM mt5_live.mt5_deals",
        "SELECT * FROM not_whitelisted",
        "SELECT * FROM a.b.c",
        # junk
        "garbage ((",
        "",
        "   ",
    ],
)
def test_refused_mysql_statements_are_invalid_argument(sql):
    assert _code(rs.validate_sql(sql, MYSQL)) == "invalid_argument"


def test_unknown_db_is_invalid_argument():
    assert _code(rs.validate_sql("SELECT 1", "mt5_live")) == "invalid_argument"
    assert _code(rs.validate_sql("SELECT 1", None)) == "invalid_argument"


def test_non_string_sql_is_invalid_argument():
    assert _code(rs.validate_sql(123, MYSQL)) == "invalid_argument"
    assert _code(rs.validate_sql(["SELECT 1"], MYSQL)) == "invalid_argument"


def test_oversized_sql_is_refused_before_parsing():
    sql = "SELECT " + ", ".join(["1"] * 3000)
    assert len(sql) > rs.MAX_SQL_CHARS
    env = rs.validate_sql(sql, MYSQL)
    assert _code(env) == "invalid_argument" and "longer" in env["error"]["message"]


def test_the_refusal_names_the_gate_and_echoes_the_sql():
    env = rs.validate_sql("SELECT * FROM mysql.user", MYSQL)
    assert "whitelist" in env["error"]["message"]
    assert env["error"]["detail"]["sql"] == "SELECT * FROM mysql.user"


def test_cte_name_is_allowed_as_a_table_reference_but_not_as_a_disguise():
    # The CTE alias `c` is not a real table and must pass …
    assert rs.validate_sql("WITH c AS (SELECT 1 AS x FROM users) SELECT x FROM c", MYSQL) is None
    # … but a CTE named like a forbidden schema.table does not launder the real one.
    assert _code(rs.validate_sql("WITH c AS (SELECT 1) SELECT * FROM mysql.user, c", MYSQL)) == "invalid_argument"


def test_every_table_node_is_checked_not_just_the_first():
    sql = "SELECT * FROM mt4_users m JOIN mysql.user u ON u.User = m.LOGIN"
    assert _code(rs.validate_sql(sql, MYSQL)) == "invalid_argument"
    sql = "SELECT * FROM mt4_users WHERE userId IN (SELECT id FROM secret_table)"
    assert _code(rs.validate_sql(sql, MYSQL)) == "invalid_argument"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM kcm.crm_user_tags LIMIT 5",
        "SELECT * FROM public.risk_cases",
        "SELECT * FROM risk_cases",
        "WITH a AS (SELECT 1 AS x) SELECT x FROM a",
    ],
)
def test_allowed_pg_selects_pass(sql):
    assert rs.validate_sql(sql, PG) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT pg_sleep(20)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM risk_cases FOR UPDATE",
        "COPY risk_cases TO '/tmp/x'",
        "DELETE FROM risk_cases",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT * FROM other_schema.t",
        "SELECT 1; SELECT 2",
        "BEGIN",
        "COMMIT",
    ],
)
def test_refused_pg_statements_are_invalid_argument(sql):
    assert _code(rs.validate_sql(sql, PG)) == "invalid_argument"


# ── ① single-statement connection ────────────────────────────────────────────


def test_connect_readonly_never_enables_multi_statements(monkeypatch):
    """`SELECT 1; FLUSH TABLES` as ONE string would only work if pymysql were
    given CLIENT.MULTI_STATEMENTS. connect_readonly must not pass client_flag
    at all (pymysql's default excludes it), and the harness must not add it."""
    from app.core import mysql_readonly

    captured: dict[str, Any] = {}

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a, **_k):
            return 0

    class _FakeConn:
        def cursor(self, *_a, **_k):
            return _FakeCursor()

        def close(self):
            pass

    def _fake_connect(**kwargs):
        captured.update(kwargs)
        return _FakeConn()

    monkeypatch.setattr(mysql_readonly.pymysql, "connect", _fake_connect)

    class _S:
        DB_HOST = "h"
        DB_USER = "u"
        DB_PASSWORD = "p"
        FXBACK_DB_NAME = "fxbackoffice"
        DB_PORT = 3306
        DB_CHARSET = "utf8mb4"

    mysql_readonly.connect_readonly(_S(), max_execution_ms=rs.STATEMENT_TIMEOUT_MS)
    assert "client_flag" not in captured
    assert captured["autocommit"] is True
    assert captured["read_timeout"] * 1000 > rs.STATEMENT_TIMEOUT_MS
    # And the default flag set pymysql would use really lacks it.
    from pymysql.constants import CLIENT

    sig = inspect.signature(pymysql.connections.Connection.__init__)
    default_flag = sig.parameters["client_flag"].default
    assert not (default_flag & CLIENT.MULTI_STATEMENTS)


def test_execute_mysql_passes_the_15s_statement_budget(monkeypatch):
    seen: dict[str, Any] = {}

    class _Cur:
        description = [("n",)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            seen["sql"] = sql

        def fetchall(self):
            return [(1,)]

    class _Conn:
        def cursor(self, *_a):
            return _Cur()

        def close(self):
            seen["closed"] = True

    def _fake_connect(settings, *, max_execution_ms):
        seen["max_execution_ms"] = max_execution_ms
        return _Conn()

    monkeypatch.setattr(rs, "connect_readonly", _fake_connect)
    out = rs.execute_mysql(object(), "SELECT 1 LIMIT 201", 200)
    assert seen["max_execution_ms"] == 15_000
    assert seen["closed"] is True
    # The executor runs EXACTLY the prepared text (prepare_sql owns the limit).
    assert seen["sql"] == "SELECT 1 LIMIT 201"
    assert out["rows"] == [[1]]


# ── ③ timeouts → upstream_timeout ────────────────────────────────────────────


def _mysql_conn_raising(exc):
    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            raise exc

    class _Conn:
        def cursor(self, *_a):
            return _Cur()

        def close(self):
            pass

    return lambda settings, *, max_execution_ms: _Conn()


@pytest.mark.parametrize("errno", [3024, 1317, 2013])
def test_mysql_server_side_kill_maps_to_upstream_timeout(monkeypatch, errno):
    monkeypatch.setattr(rs, "connect_readonly", _mysql_conn_raising(pymysql.err.OperationalError(errno, "killed")))
    out = rs.execute_mysql(object(), "SELECT 1", 10)
    assert out["ok"] is False and out["error"]["code"] == "upstream_timeout"


def test_mysql_rejecting_the_query_is_invalid_argument_with_the_server_message(monkeypatch):
    monkeypatch.setattr(rs, "connect_readonly", _mysql_conn_raising(pymysql.err.ProgrammingError(1054, "Unknown column 'nope'")))
    out = rs.execute_mysql(object(), "SELECT nope FROM users", 10)
    assert out["error"]["code"] == "invalid_argument"
    assert "Unknown column" in out["error"]["message"]
    assert out["error"]["detail"]["mysql_errno"] == 1054


def test_pg_query_canceled_maps_to_upstream_timeout(monkeypatch):
    import psycopg2.errors

    class _Cur:
        description = None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            if "statement_timeout" in sql or "READ ONLY" in sql:
                return
            raise psycopg2.errors.QueryCanceled("canceling statement due to statement timeout")

    class _Conn:
        def cursor(self):
            return _Cur()

    from contextlib import contextmanager

    @contextmanager
    def _fake_session(dsn):
        yield _Conn()

    class _S:
        def risk_cases_pg_dsn(self):
            return "dbname=x"

    monkeypatch.setattr(rs, "pg_session", _fake_session)
    out = rs.execute_pg(_S(), "SELECT 1", 10)
    assert out["error"]["code"] == "upstream_timeout"


def test_pg_session_sets_read_only_and_the_15s_budget(monkeypatch):
    executed: list[str] = []

    class _Cur:
        description = [("a",), ("email",)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            executed.append(sql)

        def fetchall(self):
            return [(1, "x@y")]

    class _Conn:
        def cursor(self):
            return _Cur()

    from contextlib import contextmanager

    @contextmanager
    def _fake_session(dsn):
        yield _Conn()

    class _S:
        def risk_cases_pg_dsn(self):
            return "dbname=x"

    monkeypatch.setattr(rs, "pg_session", _fake_session)
    out = rs.execute_pg(_S(), "SELECT 1", 10)
    assert executed[0] == "SET TRANSACTION READ ONLY"
    assert executed[1] == "SET LOCAL statement_timeout = 15000"
    assert out["rows"] == [[1, "***"]] and out["masked_columns"] == ["email"]


# ── ④ limit / truncation / cell cap, and the output mask ────────────────────


def test_limit_is_clamped_to_200_and_truncation_is_detected():
    assert rs.clamp_limit(201) == 200
    assert rs.clamp_limit(0) == 1
    assert rs.clamp_limit("abc") == rs.DEFAULT_LIMIT
    assert rs.clamp_limit(None) == rs.DEFAULT_LIMIT
    assert rs.wrap_with_limit("SELECT 1;", 200) == "SELECT * FROM (SELECT 1) AS _q LIMIT 201"
    page = rs.shape_rows(["x"], [(i,) for i in range(201)], 200)
    assert page["row_count"] == 200 and page["truncated"] is True
    page = rs.shape_rows(["x"], [(i,) for i in range(200)], 200)
    assert page["row_count"] == 200 and page["truncated"] is False


def test_cells_are_cut_at_500_chars_and_odd_types_are_json_safe():
    import decimal
    from datetime import datetime

    page = rs.shape_rows(["s", "d", "t", "b"], [("a" * 600, decimal.Decimal("1.50"), datetime(2026, 9, 27, 1, 2, 3), b"bytes")], 10)
    s, d, t, b = page["rows"][0]
    assert len(s) == rs.CELL_MAX_CHARS + 1 and s.endswith("…")
    assert d == 1.5 and t == "2026-09-27T01:02:03" and b == "bytes"


@pytest.mark.parametrize("col", ["email", "EMAIL", "Phone", "name", "last_ip", "IPAddress", "password"])
def test_sensitive_columns_are_masked_by_result_name(col):
    page = rs.shape_rows([col, "login"], [("secret", 8522845), (None, 1)], 10)
    assert page["rows"][0] == ["***", 8522845]
    assert page["rows"][1] == [None, 1]  # NULL stays NULL — nothing to hide
    assert page["masked_columns"] == [col]


def test_alias_does_not_unmask_but_a_non_sensitive_alias_does():
    # The mask is by RESULT column name: `email AS e` is not caught (documented
    # limitation), `login AS email` is masked. Pin the rule so nobody "fixes"
    # it silently in either direction.
    assert rs.shape_rows(["e"], [("x@y",)], 5)["rows"] == [["x@y"]]
    assert rs.shape_rows(["email"], [(123,)], 5)["rows"] == [["***"]]


# ── ⑥ restricted caller ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_restricted_caller_gets_scope_denied_even_if_the_impl_is_reached():
    out = await rs.run_sql(_ctx(scope=[1]), MYSQL, "SELECT 1")
    assert out["ok"] is False and out["error"]["code"] == "scope_denied"
    out = await rs.run_sql(_ctx(scope=[]), MYSQL, "SELECT 1")
    assert out["error"]["code"] == "scope_denied"


def test_harness_registers_run_sql_only_for_unrestricted_callers():
    from app.ai_agent import harness

    async def _emit(_e, _d):
        pass

    unrestricted = [t.name for t in harness.build_tools(_ctx(None), _emit)]
    restricted = [t.name for t in harness.build_tools(_ctx([1]), _emit)]
    empty_scope = [t.name for t in harness.build_tools(_ctx([]), _emit)]
    assert "run_sql" in unrestricted
    assert "run_sql" not in restricted
    assert "run_sql" not in empty_scope
    # Every certified tool (Tier 1 + Tier 2) is offered to everyone; run_sql is
    # the only tool whose presence depends on the caller.
    certified = ["get_client_overview", "get_trade_activity", "get_risk_signals", "rank_accounts", "get_economic_calendar"]
    assert unrestricted == certified + ["run_sql"]
    assert restricted == empty_scope == certified
    # OPT-0066: a third list — `risk` holders additionally get the three Risk
    # control tools, but only when unrestricted (docs/ai-agent/11 §0 T1).
    risk_tools = ["get_risk_alerts", "get_alert_orders", "get_window_scan"]

    def _risk_ctx(scope):
        return ctx_from_request({"user_id": 7, "email": "x@kohleservices.com", "role": "user",
                                 "allowed_modules": ["ai", "risk"]}, scope, "trace-1")

    with_risk = [t.name for t in harness.build_tools(_risk_ctx(None), _emit)]
    risk_restricted = [t.name for t in harness.build_tools(_risk_ctx([1]), _emit)]
    assert sorted(with_risk) == sorted(certified + ["run_sql"] + risk_tools)
    assert risk_restricted == certified


# ── the envelope ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_success_envelope_is_uncertified_and_echoes_the_sql(monkeypatch):
    monkeypatch.setattr(
        rs,
        "execute_mysql",
        lambda settings, sql, limit: {"columns": ["n"], "rows": [[5]], "row_count": 1, "truncated": False, "masked_columns": []},
    )
    out = await rs.run_sql(_ctx(None), MYSQL, "  SELECT COUNT(*) AS n FROM tags  ", limit=999)
    assert out["ok"] is True
    assert out["source"]["certified"] is False
    assert out["source"]["function"] == "run_sql"
    assert out["definition"]["summary"] == rs.UNCERTIFIED_SUMMARY
    assert out["definition"]["day_basis"] == "as written in the SQL"
    assert out["data"]["sql"] == "SELECT COUNT(*) AS n FROM tags"
    assert out["data"]["db"] == MYSQL
    assert out["data"]["limit"] == 200
    assert out["scope"]["cids_applied"] == "all"
    caveats = " ".join(out["definition"]["caveats"])
    for token in ("CEN", "demo", "sid=5", "日界", "***"):
        assert token in caveats


@pytest.mark.anyio
async def test_the_guard_runs_before_any_connection_is_opened(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("a connection was opened for a refused statement")

    monkeypatch.setattr(rs, "execute_mysql", _boom)
    monkeypatch.setattr(rs, "execute_pg", _boom)
    out = await rs.run_sql(_ctx(None), MYSQL, "DELETE FROM users")
    assert out["error"]["code"] == "invalid_argument"
    out = await rs.run_sql(_ctx(None), PG, "SELECT pg_sleep(1)")
    assert out["error"]["code"] == "invalid_argument"


@pytest.mark.anyio
async def test_truncated_result_sets_the_envelope_flag_and_a_caveat(monkeypatch):
    monkeypatch.setattr(
        rs,
        "execute_mysql",
        lambda settings, sql, limit: rs.shape_rows(["x"], [(i,) for i in range(limit + 1)], limit),
    )
    out = await rs.run_sql(_ctx(None), MYSQL, "SELECT LOGIN AS x FROM mt4_users", limit=3)
    assert out["truncated"] is True and out["data"]["row_count"] == 3
    assert any("More than 3 rows" in c for c in out["definition"]["caveats"])


# ── cold-review hardening (2026-09-28): comments, catalog objects, LIMIT push-down, PII at the AST ──


@pytest.mark.parametrize(
    "sql",
    [
        # MySQL executes the body of a version comment; every parser skips it.
        "SELECT 1 /*!50000 UNION SELECT user FROM mysql.user */",
        "SELECT /*! * FROM mysql.user */ FROM users",
        "SELECT 1 /* harmless */",
        "SELECT 1 -- x",
        "SELECT 1 # x",
        "SELECT * FROM tags -- WHERE 1=1",
        "SELECT 'a' # ' FROM mysql.user",
    ],
)
def test_comments_are_refused_before_parsing(sql):
    env = rs.validate_sql(sql, MYSQL)
    assert _code(env) == "invalid_argument"
    assert "comments are not allowed" in env["error"]["message"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("SELECT * FROM tags WHERE name = '#1'", None),
        ("SELECT * FROM tags WHERE name = 'a--b'", None),
        ("SELECT * FROM tags WHERE name = 'it''s -- #'", None),
        ('SELECT "x--y" FROM tags', None),
        ("SELECT `a#b` FROM tags", None),
        ("SELECT 1 -- x", "line comment (--)"),
        ("SELECT 1 # x", "line comment (#)"),
        ("SELECT 'a' # ' b", "line comment (#)"),
        ("SELECT 1 /* x */", "block comment (/* */)"),
        ("SELECT '/*' FROM tags", "block comment (/* */)"),  # refused even inside a literal, on purpose
    ],
)
def test_find_comment_respects_string_literals(text, expected):
    assert rs.find_comment(text) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT @@datadir",
        "SELECT @@hostname, 1",
        "SELECT @@version_comment",
        "SELECT @@datadir, USER()",
        "SELECT USER()",
        "SELECT CURRENT_USER()",
        "SELECT SESSION_USER()",
        "SELECT SYSTEM_USER()",
        "SELECT version()",
        "SELECT DATABASE()",
        "SELECT SCHEMA()",
        "SELECT CONNECTION_ID()",
        "SELECT id FROM tags WHERE id = @x",
    ],
)
def test_mysql_server_identity_is_refused(sql):
    assert _code(rs.validate_sql(sql, MYSQL)) == "invalid_argument"


@pytest.mark.parametrize(
    "sql",
    [
        # pg_catalog is implicitly on search_path: unqualified names reach it.
        "SELECT * FROM pg_stat_activity",
        "SELECT rolname FROM pg_roles",
        "SELECT usename FROM pg_user",
        "SELECT name, setting FROM pg_settings",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM public.pg_anything",
        "SELECT * FROM information_schema.tables",
        "SELECT pg_ls_waldir()",
        "SELECT pg_ls_dir('.')",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_stat_file('x')",
        "SELECT pg_sleep_for('1 second')",
        "SELECT current_setting('data_directory')",
        "SELECT set_config('x', 'y', false)",
        "SELECT lo_get(1)",
        "SELECT * FROM dblink('dbname=x', 'select 1') AS t(a int)",
        "SELECT pg_terminate_backend(1)",
        "SELECT pg_cancel_backend(1)",
        "SELECT inet_server_addr()",
        "SELECT inet_client_addr()",
        "SELECT version()",
        "SELECT current_user",
        "SELECT session_user",
        "SELECT current_database()",
        "SELECT pg_backend_pid()",
    ],
)
def test_pg_catalog_objects_and_server_functions_are_refused(sql):
    assert _code(rs.validate_sql(sql, PG)) == "invalid_argument"


def test_pg_whitelisted_relations_still_pass():
    assert rs.validate_sql("SELECT id FROM kcm.crm_user_tags LIMIT 3", PG) is None
    assert rs.validate_sql("SELECT id FROM public.risk_cases", PG) is None
    assert rs.validate_sql("SELECT generate_series(1, 10)", PG) is None  # bounded; the 15s budget covers abuse


# ── LIMIT is pushed into the statement, not wrapped around it ────────────────


def test_prepare_sql_pushes_limit_and_keeps_order_by_at_top_level():
    out = rs.prepare_sql("SELECT id FROM users ORDER BY id DESC", MYSQL, 200)
    assert out == "SELECT id FROM users ORDER BY id DESC LIMIT 201"
    assert "_q" not in out


def test_prepare_sql_keeps_a_smaller_existing_limit():
    assert rs.prepare_sql("SELECT id FROM users ORDER BY id DESC LIMIT 5", MYSQL, 200).endswith("LIMIT 5")
    assert rs.prepare_sql("SELECT id FROM users LIMIT 500", MYSQL, 200).endswith("LIMIT 201")


def test_prepare_sql_wraps_only_a_union():
    out = rs.prepare_sql("SELECT LOGIN FROM mt4_users UNION SELECT id FROM users", MYSQL, 200)
    assert out.startswith("SELECT * FROM (") and out.endswith(") AS _q LIMIT 201")


def test_prepare_sql_does_not_wrap_a_two_table_join():
    # `SELECT u.*, mu.*` inside a derived table dies with MySQL 1060 (duplicate
    # column); with the limit pushed down the join runs as written.
    out = rs.prepare_sql("SELECT u.id, mu.LOGIN FROM users u JOIN mt4_users mu ON mu.userId = u.id", MYSQL, 3)
    assert out == "SELECT u.id, mu.LOGIN FROM users AS u JOIN mt4_users AS mu ON mu.userId = u.id LIMIT 4"


def test_prepare_sql_regenerates_from_the_ast_and_uses_the_dialect():
    assert rs.prepare_sql("SELECT id FROM kcm.x ORDER BY id;", PG, 10) == "SELECT id FROM kcm.x ORDER BY id LIMIT 11"


def test_prepare_sql_refuses_to_prepare_an_unvalidated_statement():
    with pytest.raises(ValueError):
        rs.prepare_sql("DELETE FROM users", MYSQL, 10)


@pytest.mark.anyio
async def test_run_sql_executes_the_prepared_text_and_reports_it(monkeypatch):
    seen: dict[str, Any] = {}

    def _fake(settings, sql, limit):
        seen["sql"] = sql
        return {"columns": ["id"], "rows": [[1]], "row_count": 1, "truncated": False, "masked_columns": []}

    monkeypatch.setattr(rs, "execute_mysql", _fake)
    out = await rs.run_sql(_ctx(None), MYSQL, "SELECT id FROM tags ORDER BY id DESC", limit=3)
    assert out["ok"] is True
    assert seen["sql"] == "SELECT id FROM tags ORDER BY id DESC LIMIT 4"
    assert out["data"]["sql"] == "SELECT id FROM tags ORDER BY id DESC"
    assert out["data"]["sql_executed"] == seen["sql"]


# ── PII refused at the AST layer (output mask stays as belt and braces) ─────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CONCAT(email, '') AS e FROM users LIMIT 1",
        "SELECT SUBSTRING(phone, 1, 20) AS p FROM users",
        "SELECT id FROM users WHERE email LIKE '%@%'",
        "SELECT u.name FROM users u",
        "SELECT mu.NAME FROM mt4_users mu",
        "SELECT LOGIN FROM mt4_users WHERE last_ip IS NOT NULL",
        "WITH c AS (SELECT email AS e FROM users) SELECT e FROM c",
        "SELECT * FROM users",
        "SELECT u.* FROM users u",
        "SELECT u.*, mu.* FROM users u JOIN mt4_users mu ON mu.userId = u.id",
        "SELECT * FROM tags t JOIN users u ON u.id = 1",
        # unqualified `name` in a query that touches users could be users.name
        "SELECT name FROM tags t JOIN users u ON u.id = 1",
    ],
)
def test_pii_columns_and_star_over_pii_tables_are_refused(sql):
    env = rs.validate_sql(sql, MYSQL)
    assert _code(env) == "invalid_argument"
    assert "personal data" in env["error"]["message"] or "SELECT *" in env["error"]["message"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, cid, isEmployee FROM users LIMIT 5",
        "SELECT COUNT(*) FROM users",
        "SELECT COUNT(*) FROM mt4_users",
        "SELECT * FROM tags",
        "SELECT t.name FROM tags t",
        "SELECT name FROM tags",
        "SELECT t.name, ut.userId FROM tags t JOIN user_tags ut ON ut.tagId = t.id",
        "SELECT LOGIN, BALANCE FROM mt4_users WHERE `GROUP` NOT LIKE '%demo%'",
    ],
)
def test_non_pii_columns_pass_the_ast_check(sql):
    assert rs.validate_sql(sql, MYSQL) is None


def test_the_ast_check_runs_before_any_connection(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("connection opened for a PII query")

    monkeypatch.setattr(rs, "connect_readonly", _boom)
    assert _code(rs.validate_sql("SELECT email FROM users", MYSQL)) == "invalid_argument"
