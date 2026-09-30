# SOURCES — ib-and-rebate (not shown to the model)

## Port notes (OPT-0069, 2026-09-30)
This file is NEVER shown to the model (harness resource_filter; pinned by tests/test_ai_agent_skills.py).
File:line references below point at the pre-port tree and may have drifted.
- Cold review 2026-09-30: run_sql table/column recipes (stats_ib_commissions, partnerId, IB-WALLET) moved to fxbackoffice-schema "IB questions" (+ P7, guard ok, EXPLAIN ref on userId); retired rule-id band and the get_window_scan row removed (all-audience skill).
- Level tag-id TODO removed (skill says tag ids are not documented); partnerId TODO softened to "one level only; use the IB Tree page for the chain".
- Wallet row userId = IB client id: checked 2026-09-30 (see fxbackoffice-schema SOURCES).


`prompt.py` = `backend/app/ai_agent/prompt.py`; `BC` = `/home/kcm-trade/.claude/skills/kcm-business-concepts/SKILL.md`
(user-level skill, not in git); `SP` = `/home/kcm-trade/.claude/skills/kcm-sql-patterns/SKILL.md`;
`RA` = `.cursor/skills/rebate-arbitrage/SKILL.md`.

| # | Claim | Source |
|---|---|---|
| 1 | Client = users.id, leaf of tree | docs/ai-context/CONTEXT.md:10-12; BC "IB Tree Hierarchy" |
| 2 | IB = users.isIb = 1 | .cursor/skills/database-context/fxbackoffice/tables/users.md:42; prompt.py:161 |
| 3 | MIB = first external agent level; 二/三/四级 sub-agents | BC "User Roles" table + "Key Rules" |
| 4 | Staff carry a category-1 "Staff Code" tag; chain shows sales code not name | docs/features/ib-tree-query.md:21-24 |
| 5 | Venue = tag category 6 | BC "Venue / Team"; docs/features/ib-report.md §3.2 ("组别定义 … categoryId = 6") |
| 6 | Employee broader than staff; excluded from figures | BC "User Roles"; prompt.py:65 |
| 7 | One client belongs to exactly one IB tree | BC "Key Rules" |
| 8 | partnerId = introducing IB | prompt.py:162 (only source) |
| 9 | stats_ib_commissions = daily rebate per IB per referred client; ibId/refId → users.id; commission = paid | prompt.py:167-168; .cursor/skills/database-context/fxbackoffice/tables/stats_ib_commissions.md:11 |
| 10 | Full-chain rebate = every IB level; = rebate_all leg; company's real rebate cost | RA:69-73, :80-88; tools/client_overview.py:297-298 |
| 11 | CRM single-IB report shows one level; mismatch expected | RA:69-73; KCM_Risk_Control_System/docs/architecture.md:868 |
| 12 | Rebate amounts already USD | RA:66-68; KCM architecture.md:861 |
| 13 | Rebate-table lots repeat per level; never use as volume | docs/analysis/deposit-tier-lots-analysis.md:77-78; KCM architecture.md:867 |
| 14 | rebate_all lifetime, data starts 2021-08-02 | RA:113-114; prompt.py:56 |
| 15 | Commission paid into IB wallet on sid 2 (IB-WALLET group) | RA:30-31; docs/features/ib-data.md (Metrics table, IB Wallet Balance row) |
| 16 | ib withdrawal = commission cash-out, not trading money; two-leg net deposit; IB-cum-trader distortion | prompt.py:66-71; RA:60-62; docs/features/client-return-rate.md:75 |
| 17 | IB moving commission into trading account → slight net-gain over-statement | RA:126-127; deposit-tier-lots-analysis.md:53 ("ib transfer to account") |
| 18 | Net gain includes full-chain rebate | prompt.py:74-75 |
| 19 | Rules 121-130 retired, no data | prompt.py:205 |
| 20 | Rebate farming needs the rebate leg | prompt.py:232 |
| 21 | get_client_overview returns rebate_all, ib_withdrawal, net_gain, crm_tags | tools/client_overview.py:174-194 |
| 22 | get_window_scan rows carry lifetime net_deposit (trading), total_rebate, net_gain | prompt.py:266-267 |
| 23 | run_sql tables available (stats_ib_commissions, users, user_tags, tags, mt4_users); ib_tree not whitelisted | backend/app/ai_agent/tools/run_sql.py:120-122 |
| 24 | IB Tree page /cs/ib-tree gives the full chain | docs/features/ib-tree-query.md:1-11 |
| 25 | IB deposits pages /cs/ib-deposits and /warehouse/ib-data; their Net Deposit includes IB withdrawal | docs/features/ib-data.md:7-22, :55-59 |
| 26 | Batch get_client_overview ≤ 50; trivial sums allowed if stated | prompt.py:81-83, :34-35 |
| 27 | Signals-not-verdicts wording | prompt.py:51-54 |

## Unsourced / needs business confirmation
- TODO(business): current tag ids for IB / 二级 / 三级 / 四级代理 (BC says 2 / 176 / 177 / 31096). BC is an older user-level note; its "Client = cid=0" row is wrong for today (cid is the CN/Global company flag, and BC's SQL filters `u.cid = 0`, i.e. CN only). The tag ids were therefore left out of the rules and only quoted as "an older internal note".
- TODO(business): does `users.partnerId` always equal the direct IB (level 0 in `ib_tree_with_self` per ib-tree-query.md:19)? Only prompt.py asserts "introducing IB".
- TODO(business/data): IB wallet balance via run_sql assumes the wallet row's `mt4_users.userId` is the IB's own client id. Plausible (ib-data.md reads wallet balance per IB id) but the join key is not documented. Verify with EXPLAIN / a spot check before relying on it.
- "One client belongs to exactly one IB tree" is from BC only.
- MIB/level naming (一级/二级…) is from BC only; ib-tree-query.md shows chains but does not name levels.

## Notes for implementer
- No certified tool answers any IB-centric question (IB → clients, IB income, IB chain). The biggest gap vs. real use is the chain lookup, which already exists as `GET /api/v1/ib-tree/{client_id}` (cs module, data-scope masked, ib-tree-query.md §4/§4b). A thin certified `get_ib_chain(client_id)` tool wrapping `ib_tree_service` would close it without whitelisting `ib_tree` for run_sql. Out of scope for this draft; flagged only.
- `rebate_all` is the rebate paid ON the client's trading (KCM T6 maps `fromLoginSid` owner → user_id, KCM architecture.md:859), not the commission the client earns AS an IB. The skill states this; prompt.py's "full-chain rebate (every IB level)" is consistent but does not say which side — consider adding "paid on this client's trading" to the prompt definition.
- Extra forbidden words (刷单者, rebate abuser) are additions, not contradictions, of prompt.py rule 5.
