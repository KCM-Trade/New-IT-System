# users — one row per CRM client

~68K rows. Primary key `id` = the client id everywhere else.

## Columns you may use
| Column | Meaning |
|---|---|
| id | client id (= mt4_users.userId = transactions.fromUserId = user_tags.userId) |
| cid | company / region flag: 0 = CN, 1 = Global. **Not a client id — never join on it** |
| isEmployee | exclude employees with `COALESCE(isEmployee, 0) = 0` |
| isIb | 1 = the client is an IB |
| isVerified, isLead | KYC verified; still a lead (not a converted client) |
| country | 2-letter country code |
| createdAt | CRM registration time |
| firstDepositDate | first deposit time |
| partnerId | the client's introducing IB (→ users.id) |

Personal columns (name, email, phone, address, IP, birthday …) are refused; `SELECT *` is refused.

Indexed: `id`, `cid`, `country`, `createdAt`, `partnerId`, `isIb`, `(isLead, cid)`.
