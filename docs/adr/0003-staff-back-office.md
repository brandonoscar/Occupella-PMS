# ADR 0003: The staff back office

Decided by the founder on 2026-10-12, after ADR 0002's list (exports, owner and tenant
portals, leasing basics) was merged.

## Decision

The accounting the database already does gets the screens and API staff need to run a PMC
day to day, so nothing takes hand-written SQL. Built in this order, one PR at a time:

1. The back office for what is built: setting up the books; recording and matching tenant
   payments, the rent run, late fees and bounces; bills, fees, owner draws and contributions;
   approving the monthly reconciliation.
2. Bank statement import (CSV or OFX) and matching, for the three-way reconciliation.
3. A switching tool: CSV import of owners, properties, units, tenants, leases and vendors, and
   the opening balances with a tie-out report.
4. Staff roles, with disbursement limited to the staff a PMC authorizes.
5. Move-out: ending a lease and returning the security deposit with its itemized deductions.
6. Documents: W-9s, receipts, move-out photos, owner documents.

## What still holds

Everything ADR 0002 keeps out stays out: Stripe and online payments, bank files (ACH or
NACHA), email sending, 1099 filing, state rules beyond the no-negative rule, any deploy. The
back office records what happened at the bank; it never sends money anywhere. The AI role
gets no new grant.

## Setting up the books, as built

Staff set up their PMC at `/setup` (`src/pms/api/setup.py`): trust bank accounts (each with
its cash ledger account, and an operating one with the PMC's fee account), owners, properties
with an owner account per owner in an operating trust account, units, tenants, vendors,
management terms and late fee terms (each in force from a day), and the leases of tenants
already in place (a new tenant's lease comes through a lease offer). Every write is a plain
insert the app role may make or one of the database's write paths, so the database's rules
hold whoever calls, and each names the staff member to the audit log. Nothing there moves
money.

## Rent work, as built

Staff do the rent work at `/rent` (`src/pms/api/rent.py`):

- The rent run charges every lease in effect on a due date its rent (`trust_charge_rent_due`),
  once per lease and date; late fees are assessed as of today or an earlier day
  (`trust_assess_late_fees`). A fee or a credit is added to a lease by hand, once or every
  month (since 2026-10-13, product item 1 of three the founder approved): a recurring charge
  such as pet rent or parking, or a recurring credit such as a concession, is charged by the
  same rent run on every due date from its first until staff stop it
  (`trust_charge_recurring_due`, migration 0037), once per recurring charge and date.
- A tenant's payment is recorded as it reached the operating trust account. The owner's account
  takes what the lease owes (rent and fees, less credits, less what was paid, plus what
  bounced), matched to the open charges oldest first; anything more is held as the first
  tenant's prepaid rent. Prepaid rent is used against new charges the same way. When the
  property has more than one owner account, staff choose which one the rent pays.
- A security deposit is recorded into the security deposit trust account, held for the tenant.
- A payment the bank returned is recorded as bounced: what it moved goes back to cash and its
  matches are reversed, once. It is refused when the owner's account (or the prepaid rent) no
  longer holds the money, since an owner's account never goes below zero.
- Each record carries a request id the page makes, its key in `trust_post_transfers`, so a
  double click or a retry records nothing new. Records are dated on or before today; a payment
  hold or a closed period refuses them, with nothing written.

## Bills and owners' money, as built

Staff pay vendors' bills at `/bills` and handle owners' money at `/owners`
(`src/pms/api/payables.py`):

- A vendor's bill is entered against an owner's property account, once per vendor reference,
  and may name a work order of its property. A bill over the approval limit of the agreement
  in force on its date (0 without one) shows as needing the owner's approval, which staff
  record (who approved it and when) before it can be set aside.
- Setting a bill aside moves its amount from the owner's account to the vendor's
  (`trust_set_aside_bill`, opening the vendor's account in that trust bank account the first
  time); paying it records that the trust bank account paid the vendor (`trust_pay_bill`).
  Each step happens once, and never takes an owner's account below zero.
- Recurring bills are added, stopped from a day, and entered month by month through a day
  (`trust_enter_recurring_bills`).
- The owners page shows each owner's property account: its balance, the reserve of the
  agreement in force, its unpaid bills (counted as `trust_unpaid_bills` counts them) and what a
  draw may take today. A draw (`trust_draw_owner`) takes an amount or everything available,
  and an owner contribution (`trust_record_owner_contribution`) records money the owner put in;
  each carries a request id the page makes, so a retry pays or records nothing twice.
- Management fees are taken for a period, from every owner account with an agreement or one
  (`trust_post_management_fee`, once per period); an account the fee can't be taken from says
  why, and the others are taken. A leasing fee is taken once per lease from an owner of its
  property (`trust_post_leasing_fee`).
- Records are dated on or before today; a closed period refuses them. The PMS records what the
  bank did; it sends no money.

## Month-end, as built

Staff reconcile each trust bank account at `/reconcile` (`src/pms/api/reconcile.py`,
migration 0032):

- A statement is imported from the bank's CSV or OFX file (`src/pms/statements.py` reads both;
  OFX by plain text matching, never an XML parser). Statements of an account follow one
  another, day after day and balance after balance, each with all its lines; a file is
  imported once. The record starts from 0: a first statement's opening balance comes in as a
  line of its own, "Balance brought forward", so every dollar the bank holds is on a line.
- Lines are matched to the ledger transfers that moved the account's cash by the same total:
  one line to one transfer, a deposit to the several payments in it, or a payment and its bounce
  the bank never saw with no line at all. "Match the sure ones" pairs a line with the one open
  transfer of its amount within a week, when neither has another such partner. A match can be
  undone. A line or a transfer is in at most one match that stands, and no match or undo
  changes a period already approved.
- The worksheet is `trust_report_bank_reconciliation` for the end of a day: the bank balance
  the statements give, deposits in transit and outstanding withdrawals (transfers no cleared
  match holds), the adjusted bank balance, book cash, and the difference, listed as the bank
  lines the books lack. A match clears only when everything in it happened by that day.
- A period is approved (`trust_approve_bank_reconciliation`) only when that difference is 0:
  a line the books lack (a bank fee, interest) is recorded in the books and matched first. The
  approval records the bank balance the statements give and closes the account through the
  day, as `trust_approve_reconciliation` always has; the three-way report shows the period.
- One staff member prepares a period and another, who holds the approve role, approves it
  (staff roles, below).
- A cutover's checks and deposits the bank had not cleared are carried over by the switching
  tool (below), so a migrated account reconciles from its first period.

## Switching, as built

A PMC moving from another system uses `/switching` (`src/pms/api/switching.py`, migration
0033):

- The roster comes in from CSV files, one kind at a time and in order: owners, vendors,
  properties with their owners (each owner's property account in an operating trust account),
  units, tenants, leases in place, and tenants' unpaid charges. Names are matched exactly within
  the PMC, so a file sent again adds nothing twice; a file goes in whole or not at all, and a
  row that can't go in names its line.
- Each trust bank account opens with what each account in it held in the old system at the
  start of the cutover date, from a CSV of balances by name (owner and property, a tenant's
  prepaid rent or deposit, a vendor, the PMC's fees), checked against the old book cash by
  `trust_post_opening_balances`.
- The checks and deposits the bank had not cleared at the cutover are entered as items carried
  over (`trust_cutover_items`): the cash each had already moved in the old books.
- Staff sign off the tie-out (`trust_report_cutover`: the opening balances against the old
  book cash, the items, the bank balance to expect, who signed it and when). The sign-off
  freezes the items.
- What the old system paid each vendor in the cutover's year before it comes in from a CSV of
  vendor and amount (`trust_vendor_payments_carried_over`, migration 0038; since 2026-10-13,
  product item 2 of three the founder approved), once per vendor and year. The year's vendor
  payments report (`trust_report_vendor_payments`) adds it to what the PMS paid, so the year's
  1099 preparation counts the whole year. A January 1 cutover carries over the year before.
- At month-end, the first statement's balance brought forward is matched to the opening
  balances less the items (`trust_match_cutover_opening`); each item then clears when a later
  bank line does, and until then it is outstanding in the reconciliation. So the account's first
  periods approve like any other.

## Staff roles, as built

Who may do what (`/staff`, `src/pms/api/staff.py`, migration 0034). Each staff member holds any
of three roles:

- **admin** changes roles, adds staff members, and invites or disables a staff login. A new
  invite sets a password, so an admin is the only one who can send one to staff: otherwise anyone
  could take over an approver's login.
- **approve** approves a month-end period and signs off a cutover's tie-out.
- **disburse** pays out money: records that a bill was paid and pays an owner draw.

Everything else (entering bills, setting them aside, rent work, matching, preparing a period) is
any staff member's.

How it holds:

- The database keeps the rules for whoever the app names (`trust.staff_id`, set on every staff
  request): a payment of a bill, an owner draw or an approved reconciliation is refused unless
  that staff member holds the role, and the refusal is answered 403. A write that names nobody,
  as the server's command line does, is not held to them, as the audit log records it with no
  staff member.
- Roles are append-only grants and revocations (`trust_staff_roles`): only an admin changes one,
  a change has to change something, and a PMC keeps one admin. The trigger records who made the
  change and when; the audit log has it too.
- Month-end is prepared by one staff member (`trust_prepare_bank_reconciliation` records the
  bank balance and book cash through a day once the books lack no line) and approved by another
  who holds approve (`trust_approve_bank_reconciliation` takes the preparation). The approval
  stands on the latest preparation, and only while its figures still stand: a posting in the
  open period since then means preparing it again. `trust_app` no longer calls
  `trust_approve_reconciliation` itself.
- A PMC's first admin is made on the server: `python -m pms.admin make-admin <pmc> <staff>`,
  which works only while the PMC has none. On upgrade every existing staff member holds every
  role, as each could do everything before; an admin then takes away what they shouldn't hold.

## Move-out, as built

At `/move-out` (`src/pms/api/moveout.py`, migration 0035) staff see every lease that holds a
security deposit for its tenants, with what it owes:

- They set the lease's last day, and charge what the tenants owe at move-out (cleaning, damage)
  as fees on the lease, like any other charge.
- Once the lease has ended, they record the deposit's return on the day the bank paid it
  (`trust_return_deposit`, once per lease, safe to retry): what the lease owes (its rent and
  fees less what was paid and less its credits), or every deposit if that is less, is kept for
  the owner's account of the property (staff choose one when there are several) and matched to
  the open charges oldest first; its cash moves from the security-deposit trust bank account to
  the owner's. The rest goes back to each tenant out of the security-deposit account, kept from
  in name order when there are several.
- A tenant still on another lease (a renewal) keeps their deposit with it, so that lease's
  return waits. A payment hold on the lease refuses keeping money for its charges, as it
  refuses any payment.
- Returning a deposit pays out money, so it takes the disburse role.
- The statement for the tenants is `trust_report_deposit_return`, downloaded as a PDF like any
  report: each tenant's deposit, what was kept for each charge, what went back to whom, and who
  returned it when. The PMS keeps no tenant's address: staff send it.
- No state's deadline for returning a deposit, interest on it, or form of notice is built in.

## Documents, as built

At `/documents` (`src/pms/api/documents.py`, migration 0036) staff keep four kinds of document,
each about one record of their PMC: a vendor's W-9, a bill's receipt, a lease's move-out photos
and an owner's documents.

- A document is a PDF, a JPEG or a PNG of at most 5 MB, uploaded from the page (as base64 in
  the JSON body, so changes stay JSON from the app's own origin). The API checks the type's
  first bytes and the size; the database checks them again, with the SHA-256 the document's
  row records, and refuses a document without its file at commit.
- The bytes are kept apart from the document's row (`trust_document_files`): the row is audited
  like every trust record, the bytes never are, so a W-9's tax ID never reaches the audit log.
  `trust_ai_agent` may see that a document exists and never reads its file.
- Staff remove a document with a removal row; it is then neither listed nor served. Nothing is
  deleted, as with every trust record.
- A download is an attachment named after the title, served under
  `Content-Security-Policy: default-src 'none'; sandbox`, so a file opened in the browser runs
  nothing.
- An owner sees and downloads their own owner documents in the owner portal
  (`/api/owner/documents`), and no other document.
- The PMS reads nothing out of a file: no tax ID is extracted, and 1099s are still not filed.
  Files live in the database; a PMC with many photos may later want them in object storage
  (not built, and no hosted storage is used).

