#!/bin/bash
# Repeatable test for `verify.sh --changed` gate selection (OPT-0080 D5).
#
# Runs nothing heavy: every case uses `--dry-run`, and all but the bad-ref case
# feed the change set through the VERIFY_CHANGED_FILES test hook, so no pytest,
# npm or docker is needed and the result does not depend on the state of the
# working tree. The real repository computation is exercised two ways: an
# invalid --base ref against this repo (must fail loudly with exit 2 instead of
# degrading to "no changes" = PASS), and a throwaway git repo in a temp dir
# (a copy of verify.sh + a main branch + a feature branch) that checks every
# kind of change — committed, staged, unstaged, untracked, deleted, renamed —
# is seen, and that HEAD-at-base prints a warning.
#
# What it proves:
#   1. Each row of the mapping table selects exactly its gates.
#   2. An empty change set selects nothing.
#   3. Mixed sets take the union; any unmapped path escalates to all default
#      gates; docs/meta paths never add anything.
#   4. Frontend files a backend test reads (by basename, real backend/tests)
#      also select pytest.
#   5. Argument handling: --base / --base=, flag order, unknown flags, and
#      --base / --dry-run without --changed are all handled as specified.
#
# Usage: ./scripts/test-verify-changed.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERIFY="$REPO_ROOT/verify.sh"

PASS=0
FAIL=0
check() { # check <description> <actual> <expected>
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1)); echo "  PASS: $1"
  else
    FAIL=$((FAIL + 1)); echo "  FAIL: $1 (expected [$3], got [$2])"
  fi
}

# selected <files (newline-separated)> [extra verify args...]
#   → space-separated gate ids from the dry-run "run" lines, or "none".
#   Also fails the case if the dry run itself does not exit 0.
selected() {
  local files="$1"; shift
  local out rc
  out="$(VERIFY_CHANGED_FILES="$files" "$VERIFY" --changed --dry-run "$@" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ]; then echo "exit=$rc"; return; fi
  local ids
  ids="$(printf '%s\n' "$out" | awk '$1 == "run" { print $2 }' | tr '\n' ' ' | sed 's/ $//')"
  echo "${ids:-none}"
}

# exit_code <verify args...> → exit status of verify.sh (output discarded)
exit_code() {
  "$VERIFY" "$@" >/dev/null 2>&1
  echo $?
}

ALL_DEFAULT="pytest tsc vitest eslint"
FRONTEND="tsc vitest eslint"

echo "=== Mapping table, one row at a time ==="
check "frontend/src file → tsc vitest eslint" "$(selected 'frontend/src/a.tsx')" "$FRONTEND"
check "frontend/package.json → tsc vitest eslint" "$(selected 'frontend/package.json')" "$FRONTEND"
check "backend/app file → pytest only" "$(selected 'backend/app/x.py')" "pytest"
check "backend/requirements.txt → pytest only" "$(selected 'backend/requirements.txt')" "pytest"
# Design choice (documented in verify.sh classify_path): nginx.conf alone runs
# ONLY the nginx check — it is not TypeScript and cannot break tsc/vitest.
# ...but it also selects pytest, because backend/tests/test_app_assembly.py
# reads it (backend-test-reader rule, see the section below).
check "frontend/nginx.conf → pytest + nginx" "$(selected 'frontend/nginx.conf')" "pytest nginx"
check "frontend/Dockerfile.prod → nginx only" "$(selected 'frontend/Dockerfile.prod')" "nginx"
check "frontend/Dockerfile → nginx only" "$(selected 'frontend/Dockerfile')" "nginx"
check "scripts/check-nginx-conf.sh → nginx only" "$(selected 'scripts/check-nginx-conf.sh')" "nginx"
check "docs/a.md → none" "$(selected 'docs/a.md')" "none"
check "docs/ non-md asset → none" "$(selected 'docs/img/x.png')" "none"
check "backend/README.md → pytest (backend/** wins over *.md)" "$(selected 'backend/README.md')" "pytest"
check "backend ai_agent SKILL.md → pytest" \
  "$(selected 'backend/app/ai_agent/skills/x/SKILL.md')" "pytest"
check "*.md elsewhere (frontend/README.md) → none" "$(selected 'frontend/README.md')" "none"
check "root CLAUDE.md → none" "$(selected 'CLAUDE.md')" "none"
check ".cursor/** → none" "$(selected '.cursor/rules/x.mdc')" "none"
check ".claude/** → none" "$(selected '.claude/settings.json')" "none"
check "deploy.sh → all default gates" "$(selected 'deploy.sh')" "$ALL_DEFAULT"
check "verify.sh → all default gates" "$(selected 'verify.sh')" "$ALL_DEFAULT"
check "scripts/other.sh → all default gates" "$(selected 'scripts/smoke-prod.sh')" "$ALL_DEFAULT"
check "docker-compose.prod.yml → all default gates" "$(selected 'docker-compose.prod.yml')" "$ALL_DEFAULT"

echo "=== Frontend files read by backend tests (real backend/tests) ==="
PANEL="$(cd "$REPO_ROOT" && find frontend/src -name ActivityClientsPanel.tsx | head -1)"
check "ActivityClientsPanel.tsx exists in repo" "$([ -n "$PANEL" ] && echo yes)" "yes"
check "$PANEL → pytest + frontend gates" "$(selected "$PANEL")" "pytest $FRONTEND"
out="$(VERIFY_CHANGED_FILES="$PANEL" "$VERIFY" --changed --dry-run 2>&1)"
check "reason names the reading backend test" \
  "$(printf '%s\n' "$out" | grep -c 'read by backend/tests/test_activity_sort_whitelist_drift.py')" "1"
check "file no backend test mentions → no pytest" \
  "$(selected 'frontend/src/zz-no-test-mentions-this-opt0080.tsx')" "$FRONTEND"
check "basename match is whole-word (data.ts vs a.ts)" "$(selected 'frontend/src/a.ts')" "$FRONTEND"

echo "=== Empty and mixed sets ==="
check "empty change set → none" "$(selected '')" "none"
check "blank lines only → none" "$(selected $'\n\n')" "none"
check "frontend + backend → union" \
  "$(selected $'frontend/src/a.tsx\nbackend/app/x.py')" "$ALL_DEFAULT"
check "docs + deploy.sh → all default gates" \
  "$(selected $'docs/a.md\ndeploy.sh')" "$ALL_DEFAULT"
check "nginx.conf + frontend/src → frontend gates + nginx" \
  "$(selected $'frontend/nginx.conf\nfrontend/src/a.tsx')" "pytest tsc vitest eslint nginx"
check "backend + docs → pytest only" \
  "$(selected $'backend/app/x.py\ndocs/a.md\n.claude/x')" "pytest"

echo "=== Dry-run output content ==="
out="$(VERIFY_CHANGED_FILES=$'backend/app/x.py\nbackend/app/y.py' "$VERIFY" --changed --dry-run 2>&1)"
check "reason names the first triggering file" \
  "$(printf '%s\n' "$out" | grep -c 'triggered by backend/app/x.py (+1 more)')" "1"
check "unselected gates are listed as skip" \
  "$(printf '%s\n' "$out" | awk '$1 == "skip" { print $2 }' | tr '\n' ' ' | sed 's/ $//')" \
  "tsc vitest eslint nginx"
out="$(VERIFY_CHANGED_FILES='' "$VERIFY" --changed --dry-run 2>&1)"
check "empty set says no gates selected" \
  "$(printf '%s\n' "$out" | grep -c '(no gates selected)')" "1"

echo "=== Flag parsing ==="
check "flags in any order (--dry-run --quiet --changed --full)" \
  "$(VERIFY_CHANGED_FILES='backend/app/x.py' exit_code --dry-run --quiet --changed --full)" "0"
check "--base <ref> with override → exit 0" \
  "$(selected 'backend/app/x.py' --base HEAD)" "pytest"
check "--base=<ref> with override → exit 0" \
  "$(selected 'backend/app/x.py' --base=HEAD)" "pytest"
check "--base without a value → exit 2" "$(exit_code --changed --dry-run --base)" "2"
check "--base= empty → exit 2" "$(exit_code --changed --dry-run --base=)" "2"
check "unknown flag → exit 2" "$(exit_code --changed --bogus)" "2"
check "unknown flag alone → exit 2" "$(exit_code --bogus)" "2"
check "--dry-run without --changed → exit 2" "$(exit_code --dry-run)" "2"
check "--base without --changed → exit 2" "$(exit_code --base main)" "2"

echo "=== Real repository computation (no override) ==="
# Must run WITHOUT the override so the base ref is actually resolved.
check "--base with an invalid ref → exit 2" \
  "$(env -u VERIFY_CHANGED_FILES "$VERIFY" --changed --dry-run --base no-such-ref-opt0080 >/dev/null 2>&1; echo $?)" "2"
check "--base HEAD resolves → exit 0" \
  "$(env -u VERIFY_CHANGED_FILES "$VERIFY" --changed --dry-run --base HEAD >/dev/null 2>&1; echo $?)" "0"

echo "=== Real git computation in a throwaway repo ==="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
R="$TMP/repo"
g() { git -C "$R" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false "$@" >/dev/null 2>&1; }
mkdir -p "$R/backend/app" "$R/frontend/src" "$R/scripts" "$R/docs"
cp "$VERIFY" "$R/verify.sh"
for f in backend/app/c.py backend/app/s.py backend/app/u.py frontend/src/del.ts \
         scripts/old.sh docs/keep.md; do echo base > "$R/$f"; done
git init -q -b main "$R" 2>/dev/null || { git init -q "$R"; git -C "$R" checkout -q -b main; }
g add -A; g commit -m base
# On main itself: HEAD == merge-base → warning, no committed changes visible.
out="$(env -u VERIFY_CHANGED_FILES "$R/verify.sh" --changed --dry-run --base HEAD 2>&1)"; rc=$?
check "on main, --base HEAD → exit 0" "$rc" "0"
check "on main, --base HEAD → HEAD-at-base warning" \
  "$(printf '%s\n' "$out" | grep -c 'HEAD is at the base')" "1"
check "on main, clean tree → no gates" "$(printf '%s\n' "$out" | grep -c '(no gates selected)')" "1"
out="$(env -u VERIFY_CHANGED_FILES "$R/verify.sh" --changed --dry-run --quiet 2>&1)"
check "on main, default base → warning too (even with --quiet)" \
  "$(printf '%s\n' "$out" | grep -c 'HEAD is at the base')" "1"
# Feature branch with every kind of change.
g checkout -b feat
echo changed > "$R/backend/app/c.py"; g commit -am committed
echo staged > "$R/backend/app/s.py"; g add backend/app/s.py
echo unstaged > "$R/backend/app/u.py"
echo new > "$R/frontend/src/new.tsx"
rm "$R/frontend/src/del.ts"
g mv scripts/old.sh scripts/renamed.sh
out="$(env -u VERIFY_CHANGED_FILES "$R/verify.sh" --changed --dry-run 2>&1)"; rc=$?
files="$(printf '%s\n' "$out" | awk '$1 == "file" { print $2 }' | tr '\n' ' ' | sed 's/ $//')"
check "branch dry run → exit 0" "$rc" "0"
for f in backend/app/c.py backend/app/s.py backend/app/u.py frontend/src/new.tsx \
         frontend/src/del.ts scripts/old.sh scripts/renamed.sh; do
  check "sees $f" "$(printf '%s\n' "$out" | awk -v f="$f" '$1 == "file" && $2 == f' | wc -l | tr -d ' ')" "1"
done
check "branch sees exactly the 7 changed paths (no verify.sh, no docs)" "$files" \
  "backend/app/c.py backend/app/s.py backend/app/u.py frontend/src/del.ts frontend/src/new.tsx scripts/old.sh scripts/renamed.sh"
check "branch → no HEAD-at-base warning" "$(printf '%s\n' "$out" | grep -c 'HEAD is at the base')" "0"
check "branch → all default gates (scripts/ is unmapped)" \
  "$(printf '%s\n' "$out" | awk '$1 == "run" { print $2 }' | tr '\n' ' ' | sed 's/ $//')" "$ALL_DEFAULT"

echo "=== Result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
