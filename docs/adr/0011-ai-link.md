# ADR 0011: The AI plugs into the PMS through the PMS's own API

Decided by the founder on 2026-10-31: three decisions answering the 10/24 "One Occupella"
plan, and then the link proposed below, approved as written:

- Rule 29 is reworded.
- Hosting runs from a separate private repository.
- The AI features are not open source: a self-hosted PMS gets them by turning on "Enable AI"
  and paying.

How the PMS takes the AI in (keys, drafts, events, the MCP server, the Enable AI button) is
the second part, below.

## Context

The plan keeps the code apart and makes it one product at the API, the sign-in and the screen:

- **`occupella-pms`, this repository:** public under Apache-2.0.
- **`agentichelixis`, to become `occupella-ai`:** private. It holds the assistant, its
  connectors and its billing.
- **`occupella-cloud`, new:** private. It runs this repository's image, unchanged, for hosted
  companies.

Two written rules forbade even that link:

- **Rule 29** of AgenticHelixis, as written, forbids the merge.
- **This repository's hard rule 4** forbids any hosted service.

The AI's role here, `trust_ai_agent`, already reads only what the app reads, never contact
details, notes, applicants or files (`AI_NEVER_READS`), and holds no grant that writes. That
stays true whatever the AI is sold as.

## Decisions (the founder, 2026-10-31)

1. **Rule 29 reworded** (in AgenticHelixis, with its twins, Propertyware rule 23 and Rent Manager
   rule 22): *connector code and vendor data never enter the PMS; the AI reads the PMS only
   through its public API; switching uses files the company exports.* Hard rule 1 here already
   says the first part and stays as it is.
2. **Hosting.** Hard rule 4 stays for this repository and its CI: no hosted database, Supabase
   project, Render service or deploy from here, and CI uses a throwaway Postgres. Hosted
   companies run from `occupella-cloud`, under its own rules, on this repository's published
   image, unchanged. CLAUDE.md's rule 4 says so.
3. **The AI is not open source, and nothing of it ships in the PMS:** no prompts, models,
   connectors, billing or chat. A self-hosted PMS gets its features by turning on Enable AI and
   paying for Occupella AI. What the PMS ships is open: the API keys, drafts, events and MCP
   server below, the Enable AI button and the screens that show drafts. They work with any AI,
   Occupella's or a company's own.

## The link (approved 2026-10-31)

- **API keys** (`trust_api_keys`: a name, what it may do, who made it).
  - An admin makes one in Settings, AI. Its secret is shown once and kept only as its SHA-256,
    like every other token here.
  - A request with a key runs as `trust_ai_agent`. It can read what that role reads and write
    drafts, and nothing else.
  - The database holds that line, not the app: the role has no grant to any write path, so no
    key can move money.
  - A key is taken back by a row (append-only); its requests are named in the audit log, and
    each key is rate-limited.
- **Drafts** (`trust_drafts`, with their decisions; append-only and audited).
  - The AI proposes; a person approves; the PMS does it, through its normal write path, as the
    staff member who approved, with that person's roles.
  - A draft names records by id, never contact details: "late notice to 2A, 3C and 4B, from the
    template Late rent." The PMS fills in the addresses when it sends.
  - Staff see drafts on the Dashboard, in a Drafts inbox and beside the record. They approve,
    edit or decline each, and the audit log keeps draft, decision and result.
  - The first kinds: an email or notice to tenants, owners or vendors; a work order opened,
    assigned or scheduled; a note.
  - Never anything that moves money: no payment, charge, bill, draw or fee.
- **Events** (`trust_events`).
  - Written in the same transaction as what happened: a payment recorded, rent late, a work
    order opened, a bill waiting for an owner, a lease ending, a draft decided.
  - Each carries its kind and ids only. The receiver reads the rest through the API, as its key
    allows.
  - Delivered as webhooks signed with HMAC-SHA256 under a secret per subscription, retried with
    backoff, each with an id so a repeat is harmless. Also readable by polling
    (`GET /api/events?after=`).
- **The MCP server** (`pms.mcp`, at `/mcp`, the key as its bearer).
  - Read tools: the reports, the rent roll, delinquency, a lease's ledger, work orders.
  - "Propose" tools that write drafts.
  - Any Claude, or any MCP client, plugs in with a key. The plan makes it the free way to try
    AI on a PMS.
- **Enable AI** (Settings, AI; admins only).
  1. The admin presses Enable AI. The PMS makes a key waiting to be paired, and a one-time
     pairing code that lasts ten minutes.
  2. The PMS sends the admin to Occupella's sign-up with the PMS's address and the code. Billing
     happens there: per unit a month (the 10/24 decision); the PMS never sees a card.
  3. Once billing is active, Occupella AI trades the code for the key over HTTPS
     (`POST /api/ai/pair`), and the key turns on.
  4. Pressing Turn off AI takes the key back. Occupella AI stops at its next request.
  - While a paired key is on, the PMS shows what the AI made (drafts, its inbox).
  - Its chat opens on Occupella's own site, signed in by a short-lived token the PMS issues, so
    the PMS's page never frames another site and its Content-Security-Policy stays as strict as
    it is.

The work, in this order, each piece a PR with its tests:

1. Keys.
2. Drafts.
3. Events.
4. The MCP server.
5. Enable AI and its screens.
6. Then, in `occupella-ai`, the connector to this API. AgenticHelixis is added to a session
   for that, last.

## Not decided here

- **A self-hosted PMS behind a firewall.** Occupella AI has to reach the PMS over HTTPS. A
  relay can come later.
- **What a company agrees to when it turns on AI.** Its records, never contact details, go to
  Occupella AI. That needs consent text on Enable AI and a data processing agreement (LAWYER).
- **How long events are kept,** and how webhook secrets are rotated.
- **More draft kinds** (renewal offers, rent changes): each needs its own approval. Anything
  that moves money stays out.
