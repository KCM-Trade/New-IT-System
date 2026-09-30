# mt4_trades — every MT order (open and closed), all servers

~48M rows. One row per order. Primary key `ticketSid` = `{sid}-{TICKET}`.

## Columns you may use
| Column | Meaning |
|---|---|
| ticketSid | primary key, `{sid}-{TICKET}` |
| loginSid | account, `{sid}-{LOGIN}` — join to `mt4_users.loginSid` (indexed) |
| sid | server: 1 MT4 Live, 5 MT5, 6 MT4 Live 2 (4 = retired, has leftover rows) |
| TICKET, LOGIN | order number and account number on that server (text) |
| SYMBOL | instrument, e.g. `XAUUSD`, `XAUUSD.c`, `XAUUSD.cent` |
| CMD | 0 buy, 1 sell, 2-5 pending orders, 6 balance operation (not a trade) |
| VOLUME / lots | `lots` = VOLUME / 100 (stored, use it). Cent symbols: ÷100 again |
| OPEN_TIME, CLOSE_TIME | MT server wall clock; **not indexed** |
| openDate, closeDate | MT server day of open / close; **indexed** |
| OPEN_PRICE, CLOSE_PRICE, SL, TP | prices |
| PROFIT, SWAPS, COMMISSION | money in account currency (CEN = cents) |
| totalProfit | PROFIT + SWAPS + COMMISSION for CMD 0/1/6; on an open order = its floating P/L |
| isDeleted | 1 = row no longer exists on the MT server; exclude with `COALESCE(isDeleted,0) = 0` |

There is **no client id** on this table — join through `mt4_users`.

## Indexes (only these make a query fast)
- `closeDate` · `openDate` · `loginSid` · `(loginSid, closeDate)` · `(SYMBOL, sid, closeDate)`.
- Open (still-held) orders: `closeDate = '1970-01-01'`. Never `CLOSE_TIME = '1970-01-01'` (refused).

## Traps
- sid 5 **closed** rows store the exit side in CMD (buy position closed → CMD 1). Open rows are the
  position side on every server. Normalise closed sid 5 with `IF(t.sid = 5, 1 - t.CMD, t.CMD)`.
- sid 5 order numbers in this table are NOT the MT5 terminal's Position ID — a Position ID copied from
  MT5 will not be found here.
- An open-positions query without `sid IN (1,5,6)` picks up thousands of dead sid 4 rows.
- Adding an `openDate` range to an open-positions question drops every position opened earlier.
- `OR` between date conditions disables the index; self-joins do not finish in 15 s.
- Cent: money ÷100 when the account is CEN or the symbol ends in `.cent`/`.kcmc`; lots ÷100 only for
  those symbols (in the live data CEN accounts trade only those symbols). `XAUUSD.c` is not cent.
