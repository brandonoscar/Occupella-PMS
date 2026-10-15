# CLAUDE.md

Free trust-accounting PMS for property management companies (PMCs). Built standalone now and
merged into Occupella later, so it follows Occupella's conventions from day one. Phase 0 built
the ledger schema and its tests; the app (exports, portals, leasing) now grows on top of it.
Background: AgenticHelixis `docs/decisions/ADR-free-pms-placement.md` and
`docs/research/free-pms-gaps.md`. CI: `docs/CI.md`.

## Hard rules

1. Nothing is copied from AgenticHelixis's connector code (`src/helixis/buildium`, `rentvine`,
   `rentmanager`, `propertyware`, `doorloop`, `pms`), and no vendor data ever enters this repo
   (rule 29).
2. Synthetic data only, everywhere: fixtures, logs and CI artifacts. No real PMC, owner, tenant
   or bank data, and no real names, addresses, bank numbers or tax IDs.
   `tools/check_synthetic_data.py` lists the never-issued forms to use instead.
3. The repo is public under Apache-2.0 (`LICENSE`). Every dependency's license must be on the
   allowlist in `ci/licenses.toml`; never a non-commercial or source-available one.
4. No hosted database, Supabase project, Render service or deploy. CI uses a throwaway Postgres.
5. No secrets exist in Phase 0. Never put a credential on a command line or print one. No secret
   in any workflow a PR can trigger, and never `pull_request_target`.
6. Say what you measured and what you didn't. Write NOT VERIFIED when that's the truth.

Carried over from Occupella: the AI never moves money. The API key Occupella will hold must not
be able to move money in this system either: its role, `trust_ai_agent`, can't (see below).

In scope since 2026-10-11 (founder, `docs/adr/0002-portals-and-exports.md`): exports, the owner
portal, the tenant portal and leasing basics, with the API and logins they need. Since
2026-10-12 (`docs/adr/0003-staff-back-office.md`): the staff back office for the accounting,
bank statement import, a switching tool, staff roles, move-out and documents. Since
2026-10-13 (`docs/adr/0004-email.md`): one email, to a rental owner when a bill waits for
their approval. Still out of scope: Stripe and online payments, bank files, 1099 filing, any
other email, and state rules beyond the no-negative rule. If one of these looks needed, stop
and ask the founder.

## The standing rule: tests come with the code

1. Every PR that adds or changes code adds the tests that cover it. No "tests later".
2. New money-moving or ledger logic gets a new invariant or property test
   (`tests/properties/`), not just an example test.
3. A new migration is covered by db.yml (squawk, apply from empty, down/up, drift, and an
   upgrade of a database that already holds money).
4. A new report or statement gets a golden case (`tests/golden/cases/<function>/`).
5. A new API endpoint gets API tests for the success path, auth failure and bad input, and a
   feature in `ci/features.toml` claims it with a browser test that calls it. Portal code
   (`frontend/src`) gets a vitest test next to it or a browser test in `frontend/e2e/`.
6. A new feature area that no workflow covers gets a workflow added or extended in the same PR.
7. Never weaken a check, lower a threshold or skip a test to get green. If something can't pass
   yet, open an issue and link it: in `ci/registry.toml` (`pending`), or as a strict xfail whose
   reason is the issue URL. Skips fail the run.
8. A test that depends on threads, lock waits or timeouts gets `@pytest.mark.timing`; nightly CI
   runs those 50 times. A flaky test is fixed, never retried until green.

CI enforces most of this (`docs/CI.md`): diff coverage on changed lines, `tools/check_pr_rules.py`
for tests-with-code, `tools/check_thresholds.py` for floors, `tools/check_registry.py` for every
required invariant, report and self-host step, and `tools/check_features.py` for every feature:
each API operation belongs to one, and e2e fails a feature whose browser tests stop calling it.

## Conventions

- Python 3.11, Postgres 16, plain SQL and functions. `pgcrypto` is the only extension the schema
  may use; nothing a Supabase project couldn't run. (plpgsql_check is loaded only in CI's
  coverage database, never by a migration.)
- App code is the `pms` package in `src/pms/` (Occupella keeps `src/helixis`); pytest and mypy
  find it, and a shell needs `PYTHONPATH=src`. It reads the ledger and never moves money
  except through the `trust_*` write paths.
- Exports: `src/pms/exports.py` turns any `trust_report_*` (named through the catalog, run
  under the caller's role) into CSV, Excel or PDF, and a PMC's ledger into a zip of CSV.
  Text a spreadsheet would run as a formula is never a formula.
- The API (`src/pms/api`, `python -m pms.api`, FastAPI) runs every request in one transaction
  as `trust_app`, committed before the answer is sent; a staff member's request names them to
  the audit log. Every endpoint gets tests in `tests/api/` for success, a caller who isn't
  allowed and bad input. A report's PMC always comes from the session (every report takes
  `p_pmc_id` first; a test pins it). Changes must be JSON from the app's own origin, and the
  session cookie is HttpOnly and SameSite=Strict. A refusal from the database reaches people in
  words, without internal ids (`deps.refusal`: an account short of money, a draw over what is
  available, the last admin).
- Logins (`src/pms/accounts.py`, migration 0028): staff invite a staff member, owner or tenant
  by email; the link carries a token kept only as its SHA-256, in the URL fragment. Accepting
  sets an argon2id password (and for staff an authenticator, TOTP); a new invite resets a
  password. Staff disable a login from the overview, for good: its sessions end and it takes no
  new invite. `trust_login_secrets` is never audited. The first staff member, their first admin
  role and their invite come from `python -m pms.admin`. A demo PMC to show the PMS with
  comes from `python -m pms.demo` (`src/pms/demo.py`): synthetic, built through the API's own
  route functions as its staff, with last month's reconciliation waiting for approval.
  Payments, any email but ADR 0004's and the real screening and e-sign partners are still
  out (`docs/adr/0002-portals-and-exports.md`).
- Email (`src/pms/email.py`, migration 0042, `docs/adr/0004-email.md`): entering a bill over
  the owner's approval limit writes, in the same transaction, an email to the owner's active
  portal login (`trust_emails`, once per bill) naming the bill and linking to the portal; it
  decides nothing and carries no token. After the request commits, the app hands what waits to
  the server's `PMS_EMAIL_SENDER` ("fake" keeps it in the outbox and sends it nowhere, for
  development and CI; "smtp" with `PMS_SMTP_*` and `PMS_EMAIL_FROM` from the environment, over
  STARTTLS; none by default) and records each attempt (`trust_email_deliveries`, delivered at
  most once, given up after five failures). `python -m pms.email deliver` delivers what waits.
  Staff see each bill's email and the outbox at `/bills`. Emails are not audited, and the AI
  never reads them.
- The portal (`frontend/`): React 19, Vite and Tailwind v4, following Occupella's `DESIGN.md`
  and looking like Occupella's app (founder, 2026-10-14): its light theme, a full-height
  light-blue left sidebar with the wordmark, the sections (lucide icons) and Sign out, a white
  bar above the page with the avatar, and on white each page's Fraunces title, its parts under
  small uppercase labels and its tables as Occupella's Operations table (`components/Shell.tsx`).
  Its build is served by the API (`PMS_STATIC_DIR`) under a strict Content-Security-Policy.
  Colours, sizes and fonts come only from the tokens in `frontend/src/styles/globals.css`,
  Occupella's values (`designTokens.test.ts` pins them and fails on a raw colour or a size off
  the scale). A state (paid up, bounced, approved) is a badge (`components/Badge.tsx`), a page
  with nothing to list says so with `EmptyState`, an error comes with its icon (`Notice`), and
  mono type is only for links, keys and hashes (the same test checks it). Money stays text from
  the database to the screen, never a float. Staff's pages have sections (`pages/staffNav.ts`);
  they see what is late or waits on them at `/today` (`src/pms/api/today.py`: read only, each
  part the work page's own answer), set up the books at `/setup` (`src/pms/api/setup.py`: plain inserts and the database's
  own write paths, nothing that moves money), do the rent work at `/rent`
  (`src/pms/api/rent.py`), pay bills and handle owners' money at `/bills` and `/owners`
  (`src/pms/api/payables.py`), reconcile at `/reconcile` (`src/pms/api/reconcile.py`), bring a
  PMC over from another system at `/switching` (`src/pms/api/switching.py`), see and change
  staff roles at `/staff` (`src/pms/api/staff.py`), handle move-outs at `/move-out`
  (`src/pms/api/moveout.py`), and keep documents at `/documents` (`src/pms/api/documents.py`).
  npm packages are pinned exactly and their
  licenses checked like Python's. An owner's pages (`src/pms/api/owner.py`) show the session's
  owner only: no request names an owner, a property or a PMC, and a bill named in a path is
  answered only if it is theirs (they approve or decline those over their limit). A tenant's pages
  (`src/pms/api/tenant.py`) show the session's tenant only: a lease named in a path is answered
  only if they are on it, and reads as not found otherwise. They show what each lease charges
  every month and what is charged already but due later, and once a deposit was returned at
  move-out, its statement (`trust_report_deposit_return`) to download. The portal takes no payments.
- Leasing (`src/pms/api/leasing.py`, `src/pms/leasing/`): applicants have no login. They apply
  through a link staff open for a unit (`/apply#<token>`) and follow it with a status link;
  both tokens travel in JSON bodies and are kept only as SHA-256. Screening goes through
  `pms.leasing.screening.Partner`; the server's `PMS_SCREENING_PARTNER` picks one ("fake" for
  development and CI, none by default). The PMS keeps only applicants' names and emails; the
  partner collects anything else on its own side. Lease offers (`src/pms/api/offers.py`) carry
  the PMC's own lease PDF (the PMS writes no lease text) and are signed through
  `pms.leasing.esign.Partner`, picked by `PMS_ESIGN_PARTNER` ("fake" for development and CI,
  none by default; the fake's signing page, `/esign#<token>`, is served only while it is on).
  A tenant signs a renewal of their own lease from the portal, as the signer with their
  login's email. Staff may list an open link on the PMC's public vacancy page
  (`/listings?pmc=<id>`, `src/pms/api/listings.py`, migration 0047): no login, it shows each
  listed unit's property, unit, rent and description, and an applicant applies through the
  listing as through its link until it is removed or the link closed. Nothing is syndicated.
- Every table we add carries `pmc_id uuid`. It maps to Occupella's `companies.id` at merge time.
- Table names: `pgledger_*` from upstream, `trust_*` for ours. Our functions and triggers are
  `trust_*` too. A report function is `trust_report_<name>`. `tests/test_schema_conventions.py`
  checks these against the built schema.
- A report prints the same on every server: times in UTC, and text sorted with `COLLATE "C"`
  (servers sort by language rules or by bytes; `helpers.language_sorted_database` tests both).
- Migrations use dbmate: plain SQL files in `db/migrations/`, applied ones recorded in
  `schema_migrations`. A committed migration is never edited (`tools/check_upgrade.py` fails the
  PR). Commit a new one together with `db/schema.sql` regenerated by `scripts/dump_schema.sh`;
  CI fails if it drifts.
- Work on a branch and open a PR. Every required check (docs/CI.md) must be green before merge.

## Ledger rules (Phase 0 schema)

- The first migration holds pgledger and its two ULID helper files verbatim, at the commit in
  `THIRD_PARTY_NOTICES.md`. Never edit those blocks; CI diffs them against upstream.
- `pgledger_transfers`, `pgledger_entries`, `trust_ledger_accounts`, `trust_idempotency_keys`
  and `trust_reconciliations` are append-only, enforced by triggers. Fix a mistake with a new
  reversing transfer.
- The app role `trust_app` only reads ledger tables. It writes through `trust_post_transfers`,
  `trust_open_ledger_account`, `trust_open_vendor_account`, `trust_open_lease`, `trust_end_lease`, `trust_apply_payment`, `trust_reverse_payment`,
  `trust_post_management_fee`, `trust_post_leasing_fee`, `trust_draw_owner`,
  `trust_set_aside_bill`, `trust_pay_bill`, `trust_assess_late_fees`, `trust_charge_nsf_fee`,
  `trust_post_opening_balances`, `trust_record_owner_contribution`,
  `trust_complete_lease_offer`, `trust_match_bank_lines`, `trust_unmatch_bank_lines`,
  `trust_prepare_bank_reconciliation`, `trust_approve_bank_reconciliation`,
  `trust_match_cutover_opening` and `trust_return_deposit`, which run as the table owner (`trust_charge_rent_due` and `trust_enter_recurring_bills` run with the app's own
  grants). It can't call pgledger's posting functions: every posting it makes goes through
  `trust_post_transfers`, which takes an idempotency key, unique per PMC, so it is safe to
  retry: the same key returns the original transfers, and a different posting under it is
  refused.
- `trust_ai_agent`, the role behind Occupella's API key, reads what `trust_app` reads and runs
  the reports. It holds no other grant: no write on any table, no function that writes. Grant
  it a new table's SELECT only if `trust_app` has it, and never a write path. It never reads
  logins, invites, sessions or credentials, nor rental applications, applicants and their
  screenings, nor lease offers and signatures, nor documents' files (`AI_NEVER_READS` in
  `tests/test_privileges.py`), nor their rows in the audit log: row-level security shows it only
  the audit rows of tables it may read (migration 0043).
- A new table starts with `REVOKE ALL ... FROM PUBLIC` and grants `trust_app` only what it
  needs, never DELETE. `tests/test_privileges.py` pins the exact grants of every table and
  function: a new one fails until it is listed there, with who may use it. Postgres lets PUBLIC
  call a new function unless the migration revokes it.
- Each kind of money sits in its own kind of trust bank account, for every PMC: tenant deposits
  in the `security_deposit` one, everything else held in trust in the `operating` one (each has
  its own `bank_cash`). A trigger refuses an account opened in the other kind, and a trust bank
  account never changes kind.
- Only `bank_cash` accounts may go below zero; a trigger refuses it for every other kind.
  Every transfer stays inside one PMC. No foreign key cascades into trust tables.
- Every trust bank account ties out: its book cash equals everything held in it (owners,
  prepaid rent, deposits, vendors, the PMC's fees). A deferred trigger checks it at commit, so
  held money moves to another trust bank account only with its cash, in the same transaction.
- An approved reconciliation closes its trust bank account through the period's end
  (`trust_bank_accounts.closed_through`): a transfer dated before it is refused, whoever posts
  it, so a late correction is dated in the next open period. Periods follow one another with no
  gap or overlap, and only `trust_approve_reconciliation` moves the closing date.
- What tenants owe stays out of the trust ledger. A unit has one lease at a time (a trigger
  checks every write); a lease has one or more of the property's tenants. Charges (rent, fees,
  credits) are append-only, and rent is charged at most once per lease and due date. A payment
  posted to the ledger is matched to charges with `trust_apply_payment`: never past a charge's
  amount or the transfer's, and safe to retry. A bounced payment is undone with
  `trust_reverse_payment`, against the ledger transfer that moved the money back; matches are
  never rewritten. Leases, matches and reversals are written at READ COMMITTED.
- The owner money loop: a management agreement per owner's property account, in force from a
  date (new terms are a new row). `trust_post_management_fee` takes its percent of rent and
  recurring fees collected in a period (net of bounces; recurring fees since migration 0040),
  or the minimum, plus a flat fee, into the PMC's fee account, once per period;
  `trust_post_leasing_fee` once per lease; `trust_draw_owner` pays out the balance above the
  reserve and unpaid bills, once per request key. All at READ COMMITTED.
- Vendor bills: entered once per vendor reference against an owner's property account. A bill
  over the approval limit of the agreement in force on its date (0 without one) needs the
  owner's approval recorded first. `trust_set_aside_bill` moves its amount from the owner's
  account to the vendor's (`vendor_payable`); `trust_pay_bill` then sends it out through the
  trust bank account's cash, no earlier than it was set aside. Each step once per bill, at READ
  COMMITTED. A bill takes one owner's decision (migration 0041): an approval or a decline
  (`trust_bill_declines`, with a reason), a decline only before it is set aside, and one taken
  in the owner portal only by the owner of its property account. A declined bill is never set
  aside or paid, and from the day it was declined it holds nothing back from draws and is not
  unpaid in the reports.
- Work orders: a job at a property (maybe one of its units). Its steps (assigned to a vendor,
  completed, cancelled) are append-only and in order, and nothing follows a close. A bill may
  name a work order of its property, never a cancelled one.
- Rent automation: `trust_charge_rent_due` charges every lease in effect on a due date its rent,
  once per lease and date: for the days of the month it is in effect (migration 0045: monthly
  rent x days / days in the month, to the cent), and a lease starting later in the month its
  first part month, due on its first day, unless rent was charged in that month already. It
  never reaches back into an earlier month. A lease's end set, moved or cleared after its month
  was charged (`trust_end_lease`, a renewal included; migration 0051) adds a credit, or a fee
  for days back, to each rent and recurring charge that stood at the run's amount, so the
  month comes to the days in effect (`trust_lease_end_adjustments`); a renewal in a charged
  month charges the new lease its days. The run and an end wait for each other. A property's
  late fee terms (grace days, flat, percent of unpaid,
  cap) are in force from a date; `trust_assess_late_fees` charges a rent still open at the
  end of its last grace day (UTC) one late fee under the terms in force on its due date. READ
  COMMITTED. What is open of a charge (`trust_open_charges`, migration 0049) is its amount less
  payments matched, plus what bounced, less its share of the lease's credits, which come off
  the oldest open charges first (by due date, then id), as the delinquency report counts them. A property's NSF fee is in force from a date too (migration 0044, 0 charges
  none); `trust_charge_nsf_fee` charges the lease of a returned payment (postings under one key
  moving money back to a trust bank account's cash) the fee in force the day it came back
  (UTC), due that day, once per returned payment. Neither fee counts toward the other.
- Recurring charges (migration 0037): a fee a lease owes every month besides its rent (pet
  rent, parking) or a credit it gets every month (`trust_recurring_charges`, from a first due
  date; an end row stops it from a day). `trust_charge_recurring_due` charges each one in force
  on a due date while its lease is in effect, once per recurring charge and date; the staff
  rent run calls it with `trust_charge_rent_due`. Like rent, it is charged for the days of the
  month the lease is in effect, and a lease starting later in the month is charged its first
  part month, due on its first day (migration 0048). A charge naming one is of its PMC, lease
  and kind (`trust_charges.recurring_charge_id`), and its amount for those days (a trigger
  checks it). A management fee counts recurring fees collected with rent (migration 0040);
  late fees stay on rent only.
- Cutover: `trust_post_opening_balances` opens a trust bank account with what each account in
  it held in the old system, from its cash at the start of the cutover date (UTC), checked
  against the old book cash. Once per account, before any posting or reconciliation in it, safe
  to retry; then nothing touching it is dated before the cutover, and its first reconciliation
  starts there. Tenants' unpaid charges carry over as charges with their own due dates.
- Audit: every write to a trust table or `pgledger_transfers` is logged in `trust_audit_log`
  (append-only, written only by the `trust_audit` trigger): when, the row (and the row before an
  update), the database role, and the staff member the app names with
  `set_config('trust.staff_id', ...)`, who must be on the PMC's `trust_staff`. The API names
  the signed-in staff member on every request; the server's command line names nobody. A new
  trust table gets the trigger (`tests/test_audit_log.py`). The app reads the whole log; the AI
  role only the rows of tables it may read.
- Payment holds (an eviction filed): from a hold's first day until its release, by the
  transfer's UTC date, `trust_apply_payment` refuses to match a payment to the lease's charges
  unless the PMC allowed that transfer on every hold in force. Holds, releases and allowances
  are append-only rows the app inserts; a hold's trigger takes the lease's lock so matches and holds
  wait for each other. No state's rule is built in.
- Owner contributions: `trust_record_owner_contribution` moves cash into an owner's property
  account as its own kind (`trust_owner_contributions`), once per request key, and
  `trust_apply_payment` never matches one to a tenant's charge.
- Recurring bills: a bill that comes every month (`trust_recurring_bills`: vendor, owner's
  property account, amount, day of the month 1 to 28, first day; an end row stops it).
  `trust_enter_recurring_bills` enters each month's bill through a day, under the vendor
  reference `<prefix>-YYYY-MM`, so each month once and safe to retry; a month already entered
  under that reference is left as it is. Entered bills follow the bill rules above.
- Maintenance requests: a tenant on a lease files one (`trust_maintenance_requests`, append-only;
  a key to `trust_lease_tenants` refuses anyone else). Staff open at most one work order for it,
  at the lease's unit (`trust_maintenance_request_work_orders`, one each way), and its status is
  that work order's latest step. The portal files them only under a lease in effect, at most
  10 a day per lease.
- Rental applications: an application comes through an open application link of a unit
  (closing a link and an application wait for each other); its applicants are names and
  emails. Screenings record a partner's recommendation and reasons. Each application has one
  decision, resting only on a screening of its own; a declined or conditional decision that
  rests on a screening has its adverse action notice (reasons, reporting agency, day given),
  checked at commit, and no other decision has one. All append-only.
- Lease offers: a new lease for an approved (or conditionally approved) application at the unit
  it applied for, or the renewal of a lease at its unit, starting after it; the terms, the
  PMC's PDF (at most 5 MB) and its signers (names and emails). It is sent to the e-sign
  partner once; a signature is recorded only for a sent offer's signer, as the partner reports
  it. `trust_complete_lease_offer` makes it the lease once everyone has signed, once per offer
  and safe to retry, and an application or a lease takes one completed offer: a new lease's
  signers become its tenants; a renewal keeps the lease's tenants and ends it the day before.
  READ COMMITTED; all append-only.
- Rent work (`src/pms/api/rent.py`): a tenant's payment recorded by staff pays the owner's
  account no more than the lease owes, matched to what is open of its charges oldest first
  (`trust_open_charges`, so a credit counts), and the rest is
  the first tenant's prepaid rent; prepaid rent is used the same way. Each record carries a
  request id the page makes (its key in `trust_post_transfers`), so sending it again records
  nothing new. The page lists each payment as it came in (its whole amount, prepaid rent
  included), and a bounce moves back what the payment moved, reverses its matches and charges
  the property's NSF fee, once. A
  lease's rent work takes an advisory lock on the lease. The PMS records what the bank did;
  it sends no money.
- Bills and owners' money (`src/pms/api/payables.py`): staff enter, approve, set aside and pay
  bills, run recurring bills, record owner draws and contributions, and take management and
  leasing fees, each through its `trust_*` write path. What the owners page shows as available
  is what a draw of everything takes: the balance less the reserve and the unpaid bills, which
  it counts as `trust_unpaid_bills` does (the app may not call it). Draws and contributions
  carry a request id the page makes, so a retry pays nothing twice.
- Bank statements (migration 0032): a trust bank account's statements, imported from CSV or
  OFX, follow one another day after day and balance after balance from 0 (a first statement's
  opening balance is a line of its own), each with all its lines, and a file goes in once.
  `trust_match_bank_lines` matches lines to transfers that moved the account's cash by the same
  total; a line or a transfer is in at most one match not undone (`trust_unmatch_bank_lines`),
  and neither changes a period already approved. `trust_report_bank_reconciliation` shows a
  day's end; `trust_prepare_bank_reconciliation` prepares a period only when no line up to it is
  missing from the books. All append-only. The three-way reconciliation report adds what had
  not cleared by a period's last day to its statement balance and lists it, from that day's
  bank reconciliation (migration 0050); without an imported statement of that day it says so.
- Switching (migration 0033): after a trust bank account's opening balances, the checks and
  deposits the bank had not cleared at the cutover are carried over (`trust_cutover_items`, the
  cash each moved in the old books, issued by the cutover) until a staff member signs off the
  tie-out (`trust_report_cutover`), which freezes them. `trust_match_cutover_opening` then
  matches the first statement's balance brought forward to the opening balances less those
  items; each item clears once, by a later bank line, only after that, and the opening match is
  not undone while one is cleared. A statement starts no earlier than its account's cutover.
- Staff roles (migration 0034): admin (changes roles; invites and disables staff logins),
  approve (approves a reconciliation, signs off a cutover) and disburse (pays a bill, an owner
  draw). The database refuses those writes to a named staff member without the role; one that
  names nobody is not held to them. Roles are append-only grants and revocations
  (`trust_staff_roles`): only an admin changes one, a change changes something, and a PMC keeps
  an admin. One staff member prepares month-end and another who holds approve approves it
  (`trust_approve_bank_reconciliation` takes the preparation), on the figures prepared; the app
  can't call `trust_approve_reconciliation` itself. On upgrade every staff member holds every
  role.
- Move-out (migration 0035): once a lease has ended, `trust_return_deposit` returns its
  tenants' deposits once per lease (same request key again returns the same): what the lease owes
  (`trust_lease_owed`), or all the deposits if less, is kept for the owner's account and matched
  to its charges oldest first, the cash moving to the owner's trust bank account; the rest goes
  back to each tenant from the security-deposit account. A tenant still on another lease keeps
  their deposit with it. It takes the disburse role. `trust_report_deposit_return` is the
  itemized statement for the tenants. READ COMMITTED; append-only.
- Documents (migration 0036): a vendor's W-9, a bill's receipt, a lease's move-out photo or an
  owner's document (`trust_documents`, each kind about its one kind of record of the PMC), a
  PDF, JPEG or PNG of at most 5 MB. Its bytes sit apart (`trust_document_files`, checked at
  insert against the type's first bytes, the size and SHA-256 its row says, and there at
  commit): never audited and never read by `trust_ai_agent`. A removal is a row
  (`trust_document_removals`); nothing is deleted. The PMS reads nothing out of a file. An
  owner downloads their own owner documents in the portal.
- Vendor payments: `trust_report_vendor_payments` lists what each vendor was paid through
  `trust_pay_bill` in a calendar year (UTC), for 1099 preparation. It files nothing, holds no
  tax IDs and applies no threshold. What the old system paid a vendor in the cutover's year
  before it (`trust_vendor_payments_carried_over`, migration 0038, once per vendor and year; a
  January 1 cutover carries over the year before) is added to the vendor's year, on a line of
  its own.
- Lease expirations (migration 0046): `trust_report_lease_expirations` lists the leases whose
  last day is the as-of day or one of the 90 after it, in groups of 0-30, 31-60 and 61-90 days
  left, each with its renewal status as of the end of the day (UTC): none, drafted, sent,
  signed or renewed. It runs as the owner so the AI role, which never reads lease offers, gets
  that status word and nothing else of them (founder decision, 2026-10-13). Staff download it
  from the overview.
- Owner year-end (migration 0039): `trust_report_owner_year_end` sums each of an owner's
  property accounts over a calendar year (UTC): opening balance, collected from tenants
  (payments matched to charges, less bounces), management and leasing fees, bills set aside,
  draws, contributions, anything else, closing balance; then across the owner's properties,
  and the year's 1099-MISC rents (everything collected from tenants). It files nothing, holds
  no tax IDs and applies no threshold. Owners read and download it in the portal
  (`/api/owner/year-end`), with the year's whole statement.

## Run the checks

You need Python 3.11, a throwaway Postgres 16 with trust auth (no password), and dbmate 2.36.0
on `PATH` (CI pins the same version).

```sh
docker run --rm -d -p 5432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
export DATABASE_URL="postgres://postgres@localhost:5432/pms?sslmode=disable"
python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt

scripts/check.sh      # before every push: every PR check that can run here; commit first
```

`check.sh` lists what it couldn't run here (CodeQL, dependency review, the self-host image) as
NOT RUN. Run on Postgres built from `ci/postgres-coverage.Dockerfile`, it adds the PL/pgSQL lint
and the coverage gates; on plain Postgres those are NOT RUN too. One check at a time:

```sh
ruff check . && ruff format --check . && mypy   # ci: lint, format, typecheck
pytest                                          # every test; fresh database, no skips
HYPOTHESIS_PROFILE=nightly pytest tests/properties   # property tests, nightly depth
python -m tools.check_upgrade --base origin/main     # migrations upgrade a database with money
python -m tools.check_synthetic_data                 # no real-looking personal or bank data
(cd frontend && npm ci && npm run typecheck && npm test && npm run build)   # the portal
```

Mutation testing and the rest: `docs/CI.md`.
