# ADR 0002: Portals, leasing basics and exports

Decided by the founder on 2026-10-11, after the trust ledger and items 1 to 3 of the parity
list (staff and audit, eviction holds, recurring bills, owner contributions, vendor payments)
were merged.

## Decision

Four things leave the out-of-scope list in CLAUDE.md and are built next, one PR at a time:

1. **Exports.** Every report as CSV, Excel and PDF, and a PMC's whole ledger (accounts,
   transfers and entries) as CSV files in a zip. Research row 10: "free is only useful if you
   can get your records back out" (AgenticHelixis `docs/research/free-pms-gaps.md`).
2. **Owner portal.** Statements per property and rolled up per owner, balances, report
   downloads (row 8).
3. **Tenant portal.** The lease, its ledger and balance due, maintenance requests.
4. **Leasing basics.** Applications, screening with adverse-action notice records (including
   conditional approvals), e-sign, renewals (row 11).

The API and logins they need come with them. The rest of that list stays out until the founder
says otherwise: Stripe and online payments, bank files, 1099 filing, state rules beyond the
no-negative rule.

## Choices

| Question | Chosen | Why |
|---|---|---|
| Stack | Occupella's: Python 3.11 + FastAPI, React 19 + Vite + Tailwind v4, following Occupella's DESIGN.md | The planned merge into Occupella moves folders instead of rewriting screens. |
| Login | Email and password (argon2 hashes) set from an invite link that staff copy to the owner or tenant; staff also use authenticator-app codes (TOTP) | No email service is needed yet, so no secret enters the repo (hard rule 5). Sessions live in Postgres. |
| Screening and e-sign | An interface for each, built and tested against a fake partner | The founder picks the real partners at deploy time; their keys and terms come then. |

## What still holds

- The hard rules: no hosted database, deploy or secret; synthetic data only; Apache-2.0
  dependencies from `ci/licenses.toml`. Pillow's MIT-CMU license (fpdf2 needs it for PDFs)
  was added there in the exports PR.
- The AI never moves money. The portals add no path for `trust_ai_agent` to write, and it never
  reads a password hash or a session.
- An owner or a tenant sees only their own records. Every API endpoint gets tests for the
  success path, a refused login or a record that isn't theirs, and bad input (standing rule 5).
- Online rent payment stays out of the tenant portal: it needs Stripe keys, and hard rule 5
  allows no secrets.

## Exports, as built

`src/pms/exports.py`, run with `PYTHONPATH=src python -m pms.exports` or inside the self-host
image. A report is run by name through the catalog, so nothing but a `trust_report_*`
function can be named, and under the connection's own role, so Postgres decides who may run
it.

- CSV keeps every value exactly. Times are UTC, NULL is an empty field, and text a spreadsheet
  would run as a formula gets a leading apostrophe.
- Excel keeps types. A cell is never a formula, times are UTC to the second, control
  characters XML can't hold become "?", and line breaks are "\n".
- The PDF uses the built-in Latin-1 font, so other characters print as "?". CSV and Excel are
  the exact copies.

## Order

Exports, then the API with logins (it also closes issue #11, the self-hosted app server), then
the owner portal, the tenant portal, and leasing.

## The API and logins, as built

`src/pms/api` (FastAPI, `python -m pms.api`) and `src/pms/accounts.py`, on migration 0028.

- Staff invite a person by email: the link is `<PMS_PUBLIC_URL>/invite#<token>`. The token sits
  in the fragment, so it never reaches a server log or a referrer. It expires after 7 days and
  is accepted once. A new invite is how a forgotten password is reset, and it ends the old
  sessions.
- Passwords are 12 to 1024 characters, hashed with argon2id. Staff enroll an authenticator
  (TOTP) from their invite, and every staff sign-in needs a code that works once.
- A refused sign-in reads the same whatever the reason. Ten failures in a row lock the login
  for 15 minutes.
- Sessions last 12 hours, in an HttpOnly, SameSite=Strict cookie (Secure unless
  `PMS_COOKIE_SECURE=false`). Changes must be JSON from `PMS_PUBLIC_URL`'s origin.
- Staff download any report or the ledger for their own PMC. Owners and tenants can sign in
  and see who they are; their own pages come with their portals.
- `python -m pms.admin add-staff` and `invite` make the first staff member's link on the server.
