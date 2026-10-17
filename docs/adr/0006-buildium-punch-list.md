# ADR 0006: The Buildium punch list

Decided by the founder on 2026-10-16, after scanning every page of the demo: "works like
Buildium. A PM with 250 units in one building, or 5,000 units total, can find anything in 2
clicks." The punch list sets rules for every page, changes page by page, a demo seed with
something on every page, the backend those need, and a build order. Asked four questions, the
founder chose the recommended answer to each, below.

## Rules for every page

- One search, the bar's. A list page has no search box of its own; it has a **property group
  picker** at its top left (All properties, the groups, then each property), and the choice
  carries from page to page: in the URL (`?group=` or `?property=`) and in the browser.
- A list page opens on its list, one row per record, money right-aligned, the row opening the
  record; Add opens a side panel. A record's page has tabs.
- Every save says what it did in a toast, and a refused one says why; a button shows it is
  working while its save runs and can't be pressed twice.
- An action that can't simply be taken back (It bounced, Archive, Stop, Undo, Decline, Disable,
  Void) is in the row's ⋯ menu and asks first, naming any amount.
- Dates read MM/DD/YYYY; a lease term reads `11/01/2025 – Month to month`. No sentence explains
  a page under its title, and a page with one table has no label over it. Money shows as money.

## Decisions

1. **The four PRs already built (tenants and owners pages, accounting, maintenance and leasing,
   the wording pass) land as one PR first**, then the punch list in its build order.
2. **Applicants: ADR 0002 stands.** The PMS keeps applicants' names and emails and the
   household's monthly income; the screening partner keeps the rest (employment, rental
   history, pets, vehicles). Income to rent comes from the income the PMS has.
3. **Vendors: no tax IDs.** A vendor's page shows whether a W-9 is on file, whether they are a
   1099 vendor and how they are paid; the number stays on the W-9 itself.
4. **Fees move money only when staff ask.** Management fees are collected with one click on a
   page of their own, and a leasing fee is offered when a lease is made; neither posts by itself.

## What the founder accepted without changing

- No undeposited funds: matching already matches several receipts to one bank line.
- Transfer is only "transfer company fees"; the trust rules refuse moving held money between a
  deposit account and an operating account.
- Check or EFT is recorded as the method; the PMS writes no bank file.
- Cash basis only, so no accrual toggle until there is an accrual basis.
- A move-out's "return due by" is the strictest state deadline found, with its law cited in
  `docs/law.md`, as for the company's fees.
- Rent, recurring charges, late fees and recurring bills post by themselves every night: a
  charge or a bill entered moves no trust money.

Still out: a real e-sign partner and online payments (launch blockers, not this pass), and
anything about the landing page.

## Property groups

The first piece built (migration 0065, `src/pms/api/groups.py`, `src/pms/api/scope.py`): a
PMC's named sets of its properties. A group is renamed, never deleted; a property joins or
leaves it by an append-only row naming who did it, and each row changes something. The
properties, rent roll, tenants and owners lists narrow to a group or one property; the other
list pages and the reports follow as the build order reaches them. Groups move no money, and
the AI role reads them.

## The lease page, tenants and notes

The second piece (migration 0066, `src/pms/api/notes.py`, `src/pms/api/rentals.py`): a lease's
page puts its property, unit and tenants in its title and its balance due at the top, Receive
payment and Enter charge beside it and the rest of its rent work in its ⋯ menu, each in a side
panel. Its Ledger tab is the lease ledger report's own lines, each with the balance after it, a
charge saying what it is (rent, a recurring charge, a late or NSF fee, a credit) and a payment its
check or reference and whether it bounced. Its Tenants tab has each tenant's contact details and
portal login (invite or send again). The Tenants list shows each tenant's balance and portal
login, and every tenant named on a lease row links to their page. Notes are append-only rows
naming who wrote them; the AI role never reads them.

## Rent posts by itself, and the rent roll

The third piece (migration 0067, `src/pms/nightly.py`): every night each PMC's rent and recurring
charges for the month, the late fees of rents past their grace, and the recurring bills due post
by themselves, each once per date, so a missed night is caught up by the next and a restart
charges nothing twice. The API runs it at start and every day at 06:00 UTC; a server that turns
that off (`PMS_NIGHTLY=off`) runs `python -m pms.nightly` on its own schedule. Rent and late fees
go through `trust_run_rent`, which records each run; an admin runs it by hand from the rent roll's
⋯ menu for a catch-up, and the runs are listed there. The run moves no trust money: charges are
what tenants owe, and bills wait to be set aside and paid. Fees still post only when staff ask
(decision 4).

The rent roll lists every lease (current, to come, ended, owing or all) with its start and end,
days left, rent, the deposits held and its balance, the row opening the lease; Receive payment
finds a lease by its tenant, unit or property and records a payment with how it came (check,
money order, EFT or cash; the method only, no bank file).

## Owners, Pay owners and the fees

The fourth piece (migration 0068): the Owners list shows each owner once with what their accounts
hold, keep back and may pay out; an owner's page has Summary (contact details, portal login, each
agreement's terms), Properties, Financials (their statement and Record contribution), Statements
(each of the last twelve months and the year's income statement) and Files. Pay owners is
Buildium's owner draw: choose owners or narrow to a group or property, change an amount, say how
and when it was sent, and pay them all at once, every draw or none, each at most what is
available; how each left trust (check or EFT) is recorded, and the PMS writes no bank file. It
replaces the one-at-a-time draw. Management fees has its own page under Accounting: each
agreement's fee for a period, worked out by the database and rolled back so looking moves
nothing, then Collect fees (decision 4). The company's fees leave trust from Banking, and a
lease's leasing fee from the lease's ⋯ menu.

## Banking

The fifth piece (`src/pms/api/banking.py`, no migration): Banking lists the trust bank accounts
and adds one (Add bank account); an account's page has tabs. Transactions is its register: each
move of its cash with what it was (rent payment, security deposit, returned payment, owner draw
or contribution, bill payment, company fees, deposit returned, opening balance), who paid or was
paid, its memo, its property and the balance after it, the last ninety days at first and a page
at a time, the balance counting everything before the days shown. Reconciliations is its
month-end, where a month waiting for approval opens. Balance breakdown is what it holds by kind,
coming to its book cash (the tie-out), and each owner's money in it. Statements are its imported
files and Import statement. A rent payment, a bounce and a deposit now carry a memo naming the
tenants and the home, so the books side of a reconciliation reads as well as the bank's. Read
only: the page moves no money.

## Bills

The sixth piece (migration 0069, `src/pms/api/payables.py`): Bills lists bills a page at a time
in tabs, Unpaid, Paid, Recurring and Declined (kept: a declined bill is closed, not paid),
narrowed by property group, vendor and the day they are due by, each Due, Vendor, Property,
Memo, Ref #, Amount and its status (awaiting owner approval, approved, set aside, overdue, paid,
declined), the row opening the bill and its next step in its ⋯ menu. Record bill opens a side
panel: the bill, one expense account or lines splitting it across several (they come to the
bill, checked at commit; the income statement counts each line in its account), and the vendor's
invoice, kept with the bill as its receipt in the same request. A bill's page has its summary,
its lines, its files, the email asking its owner to decide it and whether it went (Bills' list of
emails to owners is gone), and its history; it sets the bill aside and pays it, by check or EFT.
Pay bills chooses bills ready to pay, shows what each property's owner account holds against
them, and sets aside and pays them all at once, every one or none, recorded as sent by check or
EFT. The nightly run enters each month's recurring bills; the Enter recurring bills button is
gone.

## The demo seed

The seventh piece (`showcase` in `src/pms/demo.py`): the demo PMC gets something to click into
on every list. Two more properties, Harbor Tower (48 units, a third owner's) and Oak Terrace,
make six, in two groups, North portfolio and South portfolio. This month's rent is charged to
every lease in effect and paid but three: one paid part, one none, one's payment bounced with
its NSF fee; the nightly run (as the manager runs it by hand) charges the late fees and enters
the landscaper's monthly bill. Oak Terrace has a lease ending in 45 days, its renewal out to
both tenants and signed by one, and a lease starting next month; Harbor Tower has a lease that
ended last month with its deposit still held. The vacant units have an applicant at each step
the PMS knows (received, screened, approved and sent its lease, declined with its notice). There
are bills due this week, one overdue, one waiting for its owner over the $500.00 limit and
three paid; five work orders, one at each step; four vendors, one with a W-9 on file and one
whose policy ran out; and files on a lease, a vendor, an owner and a bill. Everything this month
is dated after last month's prepared reconciliation, so the demo still opens on it.

Two of the punch list's states had no record in the PMS yet: an applicant who withdrew (the
leasing pages below add it, and the demo has one), and a lease offer kept as a draft before it
is sent (an offer goes to the e-sign partner when it is made), which Draft leases adds below.

## Listings and Applicants

The eighth piece, the first half of the build order's ninth step (migration 0070,
`src/pms/api/leasing.py`, `src/pms/api/listings.py`). Listings lists each unit that comes free
within 90 days (the first day no lease holds it) or has an open application link, narrowed by
property group, a page at a time: its available day (or Leased), its rent (its link's, else its
last lease's), the applications through its link and whether it is on the public vacancy page.
List puts it there, opening a link at the rent asked if it has none (one request); Unlist takes
it off; its ⋯ menu opens a link to post anywhere (shown once) or closes the open one. Copy public
page link copies `/listings?pmc=<id>`. Applicants lists applications newest first in tabs, Open
(new, screening, or approved and not a lease yet), Rejected, Withdrawn and All, narrowed by
property group: Applicant, Unit, Applied, Status (New, Screening, Approved, Approved on
conditions, Rejected, Withdrawn), Screening (the latest recommendation) and income to rent. An
application's page has its summary, applicants, screenings and decision with its adverse action
notice; Screen, Approve and Reject sit beside its title (a rejection resting on a screening
records the notice, as before), Create draft lease opens Draft leases on it, and its ⋯ menu
records a withdrawal. A withdrawal (`trust_application_withdrawals`) is recorded once, by staff,
with why if the applicant said; it comes before a decline and before a lease is made of the
application, and after it the database refuses a screening, a decision, an offer or a lease for
it. The applicant's status link says Withdrawn. ADR 0002 holds: the PMS keeps names, emails and
income; the partner keeps the rest, so the page shows no employment, history, pets or vehicles.

## Draft leases

The ninth piece, the second half of step 9 (migration 0071, `src/pms/api/offers.py`). Draft
leases lists lease offers a page at a time in tabs, Waiting for signatures (drafts, sent,
partially signed, signed), Leases made, Void and All, narrowed by property group: Unit, Tenants
(its signers), Start, Rent, Status (Draft, Sent, Partially signed, Signed, Lease active, Void) and
Created; the row opens its page. New draft lease opens a side panel, from an approved applicant
or a lease to renew (an application's Create draft lease and a lease's Renew lease open it on
that one): days, rent, the security deposit it asks, monthly fees and credits besides rent, the
company's own PDF and who signs; Save draft keeps it unsent, Send for signing sends it at once. A
draft lease's page shows its terms, deposit, monthly charges, signers and who has signed, and the
PDF to open; Send sends a draft, Resend shows each signer's link again, Check signatures records
what the partner reports, Activate lease makes it the lease once everyone has signed (its monthly
charges become the lease's recurring charges from its first day), and its ⋯ menu voids it, with
why. A void offer is never sent, signed or made the lease, the tenant's portal stops showing it,
and the lease expirations report no longer counts it as a renewal out (a golden case pins that).
"Waiting to be the lease" is now the Waiting for signatures tab.

The deposit is the one the lease asks: what is held still comes in through Record deposit, as
money in the security-deposit trust account, so nothing here moves money. The PDF opens in its
own tab rather than inside the page: the API serves every response under `frame-ancestors 'none'`
and `X-Frame-Options: DENY`, and that stays. The fake e-sign partner keeps a voided envelope; a
real partner's void call comes with the real partner, which launch still needs.

## Work orders

The tenth piece, the first half of step 10 (migration 0072, `src/pms/api/work_orders.py`). Work
orders lists every work order a page at a time, newest first, narrowed by property group or
property, status (Open, Assigned, Scheduled, Completed, Cancelled, All statuses) and vendor: #
(numbered in the order they were opened), Property / Unit, Title, Vendor, Priority, Status (New
before any step), Due (red once past and not closed) and Updated; the row opens its page. New
work order opens a side panel: property, unit or the whole property, title, priority (Low,
Normal, High, Urgent), what is wrong, the day it is due and whether the vendor may let themselves
in; it then opens the new work order's page. A work order's page has its summary, the tenants of
its unit's lease today with their phone and email, its photos (a document kind of their own), its
timeline and its bills; Next step assigns a vendor, schedules the visit for a day (only once a
vendor is assigned), completes or cancels it; Edit details changes its details (a new row each
time, naming who; the same again writes nothing); Create bill opens Record bill with its vendor,
property and title filled in, naming the work order.

A vendor whose insurance has run out (every policy of theirs started by the day ended before it)
is not assigned: the database refuses it and the page says to record the renewal first. A vendor
with no policy on file is not refused, as before; the vendors page and the Dashboard say so. A
scheduled work order counts with the assigned ones in the work orders report (a golden case pins
it), and a tenant's request reads "A visit is scheduled". Nothing here moves money.

## Vendors

The eleventh piece, the second half of step 10 (migration 0073, `src/pms/api/vendors.py`).
Vendors lists every vendor a page at a time, by name: Vendor, Trade, Phone, Email, W-9 (On file
or Missing) and Insurance (Current, Expires in N days, Expired or None on file, by the coverage
that runs out first); the row opens their page. Add vendor opens a side panel: name, trade, how
they are paid (check or EFT), whether they are a 1099 vendor, email and phone; it then opens the
new vendor's page. A vendor's page has tabs: Summary (trade, 1099, how they are paid, W-9, and
their contact details; Edit details in a side panel), Insurance (each policy with its standing,
and Add policy, the form that was on the list), Bills (each linking to its page), Work orders (the
ones last assigned to them) and Files (their W-9s). The Vendor insurance (PDF) download moves to
Reports. Decision 3 holds: no tax ID is kept; a 1099 vendor is a yes or no, and the PMS files
nothing. A profile is a row of `trust_vendor_profiles`, append-only, the latest holding.

## Reports

The twelfth piece, step 11 (`src/pms/api/reports.py`, `frontend/src/reports.ts`). Reports lists
every report in a group: Financial, Rentals, Owners, Vendors (the vendor payments report is the
1099 summary), Maintenance and Compliance. Each opens its own page with the filters its
arguments take: a day; days, with This month, Last month, Year to date and Last year; an owner;
a year; and, when its lines have a property, All properties, a property group or one property.
Its lines show on screen as the report wrote them (`format=json`, the first 500; Export has them
all), and Export downloads it as PDF, Excel or CSV with the same filters. Narrowed to a group or
property, a report keeps the lines of its properties and its header lines, leaves out every
other line without a property (a total across all properties would no longer add up), and its
last line says what it was narrowed to; a report without a property column is not narrowed.
The whole ledger moves to Settings, Data export. There is no accrual basis: the PMS keeps cash
basis only (decision, ADR 0006).

## Files

Step 12a (migration 0074, `src/pms/api/documents.py`). A file may be attached to any record a
property manager keeps one for: a property, unit, lease, tenant, owner, vendor, work order or
bill, each with its own categories (a lease's documents and move-out photos, a vendor's
documents and W-9s). Files lists every file of the company in one table, newest first, a page at
a time: its name (to download), what it is attached to (to that record's page), its category,
the day it came and who uploaded it, with Remove in its row's menu. It narrows by category and
by property group or property, by where each file's record is (an owner's file is at each of
the owner's properties; a vendor's is at none, so a narrowed list leaves it out). Upload opens a
side panel: the file, its name, what it is attached to and its category. Each record's page
has a Files card of its own files. The owner portal still serves an owner only their owner
documents; the PMS reads nothing out of a file, and the AI role never reads one.

## Budgets

Step 12b (`src/pms/api/budgets.py`, `frontend/src/pages/BudgetsPage.tsx`). Budgets picks a
property (and, where a property has more than one owner, which owner's share) and a year, and
shows one grid: the income statement's lines down the side, January to December and a Total
across. A total typed in spreads over its months to the cent; the last row is the net income.
One Save writes every line at once, all or none, and says how many months changed. Budget vs
actual lays each line's budget beside what happened that year, from the budget vs actual report
narrowed to the property (cash basis); Export downloads the owner's whole report.

## Settings

Step 12c. Company setup is the company itself; everything else set up there moves to where it
is used, each form in a side panel (`frontend/src/pages/SetupPanels.tsx`, the same
`/api/setup` write paths): bank accounts on Banking, owners on Owners, vendors on Vendors, a
property from Properties (then its page), and on a property's page its units, another owner,
each owner's management terms and the late fee and NSF fee terms in force, which the property's
page now shows; a lease already in place (with its tenants, added there) on a unit's page;
management terms on the owner's page too. Expense accounts are Settings, Chart of accounts.
Logins is a read-only list of everyone's login, narrowed by status, a login's Disable in its
row's menu; invites are on each person's own page (an owner's or a tenant's Portal card, and
each staff member's row on the staff page). The company's address, logo and default fee terms
come next (step 12d).
