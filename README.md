# occupella-pms

Free trust accounting for property management companies (100–5,000 doors) that hold owners'
money in trust. Occupella's AI is sold on top and will connect later through an API key that
cannot move money.

Apache License 2.0 (see `LICENSE`). Third-party code and its licenses: `THIRD_PARTY_NOTICES.md`.

## Status

Built: the trust ledger (schema and the tests that prove it), its reports, exports of every
report as CSV, Excel or PDF plus the whole ledger as CSV
(`PYTHONPATH=src python -m pms.exports --help`), the API with logins
(`python -m pms.api`; the first staff invite comes from `python -m pms.admin`), and the portals
(`frontend/`, served by the API):
- an owner sees each property's balance and their statement for any period, as PDF, Excel or
  CSV too, and approves or declines each bill over their approval limit, which the PMS emails
  them about (through the PMC's own mail server; `docs/adr/0004-email.md`);
- a tenant sees their lease, what is due and its ledger, and sends maintenance requests, which
  staff turn into work orders and follow to done. Rent is still paid the usual way: the portal
  takes no payments.

Leasing basics, part one: staff open an application link for a unit, applicants apply
through it and follow their application, and staff screen (through a partner; a fake one for
now), decide, and record the adverse action notice a decline or conditional approval needs.

Leasing basics, part two: staff offer a lease to an approved applicant, or the renewal of a
lease, with the PMC's own lease document (a PDF); the signers sign on the PMS's own signing page
(`docs/adr/0008-esign-in-house.md`: the consumer disclosures ESIGN asks, a typed and maybe drawn
signature, and a signing certificate), a tenant from their portal; and once everyone has signed,
staff make it the lease. That completes the founder's list in `docs/adr/0002-portals-and-exports.md`.

The staff back office (`docs/adr/0003-staff-back-office.md`) is being built: staff set up
their books in the portal (trust bank accounts, owners, properties, units, tenants, vendors,
management and late fee terms, leases in place) and do the rent work there (the rent run, late
fees, fees and credits, tenants' payments and deposits as they reached the bank, prepaid rent,
bounced payments), vendors' bills (entry, the owner's approval, setting aside and payment,
recurring bills), owners' money (draws, contributions, management and leasing fees) and the
month-end: bank statements imported from CSV or OFX, matched to the books, and the
reconciliation prepared by one staff member once the books lack no line and approved by
another; a switching tool that brings a PMC's roster, opening balances and uncleared checks
over from its old system, with a tie-out staff sign off; and staff roles, so only those an
admin allows approve and pay out money; and move-out, where a lease's deposits pay what it owes
and the rest goes back to the tenants with an itemized statement; and documents (vendors'
W-9s, bills' receipts, move-out photos, owners' documents, which owners download in the
portal).

Staff and tenants may each subscribe their own calendar app (Google, Apple, Outlook) to a
private link of the PMS's: staff see the company's move-ins, leases ending, visits, bills due
and insurance running out; a tenant their rent due days and repair visits.

Staff sign in with a password and a code from an authenticator app. Owners and tenants may turn
the same two-step sign-in on in their portal, and a company may require it of all of them; an
admin's new invite is how one who lost their phone gets back in.

Still true:

- no hosted database and no deploy (CI uses a throwaway Postgres 16 container);
- no real data. Every fixture is synthetic.

The placement decision and the Phase 0 plan live in AgenticHelixis:
`docs/decisions/ADR-free-pms-placement.md` (section "Phase 0 plan"), with the research in
`docs/research/free-pms-gaps.md`.

## Try the demo

On a migrated database (`dbmate up`), `PYTHONPATH=src python -m pms.demo` adds a synthetic PMC
and prints sign-in links for its manager, an owner with three properties and a tenant. Serve
the portal (`python -m pms.api`, with `PMS_STATIC_DIR=frontend/dist` and `PMS_PUBLIC_URL` set to
where it's served) and open the manager's link: last month's reconciliation is prepared and
waits for their approval, with a check not yet cleared at the bank. Every other page has
something on it, and the Dashboard lists what else waits: a journal entry, the company's fees still in
trust, a vendor's insurance running out and a lease without renters insurance. Like every staff
member, the manager signs in with a password and an authenticator app.

## Working here

Read [CLAUDE.md](CLAUDE.md) first: the hard rules, the standing rule that tests come with the
code, the conventions, and how to run the checks. [docs/CI.md](docs/CI.md) explains every CI
workflow and lists the required checks.

## Running it yourself

A company runs the PMS on its own server with its own Postgres 16: [docs/release.md](docs/release.md)
says how a release reaches it and how to upgrade (fixed forward, never rolled back once money is
posted), and [docs/backup.md](docs/backup.md) how to back up the books and restore them
(`scripts/backup.sh`).
