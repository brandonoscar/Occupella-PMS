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
