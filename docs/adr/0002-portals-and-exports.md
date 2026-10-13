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

## The owner portal, as built

`frontend/` (React 19, Vite, Tailwind v4, Occupella's DESIGN.md tokens), built into
`frontend/dist` and served by the API from `PMS_STATIC_DIR`, so one process and one origin
serve the pages and the API. The self-host image builds it in a Node stage.

- Sign-in, the invite page (password, and for staff the authenticator's QR code) and a home
  page for each kind of login. Staff see their PMC's people and whether each has a login.
- An owner sees each property they hold money in with its balance now, and their statement
  (`trust_report_owner_statement`) for whole UTC days, at most 3660, on screen or as PDF, Excel
  or CSV. The owner and the PMC come from the session only.
- Since 2026-10-13 (product item 3 of three the founder approved), an owner also reads their
  year-end package for a calendar year (`trust_report_owner_year_end`): per property and
  across them, the opening balance, what tenants paid (less bounces), fees, bills, draws,
  contributions, anything else and the closing balance, and the year's 1099-MISC rents, on
  screen or as PDF, Excel or CSV, with the year's whole statement as a PDF. It files nothing.
- Since 2026-10-13 (product item 2 of the founder's second list), an owner approves or
  declines, with a reason, each of their bills over their agreement's approval limit
  (`/api/owner/bills`). No money moves: an approval lets staff set the bill aside and pay it;
  a declined bill is never paid, and from that day it holds nothing back from the owner's
  draws. Staff record a decline an owner gives them the same way they record an approval.
- Pages are served with a Content-Security-Policy that allows only the app's own scripts,
  styles, fonts and data: images (the QR code), and no framing.
- Tests: vitest for the pages and helpers, including one that fails on a raw colour or a size
  off DESIGN.md's scale; API tests for each endpoint; and Playwright in Chromium against the
  served build on a seeded database (`e2e.yml`), which fails on anything the policy blocks.

## The tenant portal, as built

`src/pms/api/tenant.py` and `src/pms/api/maintenance.py` on migration 0029, and the tenant's
and staff's pages in `frontend/`.

- A tenant sees every lease they are on (the one in effect first), with its rent, who else is
  on it and the balance due at the end of today (UTC), and each lease's ledger
  (`trust_report_tenant_ledger`) on screen or as PDF, Excel or CSV. A lease they aren't on
  reads as not found, whoever's it is.
- No payments: the page says to pay the PMC as before (online payment needs Stripe keys,
  which hard rule 5 doesn't allow).
- Maintenance requests: a tenant on a lease in effect says what needs fixing and whether
  someone may come in while they're out, at most 10 a day per lease. Their co-tenants see the
  request too. The database refuses a request from anyone not on the lease, and never changes
  one.
- Staff see open requests, open a work order for one (at the lease's unit, at most one per
  request), assign it to a vendor, and mark it done or closed without work. The tenant sees
  each step as the request's status, never the work order or the vendor.
- Tests: migration tests (`tests/test_maintenance_requests.py`), API tests for each endpoint,
  the audit property machine (requests filed, refused and given work orders), vitest for the
  pages, and Playwright: a tenant joins, sees what is due and asks for a repair; staff take a
  request through to done.

## Leasing basics, part one, as built

Applications through a link per unit (the founder's choice, 2026-10-11), screening and adverse
action records: `src/pms/api/leasing.py`, `src/pms/leasing/screening.py`, migration 0030. E-sign
and renewals are part two.

- Staff open an application link for a unit at a rent and post it anywhere; it is shown once
  and kept as its token's SHA-256. Anyone with it applies, with no login: a move-in day, the
  household's monthly income, and up to six applicants' names and emails. A link takes at most
  50 applications a day. The applicant keeps a status link to see where it stands: received,
  in review, then the decision (with any conditions). The screening itself is never shown to
  them.
- Screening goes through a partner interface. The founder picks the real partner at deploy
  time; until then the only one is a fake that pulls no report and answers from income against
  rent (3x or more approve, 2x to 3x conditions, less decline). It is on only where
  `PMS_SCREENING_PARTNER=fake` (development and CI); otherwise screening is refused.
- A decision is approve, approve on conditions, or decline, maybe resting on a screening. A
  decline or a conditional approval that rests on a screening records its adverse action notice
  with it: the reasons, the reporting agency the partner names, and the day it was given. Staff
  download the notice as a PDF to give the applicants. Its wording lists what such a notice
  usually carries (the agency, that it didn't make the decision, a free copy within 60 days,
  the right to dispute); it is not legal advice, and a PMC should have its own counsel check it
  before relying on it.
- `trust_ai_agent` reads none of it. No application fee: payments stay out.

## Leasing basics, part two, as built

Lease offers, e-sign and renewals: `src/pms/api/offers.py`, `src/pms/leasing/esign.py`,
migration 0031. With it, the four things this ADR moved into scope are built.

- Staff offer a lease to an application they approved (outright or on conditions), at the unit
  it applied for, or the renewal of a lease that hasn't ended, starting after it does. An offer
  has its terms (start, end or month to month, rent) and the PMC's own lease document, a PDF of
  at most 5 MB uploaded with it: the PMS writes no lease text, so what a lease says stays the
  PMC's (and its counsel's) to decide.
- Who signs: for a new lease, the applicants unless staff name others; for a renewal, the
  people staff name (prefilled with the lease's tenants and their login emails). Up to six,
  each with their own email.
- The offer goes to the e-sign partner once. Staff send each signer their link (the PMS sends
  no email yet) and check the signatures, which are recorded as the partner reports them. A
  tenant signs a renewal of their own lease from the portal, matched by their login's email,
  and sees their signature as soon as the partner has it.
- When everyone has signed, staff make it the lease (`trust_complete_lease_offer`): a new
  lease's signers become its tenants, and a renewal keeps the old lease's tenants and ends it
  the day before. An application or a lease takes one completed offer, and the unit's one
  lease at a time still holds.
- The founder picks the real e-sign partner at deploy time. Until then the only partner is a
  fake, on only where `PMS_ESIGN_PARTNER=fake` (development and CI): its envelopes live in the
  server process's memory, its signing page (`/esign#<token>`) is part of the portal, and
  typing your name there is not a signature, which the page says. Without a partner no offer
  can be sent.
- `trust_ai_agent` reads none of it.
