#!/usr/bin/env bash
#
# verify.sh — the single canonical red/green gate for the KCM IT System.
#
# This is the "oracle" that any autonomous / headless development loop trusts
# to decide whether a change is safe to propose. Exit code is the machine
# signal: 0 = every hard gate is green, non-zero = at least one failed.
# A human-readable summary prints at the end; the final line is
# `VERIFY: PASS` or `VERIFY: FAIL` for easy grepping.
#
# Hard gates (must pass — they gate the exit code):
#   1. backend  pytest   — the full backend suite, the strongest correctness signal
#   2. frontend tsc -b   — type check (build was silently broken before this)
#   3. frontend vitest   — unit tests (commission math, filter persistence)
#   (4. nginx conf check — scripts/check-nginx-conf.sh; only ever selected by
#       --changed, never part of the default run — see below)
#
# Advisory (reported but does NOT gate exit code):
#   - frontend eslint    — ~337 pre-existing errors (mostly no-explicit-any).
#       Can't be a hard gate until that debt is paid down. Future hardening:
#       turn this into a *delta* gate (fail only if the current diff ADDS new
#       lint errors vs the base branch) — that's the right autonomy guardrail.
#
# Speed: tests marked `slow` are deselected by default (2026-08-17). One test —
# case_metrics' idempotency check — runs the real nightly batch job twice over
# the live ~1,050-client roster and takes ~11 minutes, which is 88% of the whole
# gate and made people avoid running verify.sh at all. Pass --full to include
# it. The deselection is announced in the output on every run: a gate that
# quietly covers less than it used to is worse than a slow one.
#
# Usage:
#   ./verify.sh            # every gate, minus `slow` tests
#   ./verify.sh --full     # every gate, including `slow` tests (adds ~11 min)
#   ./verify.sh --quiet    # only the per-gate PASS/FAIL + final verdict
#   ./verify.sh --changed [--base <ref>] [--dry-run]
#                          # only the gates the changed files can affect
#   Flags combine in any order (e.g. `--changed --full --quiet`).
#
# --changed (OPT-0080): pick gates from the files that differ from <base>
# (default `main`; `--base <ref>` or `--base=<ref>`):
#     diff vs merge-base(base, HEAD)  ∪  staged + unstaged  ∪  untracked
# Mapping (union over all changed files — classify_path below is the SSOT):
#     backend/** (including *.md — skill files are
#       runtime resources pinned by tests)          → backend pytest
#     docs/**, any other *.md, .cursor/**, .claude/** → nothing
#     frontend/nginx.conf, frontend/Dockerfile*,
#     scripts/check-nginx-conf.sh                   → nginx conf check
#     frontend/** (everything else)                 → tsc + vitest + eslint
#     any frontend/** path whose basename appears in
#       a backend/tests/**/*.py file                → + backend pytest
#     anything else (verify.sh, deploy.sh, compose,
#     scripts/**, root files, ...)                  → every default gate
# An empty change set runs nothing and passes; the coverage block at the end
# names every gate that was NOT run and why. `--dry-run` prints the selection
# (with the file that triggered each gate) and runs nothing.
# If merge-base(base, HEAD) == HEAD (e.g. running on main itself) committed work
# is invisible: only uncommitted/untracked files count, and the output says so.
# Test hook: VERIFY_CHANGED_FILES (newline-separated) replaces the repository
# computation entirely — scripts/test-verify-changed.sh relies on it.
#
# Why --changed is opt-in rather than the default (OPT-0080 D6): deploy.sh and
# the autonomous loop treat a bare `./verify.sh` as the oracle. A diff-based
# selection is only as good as its mapping table, so the oracle stays the full
# run and --changed is a fast inner-loop tool.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUIET=0
FULL=0
CHANGED=0
DRY_RUN=0
BASE="main"
BASE_GIVEN=0

usage_error() { printf 'verify.sh: %s\n' "$1" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet)   QUIET=1 ;;
    --full)    FULL=1 ;;
    --changed) CHANGED=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --base)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || usage_error "--base needs a ref"
      BASE="$2"; BASE_GIVEN=1; shift ;;
    --base=*)
      BASE="${1#--base=}"; BASE_GIVEN=1
      [[ -n "$BASE" ]] || usage_error "--base needs a ref" ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

# --base / --dry-run only mean something for a diff-based selection. Silently
# accepting them on a full run would let `./verify.sh --dry-run` launch the
# whole 3-minute suite when the caller expected a no-op preview.
if [[ $CHANGED -eq 0 ]]; then
  [[ $BASE_GIVEN -eq 0 ]] || usage_error "--base only makes sense with --changed"
  [[ $DRY_RUN -eq 0 ]]    || usage_error "--dry-run only makes sense with --changed"
fi

# Deselect by marker, not by --ignore path: a mistyped --ignore path is accepted
# silently by pytest and ignores nothing, which is exactly how an 11-minute run
# got mistaken for a 90-second one on 2026-08-17.
if [[ $FULL -eq 1 ]]; then
  PYTEST_SELECT=()
else
  PYTEST_SELECT=(-m "not slow")
fi

# ── Gate selection (one source of truth for real runs AND --dry-run) ────────
# Gate ids in execution order. `nginx` is optional: only --changed can select
# it. The default run must stay exactly as it was before OPT-0080 (D6), and
# deploy.sh already runs check-nginx-conf.sh itself. For the same reason the
# "unmapped path → all gates" row means the DEFAULT set, not nginx too: a
# --changed run should never cover more than a bare ./verify.sh.
GATE_IDS=(pytest tsc vitest eslint nginx)
DEFAULT_GATES=(pytest tsc vitest eslint)
declare -A GATE_LABEL=(
  [pytest]="backend pytest"
  [tsc]="frontend tsc"
  [vitest]="frontend vitest"
  [eslint]="frontend eslint (advisory)"
  [nginx]="nginx conf check"
)
declare -A GATE_ON=()        # id -> 1 when selected
declare -A GATE_WHY=()       # id -> first file that triggered it
declare -A GATE_NTRIG=()     # id -> number of files that triggered it
CHANGED_FILES=()
IGNORED_COUNT=0              # changed files that map to no gate (docs/meta)
CHANGE_SOURCE=""             # where the file list came from, for the output
BASE_AT_HEAD=0               # 1 when merge-base(base, HEAD) == HEAD

# classify_path <path> → prints one of: none | backend | nginx | frontend | all
# Order matters: backend/** wins over the "nothing" row — Markdown under
# backend/ is not always prose (backend/app/ai_agent/skills/**/SKILL.md are
# runtime resources pinned by test_ai_agent_skills.py), and a backend README
# costing one pytest run is the cheap side of that trade. nginx.conf /
# Dockerfile* are carved out of frontend/** before it.
# frontend/nginx.conf selects the nginx check, not tsc/vitest: it is not
# TypeScript and cannot break them. (It also selects pytest, but through the
# separate backend_test_reader rule below — test_app_assembly.py reads it.) scripts/check-nginx-conf.sh maps to the nginx check too — it is
# the checker itself, and an edit to it is only proven by running it.
classify_path() {
  case "$1" in
    backend/*)                       echo backend ;;
    docs/*|*.md|.cursor/*|.claude/*) echo none ;;
    frontend/nginx.conf|frontend/Dockerfile*|scripts/check-nginx-conf.sh)
                                     echo nginx ;;
    frontend/*)                      echo frontend ;;
    *)                               echo all ;;
  esac
}

mark_gate() { # mark_gate <id> <reason>
  GATE_ON[$1]=1
  [[ -n "${GATE_WHY[$1]:-}" ]] || GATE_WHY[$1]="$2"
  GATE_NTRIG[$1]=$(( ${GATE_NTRIG[$1]:-0} + 1 ))
}

# backend_test_reader <frontend path> → prints the first backend test file that
# mentions the path's basename, or nothing. Some backend tests read frontend
# sources (test_activity_sort_whitelist_drift.py parses ActivityClientsPanel.tsx,
# test_app_assembly.py reads frontend/nginx.conf, ...), so a frontend-only
# change can break pytest. Self-maintaining on purpose: a new test that reads a
# new frontend file is picked up without editing a list here.
# Matching is whole-name: the basename must not be flanked by a filename
# character [A-Za-z0-9_.-], so `a.ts` does not match inside `data.ts`,
# `Dockerfile` does not match `Dockerfile.ai-agent`, and `nginx.conf` would not
# match `nginx.conf.template`. (grep -w is not enough: it treats `.` and `-` as
# boundaries.) There is deliberately NO
# skip list of "generic" basenames (index.ts, types.ts, ...): today none of
# them appears in backend/tests, and if a test ever names one, the cost of
# matching is an extra pytest run, whereas skipping it would silently miss the
# one test that reads that file. Over-selecting is the safe direction.
backend_test_reader() {
  local base="${1##*/}" dir="$ROOT/backend/tests" hit re
  [[ -n "$base" && -d "$dir" ]] || return 0
  re="$(printf '%s' "$base" | sed 's/[][\\.*^$+?(){}|]/\\&/g')"
  re="(^|[^A-Za-z0-9_.-])${re}(\$|[^A-Za-z0-9_.-])"
  hit="$(command grep -rlE --include='*.py' -- "$re" "$dir" 2>/dev/null | sort | head -1)"
  [[ -n "$hit" ]] && printf '%s' "${hit#"$ROOT"/}"
  return 0
}

# select_gates — fills GATE_ON / GATE_WHY from CHANGED_FILES.
select_gates() {
  local f g cls reader
  for f in "${CHANGED_FILES[@]}"; do
    cls="$(classify_path "$f")"
    if [[ "$f" == frontend/* ]]; then
      reader="$(backend_test_reader "$f")"
      [[ -z "$reader" ]] || mark_gate pytest "$f (read by $reader)"
    fi
    case "$cls" in
      none)     IGNORED_COUNT=$(( IGNORED_COUNT + 1 )) ;;
      backend)  mark_gate pytest "$f" ;;
      nginx)    mark_gate nginx "$f" ;;
      frontend) mark_gate tsc "$f"; mark_gate vitest "$f"; mark_gate eslint "$f" ;;
      all)      for g in "${DEFAULT_GATES[@]}"; do
                  mark_gate "$g" "$f (unmapped path → all gates)"
                done ;;
    esac
  done
}

gate_reason() { # gate_reason <id> → "triggered by X (+N more)"
  local n=${GATE_NTRIG[$1]:-0} r="triggered by ${GATE_WHY[$1]}"
  (( n > 1 )) && r+=" (+$(( n - 1 )) more)"
  printf '%s' "$r"
}

append_lines() { # append_lines <newline-separated text> → CHANGED_FILES
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && CHANGED_FILES+=("$line")
  done <<< "$1"
}

# compute_changed_files — fills CHANGED_FILES. A bad base ref is a hard error
# (exit 2), never "no changes": an empty set means PASS, so a typo in --base
# silently falling back to empty would turn the gate into a rubber stamp.
compute_changed_files() {
  if [[ -n "${VERIFY_CHANGED_FILES+x}" ]]; then
    CHANGE_SOURCE="VERIFY_CHANGED_FILES override"
    append_lines "$VERIFY_CHANGED_FILES"
    return 0
  fi
  git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || usage_error "--changed needs a git work tree at $ROOT"
  git -C "$ROOT" rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null \
    || usage_error "--base '$BASE' is not a valid git ref (refusing to guess)"
  local mb
  mb="$(git -C "$ROOT" merge-base "$BASE" HEAD)" \
    || usage_error "no merge-base between '$BASE' and HEAD"
  CHANGE_SOURCE="$BASE (merge-base ${mb:0:8})"
  [[ "$mb" == "$(git -C "$ROOT" rev-parse HEAD)" ]] && BASE_AT_HEAD=1
  # --no-renames so a rename reports BOTH the old and the new path: moving a
  # file out of backend/ must still run the backend gate. Each command is
  # checked on its own — a failure inside a pipeline would otherwise be
  # swallowed and read as "nothing changed".
  local out
  out="$(git -C "$ROOT" diff --name-only --no-renames "$mb" HEAD)" \
    || usage_error "git diff vs merge-base failed"
  append_lines "$out"
  out="$(git -C "$ROOT" diff --name-only --no-renames --cached)" \
    || usage_error "git diff --cached failed"
  append_lines "$out"
  out="$(git -C "$ROOT" diff --name-only --no-renames)" \
    || usage_error "git diff (unstaged) failed"
  append_lines "$out"
  out="$(git -C "$ROOT" ls-files --others --exclude-standard --full-name)" \
    || usage_error "git ls-files (untracked) failed"
  append_lines "$out"
  # De-duplicate (the same file can be committed AND modified again).
  if [[ ${#CHANGED_FILES[@]} -gt 0 ]]; then
    out="$(printf '%s\n' "${CHANGED_FILES[@]}" | sort -u)"
    CHANGED_FILES=()
    append_lines "$out"
  fi
}

# Printed in the dry run AND the coverage block (also under --quiet): with HEAD
# at the base, every committed change is invisible to the diff.
base_at_head_warning() {
  printf '\033[33mWARNING: HEAD is at the base (%s) — only uncommitted/untracked changes were considered;\033[0m\n' "$BASE"
  printf '         pass --base <ref> (e.g. origin/main or HEAD~N) to include commits\n'
}

if [[ $CHANGED -eq 1 ]]; then
  compute_changed_files
  select_gates
else
  for g in "${DEFAULT_GATES[@]}"; do GATE_ON[$g]=1; done
fi

# ── --dry-run: print the selection, run nothing ─────────────────────────────
# Line format is stable on purpose (scripts/test-verify-changed.sh parses it):
#   "  run   <id> <label> triggered by <file>"   /   "  skip  <id> <label> ..."
#   then one "  file  <path>" line per changed file.
if [[ $DRY_RUN -eq 1 ]]; then
  printf 'verify --changed (dry run) — base: %s, %d changed file(s), %d docs/meta ignored\n' \
    "$CHANGE_SOURCE" "${#CHANGED_FILES[@]}" "$IGNORED_COUNT"
  [[ $BASE_AT_HEAD -eq 0 ]] || base_at_head_warning
  any=0
  for g in "${GATE_IDS[@]}"; do
    if [[ -n "${GATE_ON[$g]:-}" ]]; then
      printf '  run   %-7s %-27s %s\n' "$g" "${GATE_LABEL[$g]}" "$(gate_reason "$g")"
      any=1
    fi
  done
  [[ $any -eq 1 ]] || printf '  (no gates selected)\n'
  for g in "${GATE_IDS[@]}"; do
    [[ -n "${GATE_ON[$g]:-}" ]] \
      || printf '  skip  %-7s %-27s no changed file maps to it\n' "$g" "${GATE_LABEL[$g]}"
  done
  # The full change set, so a preview shows exactly what the diff saw.
  for f in "${CHANGED_FILES[@]}"; do
    printf '  file  %s\n' "$f"
  done
  exit 0
fi

# Pick the backend python: prefer the project venv, fall back to PATH.
PYTEST="$ROOT/backend/.venv/bin/pytest"
[[ -x "$PYTEST" ]] || PYTEST="pytest"

FAILED_GATES=()

# run_gate <label> <workdir> <command...>
#   Runs a hard gate, records failure, streams output unless --quiet.
run_gate() {
  local label="$1" workdir="$2"; shift 2
  printf '\n\033[1m── %s ──\033[0m\n' "$label"
  local start; start=$(date +%s)
  local rc=0
  if [[ $QUIET -eq 1 ]]; then
    ( cd "$workdir" && "$@" ) >/dev/null 2>&1 || rc=$?
  else
    ( cd "$workdir" && "$@" ) || rc=$?
  fi
  local dur=$(( $(date +%s) - start ))
  if [[ $rc -eq 0 ]]; then
    printf '\033[32m✓ %s\033[0m (%ds)\n' "$label" "$dur"
  else
    printf '\033[31m✗ %s\033[0m (%ds, exit %d)\n' "$label" "$dur" "$rc"
    FAILED_GATES+=("$label")
  fi
}

# ── Hard gates ─────────────────────────────────────────────────────────────
# Same commands as before OPT-0080; GATE_ON only decides whether each runs.
if [[ -n "${GATE_ON[pytest]:-}" ]]; then
  run_gate "backend pytest"  "$ROOT/backend"  "$PYTEST" tests/ -q "${PYTEST_SELECT[@]}"
fi
if [[ -n "${GATE_ON[tsc]:-}" ]]; then
  run_gate "frontend tsc"    "$ROOT/frontend" npx tsc -b
fi
if [[ -n "${GATE_ON[vitest]:-}" ]]; then
  run_gate "frontend vitest" "$ROOT/frontend" npm test --silent
fi
# Optional gate, only ever selected by --changed. Needs docker and the compose
# network (see the script's own header).
if [[ -n "${GATE_ON[nginx]:-}" ]]; then
  run_gate "nginx conf check" "$ROOT" ./scripts/check-nginx-conf.sh
fi

# ── Advisory: lint (never gates the verdict) ─────────────────────────────────
if [[ -n "${GATE_ON[eslint]:-}" ]]; then
  printf '\n\033[1m── frontend eslint (advisory) ──\033[0m\n'
  lint_out="$( cd "$ROOT/frontend" && npm run lint 2>&1 )" || true
  lint_summary="$(printf '%s\n' "$lint_out" | grep -E '✖ [0-9]+ problem' | tail -1)"
  if [[ -n "$lint_summary" ]]; then
    printf '\033[33m⚠ %s\033[0m (advisory — does not block)\n' "$lint_summary"
  else
    printf '\033[32m✓ lint clean\033[0m\n'
  fi
fi

# ── Advisory: fxbackoffice schema-context freshness (OPT-0036, never gates) ──
FXBO_INDEX="$ROOT/.cursor/skills/database-context/fxbackoffice/_index.md"
if [[ -f "$FXBO_INDEX" ]]; then
  gen_date="$(grep -oE '生成 [0-9]{4}-[0-9]{2}-[0-9]{2}' "$FXBO_INDEX" | grep -oE '[0-9-]+' || true)"
  if [[ -n "$gen_date" ]]; then
    age_days=$(( ( $(date +%s) - $(date -d "$gen_date" +%s) ) / 86400 ))
    if (( age_days > 30 )); then
      printf '\033[33m⚠ fxbackoffice schema context is %d days old (gen %s) — re-run backend/scripts/dump_fxbackoffice_schema.py\033[0m (advisory)\n' "$age_days" "$gen_date"
    fi
  fi
fi

# ── Coverage disclosure ──────────────────────────────────────────────────────
# Printed on EVERY run, including --quiet. An autonomous loop trusts this
# script's exit code as an oracle; if the default run covers less than the
# full suite, that has to be visible in the same breath as the verdict rather
# than buried in a comment nobody reads.
printf '\n────────────────────────────────────────\n'
if [[ $CHANGED -eq 1 ]]; then
  [[ $BASE_AT_HEAD -eq 0 ]] || base_at_head_warning
  # --changed: name every gate that did NOT run, with the reason. A PASS from
  # a partial run must never read like a PASS from the full one.
  if [[ ${#CHANGED_FILES[@]} -eq 0 ]]; then
    printf '\033[33mcoverage: no changes vs %s — no gates run\033[0m\n' "$CHANGE_SOURCE"
  else
    printf '\033[33mcoverage: --changed vs %s\033[0m — %d file(s), %d docs/meta ignored\n' \
      "$CHANGE_SOURCE" "${#CHANGED_FILES[@]}" "$IGNORED_COUNT"
    for g in "${GATE_IDS[@]}"; do
      [[ -n "${GATE_ON[$g]:-}" ]] \
        || printf '           NOT run: %s — no changed file maps to it\n' "${GATE_LABEL[$g]}"
    done
  fi
  if [[ -n "${GATE_ON[pytest]:-}" && $FULL -eq 0 ]]; then
    printf '           NOT run: tests marked `slow` (live cloud-DB, ~11 min)\n'
  fi
  printf '           run \033[1m./verify.sh\033[0m (no --changed) before trusting this as a release gate\n'
elif [[ $FULL -eq 1 ]]; then
  printf '\033[32mcoverage: FULL\033[0m — `slow` tests included\n'
else
  printf '\033[33mcoverage: tests marked `slow` were NOT run\033[0m (live cloud-DB, ~11 min)\n'
  printf '           run \033[1m./verify.sh --full\033[0m before trusting this as a release gate\n'
fi

# ── Verdict ──────────────────────────────────────────────────────────────────
if [[ ${#FAILED_GATES[@]} -eq 0 ]]; then
  printf '\033[32mVERIFY: PASS\033[0m\n'
  exit 0
else
  printf '\033[31mVERIFY: FAIL\033[0m — %s\n' "${FAILED_GATES[*]}"
  exit 1
fi
