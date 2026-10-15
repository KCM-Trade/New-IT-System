---
id: OPT-0080
title: verify.sh 加 --changed 按 diff 选闸门 + deploy.sh 部署前跑 verify、部署后跑 smoke
status: done
priority: P1
area: devops
effort: S
created: 2026-10-15
related: [[OPT-0051]], [[OPT-0053]]
---

## 问题

2026-10-15 在 `main`（`f14f61f`）实测 `./verify.sh` = **3m05s**：pytest 3189 passed / 166s（90%）· tsc 8s · vitest 433 / 2s · eslint advisory 316。

1. **慢在不看改了什么。** 只改前端也要等 ~2m50s 的后端 pytest；只改 docs 也全跑。pytest 没有可砍的单个慢测试（最慢 `test_risk_cases_api.py::test_watchlist_end_to_end_with_fixtures` 21s，其余长尾 ~40ms/个）——按 diff 选闸门是唯一的大头收益。
2. **闸门没人强制。** `deploy.sh` 只调 `./scripts/check-nginx-conf.sh`（`deploy.sh:29`），从不调 `verify.sh`；main 上 pytest/tsc 红照样能部署（08-13 tsc 红是在 web 镜像 build 那步才炸，`package.json` build=`tsc -b && vite build`）。
3. **部署后没人看结果。** `deploy.sh` 结尾只打印 `docker compose ps`。2026-09-28 nginx 钉死 api 旧 IP → 全站 502，部署本身「成功」。

## 决定（用户 2026-10-15 拍板：全部按建议）

- **D1** `deploy.sh` 在 `git pull` 之后、build 之前跑 `./verify.sh`（默认口径，不加 `--full`、不加 `--changed`）。失败 = 中止部署（`set -e` 语义）。
- **D2** 唯一逃生口 `./deploy.sh --skip-verify`：打印醒目 WARNING（含「跳过的是什么」），不中止。未知参数 exit 2。不用环境变量做逃生口（不留痕）。
- **D3** smoke 抽成独立脚本 `scripts/smoke-prod.sh`（部署后自动跑 + 可单独手跑）。在 `docker compose up -d --build` 之后、`docker image prune` **之前**跑；smoke 失败 → **跳过 prune**（保留旧镜像作回滚点）、打印回滚提示、`deploy.sh` exit 非 0。**不自动回滚**。
- **D4** smoke 检查项（2026-10-15 已实测）：
  - `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/api/v1/auth/login` 期望 **303**（nginx→api 往返且免 API key；502 = upstream 断，即 09-28 事故）。⚠ **不能用 `/api/v1/health`**——它要 API key，健康时也回 403。
  - `http://127.0.0.1:3000/login` 期望 **200**。
  - `docker compose -f docker-compose.prod.yml ps` 中无 `restarting` / `exited` 的服务（mkdocs 等全部算）。
  - 启动需要时间：每个 HTTP 检查轮询重试，总等待上限 ~90s（可用 env `SMOKE_TIMEOUT` 覆盖），全部通过即提前返回。
  - base URL 可用 env `SMOKE_BASE_URL` 覆盖（为了测试）。
- **D5** `verify.sh --changed [--base <ref>]`：
  - 变更文件 = `git diff --name-only <merge-base(base, HEAD)>` ∪ 未提交（staged + unstaged）∪ untracked（`git ls-files --others --exclude-standard`）。`--base` 缺省 `main`。
  - 映射（取并集）：
    | 路径 | 闸门 |
    |---|---|
    | `backend/**` | backend pytest（整套 `-m "not slow"`，不做测试级选择） |
    | `frontend/nginx.conf`、`frontend/Dockerfile*` | `scripts/check-nginx-conf.sh`（新增为可选闸门） |
    | `frontend/**`（其余） | tsc + vitest + eslint advisory |
    | `docs/**`、`**/*.md`、`.cursor/**`、`.claude/**` | 无 |
    | 其他任何路径（`verify.sh`、`deploy.sh`、compose、`scripts/**`、根目录文件…） | **全部闸门**（保守） |
  - 变更为空 → 不跑任何闸门，`VERIFY: PASS` + 披露「no changes vs <base>」。
  - 测试钩子：env `VERIFY_CHANGED_FILES`（换行分隔）覆盖 git 计算；`--dry-run` 只打印选中的闸门与原因，不执行。
  - 覆盖披露行（verify.sh 现有 `coverage:` 段）必须列出本次**没跑**的闸门及原因。
  - `--changed` 与 `--full` / `--quiet` 任意组合。
- **D6** 不默认启用 `--changed`：`./verify.sh` 无参数行为不变（deploy 与自治循环依赖它）。

## AC

1. `VERIFY_CHANGED_FILES=$'frontend/src/a.tsx' ./verify.sh --changed --dry-run` 只列 tsc / vitest / eslint；`backend/app/x.py` 只列 pytest；`docs/a.md` 列「无」；`deploy.sh` 列全部；`frontend/nginx.conf` 含 nginx check。
2. `scripts/test-verify-changed.sh` 覆盖上表每一行 + 空变更 + 混合 + `--base` 解析 + 未知参数 exit 2，全绿。
3. 在真实工作树上 `./verify.sh --changed`（只改一个前端文件时）实测 < 20s。
4. `./deploy.sh --bogus` exit 2 且不做任何事；`deploy.sh` 中 verify 位于 `git pull` 之后、archive/build 之前。
5. `scripts/test-smoke-prod.sh` 用本地 stub HTTP 服务覆盖：全通过 / 302 当 303 失败 / 502 失败 / 超时失败；全绿。
6. 在 prod 上手跑 `scripts/smoke-prod.sh` 通过（只读，不部署）。
7. `./verify.sh` 无参数行为与改前一致（同口径计数相同）。
8. 文档：`docs/ai-context/verify-profile.md`、`docs/deployment/dev-prod-guide.md`、`CLAUDE.md` 的 deploy.sh 约定行更新。

## 开放问题

无（全部按 D1–D6）。

## 结果

实施（2026-10-15，四个提交，两个 agent 按文件所有权并行，主线程集成 + 冷审 + 文档）：
`a1e7419` deploy 闸门 + smoke · `6ec4bde` verify `--changed` · `36cf965` / `61e9306` 冷审修复。

**AC 对照**
1. ✅ 五种路径的 dry-run 结果正确（nginx.conf 现在是 nginx check **+ pytest**，见偏差 2）。
2. ✅ `scripts/test-verify-changed.sh` 60/60，含临时 git 仓库里的真实 diff（已提交/已暂存/未暂存/未跟踪/删除/重命名）。
3. ✅ 只改一个前端文件：干净 worktree 实测 13.2s。⚠ 共用工作区里有别人留下的 `backend/` 未跟踪文件时会带上 pytest（3m11s 实测），属于设计内的保守方向。
4. ✅ `./deploy.sh --bogus` exit 2、什么都不做；顺序：dirty 检查 < pull < verify < HEAD/工作区复查 < build < smoke < prune（测试里有静态检查）。
5. ✅ `scripts/test-smoke-prod.sh` 72/72（本地假服务器 + PATH 上的假 docker）。
6. ✅ 对真实 prod 手跑 `scripts/smoke-prod.sh`：PASS（2 轮，含 10s settle）。
7. ✅ 不带参数的 `./verify.sh`：改前改后都是 pytest 3189 passed / 1 deselected，约 3m06s。
8. ✅ verify-profile / dev-prod-guide / CLAUDE.md 已更新（三者都在 gitignore 里，是本地文件）。

**与决定的偏差**
1. 未映射路径跑**默认四项**闸门，不含 nginx check（nginx check 要 docker，deploy 本来就会跑它）。
2. 前端文件的文件名只要出现在 `backend/tests/**/*.py` 里，就带上 pytest（冷审 #2）。这条规则会随测试自动更新，不维护手写名单。
3. `backend/**` 下的 `.md` 也跑 pytest（冷审 #1：ai_agent skills 是运行时资源）。
4. 新增 D7：已跟踪文件有未提交改动就拒绝部署（`git pull` 之前查），build 前再查一次 HEAD 和工作区；逃生口 `--allow-dirty`（冷审 #5）。
5. smoke 增加 settle 复查：10 秒后 RestartCount 不能变（冷审 #4）；环境变量做整数校验，每个 docker 调用套 `timeout 15`（冷审 #6）。

**Stage 1 冷审处理**（零上下文独立 agent，8 条，主线程逐条复核全部属实）
- #1 skills 下的 `.md` 一个闸门都不选（high）→ 当场修 `36cf965`
- #2 后端测试读前端文件（med-high）→ 当场修 `36cf965`
- #3 在 main 上时已提交的改动看不见 → 当场修（加警告）`36cf965`
- #4 smoke 没有稳定性检查 → 当场修 `61e9306`
- #5 闸门测的树和 build 的树可能不同 → 当场修 `61e9306`
- #6 smoke 可能无限轮询 → 当场修 `61e9306`
- #7 泄漏的 `VERIFY_CHANGED_FILES=""` / 被 gitignore 的输入 → live with：输出里已标明 override；`.env` 改动本来就不在 diff 里
- #8 测试没覆盖真实 git 和 docker 分支 → 当场修（两个提交都有）

**Follow-up**
- 第 3 条建议（optimization-tracker Stage 1 按档位决定是否冷审）未做，等用户另行决定。
- eslint 增量闸门、pytest-xdist 并行实验：未立 OPT。
