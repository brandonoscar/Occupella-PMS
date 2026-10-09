#!/usr/bin/env bash
# Every check a PR's CI runs that can run here, in one command, so a push doesn't come back red.
#
# Needs what CLAUDE.md ("Run the checks") lists: Python 3.11 with requirements-dev.txt, dbmate,
# and DATABASE_URL naming a throwaway Postgres 16 with trust auth. On a server with
# plpgsql_check (ci/postgres-coverage.Dockerfile) it also lints the PL/pgSQL; started with
# shared_preload_libraries = 'plpgsql_check', it also runs the coverage gates. Whatever it can't
# run here it lists as NOT RUN at the end, never as passed.
#
# It compares against the merge base with origin/main, or $BASE. The standing-rule, threshold
# and registry checks read commits, so commit first.
#
# usage: scripts/check.sh      exit 0 only if every check that ran passed
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

if [ -z "${DATABASE_URL:-}" ] || ! command -v dbmate >/dev/null; then
  echo "FAIL: needs DATABASE_URL (a throwaway Postgres 16) and dbmate on PATH; see CLAUDE.md."
  exit 1
fi
base=${BASE:-$(git merge-base HEAD origin/main)} || exit 1
scratch_url=$(python -c 'import os, sys; from tools.scratch_db import url_for
print(url_for(os.environ["DATABASE_URL"], sys.argv[1]))' "pms_check_$$") || exit 1
failed=()
not_run=()

step() {
  local name=$1
  shift
  printf '\n== %s\n' "$name"
  if "$@"; then
    echo "ok   $name"
  else
    echo "FAIL $name"
    failed+=("$name")
  fi
}

skip() {
  printf '\n== %s\nNOT RUN: %s\n' "$1" "$2"
  not_run+=("$1: $2")
}

on_scratch() { DATABASE_URL=$scratch_url "$@"; }
cleanup() { on_scratch dbmate drop >/dev/null 2>&1; }
trap cleanup EXIT

server_has() {
  # $1: "available" (installable) or "preloaded"
  python - "$1" <<'EOF'
import os, sys
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    if sys.argv[1] == "preloaded":
        found = "plpgsql_check" in conn.execute("SHOW shared_preload_libraries").fetchone()[0]
    else:
        found = conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'plpgsql_check'"
        ).fetchone() is not None
sys.exit(0 if found else 1)
EOF
}

squawk_new_migrations() {
  local changed
  changed=$(git diff --name-only --diff-filter=AM "$base"...HEAD -- 'db/migrations/*.sql')
  if [ -z "$changed" ]; then
    echo "No new or changed migrations to lint."
    return 0
  fi
  # shellcheck disable=SC2086  # one path per word; migration names have no spaces
  squawk --config .squawk.toml $changed
}

schema_has_not_drifted() {
  on_scratch scripts/dump_schema.sh && git diff --exit-code -- db/schema.sql
}

coverage_gates() {
  coverage xml -q -o coverage/python.xml || return 1
  python -m tools.coverage_gate --sql-raw coverage/sql-raw.json \
    --python-xml coverage/python.xml --sql-xml-out coverage/sql.xml || return 1
  diff-cover coverage/python.xml coverage/sql.xml --compare-branch="$base" \
    --fail-under="$(python -m tools.coverage_gate --print coverage.changed_lines_min)"
}

if ! git diff --quiet HEAD; then
  echo "note: uncommitted changes. The standing-rule, threshold and registry checks see commits only."
fi
echo "base: $(git log -1 --format='%h %s' "$base")"

# ci.yml
step "lint" ruff check .
step "format" ruff format --check .
step "typecheck" mypy
step "synthetic data" python -m tools.check_synthetic_data
if command -v shellcheck >/dev/null; then
  step "shell scripts" shellcheck scripts/*.sh
else
  skip "shell scripts" "shellcheck is not installed; CI's ci job runs it"
fi
step "standing rule" python -m tools.check_pr_rules --base "$base" --head HEAD
step "thresholds" python -m tools.check_thresholds --base "$base"
step "registry" python -m tools.check_registry --check-issues --base "$base"
# licenses.yml
step "licenses" python -m tools.check_licenses
# db.yml, on a throwaway database
step "pgledger verbatim" python scripts/check_pgledger_verbatim.py
step "squawk on new migrations" squawk_new_migrations
on_scratch dbmate create >/dev/null
step "migrations apply to an empty database" on_scratch dbmate --no-dump-schema up
step "down/up" on_scratch python -m tools.check_rollback
step "schema drift" schema_has_not_drifted
step "upgrade a database that holds money" python -m tools.check_upgrade --base "$base"
# coverage.yml, ledger-invariants.yml, golden.yml and the ci job's tests
if server_has preloaded; then
  # As in coverage.yml: the lint runs under coverage, then every test adds to it.
  rm -rf .coverage coverage
  step "PL/pgSQL lint" coverage run -m tools.sql_lint
  step "every test, with coverage" env PMS_SQL_COVERAGE_OUT=coverage/sql-raw.json \
    coverage run -a -m pytest -q -p no:cacheprovider
  step "coverage gates" coverage_gates
else
  if server_has available; then
    step "PL/pgSQL lint" python -m tools.sql_lint
  else
    skip "PL/pgSQL lint" "the server has no plpgsql_check; CI's coverage job runs it"
  fi
  step "every test" pytest -q -p no:cacheprovider
  skip "coverage gates" "needs plpgsql_check in shared_preload_libraries; CI's coverage job runs them"
fi
# security.yml
if command -v gitleaks >/dev/null; then
  step "gitleaks on this branch" gitleaks git --config .gitleaks.toml --redact --no-banner \
    --log-opts="$base..HEAD" .
else
  skip "gitleaks" "not installed; CI's deps-and-secrets job runs it"
fi
skip "CodeQL and dependency review" "GitHub-only; CI's security workflow runs them"
skip "self-host image" "needs Docker; CI's selfhost job runs it when the build changes"

printf '\n== summary (base %s)\n' "$(git rev-parse --short "$base")"
for item in "${not_run[@]}"; do echo "NOT RUN  $item"; done
for item in "${failed[@]}"; do echo "FAIL     $item"; done
if [ ${#failed[@]} -gt 0 ]; then
  exit 1
fi
echo "ok: every check that ran passed; ${#not_run[@]} listed as NOT RUN."
