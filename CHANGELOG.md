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
- **The portal fails gently:** a page that can't be drawn says so, that nothing saved is lost,
  and offers Reload, instead of going blank; and a session that ends while a page is open (an
  hour away) takes the person to sign in again, saying why, instead of a page of "sign in first".
- **Fixed, from the edge-case catalog:** a decimal comma in a file ("1234,56") is refused instead
  of read as $123,456.00; the uncleared checks and deposits file sent twice no longer carries them
  over twice; two identical charges in one switching file both come in; a tenant's emergency
  request goes through after the day's ten ordinary ones (five emergencies a day); no request may
  send more than 10 MB; only a login's newest invite or reset link works; and disabling an
  owner's or tenant's login takes the "who can sign in" area, as inviting them does. A company
  keeps an admin who can sign in: an admin whose login is disabled no longer counts, so the last
  working admin can't drop admin, and `make-admin` rescues a company whose admins are all
  disabled. Migration `20261105000600`.
- **A server at the wrong address says so.** The server and its commands refuse to start while
  `PMS_PUBLIC_URL` is still `.env.example`'s `https://pms.example.com`, or is no http or https
  origin (no scheme, no host, or a path, query or fragment: the portal is served at the root).
  A change refused because it came from another origin is logged once per origin with the one
  `PMS_PUBLIC_URL` gives, so a server reached at `www.` or another domain shows which to fix.
  One written with its default port (`:443`) or capitals in its host is read as a browser sends
  it, no longer refusing every sign-in; one naming a user or password is refused, as every
  emailed link would carry them (the refusal never repeats them).
- **Security: a visitor can't name their own address behind the proxy.** Trusting whoever
  connects (`PMS_TRUSTED_PROXIES="*"`, as `compose.selfhost.yaml` sets it), the app took the first
  address of `X-Forwarded-For`, which a visitor may send themselves and nginx keeps: a new made-up
  address each try got past the sign-in limit, and a signer could write any address into a
  signature's evidence. It now takes the last, the one the proxy wrote.
- **Fixed, from the edge-case catalog:** a full database disk answers "the PMS's database is out
  of disk space, so nothing was saved: tell whoever runs it" (503, and the log says it) instead of
  "the server answered 500"; New York's renewal notice counts a 12-month lease as a year (60 days,
  not 30) and two years to the day as two (90, not 60); and a server started with the nightly run
  off says so in its log, with what to run instead.
- **Fixed: a bank return reported before its payment was lost.** When the payments partner's
  report that the bank took a payment back arrived before its report that the bank paid it (paid
  retried after a hiccup, say), the PMS acknowledged it and dropped it, so the books kept rent or
  an owner's money the bank had taken back. It now answers 409 until the payment is paid in the
  books, and the partner's next try takes it back out. A return about a payment that failed is
  still acknowledged and changes nothing.
- **Fixed: one payment could empty Banking's list of online payments waiting.** A payment paid
  online for a property with more than one owner account and no ownership shares made the whole
  list fail, so Banking showed nothing waiting, and it couldn't be recorded. It is now listed with
  the owners to choose among (Record it, naming whose), a lease at such a property takes no
  payment online (the tenant sees the company's How to pay us) nor by autopay (its month reads as
  online payments off), and the card says when the list couldn't load instead of showing nothing.
- **Fixed: one late fee in a closed year stopped the whole nightly run.** Late fee terms entered
  after a year closed, in force from before it, reached that year's rents still open; the fee was
  dated in the closed year, the year's lock refused it, and the company's whole run, rent
  included, failed every night. Such a rent is now charged no late fee and the run goes on.
  Migration `20261105000700`.
- **The Dashboard says when rent stopped posting.** When no rent run (the nightly one, or an
  admin's Run rent now) ran in two days while the company has leases, because the nightly run
  failed every night, its thread died or it was off, Needs attention says so first, with the last
  day rent posted, so it is no longer only in the server's log.
- **Fixed: rent charged twice in a month.** Run rent now with Rent due on set to a day other than
  the first (the 15th, say) charged every lease due on the first a second month's rent and each
  monthly charge a second time, and a rent the old system's unpaid charges carried over on another
  day of the month was charged again on the first. The run now charges each month once.
  Migration `20261105000400`. A month charged twice before it stays so: look for two rents due in
  one month on the rent roll and credit the second.
- **Fixed: one bad photo stopped every email.** A move-out photo that says it is enormous (a PNG
  of 30000 x 30000 pixels in a few bytes) made its deposit statement fail, and its email, never
  recorded as tried, stayed first in the queue so every email behind it waited, and the nightly
  run failed with it. The statement now says that photo is with the lease's files, and any error
  making an email's file is recorded as that email's failed attempt.
- **An MCP server at `/mcp` (ADR 0011):** any MCP client, an AI assistant such as Claude, plugs in
  with an API key: it finds the company's records with their ids, reads its reports, ledgers and
  events, and proposes emails, notices, work orders and notes as drafts for staff to approve.
  Nothing it does moves money.
- **Events and webhooks (ADR 0011):** what happens in the books (a payment recorded or returned,
  a late fee, a lease opened or its end changed, a bill waiting for its owner, a work order's
  steps, a repair requested, a draft decided) is written as an event, kinds and ids only. Software
  with an API key reads them in order (`GET /api/events?after=`), and an admin adds webhooks in
  Settings, AI that each event is sent to, signed and retried. Webhooks need `PMS_DATA_KEY`;
  `PMS_WEBHOOKS=off` stops sending. Migration `20261105000500`.
- **Drafts (ADR 0011):** software with an API key, such as an AI assistant, proposes an email,
  a notice, a work order opened, assigned or scheduled, or a note, by the ids of the company's
  records; nothing is done until a staff member approves it at Drafts (or from the Dashboard),
  maybe after changing it, and then it is done as them, with their roles. Declining says why for
  the software. No draft moves money. Migration `20261105000300` (three new tables; the AI's
  role gains its one write, proposing a draft).
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
