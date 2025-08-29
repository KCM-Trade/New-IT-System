from __future__ import annotations

import os
from pathlib import Path

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


