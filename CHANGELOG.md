# Changelog

Each release of the PMS, newest first: what changed for staff, owners and tenants, each new
database migration, anything a company must do to upgrade, and every security fix. How a release
is made and taken: docs/release.md. Installing for the first time: docs/install.md.

## Unreleased

Migration `20261103000100` (the portals' second version, `docs/adr/0012-portals-two.md`).

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
- **Daily backups by themselves:** the install (`compose.selfhost.yaml`) gains a `backup`
  service that dumps the database every day at 07:00 UTC into `./backups`, keeping 35 days and
  each month's first (docs/install.md, "Every day: backups"). Upgrading as docs/install.md says
  starts it. Copying the dumps off the server is still yours to set up (docs/backup.md).
- **Fixed:** the Dashboard asked for a day at the calendar's very ends (year 1 or 9999) answered
  with a server error; it now says which days it shows (1900 to 9999).
- **Getting started on the Dashboard:** until a company is ready to go live, the Dashboard lists
  what is left (company details, trust bank accounts, a broker for each, properties, leases, a
  second person for month-end, email sending, then the optional portals and rent paid online),
  each done or not by the company's own records and linking to where it is done
  (`GET /api/go-live`).
- **Security: an idle session ends.** A session left alone for an hour is signed out, besides
  its twelve hours as before; using it keeps it going. Migration `20261105000200` (a table of
  each session's last use, updated at most every five minutes and left out of the audit log).
- **Security: sign-in limits by address.** An address that failed 20 sign-ins within 15 minutes is
  refused, whatever email it tries, until the oldest failure is 15 minutes old (429 with
  Retry-After); an address asks for at most 10 password resets an hour. Each login still locks
  after 10 wrong passwords, as before.
- **A company's status**, set by whoever runs the server with `python -m pms.admin status <company
  id> {active,suspended,closed} <reason>`. Suspended: nobody signs in, open sessions and API keys
  stop and no password reset is sent, while the nightly run keeps posting. Closed: the same, and
  the nightly run stops. Nothing is deleted. Migration `20261105000100` (a new table, no change to
  trust tables' money).
- **A starting chart of accounts:** `python -m pms.admin setup` gives a new company twelve
  expense accounts on their Schedule E lines (advertising, cleaning and maintenance, landscaping,
  pest control, insurance, legal and professional fees, repairs, supplies, property taxes,
  utilities, HOA dues, other expenses). `python -m pms.admin starting-chart <company id>` adds
  them to a company made earlier, skipping any it has whatever their case.
- **Sign-in without a link:** on a server that holds one company, the sign-in page signs in to
  it and shows its name (`GET /api/sign-in`); a server with more companies still asks for the
  company's link.
- **A signed image for each release:** pushing a release tag now publishes the self-host image to
  `ghcr.io/brandonoscar/occupella-pms:<tag>`, scanned, signed with Sigstore and with its SBOM
  attached (docs/release.md, "The self-host image"). Building from the tag still works as
  before.
- **Security: a real server refuses the test partners.** A server whose `PMS_PUBLIC_URL` is https
  no longer starts (nor do `python -m pms.nightly` and `python -m pms.email deliver`) with
  `PMS_PAYMENTS`, `PMS_SCREENING_PARTNER`, `PMS_GEOCODER` or `PMS_EMAIL_SENDER` set to `fake`:
  the fake payments partner lists every company's pending payments without a sign-in and records
  one paid at a button's press. `PMS_FAKE_PARTNERS=allow` lets a public demo of a synthetic
  company run them. Nothing to do unless your `.env` names a fake partner.
- **The server's own Stripe keys serve only a server holding one company.** They are one Stripe
  account paying out to one trust account, so on a server with more companies a company without
  keys of its own takes no payment online until an admin enters its own in Company setup
  (docs/install.md, "Payments online"). A server with one company is unchanged.

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
