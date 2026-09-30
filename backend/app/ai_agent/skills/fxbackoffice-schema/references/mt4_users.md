# mt4_users — one row per MT account (current snapshot)

~192K rows. Unique key `loginSid`.

## Columns you may use
| Column | Meaning |
|---|---|
| loginSid | `{sid}-{LOGIN}` (unique) — join key to `mt4_trades` |
| sid, LOGIN | server and account number |
| userId | CRM client id → `users.id` |
| `GROUP` | MT group (reserved word — write `` mu.`GROUP` ``). Demo filter: `LOWER(mu.`GROUP`) NOT LIKE '%demo%' AND … NOT LIKE '%test%'`. IB commission wallets have groups starting `IB-WALLET` (sid 2); a wallet row's `userId` is the IB's own client id |
| CURRENCY | `'CEN'` = cent account: BALANCE/EQUITY/CREDIT and trade money are in cents (÷100) |
| LEVERAGE | account leverage |
| BALANCE, EQUITY, CREDIT | current values in account currency. Floating P/L = EQUITY − BALANCE − CREDIT |
| MARGIN_LEVEL | equity ÷ used margin × 100 (percent), whole account, MT4 and MT5; **0 = no open positions** |
| REGDATE | account registration time (MT server clock) |
| AGENT_ACCOUNT | the IB's agent account number on the MT server |
| excludeFromReports | 1 = excluded from CRM reports |
| isDeleted | 1 = account removed |

Personal columns (name, email, phone, address, …) are refused; `SELECT *` on this table is refused.

## Notes
- Values are a synced snapshot (normally within about a minute of the MT server), not tick-real-time,
  and there is no history — you cannot get last week's margin level or equity here.
- Indexed: `loginSid`, `userId`, `(CURRENCY, userId)`, `(GROUP, sid)`.
- The certified tools also drop accounts whose NAME contains demo/test; run_sql cannot (NAME refused).
