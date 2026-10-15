#!/bin/bash
# Repeatable test for `verify.sh --changed` gate selection (OPT-0080 D5).
#
# Runs nothing heavy: every case uses `--dry-run`, and all but the bad-ref case
# feed the change set through the VERIFY_CHANGED_FILES test hook, so no pytest,
# npm or docker is needed and the result does not depend on the state of the
# working tree. The one case that exercises the real repository computation
# (an invalid --base ref) only asks git to resolve a ref, which must fail
# loudly with exit 2 instead of degrading to "no changes" (= PASS).
#
# What it proves:
#   1. Each row of the mapping table selects exactly its gates.
#   2. An empty change set selects nothing.
#   3. Mixed sets take the union; any unmapped path escalates to all default
#      gates; docs/meta paths never add anything.
#   4. Argument handling: --base / --base=, flag order, unknown flags, and
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
check "frontend/nginx.conf → nginx only" "$(selected 'frontend/nginx.conf')" "nginx"
check "frontend/Dockerfile.prod → nginx only" "$(selected 'frontend/Dockerfile.prod')" "nginx"
check "frontend/Dockerfile → nginx only" "$(selected 'frontend/Dockerfile')" "nginx"
check "scripts/check-nginx-conf.sh → nginx only" "$(selected 'scripts/check-nginx-conf.sh')" "nginx"
check "docs/a.md → none" "$(selected 'docs/a.md')" "none"
check "docs/ non-md asset → none" "$(selected 'docs/img/x.png')" "none"
check "*.md anywhere (backend/README.md) → none" "$(selected 'backend/README.md')" "none"
check "root CLAUDE.md → none" "$(selected 'CLAUDE.md')" "none"
check ".cursor/** → none" "$(selected '.cursor/rules/x.mdc')" "none"
check ".claude/** → none" "$(selected '.claude/settings.json')" "none"
check "deploy.sh → all default gates" "$(selected 'deploy.sh')" "$ALL_DEFAULT"
check "verify.sh → all default gates" "$(selected 'verify.sh')" "$ALL_DEFAULT"
check "scripts/other.sh → all default gates" "$(selected 'scripts/smoke-prod.sh')" "$ALL_DEFAULT"
check "docker-compose.prod.yml → all default gates" "$(selected 'docker-compose.prod.yml')" "$ALL_DEFAULT"

echo "=== Empty and mixed sets ==="
check "empty change set → none" "$(selected '')" "none"
check "blank lines only → none" "$(selected $'\n\n')" "none"
check "frontend + backend → union" \
  "$(selected $'frontend/src/a.tsx\nbackend/app/x.py')" "$ALL_DEFAULT"
check "docs + deploy.sh → all default gates" \
  "$(selected $'docs/a.md\ndeploy.sh')" "$ALL_DEFAULT"
check "nginx.conf + frontend/src → frontend gates + nginx" \
  "$(selected $'frontend/nginx.conf\nfrontend/src/a.tsx')" "tsc vitest eslint nginx"
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

echo "=== Result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
