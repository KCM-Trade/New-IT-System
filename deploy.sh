#!/bin/bash
# Rebuild and restart ONLY production containers
# Dev containers (on :5173 / :8001) are NOT affected
set -e

# Parse args BEFORE touching anything (no git pull, no docker) so a typo can
# never half-run a deploy. The only flag is --skip-verify; there is
# deliberately no env-var equivalent, because an env var leaves no trace in
# the terminal/history while a flag on the command line does (OPT-0080 D2).
SKIP_VERIFY=0
usage() {
  echo "Usage: $0 [--skip-verify]" >&2
  echo "  --skip-verify  skip ./verify.sh (pytest/tsc/vitest gates). Emergency only." >&2
}
for arg in "$@"; do
  case "$arg" in
    --skip-verify) SKIP_VERIFY=1 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

echo "================================"
echo "  Rebuilding Production..."
echo "================================"

cd /opt/myproject/New-IT-System

# Pull latest code
git pull origin main

# Run the verify gate on the code we are about to build (OPT-0080 D1). Before
# this, a red pytest/tsc on main deployed anyway (or, for tsc, only blew up
# mid-way through the web image build). Runs before asset archiving and the
# build so a failure leaves prod completely untouched. Plain ./verify.sh with
# no flags: its default behaviour is a stable contract.
echo ""
if [ "$SKIP_VERIFY" = "1" ]; then
  echo "################################################################"
  echo "  WARNING: --skip-verify given — ./verify.sh was NOT run."
  echo "  Skipped gates: backend pytest, frontend tsc, frontend vitest"
  echo "  (and eslint advisory). This build is deploying unverified."
  echo "################################################################"
else
  echo "--- Running verify gate (./verify.sh) ---"
  # Explicit `if !` instead of relying on set -e, so the abort reason is printed.
  if ! ./verify.sh; then
    echo ""
    echo "ERROR: ./verify.sh failed — deploy ABORTED. Nothing was built or restarted."
    echo "  Fix the failing gate, or (emergency only) re-run: ./deploy.sh --skip-verify"
    exit 1
  fi
fi
echo ""

# Preserve the currently-served hashed assets BEFORE the rebuild discards them,
# so browser tabs still holding the previous index.html keep lazy-loading their
# chunks (no forced full-page reload via lazyWithRetry after each deploy).
# Keeps the last 3 builds in frontend/.assets-archive/ (gitignored); the merged
# pool is bind-mounted read-only into the frontend container and served by
# nginx as an /assets/* fallback. Must never block a deploy — worst case we
# just lose the stale-tab convenience for this release.
./scripts/archive-frontend-assets.sh || echo "WARNING: asset archiving failed — continuing deploy"

# Validate nginx.conf BEFORE building. A bad directive still produces a
# successful image, and the container then crash-loops with the whole site
# down. `set -e` above means a failure here aborts the deploy instead.
echo ""
echo "--- Checking nginx.conf ---"
./scripts/check-nginx-conf.sh
echo ""

# Rebuild and restart prod containers only
docker compose -f docker-compose.prod.yml up -d --build

# Docs portal: `mkdocs serve` doesn't pick up host file changes (inotify doesn't
# cross the bind mount), and `up` won't recreate the pinned mkdocs image — force
# a restart so the docs site reflects the just-pulled docs/.
docker compose -f docker-compose.prod.yml restart mkdocs

# Post-deploy smoke test (OPT-0080 D3). A deploy can "succeed" with the site
# down (2026-09-28: nginx pinned the old api IP -> 502 everywhere). Runs BEFORE
# the prunes: on failure we skip them so the previous images survive as a
# rollback point. No automatic rollback — a human decides.
echo ""
if ! ./scripts/smoke-prod.sh; then
  echo ""
  echo "ERROR: post-deploy smoke test FAILED. The new containers are running but"
  echo "  prod looks unhealthy. Skipped docker image/builder prune so old images"
  echo "  remain available for rollback. No automatic rollback was done."
  echo ""
  echo "  Rollback (using the pre-deploy tags new-it-system-{api,web,ai-agent}:pre-<stage>-<date>):"
  echo "    for s in api web ai-agent; do docker tag new-it-system-\$s:pre-<stage>-<date> new-it-system-\$s:latest; done"
  echo "    docker compose -f docker-compose.prod.yml up -d --no-build api web ai-agent"
  echo "  Most recent pre-* tags for api:"
  PRE_TAGS="$(docker images new-it-system-api --format '    {{.Repository}}:{{.Tag}}  ({{.CreatedSince}})' 2>/dev/null \
    | grep ':pre-' | head -n 3 || true)"
  echo "${PRE_TAGS:-    (none found — was the image tagged before deploy?)}"
  echo ""
  docker compose -f docker-compose.prod.yml ps -a
  exit 1
fi

# Clean up dangling images (safe, always recommended)
docker image prune -f
# Clean build cache older than 7 days (keep recent cache for fast rebuilds)
docker builder prune --filter "until=168h" -f

echo ""
docker compose -f docker-compose.prod.yml ps
echo ""
echo "================================"
echo "  Done! Prod is live on :3000"
echo "  Dev still running on :5173"
echo "================================"
