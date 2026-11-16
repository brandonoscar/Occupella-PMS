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

## The bank, and the law

- **The bank.** The tie-out keeps the books consistent at every commit. Month-end then matches
  them to the bank statement line by line, prepared by one person and approved by another, and
  an approved month is closed for good.
- **State law.** Each feature follows the rule every state shares and, where states differ, the
  strictest one found ([law.md](law.md)).

## What else runs

Every workflow and the checks a pull request must pass are in [CI.md](CI.md): concurrency runs
with many staff at once, an API fuzzer, a five-year fast-forward of a company's nights, a
1,000-unit speed run, OWASP ZAP and backup restores. Work in progress on each is tracked in the
open issues.
