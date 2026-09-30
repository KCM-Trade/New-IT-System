---
id: OPT-0073
title: AI agent Skills hardening —— OPT-0069 冷审 #5–#11
status: ready
priority: P2
area: backend
effort: M
created: 2026-09-30
related: [[OPT-0069]]
---

## 问题

OPT-0069（Agent Skills，2026-09-30 上线）冷审中「不阻塞上线」的 7 条，用户拍板另立本单：

5. **token / 会话成本**：SKILL.md 6–14 KB，加载后每次模型迭代（≤40）都重发、并进入会话历史；一轮加载 3 个 ≈ 会话 32k 预算的 1/3。
   修：尺寸上限测试（如 SKILL.md ≤ 8 KB、全部 description 总长上限）；compaction 优先丢弃 `load_skill` / `read_skill_resource` 结果（可重新加载）。
6. **审计粒度**：只读 `references/x.md` 也记成整个 skill；加载失败 / 试图加载隐藏 skill 不记。
   修：`skills_loaded` 记 `skill[/resource]`，新增 `skills_denied` 计数；`ai.py` 去重前先截断。
7. **「事实即代码」测试易被绕过**：`mt4_trades` 索引列检查只看 WHERE 之后任意位置出现（`GROUP BY loginSid`、`DATE(closeDate)` 都能过）→ 改 sqlglot 解析、要求 sargable 谓词；扫描时刻检查抓不到「02:20 MT」；`PINNED_BEFORE_OPT_0069` 允许事实只在某个 skill 里——必须恒可见的事实应只对 `system_prompt(...)` 钉；带样本数字的 skill 段落加 `reviewed:` 日期。
8. **MAF 升级脆弱**：子类覆写私有 `_create_tools` / `_load_skill` / `_read_skill_resource`、用 `_find_skill`。保持 `agent-framework-core==1.19.0` 精确锁定，加注释「升级前这些测试必须绿」。
9. **skill 内容等同 system prompt**：`app/ai_agent/skills/` 加 CODEOWNERS / 工程审核要求；测试禁止「ignore previous / you may compute」类指令覆盖措辞。
10. **错工具提示**：all 受众 skill 提到调用者没有的工具（run_sql / get_risk_alerts）→ 清理或按受众拆段。
11. **事件转发默认放行**：`routes/ai.py` ~533 未知事件默认转给浏览器 → 改为可转发事件白名单。

## 验收标准

- [ ] 上述 7 条逐条修复或在结果段写明 live with 原因
- [ ] 新增/改写的测试能被对应的破坏性改动打红（变异验证）
- [ ] `./verify.sh` 绿；rebuild ai-agent 镜像部署（回滚标签 `pre-ai-skills-hardening-<date>`）

## 结果

（待实施）
