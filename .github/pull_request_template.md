## What and why

<!-- One or two sentences. Link the issue it closes. -->

## Tests that come with it (the standing rule, CLAUDE.md)

Tick what applies. CI checks most of these on its own (docs/CI.md); the rest are on you.

- [ ] Code changed, so tests changed with it. No "tests later".
- [ ] Money-moving or ledger logic changed, so a new or extended invariant or property test
      (`tests/properties/`), not only an example test.
- [ ] A new migration: `db/schema.sql` regenerated with `scripts/dump_schema.sh`; db.yml covers it.
- [ ] A new report or statement: a golden case under `tests/golden/cases/`. Any change to an
      expected file is deliberate and carries the `golden-update` label.
- [ ] A new API endpoint: API tests for the success path, auth failure and bad input.
- [ ] A new feature area that no workflow covers: a workflow added or extended in this PR.
- [ ] No check weakened, no threshold lowered, no test skipped. Anything that can't pass yet has
      an open issue, linked from `ci/registry.toml` or the xfail reason.

## Verified

<!-- What you ran and what it showed: start with the summary scripts/check.sh printed, NOT RUN
lines included. Write NOT VERIFIED for anything you didn't check. -->
