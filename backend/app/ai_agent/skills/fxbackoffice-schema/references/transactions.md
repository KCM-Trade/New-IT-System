# transactions — payment ledger (one row per payment)

~1.2M rows.

## Columns you may use
| Column | Meaning |
|---|---|
| id | payment id |
| fromUserId | client id |
| fromLoginSid | account (or wallet) the payment is booked to; its prefix is the sid (2 = wallet) |
| type | `deposit`, `withdrawal`, `ib withdrawal` (IB commission cash-out); other types exist (e.g. `transfer in`, `ib transfer to account`, bonus/credit types) |
| status | only `approved` counts |
| isFee | 1 = fee row; exclude for deposit figures |
| processedAmount, processedCurrency | amount actually processed; `processedCurrency = 'CEN'` → ÷100 |
| createdAt, processedAt | request time / processing time |

## Notes
- For a client's net deposit use `get_client_overview` (certified, two legs). Do not rebuild it here.
- Indexed: `(fromUserId, type)`, `(status, fromUserId)`, `(type, status, processedAt)`, `createdAt`, `fromLoginSid`.
- Deposits via wallet → `transfer in` are not `deposit` rows; a deposit-only sum can miss them.
