# What's proven

Each rule the README says the database refuses, where it is, and the test that holds it. Every
rule is a trigger or a check in a SQL migration under `db/migrations/`, so it holds for every
database role and every program that writes to the books, not only this app.

The property tests (`tests/properties/`) are Hypothesis state machines: they make random
postings, payments, bills, draws and month-ends, keep their own model of every balance, and
check the database against it after each step. They run on every pull request in the
`ledger-invariants` workflow's `properties` job, and at greater depth every night.

| Rule | Where it's enforced | Test |
|---|---|---|
| A trust bank account's cash equals what it holds for owners, tenants, vendors and the company, at every commit ("does not tie out (off by …)") | `20261009000005_trust_account_tie_out.sql`, a deferred trigger; `20261025000210_tie_out_across_banks.sql` | `tests/properties/test_tie_out_properties.py`, `tests/properties/test_ledger_properties.py` |
| A posted ledger line is never edited or deleted; a mistake is fixed with a reversing entry | `20261009000002_ledger_append_only.sql`, triggers that refuse even the tables' owner | `tests/test_ledger_core.py`, `tests/properties/test_ledger_properties.py` |
| No account held for someone (an owner, a tenant's deposit, a vendor, the company's fees) goes below zero | `20261009000003_trust_model.sql` | `tests/properties/test_ledger_properties.py` |
| Whoever proposed a journal entry doesn't post it | `20261015000053_journal_entries.sql` | `tests/properties/test_journal_properties.py` |
| Whoever prepared a month-end reconciliation doesn't approve it | `20261012000034_staff_roles.sql` | `tests/properties/test_staff_roles_properties.py` |
| Nothing is posted in a period an approved reconciliation closed | `20261009000006_reconciliations.sql` | `tests/properties/test_bank_matching_properties.py` |

## What these don't prove

- **That the books match the bank between month-ends.** The tie-out compares the books with
  themselves. The bank is compared only at month-end, when one person prepares the
  reconciliation and another approves it.
- **That every real event can be recorded.** A tenant's payment that bounces after the owner's
  money has already gone out (a payout or a management fee) is refused, because recording it
  would take the owner's account below zero. The bank has already taken the money back, but the
  books can't record it until the owner covers the shortfall
  ([#232](https://github.com/brandonoscar/Occupella-PMS/issues/232)). That's an open accounting
  question, not a test gap.
- **That it's legal in your state.** `docs/law.md` records what was researched. No attorney has
  reviewed it.

## What else runs

Each workflow and the checks a pull request must pass are in [CI.md](CI.md). The nightly
workflows (the five-year fast-forward, the API fuzzer, the 1,000-unit speed run, the timing
tests run 50 times) don't all pass yet. Each failure has an open issue, and none is hidden.
