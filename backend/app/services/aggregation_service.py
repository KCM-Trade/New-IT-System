from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pymysql

from ..core.config import Settings


def aggregate_to_json(settings: Settings, symbol: str, start: str, end: str, basis: str = "open") -> dict:
	"""
	Reuses existing logic: read MySQL -> write parquet -> DuckDB aggregate -> write JSON (JSONL).
	Supports basis="open" (by OPEN_TIME) or basis="close" (by CLOSE_TIME).
	"""

	# DB connection
	conn = pymysql.connect(
		host=settings.DB_HOST,
		user=settings.DB_USER,
		password=settings.DB_PASSWORD,
		database=settings.DB_NAME,
		port=int(settings.DB_PORT),
		charset=settings.DB_CHARSET,
	)

	if basis not in ("open", "close"):
		return {"ok": False, "error": f"invalid basis: {basis}"}

	# Choose time field and SQL filter by basis
	time_field = "OPEN_TIME" if basis == "open" else "CLOSE_TIME"
	if basis == "open":
		sql = (
			"SELECT ticket, login, symbol, cmd, volume, OPEN_TIME, OPEN_PRICE, "
			"CLOSE_TIME, CLOSE_PRICE, swaps, profit "
			"FROM mt4_live.mt4_trades "
			"WHERE symbol = %s "
			"  AND OPEN_TIME BETWEEN %s AND %s "
			"  AND CLOSE_TIME != '1970-01-01 00:00:00' "
			"  AND login NOT IN ("
			"    SELECT LOGIN FROM mt4_live.mt4_users "
			"    WHERE ((`GROUP` LIKE %s) OR (name LIKE %s)) "
			"      AND ((`GROUP` LIKE %s) OR (`GROUP` LIKE %s))"
			")"
		)
	else:
		sql = (
			"SELECT ticket, login, symbol, cmd, volume, OPEN_TIME, OPEN_PRICE, "
			"CLOSE_TIME, CLOSE_PRICE, swaps, profit "
			"FROM mt4_live.mt4_trades "
			"WHERE symbol = %s "
			"  AND CLOSE_TIME BETWEEN %s AND %s "
			"  AND login NOT IN ("
			"    SELECT LOGIN FROM mt4_live.mt4_users "
			"    WHERE ((`GROUP` LIKE %s) OR (name LIKE %s)) "
			"      AND ((`GROUP` LIKE %s) OR (`GROUP` LIKE %s))"
			")"
		)

	try:
		df = pd.read_sql(
			sql,
			conn,
			params=[
				symbol,
				start,
				end,
				"%test%",
				"%test%",
				"KCM%",
				"testKCM%",
			],
		)
	except Exception as exc:
		conn.close()
		return {"ok": False, "error": str(exc)}
	finally:
		try:
			conn.close()
		except Exception:
			pass

	# Ensure directories
	parquet_dir: Path = settings.parquet_dir
	parquet_dir.mkdir(parents=True, exist_ok=True)
	parquet_path = parquet_dir / ("orders.parquet" if basis == "open" else "orders_close.parquet")

	df.to_parquet(str(parquet_path), engine="pyarrow", index=False)

	public_dir: Path = settings.public_export_dir
	public_dir.mkdir(parents=True, exist_ok=True)
	json_path = public_dir / ("profit_xauusd_hourly.json" if basis == "open" else "profit_xauusd_hourly_close.json")

	con = duckdb.connect()

	# Build hourly aggregation for current window
	# Note: We'll merge with existing JSON (if any) by (date,hour), with new results overriding old.
	tmp_json_path = json_path.with_suffix(".json.tmp") if json_path.suffix != "" else Path(str(json_path) + ".tmp")

	try:
		if json_path.exists():
			# Existing JSON present: perform override-merge using DuckDB
			con.execute(
				f"""
				CREATE OR REPLACE TEMP VIEW new_agg AS
				SELECT
				  CAST({time_field} AS DATE)      AS date,
				  EXTRACT(HOUR FROM {time_field}) AS hour,
				  SUM(profit)                  AS profit
				FROM read_parquet('{str(parquet_path)}')
				GROUP BY 1,2;

				CREATE OR REPLACE TEMP VIEW old_agg AS
				SELECT
				  CAST(date AS DATE)     AS date,
				  CAST(hour AS INTEGER)  AS hour,
				  CAST(profit AS DOUBLE) AS profit
				FROM read_json_auto('{str(json_path)}');

				CREATE OR REPLACE TEMP VIEW merged AS
				SELECT o.* FROM old_agg o
				WHERE NOT EXISTS (
				  SELECT 1 FROM new_agg n
				  WHERE o.date = n.date AND o.hour = n.hour
				)
				UNION ALL
				SELECT * FROM new_agg;

				COPY (
				  SELECT * FROM merged ORDER BY date, hour
				) TO '{str(tmp_json_path)}' (FORMAT JSON);
				"""
			)
		else:
			# No existing JSON: produce fresh output
			con.execute(
				f"""
				COPY (
				  SELECT
				    CAST({time_field} AS DATE)      AS date,
				    EXTRACT(HOUR FROM {time_field}) AS hour,
				    SUM(profit)                  AS profit
				  FROM read_parquet('{str(parquet_path)}')
				  GROUP BY 1,2
				  ORDER BY 1,2
				) TO '{str(tmp_json_path)}' (FORMAT JSON);
				"""
			)
	finally:
		con.close()

	# Atomic replace to avoid partial writes
	os.replace(str(tmp_json_path), str(json_path))

	return {"ok": True, "json": str(json_path), "rows": int(df.shape[0])}




def _read_latest_datetime_from_json(json_path: Path) -> datetime | None:
	"""
	Read existing NDJSON (DuckDB COPY FORMAT JSON) and get the latest (date,hour) as aware datetime in UTC+03:00.
	Return None if file does not exist or empty.
	"""
	if not json_path.exists():
		return None

	con = duckdb.connect()
	try:
		# Use DuckDB to efficiently read and sort; cast types explicitly
		res = con.execute(
			f"""
			SELECT
			  CAST(date AS DATE) AS d,
			  CAST(hour AS INTEGER) AS h
			FROM read_json_auto('{str(json_path)}')
			ORDER BY d DESC, h DESC
			LIMIT 1
			"""
		).fetchone()
		if not res:
			return None
		latest_date, latest_hour = res[0], int(res[1])
		# Build timezone-aware datetime at UTC+3
		tz3 = ZoneInfo("Etc/GMT-3")
		# Note: Etc/GMT-3 means UTC+3
		return datetime(latest_date.year, latest_date.month, latest_date.day, latest_hour, 0, 0, tzinfo=tz3)
	finally:
		con.close()


def _now_in_utc_plus_3() -> datetime:
	"""Compute current time in UTC+03:00 from system time (which may be UTC+8)."""
	# Always base on UTC to avoid local tz ambiguity, then convert to +03:00
	utc_now = datetime.now(timezone.utc)
	return utc_now.astimezone(ZoneInfo("Etc/GMT-3"))


def _format_dt(dt: datetime) -> str:
	"""Format datetime as 'YYYY-MM-DD HH:MM:SS' in its own timezone."""
	return dt.strftime("%Y-%m-%d %H:%M:%S")


def refresh_aggregations(settings: Settings, symbol: str = "XAUUSD") -> dict:
	"""
	Determine start (from existing JSON, UTC+3 last hour + 1h) and end (now at UTC+3),
	then update both 'open' and 'close' aggregations.
	"""
	public_dir: Path = settings.public_export_dir
	open_json = public_dir / "profit_xauusd_hourly.json"
	close_json = public_dir / "profit_xauusd_hourly_close.json"

	latest_open = _read_latest_datetime_from_json(open_json)
	latest_close = _read_latest_datetime_from_json(close_json)

	# If no existing data, start from 1970-01-01 at UTC+3 to allow full backfill
	tz3 = ZoneInfo("Etc/GMT-3")
	default_start = datetime(1970, 1, 1, 0, 0, 0, tzinfo=tz3)

	start_open = (latest_open + timedelta(hours=1)) if latest_open else default_start
	start_close = (latest_close + timedelta(hours=1)) if latest_close else default_start
	end_dt = _now_in_utc_plus_3()

	open_result = aggregate_to_json(
		settings,
		symbol,
		_format_dt(start_open),
		_format_dt(end_dt),
		basis="open",
	)
	close_result = aggregate_to_json(
		settings,
		symbol,
		_format_dt(start_close),
		_format_dt(end_dt),
		basis="close",
	)

	return {
		"ok": bool(open_result.get("ok") and close_result.get("ok")),
		"open": {
			"start": _format_dt(start_open),
			"end": _format_dt(end_dt),
			"result": open_result,
		},
		"close": {
			"start": _format_dt(start_close),
			"end": _format_dt(end_dt),
			"result": close_result,
		},
	}
