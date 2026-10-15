#!/bin/bash
# Post-deploy smoke test for production (OPT-0080 D3/D4). Read-only: it only
# issues GET requests and `docker compose ps`; safe to run by hand any time.
#
# Checks (all must pass in the same round):
#   1. GET $SMOKE_BASE_URL/api/v1/auth/login  -> exactly 303
#      This is an nginx -> api round trip that needs no API key (the OIDC
#      navigation path is exempt). 502 here means nginx cannot reach the api
#      upstream — the 2026-09-28 stale-upstream-IP incident, where the deploy
#      itself "succeeded" and the whole site was down.
#      Do NOT switch this to /api/v1/health: it requires the API key and
#      answers 403 even when everything is healthy (verified 2026-10-15).
#      Redirects are deliberately not followed (we assert the 303 itself).
#   2. GET $SMOKE_BASE_URL/login              -> 200 (SPA served by nginx)
#   3. Every service in docker-compose.prod.yml has a container in state
#      "running" and none is unhealthy / still starting. Uses `ps -a` because
#      plain `ps` hides exited containers. Skippable with SMOKE_SKIP_DOCKER=1
#      (used by scripts/test-smoke-prod.sh against a stub server).
#
# Containers need time to start after `up -d --build`, so the checks are
# polled until they all pass or SMOKE_TIMEOUT seconds elapse.
#
# Env:
#   SMOKE_BASE_URL     default http://127.0.0.1:3000
#   SMOKE_TIMEOUT      default 90 (seconds, total polling budget)
#   SMOKE_INTERVAL     default 3  (seconds between rounds)
#   SMOKE_SKIP_DOCKER  set to 1 to skip the compose check
#   SMOKE_COMPOSE_DIR  directory holding docker-compose.prod.yml + .env;
#                      default = repo root (this script's parent dir)
#
# Exit 0 + "SMOKE: PASS" when all checks pass, exit 1 + "SMOKE: FAIL" otherwise.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="${SMOKE_BASE_URL:-http://127.0.0.1:3000}"
BASE_URL="${BASE_URL%/}"
TIMEOUT="${SMOKE_TIMEOUT:-90}"
INTERVAL="${SMOKE_INTERVAL:-3}"
SKIP_DOCKER="${SMOKE_SKIP_DOCKER:-0}"
COMPOSE_DIR="${SMOKE_COMPOSE_DIR:-$REPO_ROOT}"
COMPOSE_FILE="docker-compose.prod.yml"

if [ "$#" -gt 0 ]; then
  echo "Usage: $0   (configure via SMOKE_* env vars, see header)" >&2
  exit 2
fi

# Per-request cap: never let one hung request eat the whole budget.
REQ_MAX_TIME=5

RESULTS=()
ALL_OK=1

record() { # record <PASS|FAIL> <line>
  RESULTS+=("$1  $2")
  [ "$1" = "PASS" ] || ALL_OK=0
}

check_http() { # check_http <path> <expected_code>
  local path="$1" expected="$2" code
  # -w prints 000 when the connection fails; `|| true` keeps that value.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$REQ_MAX_TIME" \
    "$BASE_URL$path" 2>/dev/null || true)"
  [ -n "$code" ] || code="000"
  if [ "$code" = "$expected" ]; then
    record PASS "GET $path -> $code (expected $expected)"
  else
    record FAIL "GET $path -> $code (expected $expected)"
  fi
}

check_docker() {
  local services ps_out svc line state health bad=""
  if ! services="$(cd "$COMPOSE_DIR" && docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null)"; then
    record FAIL "docker compose config --services failed in $COMPOSE_DIR (expected service list)"
    return
  fi
  if ! ps_out="$(cd "$COMPOSE_DIR" && docker compose -f "$COMPOSE_FILE" ps -a \
      --format '{{.Service}}	{{.State}}	{{.Health}}' 2>/dev/null)"; then
    record FAIL "docker compose ps failed in $COMPOSE_DIR (expected container list)"
    return
  fi
  for svc in $services; do
    line="$(printf '%s\n' "$ps_out" | awk -F'\t' -v s="$svc" '$1 == s' | head -n1)"
    if [ -z "$line" ]; then
      bad="$bad $svc=missing"
      continue
    fi
    state="$(printf '%s' "$line" | cut -f2)"
    health="$(printf '%s' "$line" | cut -f3)"
    if [ "$state" != "running" ]; then
      bad="$bad $svc=$state"
    elif [ "$health" = "unhealthy" ] || [ "$health" = "starting" ]; then
      bad="$bad $svc=$health"
    fi
  done
  if [ -z "$bad" ]; then
    record PASS "docker compose ps -> all $(echo "$services" | wc -w) services running (expected no restarting/exited)"
  else
    record FAIL "docker compose ps ->${bad} (expected all running)"
  fi
}

run_round() {
  RESULTS=()
  ALL_OK=1
  check_http /api/v1/auth/login 303
  check_http /login 200
  if [ "$SKIP_DOCKER" = "1" ]; then
    RESULTS+=("SKIP  docker compose ps (SMOKE_SKIP_DOCKER=1)")
  else
    check_docker
  fi
}

echo "--- Smoke test: $BASE_URL (timeout ${TIMEOUT}s) ---"
start=$SECONDS
attempt=0
while :; do
  attempt=$((attempt + 1))
  run_round
  elapsed=$((SECONDS - start))
  [ "$ALL_OK" = "1" ] && break
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    break
  fi
  echo "  attempt $attempt not yet healthy (${elapsed}s elapsed), retrying in ${INTERVAL}s..."
  sleep "$INTERVAL"
done

for r in "${RESULTS[@]}"; do
  echo "  $r"
done
echo "  ($attempt attempt(s), ${elapsed}s)"
if [ "$ALL_OK" = "1" ]; then
  echo "SMOKE: PASS"
  exit 0
fi
echo "SMOKE: FAIL"
exit 1
