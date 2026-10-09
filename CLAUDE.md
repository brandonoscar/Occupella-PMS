# CLAUDE.md

Free trust-accounting PMS for property management companies (PMCs). Built standalone now and
merged into Occupella later, so it follows Occupella's conventions from day one. Phase 0 is the
ledger schema and its tests only. Background: AgenticHelixis
`docs/decisions/ADR-free-pms-placement.md` and `docs/research/free-pms-gaps.md`.

## Hard rules

1. Nothing is copied from AgenticHelixis's connector code (`src/helixis/buildium`, `rentvine`,
   `rentmanager`, `propertyware`, `doorloop`, `pms`), and no vendor data ever enters this repo
   (rule 29).
2. Synthetic fixtures only. No real names, addresses, bank numbers or tax IDs.
3. No LICENSE file. The repo stays private.
4. No hosted database, Supabase project, Render service or deploy. CI uses a throwaway Postgres.
5. No secrets exist in Phase 0. Never put a credential on a command line or print one.
6. Say what you measured and what you didn't. Write NOT VERIFIED when that's the truth.

Carried over from Occupella: the AI never moves money. The API key Occupella will hold must not
be able to move money in this system either.

Out of scope for Phase 0: any API, UI, login, Stripe, bank files, 1099s, and state rules beyond
the no-negative rule. If one of these looks needed, stop and ask the founder.

## Conventions

- Python 3.11, Postgres 16, plain SQL and functions. `pgcrypto` is the only extension allowed.
  Nothing a Supabase project couldn't run.
- Every table we add carries `pmc_id uuid`. It maps to Occupella's `companies.id` at merge time.
- Table names: `pgledger_*` from upstream, `trust_*` for ours. Our functions and triggers are
  `trust_*` too.
- Migrations use dbmate: plain SQL files in `db/migrations/`, applied ones recorded in
  `schema_migrations`. `db/schema.sql` is dumped by dbmate and checked in; CI fails if it drifts.
- A committed migration is never edited. Change the schema with a new migration, and commit it
  together with the regenerated `db/schema.sql`.
- Work on a branch and open a PR. CI must be green before merge.

## Run the tests

You need Python 3.11, a throwaway Postgres 16 with trust auth (no password), and dbmate 2.36.0
on `PATH` (CI pins the same version).

```sh
docker run --rm -d -p 5432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
export DATABASE_URL="postgres://postgres@localhost:5432/pms?sslmode=disable"

python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                  # fresh database per run, every migration applied
dbmate up                               # migrate $DATABASE_URL and rewrite db/schema.sql
git diff --exit-code db/schema.sql      # what CI's migrate job checks
ruff check . && ruff format --check .   # what CI's lint job checks
```

`pytest` creates a new database on the server in `DATABASE_URL`, applies every migration with
dbmate, runs the tests and drops the database. A skipped test fails the run.
