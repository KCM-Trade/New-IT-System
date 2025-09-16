from __future__ import annotations

from typing import Iterable, List, Set, Dict

import pymysql

from ..core.config import Settings
from ..schemas.audience import (
    AudiencePreviewRequest,
    AudiencePreviewResponse,
    AudiencePreviewItem,
    Rule,
)


def _get_connection(settings: Settings):
    return pymysql.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        port=int(settings.DB_PORT),
        charset=settings.DB_CHARSET,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _fetch_all_dicts(cur) -> List[dict]:
    rows = cur.fetchall()
    return list(rows or [])


def _safe_parse_int(value):
    """Safely parse value to int; return None for None, empty string, or invalid numeric.

    fresh grad: backend defensive programming—DB 字段可能是空字符串或 NULL，这里统一做健壮转换。
    """
    if value is None:
        return None
    try:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return int(value)
    except (ValueError, TypeError):
        return None


def _union_update(target: Set[str], items: Iterable[str]) -> None:
    for x in items:
        if x is None:
            continue
        target.add(str(x))


def _lookup_logins_by_client_ids(conn, client_ids: List[int]) -> Set[str]:
    if not client_ids:
        return set()
    sql = (
        "SELECT login FROM mt4_live.mt4_users WHERE ID IN ("
        + ",".join(["%s"] * len(client_ids))
        + ")"
    )
    with conn.cursor() as cur:
        cur.execute(sql, client_ids)
        return {str(r["login"]) for r in _fetch_all_dicts(cur)}


def _logins_from_rule(conn, rule: Rule) -> Set[str]:
    if rule.type == "customer_ids":
        ids = getattr(rule, "ids", []) or []
        return _lookup_logins_by_client_ids(conn, ids)
    if rule.type == "account_ids":
        ids = getattr(rule, "ids", []) or []
        return {str(x) for x in ids}
    if rule.type == "customer_tags":
        tags: List[str] = getattr(rule, "tags", []) or []
        if not tags:
            return set()
        operator = getattr(rule, "operator", "ANY")
        # First find matching client_ids by tags
        if operator == "ALL":
            sql = (
                "SELECT ut.userid AS client_id\n"
                "FROM fxbackoffice.user_tags ut\n"
                "JOIN fxbackoffice.tags t ON t.id = ut.tagid\n"
                "WHERE t.tag IN (" + ",".join(["%s"] * len(tags)) + ")\n"
                "GROUP BY ut.userid\n"
                "HAVING COUNT(DISTINCT t.tag) = %s"
            )
            params = list(tags) + [len(tags)]
        else:  # ANY
            sql = (
                "SELECT DISTINCT ut.userid AS client_id\n"
                "FROM fxbackoffice.user_tags ut\n"
                "JOIN fxbackoffice.tags t ON t.id = ut.tagid\n"
                "WHERE t.tag IN (" + ",".join(["%s"] * len(tags)) + ")"
            )
            params = list(tags)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            client_ids = [int(r["client_id"]) for r in _fetch_all_dicts(cur)]
        return _lookup_logins_by_client_ids(conn, client_ids)
    return set()


def _assemble_items(conn, final_logins: Set[str]) -> List[AudiencePreviewItem]:
    if not final_logins:
        return []
    # Fetch account details
    sql_users = (
        "SELECT login, `group`, name, ID, REGDATE, BALANCE, equity\n"
        "FROM mt4_live.mt4_users\n"
        "WHERE login IN (" + ",".join(["%s"] * len(final_logins)) + ")"
    )
    logins_list = list(final_logins)
    with conn.cursor() as cur:
        cur.execute(sql_users, logins_list)
        rows = _fetch_all_dicts(cur)

    # Collect client_ids for tags lookup（健壮解析 ID，忽略空/非数字）
    raw_ids: List[int] = []
    for r in rows:
        cid = _safe_parse_int(r.get("ID"))
        if cid is not None:
            raw_ids.append(cid)
    client_ids = sorted(set(raw_ids))
    tags_by_client: Dict[int, List[str]] = {}
    if client_ids:
        sql_tags = (
            "SELECT ut.userid AS client_id, t.tag AS tag\n"
            "FROM fxbackoffice.user_tags ut\n"
            "JOIN fxbackoffice.tags t ON t.id = ut.tagid\n"
            "WHERE ut.userid IN (" + ",".join(["%s"] * len(client_ids)) + ")"
        )
        with conn.cursor() as cur:
            cur.execute(sql_tags, client_ids)
            tag_rows = _fetch_all_dicts(cur)
        for tr in tag_rows:
            cid = int(tr["client_id"]) if tr.get("client_id") is not None else None
            if cid is None:
                continue
            tags_by_client.setdefault(cid, []).append(str(tr["tag"]))

    items: List[AudiencePreviewItem] = []
    for r in rows:
        # 使用安全解析，避免 int("") 报错
        cid_val = _safe_parse_int(r.get("ID"))
        items.append(
            AudiencePreviewItem(
                account_id=str(r["login"]),
                client_id=cid_val,
                name=str(r["name"]) if r.get("name") is not None else None,
                group=str(r["group"]) if r.get("group") is not None else None,
                reg_date=str(r["REGDATE"]) if r.get("REGDATE") is not None else None,
                balance=float(r["BALANCE"]) if r.get("BALANCE") is not None else None,
                equity=float(r["equity"]) if r.get("equity") is not None else None,
                tags=tags_by_client.get(cid_val, []),
            )
        )
    # Optional: deterministic order by account_id
    items.sort(key=lambda x: x.account_id)
    return items


def audience_preview(settings: Settings, req: AudiencePreviewRequest) -> AudiencePreviewResponse:
    """
    Compute final account set from rules (union of includes minus union of excludes),
    then fetch account details and aggregated tags.
    """
    conn = _get_connection(settings)
    try:
        include_logins: Set[str] = set()
        exclude_logins: Set[str] = set()

        for rule in req.rules:
            logins = _logins_from_rule(conn, rule)
            if getattr(rule, "include", True):
                _union_update(include_logins, logins)
            else:
                _union_update(exclude_logins, logins)

        final_logins = include_logins.difference(exclude_logins)
        items = _assemble_items(conn, final_logins)
        return AudiencePreviewResponse(total=len(items), items=items)
    finally:
        try:
            conn.close()
        except Exception:
            pass


