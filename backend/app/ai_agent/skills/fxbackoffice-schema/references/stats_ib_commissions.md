# stats_ib_commissions — daily rebate per IB per referred client

~4.5M rows. Primary key `(date, ibId, refId, currency)`.

## Columns you may use
| Column | Meaning |
|---|---|
| date | day of the commission |
| ibId | the IB who earns (→ users.id) |
| refId | the client whose trading generated it (→ users.id) |
| currency | currency of the amount (if `'CEN'`, ÷100) |
| commission | commission actually paid |
| lots | lots attributed to this IB row |

## Notes
- Summing `commission` over ALL `ibId` for one `refId` = the full-chain rebate paid on that client
  (every IB level). One IB's own figure is only its rows. A CRM single-IB commission report shows one
  level only, so it will be smaller than the full-chain figure — expected, not an error.
- **Do not add up `lots` here** — a client's lots appear once per IB level that earns on them, so the sum is several
  times the real lots (measured 2026-09-30 on this table: a client-day has ~3.8 IB rows on average, and almost all
  of them repeat the same lots). Take lots from `mt4_trades`.
- Always filter `date`. Indexed: `(date, ibId)`, `(ibId, date, currency)`, primary key starts with `date`.
  `refId` alone has no leading index — combine it with a date range.
