# ADR 0001: Where the free PMS is built

The decision lives in AgenticHelixis: `docs/decisions/ADR-free-pms-placement.md` (read at
commit `7a00f5a6e2a1740a7dd062090b4aef7de4bdc5e8`), with the research in
`docs/research/free-pms-gaps.md`.

In short: the free PMS is its own repository, with its own database and deploys, sharing no
tables, secrets or CI with AgenticHelixis. It started private; the founder made it public under
Apache-2.0 on 2026-10-09. Occupella reaches it later only through its API,
with a key that cannot move money. It follows Occupella's conventions so a later merge means
moving folders, not rewriting.

Phase 0 is this repository's first state: the ledger schema and the tests that prove it, with
no server, hosted database, UI, API or real data.
