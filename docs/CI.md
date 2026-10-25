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

**ci.yml** (PRs and main): lint (ruff, its security rules included: each query built with an
f-string is marked where it was read), format check (ruff), typecheck (mypy, strict on `tools/`,
`selfhost/`, `scripts/` and the app in `src/`), shellcheck on `scripts/*.sh`, zizmor on the
workflows (offline, medium severity and up), the synthetic-data scan, the standing-rule checks, and every example test against a fresh database. That includes the API
tests in `tests/api/`, which drive the app through FastAPI's test client: every endpoint for
success, for a caller who isn't allowed (no session, the wrong kind of login, another PMC) and
for bad input.

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
- leaves a required invariant with neither a test nor an open issue;
- serves an API operation that no feature in `ci/features.toml` claims, or removes a feature or
  one of its browser tests (the feature registry, below).

This is the first line of defense: a ledger bug that an example test can see never reaches main.
Run it with `ruff check . && ruff format --check . && mypy && python -m
tools.check_synthetic_data && pytest --ignore=tests/properties`.

Its **email** job (after `ci`) sends the PMS's email for real: `tests/test_email_mailpit.py`
(`pytest -m mailpit`; the default run leaves these out) starts Mailpit (MIT, a mail server for
tests) with a certificate its own throwaway authority signed, and the PMS's SMTP sender hands it
an invite, a bill's approval, a staff message and two owner statements over STARTTLS. Mailpit
says what arrived: from whom, to whom, the text as written, the HTML made from it
(`src/pms/email_html.py`), nothing attached. Its HTML check then scores the HTML against what mail
programs support (caniemail.com's data), which may not fall below `ci/thresholds.toml [email]`
(90.0%; 91.0% measured). A sender that doesn't trust the server's certificate sends nothing. The
job builds Mailpit from its source at a pinned version with the Go image the self-host Dockerfile
pins, every module checked against Go's checksum database
(`tests/tools/test_container_images.py` keeps the two images the same); a download that drops
gets up to three tries, keeping the modules already fetched. Locally:
`go install github.com/axllent/mailpit@v1.31.6`, then `pytest -m mailpit`.

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
- Back up and restore (`tools/check_backup.py`, the script companies back up with,
  `scripts/backup.sh`, docs/backup.md):
  - it applies this branch's migrations to an empty database and fills it with money: the
    self-host seed (50 doors, deposits, a month of rent) and the demo company (leases, charges,
    bills, reconciliations, logins, documents' files, the audit log);
  - it dumps it with `scripts/backup.sh dump` and restores the file with `scripts/backup.sh
    restore` into a second, empty database;
  - the copy must have the same schema (`pg_dump --schema-only`: tables, functions, triggers,
    grants, policies; a CHECK constraint whose nested ANDs Postgres flattens as it restores
    counts as the same), every table and sequence row for row, and a ledger that ties out.

  `tests/tools/test_check_backup.py` runs it too, and checks the script restores through a pipe,
  onto a server without the PMS's roles (it makes them without a login, as the migrations do),
  and refuses a database that isn't empty or a ledger that doesn't tie out. Measured locally:
  241 transfers and 175 tables and sequences; the whole check, migrations included, 11 s.

Trust records are kept for years, so a migration that silently drops or rewrites ledger data is
the worst kind of change. On PRs that touch the Dockerfile, compose file or migrations, it then
runs the self-host check. Run it with:
- `squawk --config .squawk.toml db/migrations/<new>.sql`
- `dbmate --no-dump-schema up && python -m tools.check_rollback`
- `scripts/dump_schema.sh && git diff --exit-code db/schema.sql`
- `python -m tools.check_upgrade --base origin/main`
- `PYTHONPATH=src python -m tools.check_backup`

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
- every security deposit sits in the security-deposit trust account, which holds nothing else;
- the trial balance and the deposit register as of any day match the model, and so does the
  general ledger over any period: each account's opening, entries and closing balance.

Both machines also act as the AI's role (`trust_ai_agent`): it reads the same reports, and every
posting or record change it tries is refused with nothing written.

A second machine opens and ends leases, charges rent, fees and credits, and posts and matches
payments (from cash, prepaid rent, or a deposit kept with its cash), bounces some of them back,
records management agreements, takes fees and pays owners, and checks:
- two leases of one unit never overlap;
- no charge is paid past its amount (net of bounced payments) and no transfer pays more than
  it moved; a reversal undoes no more than its match; a retried match or reversal adds nothing;
- the rent roll for all time ties to every tenant's deposits and prepaid rent in the ledger, and
  the roll as of any day matches the model line by line;
- each management fee matches its agreement and the rent and recurring fees collected, once per
  period; a leasing fee once per lease; a draw never pays out more than the balance above the
  reserve, and the owner balances report as of any day matches the model.
- the rent run charges every lease in effect on its due date once; each late rent is charged one
  late fee, worked from what was unpaid when its grace ran out under the terms in force on its
  due date.
- the delinquency report matches the model line by line as of any day, and each lease's tenant
  ledger closes at its balance due.
- a payment hold refuses every match of a payment dated inside it unless the transfer was
  allowed on each hold in force; holds start on random days and on days the lease was paid.

A third machine enters vendor bills (one vendor has no account to pay from), records owners'
approvals, sets bills aside, pays them and pays owners, and checks:
- a vendor's reference is entered once; a bill is set aside at most once, with the owner's
  approval when it is over the limit in force on its date, and paid at most once, after it was
  set aside and never dated before that;
- each vendor's account holds exactly what was set aside for its bills and not yet paid;
- a draw never pays out more than the balance above the reserve and the unpaid bills, and the
  owner balances and unpaid bills reports match the model as of any day;
- recurring bills, added on random days of the month and ended on random days, enter each
  month's bill once through any day asked, never under a reference the vendor already has, and
  the entered bills follow every rule above;
- the vendor payments report for a year matches the model, vendor by vendor; bills are now and
  then paid in the next year, and the years read are mostly years with payments.

A fourth machine opens the PMC's two trust bank accounts with balances carried over from another
system (now and then a cent off, or with an account of the other trust bank account), sends
openings again unchanged and changed, posts money in and out on random dates and approves
reconciliations, and checks:
- a trust bank account opens once, before any posting or reconciliation in it; the same opening
  again returns the original transfers and a different one is refused;
- no transfer touching it is dated before its cutover, and its first reconciliation starts there;
- the trial balance as of any day matches the model line by line.

A fifth machine makes random writes (postings, a batch of two, units added and renamed, staff
added, reconciliations approved) through the owner role and trust_app, each naming a random
member of the PMC's staff, nobody, another PMC's staff member or someone on no staff, and checks:
- the PMC's audit log is the model's row for row (table, action, staff, role), and the audit
  report reads the same as any role;
- a write naming someone not on the PMC's staff is refused and nothing of it is logged;
- every transfer has exactly one audit row, and the log only grows.

The export properties (`tests/properties/test_export_properties.py`) need no database. Hypothesis
builds tables of any shape and any values (text in any script, quotes, line breaks, formula
starts and control characters, money, days, times in random zones, NULLs), and checks:
- CSV reads back cell for cell, and no text starts like a formula;
- Excel reads back the same values and types, and never holds a formula;
- the PDF always builds.

`ci/registry.toml` maps every required invariant to its test or to the open issue for what isn't
built yet. A PR can't drop an entry or one of its tests, unless a founder's decision replaced
what the entry guarded: then a `[[retired]]` row names it, the decision record in `docs/adr/` and
the entry, with tests, that replaced it (the in-house e-signing of ADR 0008 retired signing
through a partner). PRs run 200 examples per property, in two halves side by side in the one
`properties` job, dealt by the same measured times (in one run they had grown to 28 to 31
minutes and hit the job's 35-minute cap on PR #168; each half is about 19 minutes on a 4-core
container). Nightly runs 3,000 examples per property, each
property twice with a seed of its own, and the properties rotate: night n (days since 1970)
takes part n mod 4 + 1 of them, dealt into 4 shards by each test's measured PR seconds
(`ci/property-times.toml`, `PMS_TEST_ROTATION`, `PMS_TEST_SHARD_TIMES`), so 8 jobs a night and
every property's nightly run once every 4 nights. 3,000 examples cost some 50 times 200, not 15:
Hypothesis grows its examples as it goes, so the first 200 are the cheap ones (the audit property
took about 3 minutes for 200 and was some 2,100 examples into 3,000 at 100 minutes, measured
10/23). Every nightly job from 10/11 to 10/23 was cancelled at its 90-minute limit with nothing
checked (measured on GitHub). A job now stops itself at `job_minutes` with an interrupt,
so a night that runs long fails and says how far it got instead of being cancelled, and
`tests/tools/test_property_times.py` checks that each job's tests, at `nightly_factor` times
their PR seconds, fit in three quarters of that, and that every property test has its time
(`python -m tools.property_times` adds a new one's). Run one part by hand from Actions
(ledger-invariants, Run workflow, `rotation`). A failing case is
uploaded as an artifact with the seed and the shortest failing sequence. Nightly also runs every `@pytest.mark.timing` test (threads, lock waits, timeouts) 50
times on GitHub's runners. One failure fails the job, because a flaky test teaches everyone to
ignore red. Measured before adding it: 100 of 100 local runs passed, 50 of them with every CPU
core busy, and the slowest took 2.2 s against limits of 20 s and more. Random sequences find the
overdraft or double-post that nobody thought to write an example for. Run it with
`HYPOTHESIS_PROFILE=pr pytest tests/properties` (or `nightly`).

The whole API is fuzzed too (`tests/api/test_api_fuzz.py`, Schemathesis, MIT): it reads the
app's own OpenAPI schema and sends every operation its boundary cases and requests Hypothesis
makes up, valid and broken, as a signed-in staff member. None may get a server error (5xx): what
the API can't take is refused in words. Every PR sends `[fuzz] pr_examples` per operation with
the other tests (measured: about 10,400 requests in 2 min 40 s locally, none a 5xx); nightly
sends `nightly_examples` with a new seed, in 4 jobs that each take the operations a hash of
`METHOD /path` gives them (`PMS_FUZZ_SHARD`), each stopped at 50 minutes so a run too long
fails instead of being cancelled (in one job, every night from 10/11 to 10/23 was cancelled at
its hour). A route made to raise on a long name failed it within
seconds. Those counts are floors in `ci/thresholds.toml`.

The portal has its own property tests (`frontend/src/properties.test.ts`, fast-check, MIT): for
any amount up to 10^18 dollars money reads back as the cents it came as, sums are exact in any
order, days keep their parts and a list's scope survives its URL.

Nightly, sixteen workers act as staff on one company's books at once for five minutes
(`tools/contention.py`, the demo's books, synthetic): payments, bounces, bills set aside and
paid, owner draws, contributions, the company's fees taken out and rent runs, each through the
API's own route functions, on the same few leases and owners. A refusal in words is part of the
load; a deadlock, a serialization failure or any error the API would answer with a 500 fails
the job, and so do books left wrong (an account's balance not what its transfers add up to, a
trust bank account that doesn't tie out, an account other than a bank's cash below zero). The
timing tests pair two transactions; this is the many-way mix. Measured locally: 8,681
operations in 60 s, none failed, books right. Run it with `python -m tools.contention`.

Nightly too, a company lives through five years of nights (`tools/fast_forward.py`, the founder's
request of 2026-10-25): the demo's books from five years back, with everything the PMS does by
itself turned on (the monthly management fees, prepaid rent paying new charges, every email it
writes, to the outbox), a monthly bill that the nightly run enters by itself, and an email address
for every tenant and owner. Each day the nightly run runs for it, then its people act through the
API's own route functions: each lease's tenants pay by a habit of their own (on time, late, half
on time and the rest late, two months at once, or on time with one payment in four bouncing three
days later), owners are paid what is available on the 10th and the open bills are paid on the
15th (Pay bills). After each night: nothing it posted is dated after it (the part of the PMS that
reads the real clock instead of the day it runs for would show here), no rent is charged twice for
a due date, the delinquency report is what is open of the leases' charges, the trial balance
balances and each trust bank account's book cash is what it holds. On the 1st, each owner's
statement for the month before ends at their accounts' balances; by a month's end, every account
under an agreement has had the month before's management fee taken. Every choice comes from one
seed (a new one each night), and a failure prints the command that replays it. Every PR runs 13
months of it with the other tests (about a minute), and three tests that break one thing each the
way a bug would and expect the night's check to name it.
Measured 10/25 here, 4 cores shared with other work: five years (1,912 nights from 2021-08-01,
seed 1) in 12 minutes, every check passing: 378 rents, 409 payments, 149 late fees, 22 bounces,
248 management fees, 251 owner payouts, 63 bills entered by the nightly run and paid, and 772
emails written. Run it with `python -m tools.fast_forward [--months 60] [--seed N]`. What it doesn't
do yet: month-end reconciliation (no bank statements come in), leases ending and renewing, move-outs
and year closes.

Nightly too, the speed test (`tools/speed.py`, the founder's review q148) times the API as staff
use it: it builds a 1,000-unit company with a year of books (the demo's books and properties of
20 units, every unit leased, its deposit held, each month's rent charged and paid but a tenth of
this month's, all through the API's own route functions, synthetic), serves it as
`python -m pms.api` does (one process, its ten database connections shared by everyone), and
signs ten staff members in over HTTP with password and authenticator. For five minutes each
opens every page once, then at random what staff open most: the Dashboard, what needs attention,
properties, a property, the rent roll, a lease and its ledger, tenants, a search, the general
ledger, a report on screen, a payment recorded, and now and then a report as a PDF. An answer
that isn't 2xx fails the job, and so does a 95th percentile over its ceiling in
`ci/thresholds.toml` (`[speed]`: a key ending in `_max` is a ceiling, which a PR may lower and
never raise). Each staff member pauses between pages as a person reading them would (between
none and two seconds; `--pause 0` sends the next as soon as the last is answered). Measured
locally (4 cores, 2026-10-21), five minutes: 2,654 answers, every page's 95th percentile 728 ms
or less (what needs attention the slowest) and PDFs' 1,539 ms; with no pause at all, 8,079
answers, and the general ledger page (1,343 ms) and what needs attention (2,310 ms) went over.
Before this test's fixes (the planner's JIT off on the API's connections, the general ledger's
closing balances added up once, PDFs drawn without fpdf's tables, what needs attention reading
only open charges), the same company with no pause gave 318 answers in two minutes, what needs
attention 15.8 s and a PDF 113 s. Run it with `python -m tools.speed`.

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
then totals that tie back to the ledger) and `trust_report_owner_balances` (per owner's
property the balance, the reserve kept back, unpaid bills and what is available to pay the
owner) and `trust_report_unpaid_bills` (every bill not yet paid, by vendor, with what was set
aside for it and how far past due it is) and `trust_report_delinquency` (each lease that owes
money, by how long it is past due) and `trust_report_tenant_ledger` (one lease's charges,
payments and bounces with a running balance) and `trust_report_trial_balance` (per trust bank
account, its book cash against everything it holds) and `trust_report_security_deposits` (each
tenant's deposit against the deposit account's cash) and `trust_report_general_ledger` (per
ledger account over a period: its opening balance, every entry with the balance after it, and
its closing balance) and `trust_report_audit_log` (each change to the PMC's records and ledger
over a period, with who made it) and `trust_report_vendor_payments` (what each vendor was paid in
a calendar year, for 1099 preparation: the vendors marked for a 1099 against the IRS's threshold
for the year; it files nothing) and
`trust_report_income_statement` (an owner's income and expenses over days, cash basis, per
property and across them: collected by what it paid, the fees, each expense account's bills, net
income, and the Schedule E lines; `trust_report_income_statement_all_owners` the same across every
owner, each property named with its owner) and `trust_report_balance_sheet` (each trust bank account's
cash against what it holds, by kind) and `trust_report_cash_flow` (each trust bank account's cash
over days, from opening to closing, by what came in and went out) and `trust_report_vacancy`
(units without a lease, since when, and occupancy) and `trust_report_work_orders` (each work
order's status, vendor, days open and what was billed) and `trust_report_tenant_directory`,
`trust_report_owner_directory` and `trust_report_vendor_directory` (who each is to the company
on a day) and `trust_report_budget_vs_actual` (an owner's budget beside their income statement,
line by line) and `trust_report_vendor_insurance` (each vendor's coverage on a day and how it
stands) and `trust_report_inspection` (a lease's move-in and move-out inspections side by side).
Run it with
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

Untested money code is where shortages hide.

The suite runs in two shards side by side, to take half the time: after the lint, the job
`coverage tests 1/2` runs half the tests (`PMS_TEST_SHARD=1/2`, see `tests/conftest.py`) and
`coverage tests 2/2` the rest, each with its own Python and PL/pgSQL coverage data. The property
tests, the slow ones, are dealt to the two by their times in `ci/property-times.toml`
(`PMS_TEST_SHARD_TIMES`), longest first to the shard with less so far; every other test goes
to them in turn. Then the `coverage` job adds them up (`coverage combine`, and
`tools/sql_coverage.py` `merge()` for the PL/pgSQL) and applies the gates. A shard's PL/pgSQL
branches are counted from its statements, the way plpgsql_check counts them, and checked against
plpgsql_check's own figure for every function before the shards are added up, so the sum is
exactly what one run of the whole suite would measure. `coverage` is the required check: it runs
even when the lint or a shard failed, and fails then.

Running it locally needs Postgres started with `shared_preload_libraries=plpgsql_check`;
`ci/postgres-coverage.Dockerfile` builds one. `scripts/check.sh` then runs the same steps as the
workflow, the two shards one after the other.

**mutation.yml** (nightly): mutation testing of the money logic. mutmut and Stryker only mutate
Python and JavaScript, and the ledger is PL/pgSQL, so `tools/sql_mutation.py` does the same job
for SQL. It flips comparisons, drops guards, deletes triggers and grants, one change at a time,
and checks that some test fails.
- Each mutant meets the tests that call the ledger directly first (`tests/test_*.py`), then the
  API's, the reports' and the property tests; `-x` stops at the first failure. In the old order
  (alphabetical: the API's first, the property tests before the direct ones) two mutants took
  211 s and 196 s; first they take 17 s and 26 s.
- All 2,058 mutants take some 40 to 50 runner-hours: a sample of 10 took 0.5 s (doesn't apply) to
  342 s (caught only by an API test), 84 s on average. So each night runs a fifth of them, over
  8 shards (every 40th from the kth, k = 8 x (days since 1970 mod 5) + the shard), and every
  mutant runs every 5 nights. Until 10/18 every shard ran a quarter of them all, and every
  nightly run from 10/15 to 10/18 was cancelled at its 60-minute limit with nothing scored.
- A mutant's tests may run 45 minutes; one that hangs past that counts as killed. The limit
  was 600 s until 10/19, when the suite (some 13 minutes on GitHub) had long outgrown it: every
  mutant no test kills ran to the limit and was scored killed. Deleting a table's
  `REVOKE ALL ... FROM PUBLIC` is no mutant at all (Postgres grants PUBLIC nothing on a table, a
  test checks it); a function's still is, and the catalog's tests (`test_privileges.py`) meet a
  deleted grant first.
- The score goes in the job summary; the run fails if it drops below the baseline in
  `ci/thresholds.toml`. Survivors are listed: each one is a missing test.

Python is mutated too (`python mutants`, mutmut, BSD-3-Clause): the modules
`pyproject.toml [tool.mutmut]` names (bank statement files, exports, logins), each mutant run
against the tests that call the function it changed. `tools/python_mutation.py` scores the run
against `[mutation] python_baseline_score`: a mutant no test reaches counts as not caught. Many
survivors are equivalent (the case of a SQL keyword, which Postgres ignores); the rest are
missing tests, listed in the summary.

Coverage shows that code ran; mutation testing shows the tests would notice if it were wrong.
Run it with `python -m tools.sql_mutation --list`, then
`python -m tools.sql_mutation --out mutation/all.json` and
`python -m tools.sql_mutation --score mutation/all.json`; the Python side with
`mutmut run`, `mutmut export-cicd-stats` and
`python -m tools.python_mutation mutants/mutmut-cicd-stats.json`.

**selfhost.yml** (nightly, by hand, and on PRs touching the Dockerfile, compose file,
requirements, migrations or `src/`): builds the image, lists what it holds (Syft, an SBOM kept as
the `sbom` artifact) and fails on a known high or critical vulnerability in it that has a fix
(Grype; both built from pinned source versions), then runs `compose.yaml` with plain Postgres 16
and nothing from Supabase. Its first run found the dbmate binary's Go and x/net and x/crypto
(critical and high) and the base image's setuptools and wheel (high): the image now builds dbmate
2.37.0 from source with Go 1.27.2 and drops setuptools and wheel, which only install packages. It applies every migration, then runs the smoke check:
- seeds a synthetic 50-door PMC;
- posts one month of rent;
- runs one owner statement for that month and checks it against the owner's ledgers;
- exports that statement as a PDF and a spreadsheet, and the whole ledger as a zip that must
  hold every transfer;
- checks database health and every ledger invariant;
- starts the API (`python -m pms.api`) and asks its health endpoint.

This proves
a PMC could run its books on its own Postgres. Run it with `docker compose build && docker
compose up -d --wait db && docker compose run --rm migrate && docker compose run --rm smoke`.

**e2e.yml** (every PR and push to main, called by licenses.yml): the portal (`frontend/`), end
to end. Inside the pinned Node image it typechecks, lints, unit-tests (vitest) and builds the
React app. The lint is oxlint (MIT; `frontend/.oxlintrc.json`: every rule it counts as
correctness, React, accessibility and vitest among them, the few off saying why) and knip (ISC;
`frontend/knip.json`: no unused file, export or dependency). Then the API serves that build on a fresh Postgres (emails go to the fake sender's outbox,
`PMS_EMAIL_SENDER=fake`, addresses are found on a map by the fake geocoder, which calls nothing,
`PMS_GEOCODER=fake`, tenants pay online through the fake payments partner, whose page's buttons
say what the bank did and which moves no money, `PMS_PAYMENTS=fake`, and the nightly rent run is
off, `PMS_NIGHTLY=off`, so rent is charged
only when a test runs it), `frontend/e2e/seed.py` makes a synthetic PMC
with an owner who has statements and a tenant with rent due and a maintenance request, and
Playwright, in its pinned image, drives Chromium (`frontend/e2e/portal.spec.ts`): the owner
reads a statement, the tenant reads what is due and asks for a repair, staff enroll an
authenticator, invite someone and take a request through to done, and an applicant applies
through a link that staff screen (with the fake partner, `PMS_SCREENING_PARTNER=fake`) and
decline with a notice, and staff offer a lease and a renewal that the applicant and the tenant
sign on the PMS's own signing page (the tenant asks for paper first, then signs through a new
link) before staff make each the lease, and staff set up a property from nothing, lease a unit and find it on the rent roll, and
record a tenant's payment (the rest held as prepaid rent) and then its bounce, and pay a
vendor's bill (after the owner's approval) and draw what is left for its owner, charge a
tenant's damage at move-out and return the rest of their deposit with its statement, and import
last month's bank statement, match it to the books and prepare the reconciliation for another
staff member, who may approve, to approve, and bring a
roster in, open an account with its old balances and a check not cleared, sign off the tie-out
and match the opening, and keep an owner's document that the owner then downloads. The seed
also builds the demo PMC (`src/pms/demo.py`), whose manager approves the month-end it opens on
and sees on the Dashboard what else waits, and whose owner reads three properties.
`frontend/e2e/features.spec.ts` then takes every feature through the browser, each test on a
demo PMC's books of its own (`demo.books`): the rent run and late fees, a lease's
fees, credits, monthly charges, payments, prepaid rent, deposit and bounce, bills the owner
approves or declines in the portal, recurring bills, management and leasing fees and draws,
staff roles and disabled logins, the owner's statement and year-end package, the tenant's lease
and ledger, a mid-month move-out, a listing on the public vacancy page, setup, every report in
every format, documents, and vendor payments carried over. On failure it keeps the Playwright
report and the API's log for 7 days. After every browser test, axe-core checks the page it
ended on against WCAG 2.2 A and AA (`frontend/e2e/a11y.ts`), and one test walks every page of a
demo of its own (staff's menu and Settings, a page of each kind a list opens, an owner's and a
tenant's portal and the public pages) the same way; a violation fails the run, naming the rule
and the elements. `frontend/e2e/phone.spec.ts` opens the owner's and the tenant's portal on a
phone's screen (390 x 844, touch): the menu folds behind its button, nothing runs off the side of
the screen outside a table's own sideways scroll, axe checks each page, a tenant files a repair
request, and a screenshot of each page is kept with the run (q124). Run it locally with `cd frontend && npm ci && npm test &&
npm run build`, then the same steps as the workflow (`PW_CHROMIUM` points Playwright at a
Chromium already installed).

The feature registry (`ci/features.toml`, `tools/check_features.py`) keeps every verified
feature verified. Each feature says what it does, names the API operations it rests on and the
browser tests that use it as a person would. The ci job fails when the API serves an operation
no feature claims (the API's own OpenAPI schema, with the fake partners on, says what it serves;
`[outside]` lists the health check, with why), when a feature names an operation that doesn't
exist, and on a PR when a feature is removed or ends with fewer browser tests than on the base.
After the browser tests, the e2e job checks the run itself: every browser test a feature names
exists and every browser test belongs to a feature (Playwright's own `--list`), every operation
a feature claims was called during the run, and no request was answered with a server error
(the API's access log). So a new endpoint can't land without a browser test that calls it, and
a page that stops calling its endpoint, or an endpoint that starts failing, turns e2e red. Run
the first half with `python -m tools.check_features --base origin/main`.

**security.yml** (PRs, main and weekly):
- CodeQL (security-extended) on the Python, the portal's TypeScript and the workflows themselves.
- Dependency review, which fails a PR that adds a dependency with a high-severity advisory.
- gitleaks: the PR's commits on PRs, full history on main and weekly. Besides the default rules,
  `.gitleaks.toml` adds SSN- and EIN-shaped patterns. They let through only the never-issued
  forms the synthetic-data scan accepts, and a test keeps the two in step.
- pip-audit: the pinned Python dependencies against known vulnerabilities, weekly and on main,
  so an advisory published after a dependency merged turns up.
- OpenSSF Scorecard: weekly and on main.

A leaked credential or a poisoned dependency in a trust-accounting system is a breach of client
money. Run gitleaks locally with `gitleaks git --config .gitleaks.toml --redact .`.

**daily-scan.yml** (daily at 07:17 UTC on main, by hand, and on a PR that changes it): what was
published since main last changed, every morning, in one job whose every step reports even when
one before it failed:
- gitleaks over every commit (security.yml's version, checksum and config);
- pip-audit on `requirements.txt` and `requirements-dev.txt` (security.yml's pinned version);
- `npm audit --omit=dev` on the portal's runtime packages, in the Node image the portal is built
  with (e2e.yml's);
- Grype on the self-host image, through Syft's list of what it holds: a known high or critical
  vulnerability with a fix fails it (selfhost.yml's pinned versions and command).

`tests/tools/test_daily_scan.py` keeps each version and image the same as the workflow it comes
from, and fails on `continue-on-error`, a write permission or a secret. Its first local run
(2026-10-21) found pypdf 6.20.0's three advisories (CVE-2026-113786, -113792, -113793; a test
dependency): pypdf is now 6.21.1, and pip-audit, npm audit and gitleaks over 199 commits find
nothing. Grype ran only in CI: the image doesn't build in that sandbox (NOT VERIFIED locally).
Run the parts locally with the commands above, `cd frontend && npm audit --omit=dev`, and
`docker compose build`, then syft and grype as in the workflow.

**licenses.yml** (PRs and main): every installed dependency, direct or transitive, must carry a
license on the allowlist in `ci/licenses.toml`: compatible with this repo's Apache-2.0, and never
non-commercial or source-available. A PMC that self-hosts needs to be able to use every piece
freely. It reads the Python packages installed and, from `frontend/package-lock.json`, every
npm package the portal ships or builds with. Run it with `python -m tools.check_licenses`.

**dependabot** (`.github/dependabot.yml`): weekly updates for GitHub Actions, pip, npm (the
portal, one grouped pull request), the Dockerfiles and the compose file. pip and Actions updates come as one grouped pull request each, because
separate pull requests for neighbouring lines of `requirements-dev.txt` conflicted with each other
as soon as one merged.

## Before you push: `scripts/check.sh`

One command for every PR check that can run locally, in CI's order:
- the ci, licenses and db checks, with the database ones on a throwaway database;
- the portal's typecheck, lint, unit tests and build, if `npm` is installed;
- the upgrade check, and the backup and restore check;
- the PL/pgSQL lint, if the server has plpgsql_check;
- every test, with the coverage gates and diff-cover if plpgsql_check is preloaded (then in
  coverage.yml's two shards, one after the other);
- the email tests through Mailpit, if `mailpit` is installed (or `$MAILPIT` names it);
- a short soak of the nightly many-staff run (8 workers for 15 seconds, about 20 s);
- a short run of the nightly speed test (3 staff on 100 units for 15 seconds, about 30 s);
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
| `email (Mailpit)` | ci.yml, after `ci` |
| `migrations` | db.yml |
| `properties` | ledger-invariants.yml |
| `golden` | golden.yml |
| `coverage` | coverage.yml (it fails unless its lint and both test shards passed) |
| `codeql` | security.yml |
| `deps-and-secrets` | security.yml |
| `licenses` | licenses.yml |
| `e2e / e2e` | e2e.yml, called by licenses.yml |

Not required, because they don't run on every PR: `selfhost (PR) / selfhost` and `daily scan`
(path-dependent). docs/release.md gives the settings and the clicks; branch protection on
`main` is off (GitHub's API, 2026-10-21) until an admin turns it on.

## Job budget

Plan: 20 concurrent jobs across the account; PR CI at most 8, nightly at most 20.
- A PR runs the 10 required checks above. `e2e` is chained after `licenses` (a job of seconds),
  `email (Mailpit)` after `ci` (about 15 minutes), the self-host check after `migrations`, and
  coverage's two test shards after its lint (a job of about a minute, by when
  `deps-and-secrets`, a job of seconds, has finished), so a PR never has more than 8 jobs
  running at once.
- Nightly runs are staggered, though the property jobs now run into the later ones' hours:
  - 03:17 UTC ledger-invariants (8 property jobs of up to 4.5 hours; 4 API fuzz jobs, timing,
    many-staff, fast-forward and speed, all done within the hour: 16 at first, 8 after 04:17).
    The property jobs overlap mutation (2 to 3 hours from 04:17, measured 10/21 to 10/23), so up
    to 18 jobs run at once, and on a Monday the security jobs can bring that to 22: GitHub queues what is
    past 20 until one ends, and a PR's checks then wait too;
  - 04:17 mutation (8 SQL shards and 1 Python job, then 1 scoring job);
  - 05:17 selfhost (1);
  - Mondays 06:17 security (codeql, gitleaks history, pip-audit, scorecard: 4);
  - 07:17 daily-scan (1).
- A push to main runs the same checks (minus `deps-and-secrets`), plus gitleaks over full
  history, pip-audit and Scorecard: 11 jobs.
- A new PR job is chained after another one, as `e2e` is, to stay within 8. The daily scan's one
  job runs on a PR only when the PR changes `daily-scan.yml`.
