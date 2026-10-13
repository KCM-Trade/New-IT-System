"""xlsx export of the fund risk-control ledger (spec §4 ``GET /export``).

One sheet, Chinese headers, every filtered row. Values are the stored order
snapshot — the replica is not queried per row, so the client name (never
persisted) is not part of the file. Order times are shown in Beijing time.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_BEIJING = timezone(timedelta(hours=8))

DIRECTION_LABELS = {"deposit": "入金", "withdrawal": "出金"}
FROZEN_SCOPE_LABELS = {
    "none": "未封", "one_card": "其中一张", "all_cards": "名下所有",
    "non_counter": "非柜", "unknown": "尚不知",
}
AUTHORITY_LABELS = {
    "bank": "银行", "police_antifraud": "反诈公安", "alipay": "支付宝", "unknown": "尚不知",
}
TRANSFER_LABELS = {"yes": "是", "no": "否", "unknown": "尚不知"}
CLIENT_SOURCE_LABELS = {"online": "线上", "offline": "线下"}
SOURCE_LABELS = {"manual": "手工登记", "import": "历史导入"}

_ENUM_LABELS: dict[str, dict[str, str]] = {
    "direction": DIRECTION_LABELS,
    "frozen_scope": FROZEN_SCOPE_LABELS,
    "authority": AUTHORITY_LABELS,
    "transfer_succeeded": TRANSFER_LABELS,
    "client_source": CLIENT_SOURCE_LABELS,
    "source": SOURCE_LABELS,
}

# (row key, header). "venue_effective" is venue_override falling back to venue.
COLUMNS: list[tuple[str, str]] = [
    ("id", "编号"),
    ("incident_group_id", "事件组"),
    ("reported_date", "登记日期"),
    ("transaction_id", "订单号"),
    ("client_id", "客户ID"),
    ("login_sid", "交易账户"),
    ("venue_effective", "场地"),
    ("direction", "出入金"),
    ("txn_type", "订单类型"),
    ("channel", "渠道"),
    ("psp_name", "PSP"),
    ("amount_cny", "金额（CNY）"),
    ("txn_status", "订单状态"),
    ("txn_created_at", "订单创建时间（北京时间）"),
    ("txn_processed_at", "订单处理时间（北京时间）"),
    ("is_test_account", "测试账户"),
    ("transfer_succeeded", "是否成功转账"),
    ("frozen_scope", "被封银行卡资料"),
    ("freeze_days", "封卡时长（天）"),
    ("freeze_note", "封卡备注"),
    ("authority", "封卡单位"),
    ("client_source", "客户来源"),
    ("client_bank_province", "客户银行卡省份"),
    ("client_bank_city", "客户银行卡城市"),
    ("trader_bank_province", "币商银行卡省份"),
    ("trader_bank_city", "币商银行卡城市"),
    ("id_province", "身份证省份"),
    ("id_city", "身份证城市"),
    ("ip_region", "IP 地区"),
    ("pwa_domain", "PWA 域名"),
    ("account_type", "账户类型"),
    ("event_text", "事件"),
    ("result_text", "查询结果"),
    ("source", "来源"),
    ("created_by", "登记人"),
    ("created_at", "登记时间（北京时间）"),
    ("updated_by", "最后修改人"),
    ("updated_at", "最后修改时间（北京时间）"),
]


def _cell(key: str, row: Mapping[str, Any]) -> Any:
    if key == "venue_effective":
        return row.get("venue_override") or row.get("venue")
    value = row.get(key)
    if value is None:
        return None
    if key in _ENUM_LABELS:
        return _ENUM_LABELS[key].get(value, value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_BEIJING)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_xlsx(rows: Iterable[Mapping[str, Any]]) -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("台账")
    ws.freeze_panes = "A2"
    header = []
    for _, label in COLUMNS:
        cell = WriteOnlyCell(ws, value=label)
        cell.font = Font(bold=True)
        header.append(cell)
    ws.append(header)
    for row in rows:
        ws.append([_cell(key, row) for key, _ in COLUMNS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(today: date) -> str:
    return f"出入金风控台账_{today.isoformat()}.xlsx"


def content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip("_") or "export.xlsx"
    if not ascii_name.lower().endswith(".xlsx") or ascii_name.startswith("."):
        ascii_name = "fund-risk-ledger.xlsx"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'
