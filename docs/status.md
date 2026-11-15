# Status

What's built and what comes next. "On `main`" means merged and shipping in the next release
(CHANGELOG.md, Unreleased).

| Capability | Where | Next |
|---|---|---|
| Trust ledger: tie-out at every commit, append-only, no negative balances held for anyone | Since v2026.11.0 | On `main`, the self-host app logs in as `pms_app`, a role that can only act through the ledger's write paths; v2026.11.0 connected as the database's owner |
| Month-end bank reconciliation, prepared by one person and approved by another | Since v2026.11.0 | |
| Journal entries, two-person | Since v2026.11.0 | |
| Rent, recurring charges, late and NSF fees posted every night | Since v2026.11.0 | |
| Owner draws by NACHA file or printed check | Since v2026.11.0 | First files sent to a company's bank |
| Owner and tenant portals | Since v2026.11.0 | On `main`: owners and tenants give a phone and their texting consent at first sign-in |
| Leasing: applications, screening, leases signed on the PMS's own page | Since v2026.11.0 | A screening partner of the company's choice, through the partner interface |
| Rent paid online, autopay, owners paying in | Since v2026.11.0 | First live Stripe accounts. A guided way to record a bounce after the owner's money has gone out ([#232](https://github.com/brandonoscar/Occupella-PMS/issues/232)) |
| Stripe Connect: bank and card through each company's own Stripe account | On `main` | First live accounts |
| Switching from another PMS by CSV | Since v2026.11.0 | First company switched |
| Daily backups in the self-host stack | On `main` | Copying them off the server: [backup.md](backup.md) |
| State rules: deposit deadlines, rent notice, card surcharges | Warnings where staff set terms | [law.md](law.md) |
| API keys, drafts, events, webhooks, MCP server | On `main` | The keys' role reads the books and adds drafts for staff to approve; it never posts to the ledger |
| 1099 filing, a mailing service, selling renters insurance, reporting rent to credit bureaus | Not in scope | |

## This repository

- It deploys nothing. CI runs on a throwaway Postgres 16. Occupella's hosted service at
  [occupella.com](https://occupella.com) runs its own copy of this code.
- Every fixture, screenshot and log is synthetic, so no one's real data is ever in it.

## Edge cases

Every edge case found, and whether it's fixed and tested: [edge-cases.md](edge-cases.md).
