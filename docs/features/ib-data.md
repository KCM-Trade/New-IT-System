# IB Data - Deposit/Withdrawal Query

> 出入金查询模块，支持按 IB ID 或按地区 (Company) 查询出入金数据。

## Overview

This page (`/warehouse/ib-data`) provides two query modules:

1. **IB 出入金查询** - Query deposit/withdrawal by specific IB IDs
2. **Company 出入金查询** - Query deposit/withdrawal aggregated by region (CN/Global)

## Features

### IB Deposit/Withdrawal Query

| Feature | Description |
|---------|-------------|
| Input | Comma-separated IB IDs (e.g., `107779,129860`) |
| Date Range | Week, Month, Custom |
| Output | Per-IB breakdown with totals |
| Data Source | MySQL `fxbackoffice.transactions` + `fxbackoffice.ib_tree_with_self` |

**Metrics**:
- Deposit (USD)
- Total Withdrawal (USD)
- IB Withdrawal (USD)
- IB Wallet Balance (USD)
- Net Deposit (USD)

### Company Deposit/Withdrawal Query

| Feature | Description |
|---------|-------------|
| Input | Date range only (no IB ID needed) |
| Date Range | Past week, This month, Last month, Custom |
| Output | Aggregated by region (CN/Global) |
| Data Source | MySQL `fxbackoffice.transactions` JOIN `fxbackoffice.users` |

**Region Logic**:
- `cid = 0` → CN (China)
- `cid = 1` → Global

**Metrics**:
- Deposit (USD)
- Withdrawal (USD)
- IB Withdrawal (USD)
- Total Withdrawal (USD)
- Net Deposit (USD)

## API Endpoints

### 1. Query by IB IDs

```
POST /api/v1/ib-data/query
```

**Request**:
```json
{
  "ib_ids": ["107779", "129860"],
  "start": "2026-01-01 00:00:00",
  "end": "2026-01-31 23:59:59"
}
```

**Response**:
```json
{
  "rows": [
    {
      "ibid": "107779",
      "deposit_usd": 12345.67,
      "total_withdrawal_usd": -5678.90,
      "ib_withdrawal_usd": -1234.56,
      "ib_wallet_balance": 500.00,
      "net_deposit_usd": 6166.77
    }
  ],
  "totals": { ... },
  "last_query_time": "2026-01-15T10:30:00Z"
}
```

### 2. Query by Region (Company)

```
POST /api/v1/ib-data/region-query
```

**Request**:
```json
{
  "start": "2026-01-01 00:00:00",
  "end": "2026-02-01 00:00:00"
}
```

**Response**:
```json
{
  "regions": [
    {
      "cid": 0,
      "company_name": "CN",
      "deposit": { "tx_count": 5000, "amount_usd": 1234567.00 },
      "withdrawal": { "tx_count": 3000, "amount_usd": -456789.00 },
      "ib_withdrawal": { "tx_count": 200, "amount_usd": -12345.00 },
      "total_deposit_usd": 1234567.00,
      "total_withdrawal_usd": 469134.00,
      "net_deposit_usd": 765433.00
    },
    {
      "cid": 1,
      "company_name": "Global",
      ...
    }
  ],
  "query_time_ms": 154.32
}
```

### 3. Get Last Query Time

```
GET /api/v1/ib-data/last-run
```

**Response**:
```json
{
  "last_query_time": "2026-01-15T10:30:00Z"
}
```

## SQL Logic

### IB Query (ib_data_service.py)

Uses CTE to traverse IB tree and aggregate transactions:

```sql
WITH tx_referrals AS (
    SELECT it.referralId
    FROM fxbackoffice.ib_tree_with_self it
    WHERE it.ibid = ?
),
tx_totals AS (
    SELECT
        SUM(CASE WHEN type = 'deposit' THEN amount ELSE 0 END) AS deposit_usd,
        SUM(CASE WHEN type = 'withdrawal' THEN amount ELSE 0 END) AS withdrawal_usd,
        SUM(CASE WHEN type = 'ib withdrawal' THEN amount ELSE 0 END) AS ib_withdrawal_usd
    FROM fxbackoffice.transactions
    WHERE status = 'approved'
      AND fromUserId IN (SELECT referralId FROM tx_referrals)
      AND processedAt BETWEEN ? AND ?
)
...
```

### Region Query (Company)

Uses JOIN + GROUP BY for efficient aggregation:

```sql
SELECT 
    u.cid,
    t.type,
    COUNT(*) AS tx_count,
    SUM(CASE 
        WHEN UPPER(t.processedCurrency) = 'CEN' THEN t.processedAmount / 100.0 
        ELSE t.processedAmount 
    END) AS amount_usd
FROM fxbackoffice.transactions t
INNER JOIN fxbackoffice.users u ON t.fromUserId = u.id
WHERE t.status = 'approved'
  AND t.type IN ('deposit', 'withdrawal', 'ib withdrawal')
  AND t.processedAt >= ? AND t.processedAt < ?
GROUP BY u.cid, t.type
```

## UI Design

### Color Scheme

| Field | Color | Meaning |
|-------|-------|---------|
| Deposit | Green (`text-emerald-600`) | Positive (money in) |
| Withdrawal | Red (`text-red-600`) | Negative (money out) |
| IB Withdrawal | Red (`text-red-600`) | Negative (money out) |
| Total Withdrawal | Red (`text-red-600`) | Negative (money out) |
| IB Wallet Balance | Red (`text-red-600`) | Liability |
| Net Deposit | Green/Red | Dynamic based on +/- |

### Visual Distinction

| Section | Background Color |
|---------|------------------|
| IB 出入金查询 | Blue (`bg-blue-50/50`) |
| Company 出入金查询 | Green (`bg-emerald-50/50`) |

## File Locations

| Type | Path |
|------|------|
| Frontend Page | `frontend/src/pages/IBData.tsx` |
| Backend Route | `backend/app/api/v1/routes/ib_data.py` |
| Backend Schema | `backend/app/schemas/ib_data.py` |
| Backend Service | `backend/app/services/ib_data_service.py` |

## Changelog

| Date | Change |
|------|--------|
| 2026-02-02 | Added Company 出入金查询 (region-based query) |
| 2026-02-02 | Renamed page title from "IB 出入金查询" to "出入金查询" |
| 2026-02-02 | Added summary row in tables with highlighted background |
| 2026-02-02 | Unified color scheme (green for deposits, red for withdrawals) |
