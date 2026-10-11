# occupella-pms

Free trust accounting for property management companies (100–5,000 doors) that hold owners'
money in trust. Occupella's AI is sold on top and will connect later through an API key that
cannot move money.

Apache License 2.0 (see `LICENSE`). Third-party code and its licenses: `THIRD_PARTY_NOTICES.md`.

## Status

Built: the trust ledger (schema and the tests that prove it), its reports, and exports of
every report as CSV, Excel or PDF plus the whole ledger as CSV
(`PYTHONPATH=src python -m pms.exports --help`).

Being built, in this order (`docs/adr/0002-portals-and-exports.md`): the API with logins, the
owner portal, the tenant portal, and leasing basics.

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
