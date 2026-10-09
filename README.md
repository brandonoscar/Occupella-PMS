# occupella-pms

Free trust accounting for property management companies (100–5,000 doors) that hold owners'
money in trust. Occupella's AI is sold on top and will connect later through an API key that
cannot move money.

Private repository. No license.

## Status: Phase 0

The ledger schema and the tests that prove it works. Nothing else:

- no server, no API, no UI, no login;
- no hosted database and no deploy (CI uses a throwaway Postgres 16 container);
- no real data. Every fixture is synthetic.

The decision and the Phase 0 plan live in AgenticHelixis:
`docs/decisions/ADR-free-pms-placement.md` (section "Phase 0 plan"), with the research in
`docs/research/free-pms-gaps.md`.

## Working here

Read [CLAUDE.md](CLAUDE.md) first: the hard rules, the conventions, and how to run the tests.
