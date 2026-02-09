# Dashboard (Home Page)

> Project document for the Dashboard feature — the landing page of KCM Analytics System.

## 1. Overview

The Dashboard serves as the **home page** of the KCM Analytics System. When users visit the root URL (`/`) or click the sidebar logo, they land on this page. It provides a high-level summary of key business metrics and quick navigation to core modules.

**Current Status**: Placeholder (skeleton page created, awaiting data integration)

### Route & Entry Points

| Entry Point | Behavior |
|-------------|----------|
| URL `/` | Renders Dashboard (index route) |
| URL `/home` | Alias route, same page |
| Sidebar "Dashboard" item | Direct link to `/` |
| Sidebar logo click | Navigates to `/` |

---

## 2. Development Plan

### Phase 1 — Frontend Skeleton (DONE)

- [x] Create `Home.tsx` placeholder component
- [x] Configure route (`/` and `/home`) in `App.tsx`
- [x] Add sidebar "Dashboard" entry with `IconHome`
- [x] Make sidebar logo clickable (links to `/`)
- [x] Add i18n translations (`nav.home`, `pages.home`, `pages.homeWelcome`)
- [x] Update header title mapping for `/` route

### Phase 2 — Define Dashboard Metrics

Determine which KPIs / summary data to display. Potential candidates:

| Metric | Source | API (existing?) |
|--------|--------|-----------------|
| Total open positions (by server) | MySQL (MT4/MT5) | `GET /api/v1/open-positions/summary` |
| Today's deposit / withdrawal | ClickHouse | `POST /api/v1/ib-data/region-query` |
| Active clients count | ClickHouse | New API needed |
| Top symbols by volume | ClickHouse | New API needed |
| System health / server status | Backend health check | `GET /api/v1/health` |

> **Action**: Confirm with stakeholders which metrics are needed.

### Phase 3 — Backend API

1. Create `backend/app/api/v1/routes/dashboard.py`
2. Create `backend/app/schemas/dashboard.py` — response models
3. Create `backend/app/services/dashboard_service.py` — query logic
4. Register in `backend/app/api/v1/routers.py`

Suggested endpoint:

```
GET /api/v1/dashboard/summary
Response:
{
  "open_positions": { "mt4": 1234, "mt5": 5678, "mt4_live2": 910 },
  "today_deposit": 123456.78,
  "today_withdrawal": 45678.90,
  "active_clients_7d": 2345,
  "top_symbols": [
    { "symbol": "XAUUSD", "volume": 12345.6 },
    ...
  ],
  "generated_at": "2026-02-09T12:00:00Z"
}
```

### Phase 4 — Frontend Integration

1. Add API call in `Home.tsx` (use `fetch` + `useEffect`, consistent with other pages)
2. Build UI cards for each metric (shadcn/ui `Card` component)
3. Optional: Add a chart (e.g., 7-day deposit trend using Recharts)
4. Handle loading / error states

### Phase 5 — Testing & Polish

- Verify data accuracy against existing pages
- Test dark/light theme
- Test responsive layout (mobile sidebar collapsed)
- Performance: Ensure dashboard loads within 2s

---

## 3. Technical Architecture

```
Frontend (Home.tsx)
  │
  │  GET /api/v1/dashboard/summary
  ▼
Backend (dashboard.py route)
  │
  │  Calls dashboard_service.py
  ▼
dashboard_service.py
  ├── ClickHouse: deposit/withdrawal, client stats, top symbols
  ├── MySQL (MT4/MT5): open positions (reuse open_positions_service)
  └── Redis: cache result (TTL 5min recommended)
```

---

## 4. File Inventory

### Existing Files (Phase 1 — already created)

| File | Purpose |
|------|---------|
| `frontend/src/pages/Home.tsx` | Dashboard page component |
| `frontend/src/App.tsx` | Route: `/` → HomePage, `/home` → HomePage |
| `frontend/src/components/app-sidebar.tsx` | Sidebar "Dashboard" entry + logo link |
| `frontend/src/components/site-header.tsx` | Header title mapping for `/` |
| `frontend/src/i18n/locales/zh-CN.ts` | Chinese translations |
| `frontend/src/i18n/locales/en-US.ts` | English translations |

### Files to Create (Phase 3-4)

| File | Purpose |
|------|---------|
| `backend/app/api/v1/routes/dashboard.py` | API route handler |
| `backend/app/schemas/dashboard.py` | Pydantic response models |
| `backend/app/services/dashboard_service.py` | Business logic & queries |

---

## 5. Recommended Development Workflow

For a full-stack developer working on this feature:

```
1. Define API contract (request/response shape)
      ↓
2. Backend: schema → service → route → register
      ↓
3. Backend: test with Swagger UI (localhost:8001/docs)
      ↓
4. Frontend: build UI with mock/hardcoded data first
      ↓
5. Frontend: integrate real API calls
      ↓
6. End-to-end testing
      ↓
7. Polish: loading states, error handling, responsive
```

**Why backend first?**
- The API contract defines what data is available
- You can test backend independently via Swagger UI
- Frontend can be developed with confidence once API is confirmed working

---

## 6. Notes

- Dashboard should reuse existing services where possible (e.g., `open_positions_service`)
- Consider Redis caching with short TTL (5min) since dashboard is frequently visited
- Keep the page lightweight — avoid heavy queries that block initial load
- All comments in English, UI text via i18n
