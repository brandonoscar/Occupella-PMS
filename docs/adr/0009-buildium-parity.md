# ADR 0009: What Buildium has, we have

Decided by the founder on 2026-10-23. Asked what Buildium has for banking, the founder set the
rule "if they have it we must have it" and chose to add back the items held out until now. This
supersedes CLAUDE.md's out-of-scope list (Stripe and online payments, text messages, bank files
for ACH and check printing, state rules beyond the no-negative rule) and the 2026-10-18 calls to
keep application fees, listing syndication and texts out (readiness q068, q077, q078, q091).

## Context

The readiness review stood at 110 of 126 questions answered yes on 2026-10-23. Every gap left
was something CLAUDE.md kept out: tenants paying rent online (q026) and by autopay (q027), owners
paying in online (q054), paying owners and vendors by ACH (q048, q059), printed checks (q058),
texts (q068, q091), listing syndication (q077), application fees (q078) and state rules (q142).
Buildium has each of them: its ePay takes rent by ACH and card with autopay, takes owners'
contributions and pays owners and vendors by EFT; it prints checks, syndicates listings and runs
homeowner associations (from Buildium's pages through web search, NOT VERIFIED against its
current product).

The research behind the state rules and the payment structure is
`docs/research/state-rules-2026-10-23.md`. It was built from search excerpts only: no statute
page could be opened from the build container, so its per-state values are NOT VERIFIED until
read in the statutes.

## Decision

In scope:

- **Rent online.** Tenants pay by bank (ACH) or card from the portal, with autopay; owners pay in
  online; applicants pay application fees online.
- **Payouts.** Owners and vendors are paid by an ACH file (NACHA) the company uploads to its own
  bank, or by printed check.
- **State and city rules** from a property's address.
- **Notices and messages.** Balance notices in one step, announcements, staff to-dos, texts.
- **Listing syndication**: a feed of the company's listed units that listing sites take.
- **Homeowner association management.**

How the money works:

- **Open source.** A company running its own server puts its own payment provider's keys in the
  server's environment, as with the email sender (`PMS_EMAIL_SENDER`): "fake" for development
  and CI, none by default. No key is ever in the repo or a workflow.
- **Hosted, free.** Each company connects its own Stripe account to the platform (Stripe Connect,
  the company's own account, direct charges). The company is the merchant of record, rent goes
  from Stripe to its trust bank account, and the platform never holds rent. The platform earns
  a fee the tenant sees before paying (a card or convenience fee), never a cut of the rent.
- **AI is a paid upsell.** The PMS does everything without it, and the AI role still moves no
  money.

Every payment feature keeps these rules:

1. Trust money is never short. What the ledger says a payment brought into trust reaches the
   trust bank account, and a processor's fee is never the owner's. If a processor takes its fee
   out of the rent before payout, the feature that brings the money in decides how the gap is
   covered and `docs/law.md` records why that is allowed. Open: the research found no regulator
   guidance on fees taken before deposit.
2. A payment is recorded once, through the existing write paths (`trust_post_transfers` under a
   key made from the provider's own id), however many times the provider reports it.
3. A payment that comes back (an ACH return, a card dispute) is recorded as a bounce is today,
   with the property's NSF fee.
4. A tenant always has a way to pay without a fee, and card fees follow each state's surcharge
   rules (California Civil Code 1947.3; Oregon, Illinois, Colorado, Connecticut, Massachusetts
   and Maine per the research, NOT VERIFIED).
5. Bank account numbers are stored encrypted, and the AI never reads them.

Three sessions build it at once, each in its own PRs under CLAUDE.md's rules:

| Lane | What | Migration versions |
|---|---|---|
| 1 | The nightly checks first, then money: rent online, autopay, the hosted version's Stripe Connect and fees, owners paying in, application fees, ACH files, checks | `YYYYMMDD000150`–`199` |
| 2 | One-step balance notices, state rules from the address, announcements and to-dos, texts | `YYYYMMDD000200`–`249` |
| 3 | Listing syndication, then associations (a design ADR approved before any money code) | `YYYYMMDD000250`–`299` |

A migration's version is the day it is written plus a number from its lane's range. Before a PR
merges, its migration must be newer than every migration on main; if not, it is renamed, which
is allowed because it isn't on main yet.

Still out of scope:

- 1099 e-filing.
- A mailing service. Where a state requires a notice by mail, the PMS gives a printable PDF; a
  mailing partner may come later as an add-on.
- Selling renters insurance. It takes a producer license or a licensed partner; the PMS still
  records any insurer's policy and picks none.
- Reporting rent to credit bureaus.
- The AI moving money.

## Consequences

- CLAUDE.md's out-of-scope line now lists only those.
- Each lane answers its readiness questions as it merges: q026, q027, q048, q054, q058, q059 and
  q078 (lane 1); q068, q091 and q142 (lane 2); q077 (lane 3).
- State rules are the company's to confirm (the founder, 2026-10-23: the property management
  company answers for the rules it follows, not the PMS). A rule enters the rules table only from
  a statute's text, read, cited and dated; each screen shows a rule as cited information with the
  day it was checked, never as a guarantee, and asks the company to confirm it. Where a law has
  exceptions the PMS can't see, the screens warn rather than refuse. The hosted version's terms
  of service say so.
