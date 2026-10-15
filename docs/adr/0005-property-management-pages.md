# ADR 0005: Pages built around properties, units, tenants and owners

Decided by the founder on 2026-10-15, after clicking through the demo beside Buildium's general
ledger: "frontend is not property management friendly and it needs to be more like builidium",
and of a review of the portal against Buildium, "i need to fix all of this, you can add to it".
Asked four questions, the founder chose: merge the six items already built first; keep online
payments and a real e-sign partner out for now; add phones and mailing addresses; keep
Occupella's look (the 2026-10-14 decision) with Buildium's structure.

## What was wrong

The portal is built around forms, not around what a property manager works on. There is no
page for a property, a unit, a tenant, a lease, an owner or a vendor; properties exist only on
Setup. The API matches: of its 140 operations (50 GET, 90 POST, measured on 2026-10-15), none
reads one property, unit, tenant, lease, owner or vendor, and none edits one. `/api/rent` loads
every lease with every payment at once. Pages open with a form instead of a list, put a summary
sentence under every title and sentences where columns belong, show dates as 2026-09-13, and use
words of their own ("Keep it", "Waiting to be the lease", "Propose entry"). Maintenance and
leasing, the two biggest workflows, have no place in the menu.

The accounting underneath stays as it is: the trust accounts, month-end prepared by one person
and approved by another, brokers and signers, bill approvals, deposit returns and Schedule E.

## Decision

**The menu**, in Occupella's sidebar and look:

- Dashboard
- Rentals: Properties, Rent roll, Tenants, Owners, Outstanding balances
- Leasing: Listings, Applicants, Draft leases, Renewals
- Accounting: Banking, Bills, General ledger, Owner draws, Budgets
- Maintenance: Work orders, Vendors
- Files, Reports, Settings

**A page for each thing, at its own address.** `/properties/:id`, `/units/:id`, `/leases/:id`,
`/tenants/:id`, `/owners/:id`, `/vendors/:id`, `/work-orders/:id`, `/bank-accounts/:id`. The way
in is Properties → property → unit → lease → its ledger; a 250-unit building is one row until
it is opened. A search box at the top finds a tenant, property or unit by name.

**Where today's pages go.**

| Today | Becomes |
|---|---|
| Overview | Dashboard: outstanding balances, open work orders, leases ending in 30, 60 and 90 days, vacancies, applications waiting; its downloads move to Reports |
| Today | The Dashboard's "Needs attention" card, every alert kept (month-end, journal entries, vendor insurance, company fees in trust, signers, renters insurance) |
| Rent | Rent roll: one row per lease, filtered by property, paged; a row opens the lease page, where payments are recorded |
| Bills | Unpaid, Paid and Draft tabs; "Record bill" opens a side panel |
| Owners | One row per owner (not per property account); an owner's page has their properties, ledger, statements and draws |
| Move-out | A button on the lease page |
| Vendors | A list and "Add vendor"; a vendor's page has insurance, W-9, bills and work orders |
| Documents | A Files tab on each property, tenant, owner and vendor page, and Files in the menu |
| Month-end | Banking → account → Reconcile tab; Banking shows each trust bank account's balance |
| Journal | A "Journal entry" button on General ledger |
| Switching | Settings → Import from another PMS |
| Staff, Setup | Settings |

**The lease page** holds everything about one lease: its ledger, payments, charges, deposit,
recurring charges, renters insurance, rent changes, inspections, documents and move-out. A
payment's bounce moves into a menu, and recording it asks to confirm.

**General ledger on screen**, as the founder's screenshot of Buildium shows it: filtered by
property, account and dates, each account's prior balance, its entries and a running balance.
Cash basis only: tenants' charges stay outside the trust ledger (CLAUDE.md), so an accrual view
would be made up.

**Lists are paged.** Every list endpoint takes a page and a page size, sorts and filters on the
server, and says how many there are, so a PMC with 5,000 units loads one page.

**Edits, never deletes.** Properties, units, tenants, owners and vendors get edit endpoints;
every edit is audited like any write (`trust_audit_log`). Nothing is deleted: "delete" becomes
archive, which hides a record from lists and keeps its history, and is refused while anything
is open on it (a lease in effect, money held, an unpaid bill). Leases change only through the
write paths they have (end, renewal, rent change); money still moves only through `trust_*`.

**Contact details.** Tenants, owners and vendors get phone numbers and a mailing address, which
staff see and edit. The AI role never reads them, and the audit log hides their rows from it,
as for logins (`AI_NEVER_READS`, migration 0043). Fixtures and the demo use only never-issued
numbers and addresses (`tools/check_synthetic_data.py`).

**How it reads.**

- Each page opens with its list, and forms open in a side panel or on their record's page.
- No summary sentence under titles; columns where there are figures.
- Dates on screen as MM/DD/YYYY. The API, exports and reports keep ISO dates and UTC, so a
  report still prints the same everywhere.
- Money as accounting writes it: ($75.00) for money out.
- Buildium's words where they exist: "Record bill", "Journal entry", "Draft leases", "Upload
  file", "Outstanding balances".
- Anything that moves money (a draw, a bill paid, the company's fees, a deposit returned) shows
  the amount and asks to confirm.
- Every save says it saved, and every invite that it was sent.

## The order

The six items already built (company fees, cutover bills, signers, rent changes, renters
insurance, the demo) merge first, on the pages they were built for. Then, one PR each:

1. This ADR.
2. Backend: paged list, detail, edit and archive for properties, units, tenants, leases, owners
   and vendors, and the contact details.
3. The menu, search and the Dashboard with "Needs attention" (Overview and Today go).
4. Properties, property, unit and lease pages, and the paged rent roll.
5. Tenants and owners.
6. Accounting: Banking with Reconcile, Bills, General ledger with Journal entry, Owner draws,
   Budgets.
7. Maintenance and Leasing.
8. Files, Reports and Settings, and a last pass over words, dates, money and confirmations.

Each PR moves its pages' browser tests with them and updates `ci/features.toml`; a test is
re-pointed to the new page, never dropped.

## What stays out

Online rent payment and a real e-sign partner stay out (ADR 0002): both need a partner's API
keys, and none exist yet (hard rule 5). A later ADR decides the processor, its fees and where
the keys live. Accrual accounting, bank files, 1099 filing and other email stay out too.
