# CLAUDE.md

Free trust-accounting PMS for property management companies (PMCs). Built standalone now and
merged into Occupella later, so it follows Occupella's conventions from day one. Phase 0 built
the ledger schema and its tests; the app (exports, portals, leasing) now grows on top of it.
Background: AgenticHelixis `docs/decisions/ADR-free-pms-placement.md` and
`docs/research/free-pms-gaps.md`. CI: `docs/CI.md`. The law each feature follows, the floor
every state shares and the strictest state found: `docs/law.md` (founder, 2026-10-15).

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
their approval; since 2026-10-17 (`docs/adr/0007-email.md`) invites, password resets, what
staff write to tenants, owners and vendors, and the emails the PMS writes by itself once a company
turns them on. Since 2026-10-20 (`docs/adr/0008-esign-in-house.md`): leases signed on the
PMS's own page instead of an outside e-sign partner. Since 2026-10-15
(`docs/adr/0005-property-management-pages.md`): pages built
around properties, units, leases, tenants, owners and vendors, with paged lists, edits (never
deletes: archive) and contact details the AI never reads. Since 2026-10-16
(`docs/adr/0006-buildium-punch-list.md`): the founder's punch list, built in its order (property
groups first; rent, recurring charges, late fees and recurring bills to post by themselves each
night; fees still move only when staff ask, unless a company turns on taking its monthly
management fees by itself, the founder's amendment of 2026-10-18). Still out of scope: Stripe and online payments, text messages, bank files (ACH, check
printing), 1099 filing, and state rules beyond the no-negative rule. If one of these looks needed, stop
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
  `p_pmc_id` first; a test pins it). Changes must be JSON from the app's own origin (a browser's
  Sec-Fetch-Site of another site refuses one whatever its Origin), the session cookie is HttpOnly
  and SameSite=Strict, and every answer forbids being framed or opened by another site (COOP),
  the camera and the like, and over https anything but https (HSTS). A refusal from the database
  reaches people in words, without internal ids (`deps.refusal`: an account short of money, a
  draw over what is available, the last admin).
- Logins (`src/pms/accounts.py`, migration 0028): staff invite a staff member, owner or tenant
  by email; the link carries a token kept only as its SHA-256, in the URL fragment. Accepting
  sets an argon2id password (and for staff an authenticator, TOTP); a new invite resets a
  password. Staff invite from the person's own page (staff from the staff page) and disable a
  login from Settings, Logins, for good: its sessions end and it takes no new invite. `trust_login_secrets` is never audited. The first staff member, their first admin
  role and their invite come from `python -m pms.admin`. A demo PMC to show the PMS with
  comes from `python -m pms.demo` (`src/pms/demo.py`): synthetic, built through the API's own
  route functions as its staff, with last month's reconciliation waiting for approval and
  something on every other page, the founder's seed among it (six properties, one of 48 units,
  two groups, applicants, offers, bills, work orders and files at every step; ADR 0006).
  `demo.books` is its books alone, for the feature tests.
  Payments and a real screening partner are still out
  (`docs/adr/0002-portals-and-exports.md`).
- Email (`src/pms/email.py`, migration 0042, `docs/adr/0004-email.md`): entering a bill over
  the owner's approval limit writes, in the same transaction, an email to the owner's active
  portal login (`trust_emails`, once per bill) naming the bill and linking to the portal; it
  decides nothing and carries no token. After the request commits, the app hands what waits to
  the server's `PMS_EMAIL_SENDER` ("fake" keeps it in the outbox and sends it nowhere, for
  development and CI; "smtp" with `PMS_SMTP_*` and `PMS_EMAIL_FROM` from the environment, over
  STARTTLS; none by default) and records each attempt (`trust_email_deliveries`, delivered at
  most once, given up after five failures). `python -m pms.email deliver` delivers what waits.
  Staff see each bill's email, and whether it went, on the bill's page (`/bills/<id>`). Emails
  are not audited, and the AI never reads them. Since migration 0076 (`docs/adr/0007-email.md`)
  the outbox also carries portal invites (emailed when the server sends email; the page still
  shows the link once), password resets an owner or a tenant asks for at sign-in (`/forgot-password`,
  `POST /api/password-reset`: the same answer whether or not the email has a login, three an hour
  at most; staff reset through an admin's invite, keeping their authenticator) and what staff
  write (`src/pms/api/emails.py`): a tenant's, owner's or vendor's Email tab
  (`components/EmailsCard.tsx`) writes to them and lists every email the PMS wrote them with
  whether it went, and a property's Email tenants writes to each tenant living there today. An
  email goes to the person's contact email, else their login's, its Reply-To the company's own
  email when Company setup has one (`trust_company_profiles.reply_to`, migration 0096; q086). An invite's or a reset's link is
  never written to the outbox: it waits in the writing process's memory until sent, ten minutes at
  most, then the email is given up on once. Writing email is the custom roles' area 'email'.
  Each email goes as its text and as HTML made from it when sent (`src/pms/email_html.py`, one
  Jinja2 template, `src/pms/templates/email.html`, in the portal's tokens): the text escaped, its
  web addresses links, its figures a table, one button for the kinds that ask one thing, and the
  company's name and mailing address; the outbox keeps the text alone. `pytest -m mailpit`
  (ci.yml's email job; left out of the default run) sends each kind to Mailpit over STARTTLS and
  checks what arrived, and Mailpit's HTML check against `ci/thresholds.toml [email]`.
  Since migration 0077 the PMS also writes emails by itself (`src/pms/notices.py`), each one a
  company turns on in Company setup (`trust_email_settings`: append-only, the latest row holding,
  naming who changed it; `/api/emails/settings`, area 'email'), and only while the server sends
  email (`outbox.SENDING`; the nightly run's `sending`): a rent reminder to each tenant of a lease
  in effect on its next due day (the first unless it picked another), from 1 to 15 days before it; a late notice to each tenant when the
  run charges a late fee; last month's statement in brief to each owner with a property account,
  from a day of the month (1 to 28); a vendor's work order when assigned or scheduled; and the
  adverse action notice to each applicant when a decision rests on a screening (the PDF's text,
  shown on the applicant's page). Each names what it is about and is written once (a unique index
  per kind); a lease under a payment hold gets no reminder or late notice. The nightly run and an
  admin's Run rent now write the first three, after the run, in its transaction.
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
  the database to the screen, never a float. On screen a day reads MM/DD/YYYY (`usDate` in
  `money.ts`; inputs, links and the API keep YYYY-MM-DD, and a report's own text reads as the
  report wrote it) and money in accounting form, a negative in parentheses; a lease's term reads
  `11/01/2025 – Month to month`. Money leaving trust (a draw, a bill paid, the company's fees
  taken out, a deposit returned) waits for staff to confirm a question naming the amount
  (`components/Confirm.tsx`), and a row's risky actions (It bounced, Archive...) sit in its ⋯ menu
  (`components/RowMenu.tsx`), each confirmed. What a save did shows as a toast
  (`components/Said.tsx`, which keeps an off-screen status line; `components/Toaster.tsx`), a
  refused change's reason too (`api.post`), and a form's submit button is busy while its save runs
  (`components/Form.tsx`). A list page has no search box of its own: its top left picks All
  properties, a property group or one property (`components/ListBar.tsx`), kept in the URL
  (`?group=`, `?property=`) and the browser so it carries from page to page (`scope.ts`); the API
  narrows a list to it with `src/pms/api/scope.py`. Staff make groups at `/settings/groups`
  (`src/pms/api/groups.py`). Staff's menu (`pages/staffNav.ts`, ADR 0005)
  groups their pages as Buildium does. They open on the Dashboard at `/`: the company's figures
  (`src/pms/api/dashboard.py`, each counted by its report) and what needs attention
  (`src/pms/api/today.py`: read only, each part the work page's own answer). Rentals
  (`src/pms/api/rentals.py`): properties a page at a time at `/properties`, each at
  `/properties/<id>` (its owners' money, its units and their leases), a unit at `/units/<id>`
  (every lease it had), the rent roll at `/rent-roll` (every lease a page at a time, current,
  to come, ended or owing, with days left, rent, deposits and balance; Receive payment, and an
  admin's Run rent now) and a lease at `/leases/<id>` (ADR 0006: its balance due on top, Receive payment, Enter
  charge and the ⋯ menu's rent work in side panels, `src/pms/api/rent.py`; tabs Summary, Ledger
  with the running balance of `trust_report_tenant_ledger`'s lines, Tenants with their portal
  logins, Files and Notes); staff rename a
  property or unit there and archive it. Tenants at `/tenants` and owners at `/owners`
  (`src/pms/api/directory.py`), each on a page of their own: a tenant's leases and the money
  held for them; an owner's tabs (ADR 0006): Summary (contact, portal login, the agreements'
  terms), Properties, Financials (their statement and Record contribution), Statements and Files.
  The Owners list shows each owner once with what they hold, keep back and may pay out, and opens
  Pay owners and Add owner. Staff keep a tenant's, owner's or vendor's contact details there, rename a tenant or owner
  and archive one. A search in the bar finds a property, unit, tenant or owner (`/api/search`).
  Lists come a page at a time (`Paged`: 50 by default, at most 200); move-outs at `/move-out` (`src/pms/api/moveout.py`). Leasing:
  Listings at `/rental-listings` (`GET /api/leasing/vacancies`: each unit coming free within 90
  days or with an open link, its rent and available day, days listed and photos; List opens its
  link if it has none and puts it on the public page, Unlist takes it off, Photos keeps the unit's
  listing photos), Leads at `/leads` (migration 0088, `src/pms/api/leads.py`: Open, Closed, All;
  Add lead; each at `/leads/<id>` with its showings, Schedule showing and Close lead), Applicants at
  `/applicants` (Open, Rejected,
  Withdrawn, All) with each application's page at `/applicants/<id>` (screen, approve or reject,
  record a withdrawal, Create draft lease), Draft leases at `/draft-leases` (Waiting for
  signatures, Leases made, Void, All; New draft lease in a side panel) with each at
  `/draft-leases/<id>` (Send, Resend, Activate lease, Void; each signer's progress and the signing certificate), the leases ending in the next 90 days with
  their renewal status at `/renewals` (`src/pms/api/renewals.py`, the expirations report's rows). Accounting:
  the trust bank accounts at `/banking` (Add bank account), each at `/bank-accounts/<id>` in tabs:
  its register and balance breakdown (`src/pms/api/banking.py`), its deposits, its month-end
  (`src/pms/api/reconcile.py`) and its statements, bills at `/bills` (Unpaid, Paid,
  Recurring and Declined tabs, a page at a time by group, vendor and due day; Record bill, maybe
  split across expense accounts or shared across properties, with the owner's markup and its
  invoice, opens a side panel), a bill at
  `/bills/<id>` (its lines, files, email and history; set aside and pay), Pay bills at
  `/pay-bills` (several at once, all or none, each property's cash shown), the general ledger on
  screen at `/general-ledger` (`src/pms/api/general_ledger.py`: trust_report_general_ledger's
  rows by account, a page of accounts at a time; cash basis) with its Journal entry button to
  `/journal` (`src/pms/api/journal.py`), Pay owners at `/owner-draws` (several owners at once, all
  or none, recorded as sent by check or EFT), management fees at `/management-fees` (each
  agreement's fee for a period, worked out and rolled back, then Collect fees), budgets at
  `/budgets` (`src/pms/api/budgets.py`: a property's year in one grid, saved at once, or beside
  what happened). The company's fees leave trust from Banking (an
  operating account's ⋯ menu), a lease's leasing fee from the lease's ⋯ menu.
  Maintenance: work orders a page at a time at `/work-orders` (ADR 0006: numbered, narrowed by
  status and vendor), each at `/work-orders/<id>` (`src/pms/api/work_orders.py`: its details,
  tenants to call, photos, timeline, bills and request; Next step, Edit details and Create bill
  in side panels; staff open one there too, its status the work orders report's word), tenants'
  requests below them, vendors a page at a time at `/vendors` (ADR 0006: trade, phone, email, W-9,
  insurance; Add vendor in a side panel), each at `/vendors/<id>` in tabs: Summary (trade, 1099,
  how paid, contact), Insurance (policies, Add policy), Bills, Work orders and Files
  (`src/pms/api/vendors.py`). Files at `/files` (`src/pms/api/documents.py`: every file a page
  at a time, newest first, by category and property group; Upload in a side panel), and a
  property's, unit's, tenant's, lease's, owner's or vendor's page has a Files card of its own
  documents (`pages/FilesCard.tsx`); every report at
  `/reports` in groups, each at `/reports/<name>` (ADR 0006: its filters, its rows on screen from
  `format=json`, narrowed to a property group or property when its lines have one by
  `src/pms/api/reports.py`, and Export), and Settings at `/settings`: staff roles and signers at `/settings/staff`
  (`src/pms/api/staff.py`; an admin invites a staff member from their row), logins at
  `/settings/logins` (read only, by status; Disable in a row's menu), the company at
  `/settings/setup` (`src/pms/api/company.py`: its name, address, logo and the fees new
  properties start with), expense accounts at `/settings/accounts` (Chart of accounts). The books'
  setup (`src/pms/api/setup.py`: plain inserts and the database's own write paths, nothing that
  moves money) opens in side panels where each thing is used (`pages/SetupPanels.tsx`, ADR
  0006): a property from Properties; a unit, another owner, management terms and late and NSF
  fee terms on the property's page (`GET /api/properties/<id>` gives the terms in force); a
  lease already in place on a unit's page; terms on the owner's page too. An owner or tenant is
  invited from their own page (`pages/PortalCard.tsx`). Bringing a PMC over from another system at `/settings/import`
  (`src/pms/api/switching.py`), and the whole ledger at `/settings/data-export`. A lease's inspections are on its move-out row
  (`src/pms/api/inspections.py`).
  npm packages are pinned exactly and their
  licenses checked like Python's. An owner's pages (`src/pms/api/owner.py`) show the session's
  owner only: no request names an owner, a property or a PMC, and a bill named in a path is
  answered only if it is theirs (they approve or decline those over their limit). A tenant's pages
  (`src/pms/api/tenant.py`) show the session's tenant only: a lease named in a path is answered
  only if they are on it, and reads as not found otherwise. They show what each lease charges
  every month and what is charged already but due later, and once a deposit was returned at
  move-out, its statement (`trust_report_deposit_return`) to download; and a lease's files (q120):
  the lease as signed, its signing certificate when signed on the PMS's page, and the lease
  documents staff keep on its Files tab (move-out photos stay with staff). Both portals show the
  manager's name, mailing address and logo (`/api/company/card`). The portal takes no payments.
- Leasing (`src/pms/api/leasing.py`, `src/pms/leasing/`): applicants have no login. They apply
  through a link staff open for a unit (`/apply#<token>`) and follow it with a status link;
  both tokens travel in JSON bodies and are kept only as SHA-256. Screening goes through
  `pms.leasing.screening.Partner`; the server's `PMS_SCREENING_PARTNER` picks one ("fake" for
  development and CI, none by default). The PMS keeps only applicants' names and emails; the
  partner collects anything else on its own side. Lease offers (`src/pms/api/offers.py`) carry
  the PMC's own lease PDF (the PMS writes no lease text) and are signed on the PMS's own signing
  page (ADR 0008, migration 0097, `src/pms/api/signing.py`, `pms.leasing.esign`; it replaced the
  fake partner): each signer's link (`/sign#<token>`, kept as its SHA-256, 30 days, the token in
  JSON bodies) shows the ESIGN consumer disclosures to agree to, or "I'd rather sign on paper";
  then the lease to open; then a typed name, maybe a drawn signature (`SignaturePad`), on the
  lease's SHA-256. Each step keeps the address and browser it came from, the signature is
  recorded as it is made, and the signer and staff download its signing certificate
  (`pms.leasing.certificate`, a PDF; no digital seal). Staff make new links (Resend, which keeps
  the old ones) and see each signer's progress.
  Since migration 0090 (q081), sending an offer while the server sends email emails each signer
  their own signing link (`signing_request`, once per signer; the link held in memory like an
  invite's, never in the outbox), and Send says whom.
  A tenant signs a renewal of their own lease from the portal, as the signer with their
  login's email. Staff may list an open link on the PMC's public vacancy page
  (`/listings?pmc=<id>`, `src/pms/api/listings.py`, migration 0047): no login, it shows each
  listed unit's property, unit, rent and description, and an applicant applies through the
  listing as through its link until it is removed or the link closed. Nothing is syndicated.
  Since migration 0087 a listed unit shows its photos there (`listing_photo` documents about the
  unit: JPEG or PNG, twelve at most, oldest first), each served to anyone by
  `GET /api/listings/<pmc>/photos/<id>` only while the unit is listed and the photo not removed;
  staff keep them from Listings (`GET /api/leasing/units/<id>/photos`) or the unit's Files.
- Calendar feeds (migration 0098, `src/pms/api/calendar.py`, `icalendar`): a staff member
  (Settings, Your calendar, maybe for one property group) or a tenant (the portal's Calendar
  card, `pages/CalendarCard.tsx`) makes a link of their own, `/api/calendar/<token>.ics`, that
  a calendar app fetches with no login (`trust_calendar_feeds`, the token kept as its SHA-256
  and shown once; `trust_calendar_feed_revocations`; append-only). Staff's shows move-ins,
  leases ending, visits (with their window), open work orders due, showings, bills due (not
  declined), rent changes, renters and vendor insurance running out and the day to take the
  company's fees out of trust; a tenant's their rent due days, their requests' visits and their
  lease's last day. Events name homes and work, never a tenant's contact details; each keeps
  its UID. A new link takes the old back, one person's links wait for each other, and a link
  taken back or whose login is disabled reads as not found. The access log prints the token as
  "…". It is no custom-role area (each person's own); the AI role reads neither table.
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
  `trust_post_management_fee`, `trust_post_leasing_fee`, `trust_take_owner_fee`, `trust_draw_owner`, `trust_withdraw_pmc_fees`,
  `trust_set_aside_bill`, `trust_pay_bill`, `trust_assess_late_fees`, `trust_charge_nsf_fee`,
  `trust_post_opening_balances`, `trust_record_owner_contribution`,
  `trust_complete_lease_offer`, `trust_match_bank_lines`, `trust_unmatch_bank_lines`,
  `trust_prepare_bank_reconciliation`, `trust_approve_bank_reconciliation`,
  `trust_match_cutover_opening`, `trust_return_deposit`, `trust_transfer_lease`, `trust_open_deposit_account`, `trust_propose_journal_entry`,
  `trust_post_journal_entry`, `trust_decline_journal_entry`, `trust_apply_assistance_payment` and
  `trust_bill_back_utility`, which run as the table owner (`trust_charge_rent_due`, `trust_enter_recurring_bills` and
  `trust_run_rent` run with the app's own grants). It can't call pgledger's posting functions: every posting it makes goes through
  `trust_post_transfers`, which takes an idempotency key, unique per PMC, so it is safe to
  retry: the same key returns the original transfers, and a different posting under it is
  refused.
- `trust_ai_agent`, the role behind Occupella's API key, reads what `trust_app` reads and runs
  the reports. It holds no other grant: no write on any table, no function that writes. Grant
  it a new table's SELECT only if `trust_app` has it, and never a write path. It never reads
  logins, invites, sessions or credentials, nor rental applications, applicants and their
  screenings, nor lease offers and signatures, nor documents' files, nor contact details, nor notes,
  nor vendors' links, nor leads and showings, nor screening criteria (`AI_NEVER_READS` in
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
- Owners' fees (migration 0082, the founder's review q049 to q051): a company may have the
  nightly run take last month's management fees by itself from a day of the month
  (`trust_management_fee_settings`: append-only, the latest row holding, naming who changed it;
  Settings, Company setup, `/api/payables/management-fees/automatic`, area owners; off by
  default). `trust_take_monthly_management_fees` takes each owner's property account's fee
  through `trust_post_management_fee`, once per account and month, leaving one it can't take
  (the owner short, terms from mid-month, a period taken by hand) for staff and the next night.
  Staff who hold disburse take other fees from an owner (`trust_take_owner_fee`,
  `trust_owner_fees`: an inspection, lease-up or other fee, with what it is for, once per request
  key, from the owner's account to the company's fee account in its trust bank account; the
  owner's Financials tab, Take a fee). The income statement and the year-end package count a
  lease-up fee with the leasing fees and the others with the management fees.
- The company's fees out of trust (migration 0059, `docs/law.md`): earned fees left in trust are
  commingling, which every state forbids. `trust_withdraw_pmc_fees` pays a trust bank account's
  fee account out through its cash to the company, never more than it holds, once per request
  key, by staff who hold disburse (`trust_pmc_fee_withdrawals`, append-only). The owners page and
  the Dashboard show what each holds, since when the oldest waited and the day to take it out by, 25
  days after (California's deadline, the strictest found). No account holds the broker's own
  money in trust.
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
- Shared bills and markups (migration 0083, `docs/law.md`): one vendor invoice shared across
  properties (`trust_shared_bills`, `trust_shared_bill_parts`; `POST /api/payables/shared-bills`)
  is a bill for each owner's property account's share, under the invoice's reference with /1,
  /2..., each following every bill rule on its own; at commit the shares (2 to 20, each a
  different account, of the invoice's vendor, with one date, due day and memo) come to the
  invoice, and a vendor's reference is one bill or one shared invoice, never both. An agreement's
  `bill_markup_percent` (0: none) is the markup the company may add to a bill; staff add it to a
  bill before it is set aside (`trust_bill_markups`: the agreement's percent on the bill's date,
  to the cent, worked out by the database, once, never on a declined bill), and
  `trust_set_aside_bill` takes it with the bill's money into the company's fee account
  (`trust_bill_markup_fees`), both or neither. Draws hold a bill back with its markup until it is
  set aside; the income statement and year-end count it with the management fees; the owner sees
  it in the approval email, the portal and their statement.
- Ownership shares (migration 0094, the founder's review q044, `docs/law.md`): a property's
  owners' percents from a day (`trust_ownership_shares` and its lines: each above 0 to four
  decimals, an owner's account at the property, together 100 and in one trust bank account at
  commit; append-only, naming who; the latest from a day not after it holding,
  `trust_ownership_on`). From that day a payment, prepaid rent used (`src/pms/api/rent.py`) and a
  deposit kept (`trust_return_deposit`) go to the owners by their shares, and a bill may be split
  by ownership (`by_ownership` on `POST /api/payables/shared-bills`, a bill for each share):
  `trust_split_by_ownership` rounds each percent down to the cent and gives the cents left over
  to the largest remainders (the account first by id, `COLLATE "C"`), so the parts come to the
  amount. Staff set it on the property's page (Ownership, Set ownership; area setup). Without
  shares staff choose the owner's account, as before; a lease moved from keeps the account its
  move names. The AI role reads them.
- Housing assistance (migration 0095, the founder's review q034, `docs/law.md`): a PMC's housing
  authorities (`trust_housing_authorities`) and a lease's assistance from the first of a month
  (`trust_rent_assistance`: what the authority pays of each month's rent, at most the rent, 0
  ends it; after the last month charged; append-only, naming who; `trust_assistance_on`). As rent
  is charged a trigger splits it: the authority's part (`trust_assistance_part`, to the cent,
  prorated as the rent) becomes its charge (`trust_assistance_charges`, once per lease and month)
  and the tenant is charged the rest, so the tenant's late fees, deposit and balance count their
  part alone (24 CFR 982.310(b)). `trust_end_lease` moves both parts; a renewal keeps the
  assistance in force on its first day. Staff charge or credit an authority by hand (never past
  what is open). `trust_apply_assistance_payment` matches an authority's payment (cash into the
  property's owners' accounts, never a tenant's payment or a contribution; a transfer pays a
  tenant's charges or an authority's, never both) to its charges; the management fee, income
  statement and year-end count it as rent. Rentals, Housing authorities
  (`/housing-authorities`, `src/pms/api/assistance.py`, area rent) records its payment for many
  leases at once; the lease's Summary shows the split. No payment hold or bounce applies to it.
  The AI role reads all four tables.
- Utility billing (migration 0096, the founder's review q021, `docs/law.md`): a property's plan
  for each utility from a day (`trust_utility_plans`: by unit, square feet, occupants or submeter,
  and the owner's percent for the common areas; 'none' stops it), units' square feet
  (`trust_unit_areas`) and which utilities a lease is billed for from a day
  (`trust_lease_utilities`), all append-only, the latest holding, naming who.
  `trust_bill_back_utility` bills a utility bill for a period back once (no two of a property's
  bills for a utility cover a day; maybe naming the vendor's bill of the property): by the plan
  in force on its first day (`trust_utility_allocation`, which a preview reads), the owner's
  share comes off and each billed lease's share of the rest is rounded down to the cent and
  charged to it as a fee (`trust_utility_shares`), so tenants are never charged more than the
  bill; a vacant unit's days, a lease not billed and every part of a cent stay with the owner.
  Accounting, Utility billing (`/utility-billing`, `src/pms/api/utilities.py`, area rent). The
  AI role reads all five tables.
- Work orders: a job at a property (maybe one of its units). Its steps (assigned to a vendor,
  scheduled for a day, completed, cancelled) are append-only and in order, and nothing follows a
  close. A bill may name a work order of its property, never a cancelled one. Migration 0072: a
  visit is scheduled only once a vendor is assigned; a vendor whose insurance ran out (every
  policy of theirs started by the day ended before it) is not assigned, while one with none on
  file is; its details (priority, what is wrong, the day due, whether the vendor may let
  themselves in) are append-only rows of `trust_work_order_details`, the latest holding, naming
  who recorded them; its photos are documents (`work_order_photo`).
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
  end of its last grace day (UTC) one late fee under the terms in force on its due date, reaching
  its rents in the order their grace ends so one run charges what a run every day would (a late
  fee is an open charge too; migration 0056). READ COMMITTED. What is open of a charge (`trust_open_charges`, migration 0049) is its amount less
  payments matched, plus what bounced, less its share of the lease's credits, which come off
  its rent first, as the delinquency report counts them (Rent first, below).
  A waiver (migration 0079, `trust_charge_waivers`; staff waive from an open charge's ⋯ menu on
  the lease page, `POST /api/rent/charges/<id>/waive`, area rent) is a credit linked to the charge
  it waives, with why, naming who: it comes off that charge first, never past what is left of it
  once payments and earlier waivers count (its trigger takes the charge's lock, as a match does);
  `trust_apply_payment` never matches past what was paid and waived, and `trust_return_deposit`
  keeps nothing for what was waived. A property's NSF fee is in force from a date too (migration 0044, 0 charges
  none); `trust_charge_nsf_fee` charges the lease of a returned payment (postings under one key
  moving money back to a trust bank account's cash) the fee in force the day it came back
  (UTC), due that day, once per returned payment. Neither fee counts toward the other.
- Rent changes (migration 0061, `docs/law.md`): a lease's new monthly rent from the first of a
  month (`trust_rent_changes`, one per lease and month, append-only, naming who recorded it),
  after the lease starts and within it, with written notice given by the first of the month
  before and how it was served; a lease with an end date names the term that allows it, and a
  month already charged keeps its rent. `trust_rent_on` is the rent in force on a day: the rent
  run charges it, `trust_end_lease` adjusts at it, the rent roll, lease expirations, tenant
  directory and vacancy reports show it. Staff record one on the rent page; the tenant sees it
  coming in the portal.
- Rent due day (migration 0081, the founder's review q013): a lease's rent is due on the first
  unless it picks another day, 1 to 28, from the first of a month (`trust_rent_due_days`,
  append-only, one per lease and month, naming who recorded it), within the lease's months and
  after the last month whose rent is charged. `trust_rent_due_on` is its due date in a month,
  kept within the days the lease is in effect then. Leases due on the first are charged as
  before; `trust_charge_rent_on_due_days` and `trust_charge_recurring_on_due_days` charge the
  rest on their due date, the month's rent and recurring charges for the days in effect, once a
  month, and `trust_run_rent` calls all four. Late fees count their grace from the due date.
  Staff pick it in Add lease and from the lease's ⋯ menu (`POST /api/rent/leases/<id>/due-day`);
  a renewal keeps the due day of the lease it renews from its first month not charged; the
  tenant sees it in the portal.
- Renters insurance (migration 0062, `docs/law.md`): a lease's terms say whether it requires
  renters insurance and the least liability it asks, from a day (`trust_renters_insurance_terms`);
  a policy (`trust_renters_insurance`) names any insurer the tenant chose, its number, its days,
  its coverage, recorded by staff or by a tenant on the lease from the portal. Append-only; the
  PMS picks no insurer and charges nothing. `trust_report_renters_insurance` gives each lease in
  effect on a day: the policy that runs out last of those started, its standing and whether it is
  below the minimum; the rent page, the portal and the Dashboard read it.
- Rent first (migration 0093, `docs/law.md`, the founder 2026-10-18): a credit, a tenant's
  payment, prepaid rent used and a deposit kept pay a lease's rent before its other charges: by
  `trust_pay_rank` (0 rent, 1 a monthly charge: a recurring fee, or a lease end's fee for days back
  of either, 2 anything else), rent and monthly charges first, oldest first (rent before a monthly
  charge due the same day), then the rest oldest first, a tie by when charged, then id.
  `trust_open_charges`, the delinquency report, `trust_return_deposit` and the rent page
  (`PAY_ORDER` in `src/pms/api/rent.py`) all follow it, so a late fee no longer turns on random
  ids; a moved tenant's payment and deposit pay the rent of every lease they moved through before
  any of their fees. Lists still show charges by due date.
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
  wait for each other. No state's rule is built in. Staff place one from the lease page's ⋯ menu
  (one not released at a time), release it from a day (naming who did) and accept a payment past
  it on Receive payment, which allows that payment on every hold in force, naming who accepted it,
  in the same transaction (`src/pms/api/rent.py`).
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
  10 a day per lease. Since migration 0084 (`docs/law.md`) a tenant marks one an emergency and
  files up to three photos with it (`request_photo` documents about it); its work order opens
  urgent with the tenant's word on entry copied, and each staff member on call
  (`trust_on_call_staff`: append-only, the latest row holding, naming who changed it; Work orders,
  On call, `/api/maintenance/on-call`, area maintenance) is emailed at their login's email while
  the server sends email (`emergency_request`, once per request and staff member; no texts), the
  work order naming whom. A schedule step may name a visit window ("8-10 am",
  `trust_work_order_steps.visit_window`), which the tenant sees with the day and the vendor's email
  gives; work orders count their days open. Since migration 0085 a tenant is charged for a work
  order (a repair they caused) by a fee naming it (`trust_charges.work_order_id`, only a fee, only
  a lease at its unit or property; the work order's Charge the tenant, through
  `POST /api/rent/leases/<id>/charges`), and a job comes back by itself
  (`trust_recurring_work_orders`: every 1 to 12 months on a day 1 to 28 from a day, maybe with a
  vendor and a priority; an end row stops it; append-only, naming who), opened once per day by
  `trust_open_recurring_work_orders`, which the nightly run and adding one call (Work orders,
  Recurring; `src/pms/api/recurring_work_orders.py`, area maintenance).
  Since migration 0086 a vendor has no login but a link staff make from the vendor's Work orders
  tab (`src/pms/api/vendor_links.py`; `trust_vendor_links`, the token kept only as its SHA-256 and
  shown once, a new one taking back the old; `trust_vendor_link_revocations`, Take back; both
  append-only, naming who, area setup). With it (`/vendor#<token>`, the token in JSON bodies) the
  vendor sees the work orders last assigned to them and still open (where, what, priority, the day
  due, entry, the visit; no tenant's contact details), schedules a visit (a day, maybe a window) or
  says the work is done with a note: a step naming the link (`vendor_link_id`), which a trigger
  allows only for those two steps, by the vendor last assigned, while the link stands. The
  timeline marks it "(by the vendor)". The AI role reads neither table.
- Leads and showings (migration 0088, the founder's review q083): a prospect who asked about
  renting before applying (`trust_leads`: a name, an email or a phone, maybe a property and one of
  its units, a source, a move-in day, what they asked), showings of a unit at a time, maybe by a
  staff member (`trust_showings`), how each went once (`trust_showing_outcomes`: shown, no-show,
  cancelled) and the lead closed once (`trust_lead_closings`: applied or lost, why). A closed lead
  takes no showing (a trigger, the closing and showings waiting for each other). All append-only,
  audited, naming who; area leasing. Nothing records what a fair housing law protects, and the AI
  role reads none of it.
- Rental applications: an application comes through an open application link of a unit
  (closing a link and an application wait for each other); its applicants are names and
  emails. Screenings record a partner's recommendation and reasons. Each application has one
  decision, resting only on a screening of its own; a declined or conditional decision that
  rests on a screening has its adverse action notice (reasons, reporting agency, day given),
  checked at commit, and no other decision has one. All append-only. An applicant's withdrawal
  (migration 0070, `trust_application_withdrawals`, once, naming who recorded it) comes before
  a decline or a lease made of it; after it the application takes no screening, decision or
  offer, and none of its offers becomes a lease (each waits for a withdrawal under way).
  Screening criteria (migration 0089, the founder's review q085, `src/pms/api/screening_criteria.py`):
  a company's criteria (`trust_screening_criteria`: the income, as times the rent, approved and
  approved on conditions, and the criteria in words; append-only, the latest row holding, naming
  who; Company setup, area leasing), which the partner applies (`screening.Criteria`; without
  any, `screening.DEFAULT`, 3 and 2 times), each screening naming them (`criteria_id`), and the
  apply page shows every applicant in words. A decision against the recommendation of its
  application's latest screening says why (`override_reason`, a trigger; no other decision
  does), and the applicant's page marks it. The applicant's status page shows the adverse action
  notice (q080).
- Lease offers: a new lease for an approved (or conditionally approved) application at the unit
  it applied for, or the renewal of a lease at its unit, starting after it; the terms, the
  PMC's PDF (at most 5 MB) and its signers (names and emails). It is sent to sign once; a
  signature is recorded only for a sent offer's signer. Since migration 0097 (ADR 0008) an offer
  sent to sign here ('PMS e-sign') is signed only through a signer's link that still signs
  (`trust_signing_link_open`: made in the last 30 days, the offer neither void nor a lease, the
  signer not signed), after they agreed to sign electronically (`trust_signing_events`, the
  disclosures' words kept) and opened the lease, never after asking for paper through it; the
  signature (`trust_signature_evidence`: their name as on the offer, the lease's SHA-256, a drawn
  PNG of at most 100 KB if any, the address and browser, the database's own time) writes their
  `trust_signatures` row, which such an offer takes no other way. A void and a signature wait
  for each other. `trust_complete_lease_offer` makes it the lease once everyone has signed, once per offer
  and safe to retry, and an application or a lease takes one completed offer: a new lease's
  signers become its tenants; a renewal keeps the lease's tenants and ends it the day before.
  READ COMMITTED; all append-only. Since migration 0071 an offer may be kept as a draft (no
  signature request until staff send it), asks a security deposit (`security_deposit`; asked,
  not held) and lists up to ten monthly fees or credits (`trust_lease_offer_charges`, written
  only before it is sent) that become the lease's recurring charges from its first day when it
  completes; staff void an offer that is not a lease yet (`trust_lease_offer_voids`, once,
  naming who and why), after which it is never sent, signed or completed (a void and a
  completion wait for each other), the tenant no longer sees it, and the expirations report
  leaves it out. The AI role reads none of it.
- Rent work (`src/pms/api/rent.py`): a tenant's payment recorded by staff pays the owner's
  account no more than the lease owes, matched to what is open of its charges rent first
  (`trust_open_charges`, so a credit counts), and the rest is
  the first tenant's prepaid rent; prepaid rent is used the same way. Each record carries a
  request id the page makes (its key in `trust_post_transfers`), so sending it again records
  nothing new. The page lists each payment as it came in (its whole amount, prepaid rent
  included), and a bounce moves back what the payment moved, reverses its matches and charges
  the property's NSF fee, once. A
  lease's rent work takes an advisory lock on the lease. The PMS records what the bank did;
  it sends no money. A payment came by check, cashier's check, money order, EFT or cash. Each has
  a receipt (`src/pms/api/receipts.py`): a PDF saying who paid, for which home, when, how much and
  how, what it paid of each charge, what was held as prepaid rent and, once it bounced, that it
  came back, with the company's name and address; staff print it from the lease's Ledger tab and
  the tenant downloads their own in the portal (another's reads as not found).
- Bills and owners' money (`src/pms/api/payables.py`): staff enter, approve, set aside and pay
  bills, run recurring bills, record owner draws and contributions, and take management and
  leasing fees, each through its `trust_*` write path. What the owners page shows as available
  is what a draw of everything takes: the balance less the reserve and the unpaid bills, which
  it counts as `trust_unpaid_bills` does (the app may not call it). Draws and contributions
  carry a request id the page makes, so a retry pays nothing twice; Pay owners keys each draw by
  its run and account. How a payout left trust (`trust_payout_methods`, migration 0068: check or
  EFT, once per owner draw or bill paid of the PMC, append-only) describes money already moved.
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
  Bank deposits (migration 0078, `src/pms/api/bank_deposits.py`, the account's Deposits tab):
  what staff take to the bank together. Each item is a transfer that brought money into the
  deposit's account by its day (a payment's whole, prepaid rent too), in at most one deposit,
  checked by a trigger; append-only, naming who recorded it. A deposit moves no money; it has a
  slip (PDF), and at month-end auto-match pairs its one bank line with all of it (and ticking one
  of its transfers ticks the deposit).
- Switching (migration 0033): after a trust bank account's opening balances, the checks and
  deposits the bank had not cleared at the cutover are carried over (`trust_cutover_items`, the
  cash each moved in the old books, issued by the cutover) until a staff member signs off the
  tie-out (`trust_report_cutover`), which freezes them. `trust_match_cutover_opening` then
  matches the first statement's balance brought forward to the opening balances less those
  items; each item clears once, by a later bank line, only after that, and the opening match is
  not undone while one is cleared. A statement starts no earlier than its account's cutover.
  The vendors' bills the old system had not paid come over from a CSV file as ordinary bills
  with their old references and dates, once, so draws hold them back from the first day; one
  over the owner's limit keeps the old system's approval or waits for the owner
  (`src/pms/api/switching.py`, `docs/law.md`).
- Staff roles (migration 0034): admin (changes roles; invites and disables staff logins),
  approve (approves a reconciliation, signs off a cutover, posts a journal entry) and disburse
  (pays a bill, an owner draw). The database refuses those writes to a named staff member
  without the role; one that names nobody is not held to them. Roles are append-only grants and
  revocations (`trust_staff_roles`): only an admin changes one, a change changes something, and
  a PMC keeps an admin. One staff member prepares month-end and another who holds approve approves it
  (`trust_approve_bank_reconciliation` takes the preparation), on the figures prepared; the app
  can't call `trust_approve_reconciliation` itself. On upgrade every staff member holds every
  role.
- Trust account signers (migration 0060, `docs/law.md`): an admin names a trust bank account's
  broker, and the broker authorizes its signers in writing (`trust_account_signers`, append-only,
  the latest row per account and staff member holding), each licensed or bonded to a day. Once
  an account has its broker on file, a payout from it (draw, bill paid, deposit returned, fees
  taken out, a journal entry taking cash out) by a named staff member who isn't its broker or a
  signer, or whose bond ran out, is refused; an account with none keeps the roles alone. One
  broker per account (the change trigger, under the account's lock), signers only while it has
  one (at commit). Staff see and change it at `/settings/staff` (`src/pms/api/signers.py`); the
  Dashboard lists accounts without a broker and bonds running out.
- Custom roles (migration 0057): an admin names roles, each allowing areas of the work (rent,
  bills, owners' money, budgets, journal, month-end, switching, setup, move-out, documents,
  leasing, maintenance, who can sign in), and gives them to staff (`trust_custom_roles`,
  `trust_custom_role_areas`, `trust_staff_custom_roles`; append-only, latest row holding, only
  an admin changing them). `trust_staff_may` says whether a staff member may change an area:
  always with no custom role, else when one of theirs allows it. The API asks on every staff
  write (`AREA_PATHS` in `src/pms/api/deps.py`; a test checks every staff write is some
  area's or an admin's own); reads stay open, so every staff member still sees every page.
  The three roles above are unchanged.
- Move-out (migration 0035): once a lease has ended, `trust_return_deposit` returns its
  tenants' deposits once per lease (same request key again returns the same): what the lease owes
  (`trust_lease_owed`), or all the deposits if less, is kept for the owner's account and matched
  to what is open of its charges rent first, the cash moving to the owner's trust bank account;
  the rest goes back to each tenant from the security-deposit account. A tenant still on another lease keeps
  their deposit with it. It takes the disburse role. `trust_report_deposit_return` is the
  itemized statement for the tenants. READ COMMITTED; append-only.
- Property groups (migration 0065, ADR 0006): a PMC's named sets of its properties, a name once
  per PMC whatever its case. A group is renamed, never deleted; a property joins or leaves it by
  a row of `trust_property_group_members` (append-only, the latest for a group and property
  holding, naming who did it, `trust_group_properties` reading it), and each row changes
  something. They move no money; the AI role reads them.
- Rent posts by itself (migration 0067, ADR 0006, `src/pms/nightly.py`): every night each PMC's
  rent and recurring charges for the month (due on its first day, or on a lease's own due day
  once it comes; migration 0081), the late fees of rents past
  their grace as of the day, the recurring bills due, with their owners' emails, and the recurring
  work orders due (migration 0085). Rent and late
  fees go through `trust_run_rent`, which records the run (`trust_rent_runs`, append-only, naming
  nobody); an admin runs it by hand from the rent roll (Run rent now), and a named staff member
  without admin is refused. Each part charges once per date, so a run again charges nothing new.
  The API runs it at start and every day at 06:00 UTC unless `PMS_NIGHTLY` is "off" (e2e turns it
  off); `python -m pms.nightly` runs it once, one run at a time across servers (an advisory lock:
  another leaves the work to it). It moves no trust money but a company's monthly
  management fees, once it turns them on (migration 0082), inside trust.
- Notes (migration 0066, ADR 0006): what staff write about a property, unit, lease, tenant, owner
  or vendor of the PMC (`trust_notes`, exactly one of them, append-only, naming who wrote it; a
  correction is a new note), on its Notes tab (`src/pms/api/notes.py`). They move no money, and
  the AI role never reads them (`AI_NEVER_READS`).
- Archiving (migrations 0063 and 0064, ADR 0005): nothing in a PMC is deleted; a property, unit,
  tenant or owner is archived instead, a row in `trust_archivals` (append-only, the latest for a
  record holding, `trust_is_archived` reading it), naming who did it. It is refused while
  anything is open on it: a lease in effect or to come (of a unit, of a property's units, or with
  the tenant on it), money held (a property's owners', an owner's, a tenant's prepaid rent or
  deposit) or a bill neither paid nor declined (a property's or an owner's). Bringing one back is
  always allowed. An archived one leaves its list (and search) unless staff ask for archived
  ones, an archived unit is marked on its property's page, and each keeps its history; it moves
  no money.
- Contact details (migration 0064, ADR 0005): a tenant's, owner's or vendor's email, phone, other
  phone and mailing address (whole or none), in `trust_contacts`: append-only, the latest row
  for a person holding, naming who recorded it; saving the same details again writes nothing.
  Staff see and change them on the person's page (`src/pms/api/directory.py`) and on Vendors.
  The AI role never reads them (`AI_NEVER_READS`), so the audit log hides their rows from it, and
  no report prints them.
- Documents (migration 0036): a vendor's W-9, a bill's receipt, a lease's move-out photo, an
  owner's document or a work order's photo (`trust_documents`, each kind about its one kind of
  record of the PMC; work order photos since migration 0072; a maintenance request's photos since
  migration 0084), or since migration 0074 a
  property's, unit's, tenant's, lease's or vendor's document, a
  PDF, JPEG or PNG of at most 5 MB. Its bytes sit apart (`trust_document_files`, checked at
  insert against the type's first bytes, the size and SHA-256 its row says, and there at
  commit): never audited and never read by `trust_ai_agent`. A removal is a row
  (`trust_document_removals`); nothing is deleted. The PMS reads nothing out of a file. An
  owner downloads their own owner documents in the portal. The company's logo is a document too
  (`company_logo`, migration 0075: a PNG or JPEG about no one record, the latest not removed
  holding), kept by `src/pms/api/company.py` and in no list of files.
- The company (migration 0075, ADR 0006): its name is `trust_pmcs.display_name`, which staff
  rename; its mailing address (whole or none) and the late fee and NSF fee terms a property added
  from then on starts with, from its first day (UTC), are `trust_company_profiles`
  (append-only, the latest row holding, naming who recorded it; saving the same again writes
  nothing). A default changes no property already there. The AI role never reads it.
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
  from Reports.
- Owner year-end (migration 0039): `trust_report_owner_year_end` sums each of an owner's
  property accounts over a calendar year (UTC): opening balance, collected from tenants
  (payments matched to charges, less bounces), management and leasing fees, bills set aside,
  draws, contributions, anything else, closing balance; then across the owner's properties,
  and the year's 1099-MISC rents (everything collected from tenants). It files nothing, holds
  no tax IDs and applies no threshold. Owners read and download it in the portal
  (`/api/owner/year-end`), with the year's whole statement.
- Expense accounts and the income statement (migration 0052): a PMC's expense accounts
  (`trust_expense_accounts`, a name once per PMC) each sit on an expense line of Schedule E
  (5 to 19; `SCHEDULE_E` in `src/pms/api/setup.py` holds the labels the report uses). A bill's
  latest row in `trust_bill_expense_accounts` is its account (a correction is a new row); lines
  naming that row (`trust_bill_splits`, migration 0069: each account once, coming to the bill at
  commit) split it across several instead. A recurring bill's latest goes on each month
  `trust_enter_recurring_bills` enters. It describes
  money already moved and moves none. `trust_report_income_statement` sums an owner's property
  accounts over days (UTC), cash basis: collected from tenants (as the year-end counts it) by
  what each charge is (rent, recurring charges, late fees, NSF fees, other; a lease end's
  adjustment is what it adjusts), management fees (line 11), leasing fees (line 8), bills set
  aside by expense account (a split bill by its lines; those in none on no line), net income,
  then by Schedule E line. It
  files nothing. Owners read and download a year's in the portal (`/api/owner/income-statement`);
  staff download any owner's from the owners page.
- Journal entries (migration 0053): a correction between a PMC's ledger accounts that no other
  write path makes. A staff member proposes one (`trust_propose_journal_entry`: a day, what it
  corrects, 1 to 20 lines of whole cents between accounts of the PMC); another who holds approve
  posts it (`trust_post_journal_entry`: once, its lines as ledger transfers dated its day, and the
  same call again returns them), never whoever proposed it; anyone declines it, with why, before
  it is posted. Posting keeps every trust rule above or moves nothing. Every step names a staff
  member (the command line can't take one); entries, lines and decisions are append-only, and the
  AI only reads them. Entries waiting show on the Dashboard.
- More reports (migration 0054), each read only and on Reports: the balance
  sheet (`trust_report_balance_sheet`: each trust bank account's cash by the books against what
  it holds by kind, at a day's end), cash flow (`trust_report_cash_flow`: each one's cash over
  days, every move of it in or out once, by what it was), vacancy (units with no lease in
  effect, since when; never leased is unknown), work orders (status and vendor as of a day,
  days open, billed), and directories of tenants, owners and vendors (names and what they are
  to the company on a day; contact details stay out of reports).
- Budgets (migration 0055): a month's budget of an owner's property account on one of the
  income statement's lines (each income class, the management and leasing fees, an expense
  account, bills in none; `trust_budgets`, append-only, a line's latest row counting). Staff
  set a property's lines month by month at `/budgets` (`src/pms/api/budgets.py`), every line in
  one save, all or none (a line's total typed in spreads over its months to the cent); a month
  left as it was writes nothing. `trust_report_budget_vs_actual` lays an owner's
  budget for the months that start within some days beside their income statement. A budget
  moves no money.
- Vendor profiles (migration 0073, ADR 0006): a vendor's trade, whether they are a 1099 vendor
  and how they are paid (check or EFT), in `trust_vendor_profiles` (append-only, the latest row
  holding, naming who recorded it; the same again writes nothing). No tax ID is kept: it stays on
  the W-9. A profile decides nothing and moves no money; the AI role reads it.
- A lease's people (migration 0080, `docs/law.md`): a tenant leaves a lease others stay on, once,
  from a day within it (`trust_remove_lease_tenant`, writing `trust_lease_departures`, naming
  who recorded it); the last tenant can't (the lease ends instead), nor anyone once its deposit
  was returned. Their deposit moves, all of it, inside the security-deposit account to a tenant
  who stays, or stays under their name until the lease's deposit is returned; one on another
  lease still keeps it with that lease. A tenant who left gets no reminder or late notice, files
  nothing more under the lease in the portal (they still read it), holds no prepaid rent a
  payment brings, and a renewal leaves them out. Others living there (`trust_lease_occupants`),
  pets and assistance animals (`trust_lease_pets`: no pet rent, fee or deposit for an assistance
  animal), vehicles (`trust_lease_vehicles`) and guarantors (`trust_lease_guarantors`) are kept on
  the lease's Tenants tab (`src/pms/api/household.py`, area setup) and taken off by a row of
  `trust_lease_household_removals`, once; all append-only, and the AI role reads none of them. A
  lease offer's signer is a tenant or a guarantor (`trust_lease_offer_signers.role`): guarantors
  sign and become the lease's guarantors, never tenants, and an offer needs a tenant. A
  property's lease-break fee terms (`trust_lease_break_fees`: a flat fee and months of rent, from
  a day) are those in force on a lease's first day; `POST /api/move-out/{id}/end` charges the fee
  (due on the new last day) only when staff ask and the lease ends before its end date. Any lease
  is ended from its page's ⋯ menu, and Move-outs lists an ending lease with no deposit for 90 days.
- Moving to another unit (migration 0091, `docs/law.md`, the founder's review q010 and q042):
  `trust_transfer_lease` ends a lease the day before a move and opens one at another unit of the
  PMC, maybe another owner's property, for the tenants staying on it, who become that property's
  tenants (a tenant is one record, with one login, wherever they rent); once per lease
  (`trust_lease_transfers`, append-only, naming who, the old lease's owner's account and the day
  the tenants agreed in writing that their deposits stay held, needed when they hold one). It
  refuses a lease on a payment hold, one whose deposit was returned, one a tenant left with a
  deposit under their name, and a move to another property while another lease of a tenant runs.
  Deposits stay held, as they are the tenants'; their prepaid rent first pays what the old lease
  owes, and the rest moves with its cash to the new property's owner's trust bank account when it
  keeps its owners' money in one. What the old lease still owes stays its owner's: the new lease's
  payments (`src/pms/api/rent.py`) pay what each lease it came from owes first, oldest first (the
  rent of every one before any of their other charges), into the account each move names
  (`trust_lease_moved_from`), and `trust_return_deposit` keeps it the same way, each lease's for
  its own owner; a lease moved from has no return of its own and leaves
  Move-outs. The lease page's ⋯ menu has Move to another unit (`POST /api/move-out/<id>/transfer`,
  area move-out); a lease shows what it carries and where its tenants went.
- Deposit kinds (migration 0092, `docs/law.md`, the founder's review q043): a tenant's pet or key
  deposit is held in a deposit account of its own beside their security deposit
  (`trust_ledger_accounts.deposit_kind`, NULL for the security deposit; one of each kind per tenant
  and trust bank account, always in the security-deposit one), opened by
  `trust_open_deposit_account`. Staff pick the kind on Record deposit (`POST /api/rent/deposits`,
  `kind`); a pet deposit is refused for a tenant whose only animals are assistance animals. The
  lease shows each kind (`tenants[].deposits`), the register and the return's statement name it,
  a leaving tenant's deposits move to the staying tenant's of the same kind, and
  `trust_return_deposit` keeps from each tenant's security deposit first, then key, then pet.
- Vendor insurance and inspections (migration 0058): a vendor's policies
  (`trust_vendor_insurance`: coverage, carrier, number, the days it runs, a limit; a renewal is
  a new policy), and a lease's move-in and move-out inspections (`trust_inspections`, their
  rooms in `trust_inspection_items`, each room once whatever its case, in one of five
  conditions). All append-only and naming who recorded them. `trust_report_vendor_insurance`
  gives each vendor's coverage on a day: of the policies started by then, the one that expires
  last, standing current, expiring within 30 days or expired (a vendor with none: no insurance
  on file); the vendors page and the Dashboard read it. `trust_report_inspection` lays a lease's latest
  inspection of each kind side by side, each room's change marked. Neither moves money.

## Run the checks

You need Python 3.11, a throwaway Postgres 16 with trust auth (no password), and dbmate 2.36.0
on `PATH` (CI pins the same version).

```sh
docker run --rm -d -p 5432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
export DATABASE_URL="postgres://postgres@localhost:5432/pms?sslmode=disable"
python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt

scripts/check.sh      # before every push: every PR check that can run here; commit first
```

The email tests also need Mailpit (`go install github.com/axllent/mailpit@v1.31.6`, or `MAILPIT`
naming it); without it `check.sh` lists them as NOT RUN.

`check.sh` lists what it couldn't run here (CodeQL, dependency review, the self-host image) as
NOT RUN. Run on Postgres built from `ci/postgres-coverage.Dockerfile`, it adds the PL/pgSQL lint
and the coverage gates; on plain Postgres those are NOT RUN too. One check at a time:

```sh
ruff check . && ruff format --check . && mypy   # ci: lint, format, typecheck
pytest                                          # every test; fresh database, no skips
HYPOTHESIS_PROFILE=nightly pytest tests/properties   # property tests, nightly depth
python -m tools.check_upgrade --base origin/main     # migrations upgrade a database with money
python -m tools.check_synthetic_data                 # no real-looking personal or bank data
(cd frontend && npm ci && npm run typecheck && npm run lint && npm run knip && npm test && npm run build)   # the portal
```

Mutation testing and the rest: `docs/CI.md`.
