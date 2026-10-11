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

Being built next (`docs/adr/0002-portals-and-exports.md`): leasing basics.

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
