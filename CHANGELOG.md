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
- **Dates in files read as the company writes them.** 05/01/2026 is 1 May in the US and
  5 January in most of the world; a day-first file went in as May with no word. Every file the
  PMS reads (bank statements, payment files, the switching tool's) now reads its dates in the
  order the file proves (13/01/2026 can only be day first), else the company's own: month first
  until it picks day first in Company setup. A file mixing both is refused with its lines, and
  dates with dots or dashes read too. Migration `20261105001200`.
- **Fixed: switching put an old tenant's charge on the new tenant.** A charges file names a unit,
  and every charge went on the newest lease started by its due day, so a move-out fee dated after
  the next tenant moved in became theirs. A charges file may now say whose each charge is (a
  Tenant column); without one, rent goes on the lease in effect on its due day, and a fee that
  more than one tenant could owe stops the import with the line and the leases it could be on.
- **Fixed: a bank statement of positive amounts read as all money in.** A bank CSV whose amounts
  are all positive, with a column saying Debit or Credit (Type, Transaction type, Dr/Cr), went in
  with every check and fee as a deposit. Each line's way now comes from that column; a type saying
  neither is refused with its line, and a bank that signs its amounts keeps its signs.
- **Fixed: a mid-month switch charged the old system's month again.** Leases brought in after
  the old system charged and collected the month's rent were charged the whole month again the
  first night, and a late fee on it once past the grace. The rent run now charges no rent or
  monthly charge due before the cutover of the trust bank account holding the property's owners'
  money; from the cutover on, every month is charged as before. A run between bringing the roster
  in and posting the opening balances still charges it (docs/edge-cases.md M-152). Migration
  `20261106100000`.
- **Fixed: one check recorded twice.** Receive payment made a new request id each time its panel
  opened, so a payment whose answer was lost and recorded again, or one check entered from two
  tabs, went in twice. A payment like one recorded to the lease (the same amount received the
  same day, by the same way and check number where both say one) now asks staff to say it is
  another payment first; the panel says so before it is sent.
- **Fixed: a payment online with no word stayed "on its way" for good.** A payment whose
  partner's report never came lowered every later balance autopay, and staff never saw it.
  After 14 days with no word it no longer counts as on its way, and Banking's Online payments
  waiting lists it, to look up at the payments partner. An autopay whose payments were never
  sent two months running stops by itself, with its email, as two failed ones do.
- **Security: a tenant marked "the same person" by mistake.** Nothing undid a link, so whoever
  signed in as the first record kept reading and acting on the other's lease. Staff now take it
  back from either record's Other properties (Not the same person, asked first): the record is
  its own person again at once, its autopays stop, and it takes an invite of its own. Migration
  `20261106100100`.
- **A state's notice on Change rent.** The Change rent panel now warns, as the renewal panel
  does, when the property's state (California, Oregon, Washington, Nevada, New York) asks more
  written notice before the increase, counted from the day notice was given: a change from
  March 1 with notice on February 1 is 28 days, short of California's 30. A warning only.
- **Security: sign-in limits and IPv6.** An IPv6 visitor counts as their whole /64, so a new
  address each try no longer gets past the sign-in and reset limits. A request that comes through
  a proxy `PMS_TRUSTED_PROXIES` doesn't name is logged once, naming the proxy, since every visitor
  behind it shares its address until it is named.
- **Fixed: a fee or a monthly charge entered twice.** Enter charge and Add recurring charge sent
  again after their answer was lost (a dropped connection, a second click on a slow page) charged
  the tenant twice, or billed them twice every month. Each now carries its panel's request id, as
  Receive payment does: sent again, it charges nothing more. Migration `20261106110000`.
- **Fixed: a journal entry proposed twice, or dated ahead.** A journal entry proposed again after
  its answer was lost waited twice, and an approver might post both. It now carries its page's
  request id, so the same proposal again is the one entry. An entry is dated no later than the
  company's today: one dated next year moved balances at once while every report as of today
  left it out. One proposed for a later day before this release posts on its day. Migration
  `20261106110100`.
- **Fixed: a lease-break fee charged twice.** End lease with the lease-break fee sent twice at
  once (two tabs, or a resend while the first ran) charged the fee twice. A lease is now charged
  one lease-break fee: the same end again changes nothing, and another end with the fee is
  refused, saying the fee was charged already.
- **Fixed: autopay taken just after it was turned off.** A tenant who turned autopay off while
  the night's payments were being sent could still be debited, though the stop's email said
  nothing more would be taken. A payment is now checked and sent while its autopay is held, so a
  stop under way keeps it from being sent, and a stop that comes while it is with the payments
  partner waits until it is recorded.
- **Fixed: the company's deposit for online payments' costs made twice.** Two staff recording it
  at once each deposited what waited, leaving the company's own money in trust. The second now
  waits for the first and is told nothing more is needed.
- **Fixed: prepaid rent held for a tenant who had just left.** A payment recorded while a tenant
  was being taken off the lease read the lease's tenants before it waited for the departure, so
  the part paid ahead could be held for the tenant who left. A payment, and prepaid rent used,
  now read the lease's tenants and owners once they hold the lease.
- **Fixed: management fees collected before their period ended.** Collect fees for a period still
  running took the fee on what had come in so far and closed the period to later fees, so rent
  paid after was never counted. A period is now collected once it has ended; the page says when.
- **Fixed: Stripe's answers in an old API version, and a webhook missing its events.** Every
  call to Stripe now names the API version whose shapes the PMS reads (`2024-06-20`), so an
  account whose default is older than `latest_charge` no longer fails every paid report; and
  Company setup lists the eight events the company's Stripe webhook endpoint must be sent,
  without which a bank payment never shows as paid.
- **Fixed: a disk the backups filled never freed itself.** A daily dump that failed left its
  half-written file, and dumps past keeping were removed only after a dump succeeded. A failed
  dump now removes both first. And the backup service reads unhealthy in `docker compose ps`
  once no good dump is less than 26 hours old.
- **Fixed: an older PMS ran on a database a newer one had migrated.** Nothing compared the
  database's migrations to the code's, so an older image tag, or a newer backup restored under
  one, posted money on a schema it was never tested on. The app's connections, the nightly run
  and the webhooks now refuse such a database, and one not migrated yet, saying which and what
  to do (docs/support.md). Migration `20261105001100`.
- **Fixed: a slow or hung mail server could make the whole PMS answer busy.** After every change
  the API delivered what waited in the outbox on a pooled connection, up to 20 seconds an email
  when the mail server hung, so a handful of changes filled the pool. Delivery, and autopay's
  submissions to the payments partner, now run one at a time in a process; a change made while
  one runs makes it run once more after, so nothing waits for the next change.
- **Fixed: the everything export could take the PMS down for a large company.** Settings, Data
  export's records.zip fetched every table and every document's bytes at once and zipped them in
  memory, sorting each table by its rows as text (a 5 MB file as 10 MB of hex). Rows now come two
  at a time in their key's order into a zip written to a temporary file, which the answer is
  sent from: 24 MB of files export holding about 5 MB.
- **Fixed: payouts recorded as paid by ACH under the wrong data key.** With `PMS_DATA_KEY` set
  to another key than the one that sealed the payees' bank account numbers, Pay owners and Pay
  bills recorded the payouts and only then found the ACH file couldn't be made. Each payee's
  number must now open before anything is recorded; by check, nothing needs the key.
- **Fixed: an ACH file could be taken twice without a question.** A bank pays every file it is
  given, so one uploaded twice pays everyone in it twice. Once a run's ACH file was taken, the
  Payout files list offers Download again, which asks first, and the API gives it again only when
  asked again; two first takes at the same moment wait for each other, so the second is asked
  too. Checks still print as often as needed.
- **Fixed: a lease entered on the evening of its last day never went month to month.** Whether a
  lease was entered while its term ran read the day it was entered in UTC, so one entered after
  8 pm in New York on its last day counted as entered after its end, and its rent stopped. It is
  now the company's day. Migration `20261105001000`.
- **Fixed: the audit log report over a day a lease was offered never finished as a PDF.** The log
  keeps each row as written, so a lease offer's PDF read as millions of characters in one cell.
  The report now shows a file's bytes as their size ("(5000000 bytes)"), and a PDF export cuts a
  cell at a page's lines and an Excel export at the 32,767 characters a cell holds, each ending
  in "…"; the CSV keeps everything. Migration `20261105000900`.
- **Fixed: two tenants of one name shared a QuickBooks account.** The QuickBooks export named a
  tenant's prepaid rent and deposits by their name alone, so two Pat Samples (or two vendors of
  one name) posted to one account. The oldest keeps the plain name; each after it adds its
  tenant's property, then a number, and a name once given never changes.
- **Fixed: deposit interest paid twice for the same days.** A tenant's deposit is one account, and
  each lease's interest counted all of it: a tenant on two leases at once, or a renewal and the
  lease it renewed (the old one paid through a day after its end), were paid each day twice. Each
  day of a deposit is now paid once, by whichever lease pays it first. Migration
  `20261105000800`.
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
