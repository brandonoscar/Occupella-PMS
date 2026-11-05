# Changelog

Each release of the PMS, newest first: what changed for staff, owners and tenants, each new
database migration, anything a company must do to upgrade, and every security fix. How a release
is made and taken: docs/release.md. Installing for the first time: docs/install.md.

## v2026.11.1

The PMS ready for a company's first live month: names print as people write them, a company's
day is its own time zone's, the app never logs in to Postgres as its owner, the portals' second
version, the owner's bill approvals and the founder's review of 11/03. Its database is at
migration `20261104000900`.

**Before you upgrade** (docs/release.md, "Upgrading a company"; back up first):
- **Add `PMS_APP_DB_PASSWORD` to `.env`** (`openssl rand -hex 24`, a different one from
  `PMS_DB_PASSWORD`). The app now logs in as `pms_app`, which may only act as the app, never as
  the database's owner; without the key compose refuses to start and says so (#206).
- **Pick your time zone** in Settings, Company setup, after the upgrade. Until you do, the
  company's day is America/New_York's: payments, bills, statements and the nightly rent run follow
  it (#209).
- **Behind nginx**, pass the visitor on (`proxy_set_header X-Forwarded-For
  $proxy_add_x_forwarded_for;` and `proxy_set_header X-Forwarded-Proto $scheme;`, docs/install.md
  step 4) so a lease signed on the PMS's page keeps the signer's address. Caddy does it by itself
  (#203).
- The new `backup` service starts with the upgrade. Copying its dumps off the server is still
  yours to set up (docs/backup.md).

**For staff**
- **A company's day is the date in its own time zone** (#209). Rent paid at 9 pm Eastern on
  October 31 is on October's statement; the nightly run runs for each company's own day.
- **Owner approvals** (#204): an approval staff record for an owner names the owner, how they
  approved and why, and who recorded it. A bill's shortfall now keeps the owner's reserve in the
  account.
- **Bringing a company over** (#205, #202): a CSV that Excel on Windows saved comes in with its
  names as written ("José O'Brien", not "Jos? O?Brien"), and a file whose letters still don't read
  is refused with its line, never taken wrong. An import leaves the database's planner knowing
  what it brought, so the first pages after a switch are fast.
- **PDFs print names as people wrote them** (#201): receipts, notices, deposit statements, signing
  certificates, checks and reports embed IBM Plex Sans, so Nguyễn, Łukasz and O'Brien print, not
  "?".
- **The company's own terms of use and privacy notice** (#207): links set in Company setup, shown
  on the portals, the apply page and the signing page. The PMS writes no such text; a company that
  sets neither shows nothing.
- **Its own Stripe keys** (#208): an admin enters the company's secret key and webhook secret in
  Company setup, sealed under `PMS_DATA_KEY`; the server's `PMS_PAYMENTS` account stays the
  fallback. Payments online remain optional.
- **"How to pay us"** (#204): the company's own words on how to pay replace the server's when
  online payments are off.
- **API keys for software** (#194, ADR 0011): an admin makes a key in Settings, AI; software reads
  the company's reports with it, as the AI's role, and no key can move money or change a record.
- **Maintenance requests** (#204) say what and where, can be cancelled while received, take notes,
  and warn before an emergency is sent.
- **Shorter words** (#204): a button's label says what it does, and a line under it stays only
  where it stops a mistake. A home reads "Cedar House", not "Cedar House, House".
- **Messages and announcements** (#199): tenants and owners write to the company from their portal;
  staff answer from Messages, each answer emailed too, and the Dashboard lists those waiting. Staff
  post an announcement for every property's tenants or one property's.
- **Daily backups by themselves** (#199): the install (`compose.selfhost.yaml`) gains a `backup`
  service that dumps the database every day at 07:00 UTC into `./backups`, keeping 35 days and
  each month's first (docs/install.md, "Every day: backups").

**For owners and tenants (the portals' second version, #199 and #204)**
- **The owner portal in sections:** Overview, Properties, Statements and reports, Bills, Repairs,
  Documents, Messages and Profile. Each property opens to its units, who rents each (names only),
  the rent, what each owes, what is vacant since when and who gave notice; Repairs lists the work
  orders at the owner's properties; Profile changes the owner's own contact details, which staff
  see they changed.
- **The tenant portal in sections:** Overview, Payments, Lease, Repairs, Messages and Profile. A
  tenant gives notice to move out (staff see it on the Dashboard and the lease), sends a document
  of their lease such as a renters insurance certificate (staff find it with the lease's files),
  and reads the company's announcements on the Overview.
- **Messages:** tenants and owners write to the company from their portal; staff answer from
  Messages, each answer emailed too, and the Dashboard lists those waiting. **Announcements:**
  staff post one for every property's tenants or one property's.
- **Owners** approve a bill only after confirming it, and over $5,000 sign with their name; their
  bills are in one table with where each stands; the year-end package and income statement open
  on this year; a statement line opens where it came from.
- **Tenants** see their ledger from its opening balance with each payment plainly and its receipt,
  what is coming up next (a rent change included), the deposit held and the lease to read; they
  and owners give a phone at sign-up and say whether texts are OK. No text is sent: the PMS only
  records the answer.

**Migrations** (each upgrades a database that holds money and leaves its money as it was, db.yml):
- `20261031000201` API keys and their revocations (#194).
- `20261103000100` the portals' second version: messages, move-out notices, announcements, an
  owner's own contact details (#199).
- `20261103000200` a function that analyzes the tables an import fills (#202).
- `20261103000210` the company's terms and privacy links (#207).
- `20261104000100` to `20261104000700` the founder's review (#204): owner approval names,
  staff-recorded approvals, a shortfall that keeps the reserve (changes how a bill's shortfall is
  worked out from now on, not money already moved), how to pay us, home names, request details,
  text consent.
- `20261104000800` a company's own Stripe keys (#208).
- `20261104000900` a company's time zone (#209).

**Security**
- The self-hosted app no longer connects to Postgres as its superuser (#206): `pms_app` holds
  nothing of its own and may only act as the app.
- The proxy may name the visitor only because the app's port stays on the server's own
  `127.0.0.1` (#203).
- No security advisories.

**Fixed**
- The Dashboard asked for a day at the calendar's very ends (year 1 or 9999) answered with a
  server error; it now says which days it shows (1900 to 9999) (#199).

## v2026.11.0 (first release)

The first tagged release: a whole trust-accounting PMS for property management companies, free
under Apache-2.0, run on the company's own server (docs/install.md). Its database is at migration
`20261102000150`.

**For staff**
- **Trust accounting that can't go wrong quietly:** a double-entry ledger that is append-only (a
  mistake is fixed by a reversing entry), owners' money and deposits each in their own kind of
  trust bank account, no account but a bank's ever below zero, every trust account tying out at
  every commit, and every write in an audit log naming who made it.
- **Rentals:** properties in groups, units, leases with co-tenants, household members, pets and
  guarantors; the rent roll; rent, recurring charges, prorated months, late fees and NSF fees that
  post by themselves each night; rent changes with notice; each lease's own due day; payment holds
  for evictions; renters insurance; move-out with an itemized deposit return.
- **Money in:** Receive payment (check, money order, EFT, cash) matched to what is owed, rent first;
  a bank's or lockbox's payment file; receipts; bounces with the NSF fee; bank deposits with their
  slips.
- **Owners' money:** management agreements with reserves and approval limits; management, leasing
  and other fees; owners paid by ACH file or printed check, in batches, from a draw day; owner
  contributions; the company's earned fees taken out of trust in time.
- **Bills:** vendors with W-9s, insurance and 1099 status; bills split across expense accounts or
  shared across properties, with markups; owner approval in the portal; recurring bills; pay bills
  by check or ACH file.
- **Month-end:** bank statements imported (CSV or OFX), matched, and a three-way reconciliation
  prepared by one person and approved by another; journal entries with two-person approval; close
  a year.
- **Maintenance:** work orders with priority, photos, visit windows and a vendor's private link;
  tenants' requests, with emergencies emailed to whoever is on call; recurring jobs; charging a
  tenant for a repair.
- **Leasing:** listings on the company's public vacancy page, applications with households,
  showings, screening criteria, adverse action notices, and leases signed on the PMS's own signing
  page (no e-sign service needed); renewals.
- **Reports, on screen and to download** (PDF, CSV, Excel): rent roll, delinquency, trial balance,
  balance sheet, income statement, cash flow, general ledger, owner statements and year-end
  packages, 1099 summaries, vacancy, expirations, budgets against actuals, and more; a QuickBooks
  export; the whole ledger and every record as a zip.
- **Bringing a company over** from another system: CSV files, opening balances, the checks and
  deposits not yet cleared, unpaid bills, and history, with a tie-out staff sign off.
- **Staff roles** (admin, approve, disburse, and custom roles by area), each trust account's broker
  and signers, two-step sign-in with an authenticator app for every staff member.
- **Email** (with an SMTP provider): invites, password resets, receipts, statements, reminders and
  notices, each turned on by the company.

**For owners (owner portal):** each property's balance and statement, the year-end package and
income statement, bills to approve or decline, their documents, and **adding money to a property's
account online**, with no fee.

**For tenants (tenant portal):** what is owed and when, the lease, payments and receipts, rent paid
online by bank and autopay, maintenance requests with photos, renters insurance, renewals to sign.

**Payments online** (optional, with the company's own Stripe account): rent by bank (ACH) with
autopay, and owners paying in; the processing fee on an owner's payment is the company's. Tested
against Stripe's documented answers, not a live account (NOT VERIFIED).

**To install:** docs/install.md: `compose.selfhost.yaml`, `.env.example` with every key a company
brings, and `python -m pms.admin setup` for the company and its first admin.

**Not in this release:** texts, listing syndication, application fees, card payments, a screening
company, state rules beyond what docs/law.md records, and turning on Occupella AI from inside the
PMS.

**Known:** a test run that crosses midnight UTC can fail tests that fix their day when they load;
the PMS itself is not affected.
