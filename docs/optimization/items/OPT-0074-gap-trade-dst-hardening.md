---
id: OPT-0074
title: Gap Trade DST hardening —— OPT-0072 冷审 #8–#10
status: ready
priority: P3
area: backend
effort: S
created: 2026-10-06
related: [[OPT-0072]] [[OPT-0062]]
---

## 问题

OPT-0072（Gap Trade 扫描锚 MT 时钟，2026-10-06 合并）冷审中不阻塞上线的 3 条，用户拍板另立本单：

8. **`_MTServerTZ` 未实现 `fromutc`**（`app/services/rule_intraday_return_service.py`）。默认 `fromutc` 在切换周日不一致（实测 UTC 2026-10-31 21:30 → MT 11-01 00:30 → 回转成 22:30 UTC；三月切换周日 MT 00:00–00:59 无法产生）。现在周日不扫所以无害；一旦加周日任务或更长窗口就是 bug。
   修：显式实现 `fromutc`，或改为 `ZoneInfo("America/New_York")` + 7h（NY 17:00 = MT 00:00 是真实规则）；挪到 `core/mt_clock.py`（现在 core 从 service 模块 import）。将来出现不跟美国 DST 的服务器时需要 per-server 时区映射。
9. **手动扫描 + 定时扫描重复写 alert_events**：同一 `window_date` 跑两次会追加两套看板告警（CRM 打标签有审计表去重，不涉及资金）。修：按 (window_date, rule, login) 幂等，或在文档里写明。
10. **心跳邮件默认关**（`_gap_trade_crm_heartbeat_email`）：「没邮件」分不清是平安无事还是没扫。修：默认开，或加 MT 04:00 左右的日检——当天 MT 02:00 后没有 gap-trade scan_history 就 ERROR + 邮件。

## 验收标准

- [ ] 三条逐条修复或在结果段写明 live with 原因
- [ ] `_MTServerTZ` 往返一致性单测覆盖两个切换周日
- [ ] `./verify.sh` 绿

## 结果

（待实施）
