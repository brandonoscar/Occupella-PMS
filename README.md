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
  CSV too;
- a tenant sees their lease, what is due and its ledger, and sends maintenance requests, which
  staff turn into work orders and follow to done. Rent is still paid the usual way: the portal
  takes no payments.

Leasing basics, part one: staff open an application link for a unit, applicants apply
through it and follow their application, and staff screen (through a partner; a fake one for
now), decide, and record the adverse action notice a decline or conditional approval needs.

Leasing basics, part two: staff offer a lease to an approved applicant, or the renewal of a
lease, with the PMC's own lease document (a PDF); the signers sign through an e-sign partner (a
fake one for now), a tenant from their portal; and once everyone has signed, staff make it the
lease. That completes the founder's list in `docs/adr/0002-portals-and-exports.md`.

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
and the rest goes back to the tenants with an itemized statement. Documents come next.

Still true:

- no hosted database and no deploy (CI uses a throwaway Postgres 16 container);
- no real data. Every fixture is synthetic.

The placement decision and the Phase 0 plan live in AgenticHelixis:
`docs/decisions/ADR-free-pms-placement.md` (section "Phase 0 plan"), with the research in
`docs/research/free-pms-gaps.md`.

## Working here

Read [CLAUDE.md](CLAUDE.md) first: the hard rules, the standing rule that tests come with the
code, the conventions, and how to run the checks. [docs/CI.md](docs/CI.md) explains every CI
workflow and lists the required checks.
