# Status

Where each part of the PMS stands. "On `main`" means merged but not yet in a release: the only
release so far is v2026.11.0 (CHANGELOG.md).

| Capability | State | Limits |
|---|---|---|
| Trust ledger: tie-out at every commit, append-only, no negative balances held for anyone | In v2026.11.0 | In v2026.11.0 the self-host app connects to the database as its superuser, which can turn triggers off. On `main` it logs in as `pms_app`, which can't |
| Month-end bank reconciliation, prepared by one person and approved by another | In v2026.11.0 | |
| Journal entries, two-person | In v2026.11.0 | |
| Rent, recurring charges, late and NSF fees posted every night | In v2026.11.0 | |
| Owner draws by NACHA file or printed check | In v2026.11.0 | No bank has accepted one of its files yet: NOT VERIFIED |
| Owner and tenant portals | In v2026.11.0 | Owners and tenants give a phone number when they first sign in on `main`, not in v2026.11.0 |
| Leasing: applications, screening, leases signed on the PMS's own page | In v2026.11.0 | Screening has only a test partner |
| Rent paid online, autopay, owners paying in | In v2026.11.0 | Never tried with a live Stripe account. A bounce after the owner's money went out can't be recorded yet ([#232](https://github.com/brandonoscar/Occupella-PMS/issues/232)) |
| Stripe Connect (card and bank through each company's own Stripe account) | On `main` | Never tried with a live account |
| Switching from another PMS by CSV | In v2026.11.0 | Not yet run for a real company |
| The daily backup service in the self-host stack | On `main` | Backups stay on the server until you copy them off ([backup.md](backup.md)) |
| State rules (deposit deadlines, rent notice, card surcharges) | Warnings where staff set terms | No attorney has reviewed them ([law.md](law.md)) |
| API keys, drafts, events, webhooks, MCP server | On `main` | The keys' database role reads the books and may add drafts; it can't post to the ledger |
| 1099 filing, a mailing service, selling renters insurance, reporting rent to credit bureaus | Not planned here | |

## What this repository doesn't do

- It deploys nothing and holds no hosted database. CI uses a throwaway Postgres 16 container.
  Occupella's hosted service runs its own copy of this code.
- It holds no real data. Every fixture, screenshot and log is synthetic.

## Known gaps

Every edge case found and whether it's fixed: [edge-cases.md](edge-cases.md). The nightly
workflows that don't pass yet each have an open issue.
