#!/bin/bash
# Repeatable test for scripts/smoke-prod.sh and the deploy.sh wiring
# (OPT-0080 AC 4/5). Touches NO production containers and never runs a real
# deploy: smoke-prod.sh is pointed at a throwaway local stub HTTP server with
# SMOKE_SKIP_DOCKER=1, and deploy.sh is only checked statically plus invoked
# with an unknown flag (which must exit 2 before doing anything).
#
# What it proves:
#   1. All checks good (303 + 200)          -> SMOKE: PASS, exit 0
#   2. 302 instead of 303                   -> FAIL (exact code, not "any 3xx")
#   3. 502 (nginx cannot reach api)         -> FAIL
#   4. Nothing listening                    -> FAIL within the timeout budget
#   5. Starts 502, turns healthy after ~3s  -> PASS (proves polling/retry)
#   6. deploy.sh --bogus exits 2; verify runs after `git pull` and before
#      asset archiving; smoke runs before `docker image prune`.
#
# Usage: ./scripts/test-smoke-prod.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SMOKE="$REPO_ROOT/scripts/smoke-prod.sh"
DEPLOY="$REPO_ROOT/deploy.sh"

WORK="$(mktemp -d)"
SERVER_PID=""

PASS=0
FAIL=0
check() { # check <description> <actual> <expected>
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1)); echo "  PASS: $1"
  else
    FAIL=$((FAIL + 1)); echo "  FAIL: $1 (expected [$3], got [$2])"
  fi
}

stop_server() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}

cleanup() {
  stop_server
  # Kill any delayed "turn healthy" writer still sleeping.
  jobs -p | xargs -r kill >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# --- Stub server: answers each path with the code read from $WORK/codes.json
# on EVERY request, so a case can flip it mid-run. Binds port 0 and writes the
# chosen port to $WORK/port. Run with `python3 -I` (isolated: ignores cwd and
# PYTHON* env, so nothing in the repo can be imported by accident).
cat > "$WORK/stub.py" <<'PY'
import http.server, json, sys

codes_file, port_file = sys.argv[1], sys.argv[2]

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with open(codes_file) as f:
                codes = json.load(f)
        except Exception:
            codes = {}
        code = int(codes.get(self.path, 404))
        self.send_response(code)
        if 300 <= code < 400:
            self.send_header("Location", "https://example.invalid/authorize")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass

srv = http.server.HTTPServer(("127.0.0.1", 0), H)
with open(port_file + ".tmp", "w") as f:
    f.write(str(srv.server_address[1]))
import os
os.replace(port_file + ".tmp", port_file)
srv.serve_forever()
PY

set_codes() { # set_codes <login_api_code> <login_page_code>
  printf '{"/api/v1/auth/login": %s, "/login": %s}\n' "$1" "$2" > "$WORK/codes.json.tmp"
  mv "$WORK/codes.json.tmp" "$WORK/codes.json"
}

start_server() {
  rm -f "$WORK/port"
  python3 -I "$WORK/stub.py" "$WORK/codes.json" "$WORK/port" &
  SERVER_PID=$!
  for _ in $(seq 1 50); do
    [ -s "$WORK/port" ] && break
    sleep 0.1
  done
  [ -s "$WORK/port" ] || { echo "stub server failed to start"; exit 1; }
  PORT="$(cat "$WORK/port")"
}

free_port() { # a port nobody listens on (bind, read, release)
  python3 -I -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

run_smoke() { # run_smoke <base_url> <timeout>  -> sets RC, OUT, ELAPSED
  local t0=$SECONDS
  set +e
  OUT="$(SMOKE_BASE_URL="$1" SMOKE_TIMEOUT="$2" SMOKE_INTERVAL=1 SMOKE_SKIP_DOCKER=1 "$SMOKE" 2>&1)"
  RC=$?
  set -e
  ELAPSED=$((SECONDS - t0))
}

last_line() { printf '%s\n' "$OUT" | tail -n1; }

echo "=== 1. all good (303 + 200) -> PASS ==="
set_codes 303 200
start_server
run_smoke "http://127.0.0.1:$PORT" 5
check "exit code 0" "$RC" 0
check "final line" "$(last_line)" "SMOKE: PASS"
check "returns early (no polling when healthy)" "$([ "$ELAPSED" -le 2 ] && echo yes || echo no)" yes
check "reports observed+expected code" \
  "$(printf '%s\n' "$OUT" | grep -c 'PASS  GET /api/v1/auth/login -> 303 (expected 303)')" 1

echo "=== 2. 302 instead of 303 -> FAIL ==="
set_codes 302 200
run_smoke "http://127.0.0.1:$PORT" 2
check "exit code 1" "$RC" 1
check "final line" "$(last_line)" "SMOKE: FAIL"
check "names the 302" \
  "$(printf '%s\n' "$OUT" | grep -c 'FAIL  GET /api/v1/auth/login -> 302 (expected 303)')" 1

echo "=== 3. 502 upstream down -> FAIL ==="
set_codes 502 200
run_smoke "http://127.0.0.1:$PORT" 2
check "exit code 1" "$RC" 1
check "final line" "$(last_line)" "SMOKE: FAIL"
check "names the 502" \
  "$(printf '%s\n' "$OUT" | grep -c 'FAIL  GET /api/v1/auth/login -> 502 (expected 303)')" 1

echo "=== 3b. /login page broken -> FAIL ==="
set_codes 303 500
run_smoke "http://127.0.0.1:$PORT" 2
check "exit code 1" "$RC" 1
check "names /login" "$(printf '%s\n' "$OUT" | grep -c 'FAIL  GET /login -> 500 (expected 200)')" 1
stop_server

echo "=== 4. nothing listening -> FAIL within timeout ==="
DEAD_PORT="$(free_port)"
run_smoke "http://127.0.0.1:$DEAD_PORT" 3
check "exit code 1" "$RC" 1
check "final line" "$(last_line)" "SMOKE: FAIL"
check "reports 000 (no connection)" \
  "$(printf '%s\n' "$OUT" | grep -c 'FAIL  GET /api/v1/auth/login -> 000 (expected 303)')" 1
check "gave up within timeout+slack (${ELAPSED}s <= 8s)" "$([ "$ELAPSED" -le 8 ] && echo yes || echo no)" yes

echo "=== 5. becomes healthy after ~3s -> PASS (polling) ==="
set_codes 502 200
start_server
( sleep 3; set_codes 303 200 ) &
run_smoke "http://127.0.0.1:$PORT" 20
check "exit code 0" "$RC" 0
check "final line" "$(last_line)" "SMOKE: PASS"
check "needed more than one attempt" \
  "$(printf '%s\n' "$OUT" | grep -c 'attempt 1 not yet healthy')" 1
check "returned early, well before timeout (${ELAPSED}s < 15s)" "$([ "$ELAPSED" -lt 15 ] && echo yes || echo no)" yes
stop_server

echo "=== 6. smoke-prod.sh rejects arguments ==="
set +e; "$SMOKE" --bogus >/dev/null 2>&1; rc=$?; set -e
check "smoke-prod.sh --bogus exits 2" "$rc" 2

echo "=== 7. deploy.sh wiring (static + unknown flag only — never a real deploy) ==="
# deploy.sh parses args before its hard-coded `cd` / git pull / docker, so an
# unknown flag must exit 2 having done nothing.
set +e; DEPLOY_OUT="$("$DEPLOY" --bogus 2>&1)"; rc=$?; set -e
check "deploy.sh --bogus exits 2" "$rc" 2
check "deploy.sh --bogus did not start a deploy" \
  "$(printf '%s\n' "$DEPLOY_OUT" | grep -c 'Rebuilding Production')" 0

line_of() { # first line number of a non-comment line matching <regex>
  grep -nE "$1" "$DEPLOY" | grep -vE '^[0-9]+:[[:space:]]*#' | head -n1 | cut -d: -f1
}
L_PULL="$(line_of '^git pull origin main')"
L_VERIFY="$(line_of '\./verify\.sh;? *then|^ *\./verify\.sh')"
L_ARCHIVE="$(line_of '^\./scripts/archive-frontend-assets\.sh')"
L_BUILD="$(line_of 'up -d --build')"
L_SMOKE="$(line_of '\./scripts/smoke-prod\.sh')"
L_MKDOCS="$(line_of 'restart mkdocs')"
L_PRUNE="$(line_of '^docker image prune')"
L_BPRUNE="$(line_of '^docker builder prune')"
echo "  lines: pull=$L_PULL verify=$L_VERIFY archive=$L_ARCHIVE build=$L_BUILD mkdocs=$L_MKDOCS smoke=$L_SMOKE prune=$L_PRUNE builder-prune=$L_BPRUNE"
for v in L_PULL L_VERIFY L_ARCHIVE L_BUILD L_SMOKE L_MKDOCS L_PRUNE L_BPRUNE; do
  check "deploy.sh contains anchor $v" "$([ -n "${!v}" ] && echo found || echo missing)" found
done
lt() { [ -n "$1" ] && [ -n "$2" ] && [ "$1" -lt "$2" ] && echo yes || echo no; }
check "verify after git pull"              "$(lt "$L_PULL" "$L_VERIFY")" yes
check "verify before asset archiving"      "$(lt "$L_VERIFY" "$L_ARCHIVE")" yes
check "verify before build"                "$(lt "$L_VERIFY" "$L_BUILD")" yes
check "smoke after build + mkdocs restart" "$(lt "$L_MKDOCS" "$L_SMOKE")" yes
check "smoke before docker image prune"    "$(lt "$L_SMOKE" "$L_PRUNE")" yes
check "smoke before docker builder prune"  "$(lt "$L_SMOKE" "$L_BPRUNE")" yes
check "verify called with no flags" \
  "$(grep -E '\./verify\.sh' "$DEPLOY" | grep -vE '^[[:space:]]*#|echo' | grep -cE '\./verify\.sh +-')" 0

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
