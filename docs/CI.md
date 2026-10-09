# CI

What each workflow checks, why it matters for the money a PMC holds in trust, and how to run it
yourself. Every workflow uses only synthetic data, needs no secret beyond GitHub's automatic
read-only token, and pins every action to a full commit SHA. The local commands assume the setup
in `CLAUDE.md` ("Run the checks"): Postgres 16 in `DATABASE_URL`, dbmate on `PATH`, and
`pip install -r requirements-dev.txt`.

## The workflows

**ci.yml** (PRs and main): lint (ruff), format check (ruff), typecheck (mypy, strict on `tools/`,
`selfhost/` and `scripts/`), the standing-rule checks, and every example test against a fresh
database. The standing-rule checks fail a PR that:
- changes code without changing tests;
- changes ledger SQL without a property test;
- lowers any number in `ci/thresholds.toml`;
- leaves a required invariant with neither a test nor an open issue.

This is the first line of defense: a ledger bug that an example test can see never reaches main.
Run it with `ruff check . && ruff format --check . && mypy && pytest --ignore=tests/properties`.

**db.yml** (PRs and main): checks the migrations.
- squawk lints new or changed migrations. A destructive change (drop, rename, type change) needs
  an explicit `-- squawk-ignore <rule>` line right above it.
- Every migration is applied to an empty database.
- Each migration's down section is run where one exists. Ledger migrations must refuse to roll
  back and leave the schema untouched.
- The dumped schema must equal `db/schema.sql`.
- The pgledger code must match upstream byte for byte.

Trust records are kept for years, so a migration that silently drops or rewrites ledger data is
the worst kind of change. On PRs that touch the Dockerfile, compose file or migrations, it then
runs the self-host check. Run it with:
- `squawk --config .squawk.toml db/migrations/<new>.sql`
- `dbmate --no-dump-schema up && python -m tools.check_rollback`
- `scripts/dump_schema.sh && git diff --exit-code db/schema.sql`

**ledger-invariants.yml** (PRs, main and nightly): property-based tests with Hypothesis. They
throw random sequences of postings, batches, reversals, cross-PMC attempts and history rewrites
at the ledger, and check the money invariants after every step:
- debits equal credits;
- no held account below zero;
- book cash equals what is held for others;
- history is append-only;
- running balances chain.

`ci/registry.toml` maps every required invariant to its test or to the open issue for what isn't
built yet. PRs run 200 examples per property. Nightly runs 16 jobs, each with its own seed and
3,000 examples. A failing case is uploaded as an artifact with the seed and the shortest failing
sequence. Random sequences find the overdraft or double-post that nobody thought to write an
example for. Run it with `HYPOTHESIS_PROFILE=pr pytest tests/properties` (or `nightly`).

**golden.yml** (PRs and main): every report function (`trust_report_*`) must have a golden case
under `tests/golden/cases/`. Each case builds a fixed synthetic fixture and must produce exactly
its checked-in `.expected.txt`. A PR that changes an expected file fails until a maintainer adds
the `golden-update` label; the diff is in the job summary. Owners and auditors read these
reports, so a change to one has to be on purpose. No report exists yet: the owner statement,
three-way reconciliation and rent roll are tracked in issues #8, #9 and #10. Run it with
`pytest tests/golden`, and update deliberately with `pytest tests/golden --update-goldens`.

**coverage.yml** (PRs and main): Python coverage (coverage.py) and coverage of the PL/pgSQL
money logic (the plpgsql_check profiler, loaded only in this job's database). It fails when:
- overall coverage drops below the baseline in `ci/thresholds.toml`, or rises without the
  baseline being raised to match;
- lines a PR changes are under 90% covered (diff-cover, Python and SQL);
- the money functions are under 95% statement or branch coverage.

Untested money code is where shortages hide. Running it locally needs Postgres started with
`shared_preload_libraries=plpgsql_check`; `ci/postgres-coverage.Dockerfile` builds one. Then run
`PMS_SQL_COVERAGE_OUT=coverage/sql-raw.json coverage run -m pytest`, followed by
`coverage xml -o coverage/python.xml` and `python -m tools.coverage_gate --sql-raw
coverage/sql-raw.json --python-xml coverage/python.xml --sql-xml-out coverage/sql.xml`.

**mutation.yml** (nightly): mutation testing of the money logic. mutmut and Stryker only mutate
Python and JavaScript, and the ledger is PL/pgSQL, so `tools/sql_mutation.py` does the same job
for SQL. It flips comparisons, drops guards, deletes triggers and grants, one change at a time,
and checks that some test fails.
- 4 shards run in parallel; the score goes in the job summary.
- The run fails if the score drops below the baseline in `ci/thresholds.toml`.
- Survivors are listed: each one is a missing test.

Coverage shows that code ran; mutation testing shows the tests would notice if it were wrong.
Run it with `python -m tools.sql_mutation --list`, then
`python -m tools.sql_mutation --out mutation/all.json` and
`python -m tools.sql_mutation --score mutation/all.json`.

**selfhost.yml** (nightly, by hand, and on PRs touching the Dockerfile, compose file or
migrations): builds the image and runs `compose.yaml` with plain Postgres 16 and nothing from
Supabase. It applies every migration, then runs the smoke check:
- seeds a synthetic 50-door PMC;
- posts one month of rent;
- checks database health and every ledger invariant.

The owner statement and an app health endpoint aren't built yet (issues #8 and #11). This proves
a PMC could run its books on its own Postgres. Run it with `docker compose build && docker
compose up -d --wait db && docker compose run --rm migrate && docker compose run --rm smoke`.

**e2e.yml** (PRs touching UI paths): a placeholder for Playwright tests against the seeded app.
There is no UI yet, so it only runs when UI paths change, and it fails if a UI exists while the
Playwright steps are still off. Its header lists the steps to enable it in the first PR that
adds screens.

**security.yml** (PRs, main and weekly):
- CodeQL on the Python and on the workflows themselves.
- Dependency review, which fails a PR that adds a dependency with a high-severity advisory.
- gitleaks: the PR's commits on PRs, full history on main and weekly. Besides the default rules,
  `.gitleaks.toml` adds SSN- and EIN-shaped patterns.
- OpenSSF Scorecard: weekly and on main.

A leaked credential or a poisoned dependency in a trust-accounting system is a breach of client
money. Run gitleaks locally with `gitleaks git --config .gitleaks.toml --redact .`.

**licenses.yml** (PRs and main): every installed dependency, direct or transitive, must carry a
license on the allowlist in `ci/licenses.toml`: compatible with this repo's Apache-2.0, and never
non-commercial or source-available. A PMC that self-hosts needs to be able to use every piece
freely. Run it with `python -m tools.check_licenses`.

**dependabot** (`.github/dependabot.yml`): weekly updates for GitHub Actions, pip, the Dockerfiles
and the compose file.

## Required checks for branch protection on `main`

These run on every PR, so they can all be required:

| Check | Workflow |
|---|---|
| `ci` | ci.yml |
| `migrations` | db.yml |
| `properties` | ledger-invariants.yml |
| `golden` | golden.yml |
| `coverage` | coverage.yml |
| `codeql` | security.yml |
| `deps-and-secrets` | security.yml |
| `licenses` | licenses.yml |

Not required, because they don't run on every PR: `selfhost (PR)` (path-dependent) and `e2e`
(placeholder until there is a UI). Add `e2e` when it is enabled.

## Job budget

Plan: 20 concurrent jobs across the account; PR CI at most 8, nightly at most 20.
- A PR runs the 8 required checks above. The self-host check is chained after `migrations`, so a
  PR never has more than 8 jobs running at once.
- Nightly runs are staggered so they don't overlap:
  - 03:17 UTC ledger-invariants (16 jobs);
  - 04:17 mutation (4 shards, then 1 scoring job);
  - 05:17 selfhost (1);
  - Mondays 06:17 security (codeql, gitleaks history, scorecard: 3).
- A push to main runs the same checks (minus `deps-and-secrets`), plus gitleaks over full
  history and Scorecard: 9 jobs.
- A UI PR will add `e2e`. Chain it after another job then, to stay within 8.
