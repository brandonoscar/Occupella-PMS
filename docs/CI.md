# CI

What each workflow checks, why it matters for the money a PMC holds in trust, and how to run it
yourself. Every workflow uses only synthetic data, needs no secret beyond GitHub's automatic
read-only token, and pins every action to a full commit SHA. The local commands assume the setup
in `CLAUDE.md` ("Run the checks"): Postgres 16 in `DATABASE_URL`, dbmate on `PATH`, and
`pip install -r requirements-dev.txt`. `scripts/check.sh` runs all of the PR checks it can in
one go (see "Before you push" below).

Images (Postgres for the jobs, the bases of `Dockerfile` and `ci/postgres-coverage.Dockerfile`)
come from `mirror.gcr.io/library/`, Google's mirror of Docker Hub, at the same pinned digests.
Docker Hub caps anonymous pulls per IP address, and GitHub's runners share addresses: on
2026-10-09 that cap failed five checks before any test ran. `tests/tools/test_container_images.py`
fails on an image pulled from anywhere else.

## The workflows

**ci.yml** (PRs and main): lint (ruff), format check (ruff), typecheck (mypy, strict on `tools/`,
`selfhost/` and `scripts/`), shellcheck on `scripts/*.sh`, the synthetic-data scan, the
standing-rule checks, and every example test against a fresh database.

The synthetic-data scan (`tools/check_synthetic_data.py`) reads every tracked file for values
shaped like real records: routing numbers that pass the ABA checksum with a real Fed prefix,
SSNs, ITINs and EINs that could be issued, emails outside the reserved domains, and US phone
numbers outside 555-0100 to 555-0199. It accepts only never-issued forms, reports file and line
but never the value (the logs are public), and takes exceptions only in `ci/synthetic_data.toml`
with a reason. Real names and street addresses have no shape to match; review them.

The standing-rule checks fail a PR that:
- changes code without changing tests;
- changes ledger SQL without a property test;
- lowers any number in `ci/thresholds.toml`;
- leaves a required invariant with neither a test nor an open issue.

This is the first line of defense: a ledger bug that an example test can see never reaches main.
Run it with `ruff check . && ruff format --check . && mypy && python -m
tools.check_synthetic_data && pytest --ignore=tests/properties`.

**db.yml** (PRs and main): checks the migrations.
- squawk lints new or changed migrations. A destructive change (drop, rename, type change) needs
  an explicit `-- squawk-ignore <rule>` line right above it.
- Every migration is applied to an empty database.
- Each migration's down section is run where one exists. Ledger migrations must refuse to roll
  back and leave the schema untouched.
- The dumped schema must equal `db/schema.sql`.
- The pgledger code must match upstream byte for byte.
- Upgrade with data (`tools/check_upgrade.py`):
  - it fails if a migration the base branch already has was edited or deleted;
  - it builds the base branch's schema and fills it with the base branch's own self-host seed:
    a 50-door PMC, deposits and a month of rent;
  - it applies this branch's migrations on top. Ledger history, and every account's balance and
    version, must be unchanged;
  - this branch's smoke check then runs on the upgraded database.

  The empty-database check misses migrations that only fail once rows exist, such as a NOT NULL
  column with no default or a CHECK constraint that existing rows break.

Trust records are kept for years, so a migration that silently drops or rewrites ledger data is
the worst kind of change. On PRs that touch the Dockerfile, compose file or migrations, it then
runs the self-host check. Run it with:
- `squawk --config .squawk.toml db/migrations/<new>.sql`
- `dbmate --no-dump-schema up && python -m tools.check_rollback`
- `scripts/dump_schema.sh && git diff --exit-code db/schema.sql`
- `python -m tools.check_upgrade --base origin/main`

**ledger-invariants.yml** (PRs, main and nightly): property-based tests with Hypothesis. They
throw random sequences of postings, batches, reversals, cross-PMC attempts, history rewrites and
reconciliations at the ledger, on a synthetic clock with some postings dated back into closed
periods, and check the money invariants after every step:
- debits equal credits;
- no held account below zero;
- in each trust bank account, book cash equals what is held for others;
- history is append-only;
- running balances chain;
- an idempotency key posts once, and is refused for a different posting;
- an approved reconciliation never changes, nothing dated inside its period lands after it,
  and periods follow one another;
- every security deposit sits in the security-deposit trust account, which holds nothing else.

A second machine opens and ends leases, charges rent, fees and credits, and posts and matches
payments (from cash, prepaid rent, or a deposit kept with its cash), bounces some of them back,
and checks:
- two leases of one unit never overlap;
- no charge is paid past its amount (net of bounced payments) and no transfer pays more than
  it moved; a reversal undoes no more than its match; a retried match or reversal adds nothing;
- the rent roll for all time ties to every tenant's deposits and prepaid rent in the ledger, and
  the roll as of any day matches the model line by line.

`ci/registry.toml` maps every required invariant to its test or to the open issue for what isn't
built yet. PRs run 200 examples per property. Nightly runs 16 jobs, each with its own seed and
3,000 examples. A failing case is uploaded as an artifact with the seed and the shortest failing
sequence. Nightly also runs every `@pytest.mark.timing` test (threads, lock waits, timeouts) 50
times on GitHub's runners. One failure fails the job, because a flaky test teaches everyone to
ignore red. Measured before adding it: 100 of 100 local runs passed, 50 of them with every CPU
core busy, and the slowest took 2.2 s against limits of 20 s and more. Random sequences find the
overdraft or double-post that nobody thought to write an example for. Run it with
`HYPOTHESIS_PROFILE=pr pytest tests/properties` (or `nightly`).

**golden.yml** (PRs and main): every report function (`trust_report_*`) must have a golden case
under `tests/golden/cases/`. Each case builds a fixed synthetic fixture and must produce exactly
its checked-in `.expected.txt`. A PR that changes an expected file fails until a maintainer adds
the `golden-update` label; the diff is in the job summary. Owners and auditors read these
reports, so a change to one has to be on purpose. Reports so far:
`trust_report_three_way_reconciliation` (bank statement, trust journal and beneficiary ledgers of
one approved reconciliation, with every difference listed) and `trust_report_owner_statement`
(per property an opening balance, each posting in date order with the balance after it and a
closing balance, then the owner's totals) and `trust_report_rent_roll` (per unit as of the end
of a day: the lease, its tenants, deposits and prepaid rent held, charged, paid and balance due,
then totals that tie back to the ledger). Run it with
`pytest tests/golden`, and update deliberately with `pytest tests/golden --update-goldens`.

**coverage.yml** (PRs and main): Python coverage (coverage.py) and coverage of the PL/pgSQL
money logic (the plpgsql_check profiler, loaded only in this job's database).

First it lints our PL/pgSQL (`tools/sql_lint.py`): plpgsql_check reads every statement of every
`trust_*` function without running it, including each trigger function against each of its
tables, with every warning class on. Any finding fails the job. PL/pgSQL is only parsed when it
runs, so a misspelled column or a wrong type on a branch no test reaches would otherwise ship.
The pgledger and ULID functions are upstream and verbatim, so they are listed but not linted.

Then it measures coverage, and fails when:
- overall coverage drops below the baseline in `ci/thresholds.toml`, or rises without the
  baseline being raised to match;
- lines a PR changes are under 90% covered (diff-cover, Python and SQL);
- the money functions are under 95% statement or branch coverage.

Untested money code is where shortages hide. Running it locally needs Postgres started with
`shared_preload_libraries=plpgsql_check`; `ci/postgres-coverage.Dockerfile` builds one. Then run
`python -m tools.sql_lint`, then
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
- runs one owner statement for that month and checks it against the owner's ledgers;
- checks database health and every ledger invariant.

An app health endpoint isn't built yet (issue #11). This proves
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
  `.gitleaks.toml` adds SSN- and EIN-shaped patterns. They let through only the never-issued
  forms the synthetic-data scan accepts, and a test keeps the two in step.
- OpenSSF Scorecard: weekly and on main.

A leaked credential or a poisoned dependency in a trust-accounting system is a breach of client
money. Run gitleaks locally with `gitleaks git --config .gitleaks.toml --redact .`.

**licenses.yml** (PRs and main): every installed dependency, direct or transitive, must carry a
license on the allowlist in `ci/licenses.toml`: compatible with this repo's Apache-2.0, and never
non-commercial or source-available. A PMC that self-hosts needs to be able to use every piece
freely. Run it with `python -m tools.check_licenses`.

**dependabot** (`.github/dependabot.yml`): weekly updates for GitHub Actions, pip, the Dockerfiles
and the compose file. pip and Actions updates come as one grouped pull request each, because
separate pull requests for neighbouring lines of `requirements-dev.txt` conflicted with each other
as soon as one merged.

## Before you push: `scripts/check.sh`

One command for every PR check that can run locally, in CI's order:
- the ci, licenses and db checks, with the database ones on a throwaway database;
- the upgrade check;
- the PL/pgSQL lint, if the server has plpgsql_check;
- every test, with the coverage gates and diff-cover if plpgsql_check is preloaded;
- gitleaks, if installed.

It ends with a summary. A check it couldn't run is listed as NOT RUN, never as passed: CodeQL and
dependency review are GitHub-only, and the self-host image needs Docker. It compares against the
merge base with `origin/main` (or `$BASE`), so commit first. `tests/tools/test_check_script.py`
fails if a PR workflow runs a command that `check.sh` doesn't.

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
  - 03:17 UTC ledger-invariants (16 property jobs and 1 timing job);
  - 04:17 mutation (4 shards, then 1 scoring job);
  - 05:17 selfhost (1);
  - Mondays 06:17 security (codeql, gitleaks history, scorecard: 3).
- A push to main runs the same checks (minus `deps-and-secrets`), plus gitleaks over full
  history and Scorecard: 9 jobs.
- A UI PR will add `e2e`. Chain it after another job then, to stay within 8.
