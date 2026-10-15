---
id: OPT-0080
title: verify.sh 加 --changed 按 diff 选闸门 + deploy.sh 部署前跑 verify、部署后跑 smoke
status: wip
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

（close 时填）
