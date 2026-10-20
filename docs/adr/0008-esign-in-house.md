# ADR 0008: Leases signed on the PMS's own page

Decided by the founder on 2026-10-19. Asked what should follow utility billing, the founder chose
"E-sign in-house", reversing the 2026-10-18 choice to keep the fake partner as a stand-in until a
real e-sign partner was picked at deploy time (ADR 0002). Supersedes ADR 0002's e-sign partner
paragraph.

## Context

A lease offer was signed through `pms.leasing.esign.Partner`. The only partner was a fake: its
envelopes lived in the server's memory, typing a name on its page was not a signature, and
without `PMS_ESIGN_PARTNER=fake` no offer could be sent. A real partner means a vendor account,
keys and a contract before the first lease, and a per-envelope fee for every PMC on a free PMS.

A signature is binding without a partner. Under ESIGN (15 U.S.C. 7001) and the Uniform
Electronic Transactions Act, adopted by every state but New York (which has its own Electronic
Signatures and Records Act), an electronic signature is any sound, symbol or process attached to
a record and adopted with the intent to sign it. What a partner sells is evidence: who signed,
that they agreed to sign electronically, which document, and when. The PMS can keep that itself.
`docs/law.md` has the rules and what was not verified.

## Decision

The PMS signs leases itself (migration 0097, `src/pms/api/signing.py`, `pms.leasing.esign`):

- **Every server signs.** Sending an offer records its signature request from "PMS e-sign" and
  makes each signer a link (`/sign#<token>`, kept only as its SHA-256, the token in JSON bodies).
  The PMS emails each link while the server sends email (migration 0090). A link works for 30
  days while the offer is neither void nor a lease and its signer hasn't signed. Staff's Resend
  makes new links without taking back the old ones; a tenant's portal makes them one each time
  they sign a renewal.
- **Consent first.** The page shows the consumer disclosures ESIGN asks before records are given
  electronically (7001(c)): signing on paper instead, at no charge; that the agreement covers
  this lease only; how to withdraw it and what follows; a paper copy of the signed lease, at no
  charge; what device and software it takes. The company's mailing address and the address its
  emails' replies go to (q086) say how to reach it. The signer ticks that they read it and agree,
  and the words they agreed to are kept with the agreement. "I'd rather sign on paper" (before
  or after agreeing) ends that link; staff see it.
- **Demonstrated access.** The signer opens the lease, a download of the very PDF sent, before
  they can sign; the first opening is recorded. The page shows its SHA-256.
- **The signature.** The signer types their name as it is on the offer, may draw their signature
  (a PNG of at most 100 KB), ticks that they read the lease and sign it, and signs. The
  signature names the lease's SHA-256, and the database refuses it unless it matches.
- **Evidence.** Each step keeps the database's own time, the address the request came from (as
  the server saw it: behind a proxy, the proxy's) and the browser's user agent. Everything is
  append-only and audited (the signer names no staff member). A signature is recorded as it is
  made: staff no longer "check signatures", and an offer sent here takes no signature any other
  way.
- **Copies.** Once signed, the signer downloads the lease and its signing certificate, and staff
  download the certificate from the offer's page: a PDF saying, for each signer, when their first
  link was made, when and from where they agreed, opened the lease, asked for paper and signed,
  the name typed and the drawing, and the disclosures' words.
- **Privacy.** The AI role reads no link, step or signature.
- **The fake partner is gone**, with `PMS_ESIGN_PARTNER` and `/esign`. An offer sent through
  another partner before this (development data only) takes no new link: staff void it and send
  a new one. `trust_signatures` still takes another partner's word for its offers, as before.

## Not built

- A digital seal on the signed PDF (PAdES, through pyHanko) or a timestamp authority: the
  certificate rests on the PMS's append-only records, not on cryptography a third party can
  check without them.
- Identity checks beyond the link sent to the signer's email: no ID document, knowledge-based
  questions or text-message code (texts are out of scope).
- Signing anything but a lease offer, notarization, and in-person signing on a staff device.
- Eviction notices: ESIGN (7003(b)(2)(B)) leaves notices of eviction or default under a rental
  agreement for a primary residence out of its rule, so they stay on paper; the PMS sends none.

## Consequences

- No partner account, key or fee; development, CI and production sign the same way.
- The company stands behind the disclosures' promises (paper at no charge), which the PMS states
  for it.
- The lease's words, and whether a state asks for more (a separate signature on a disclosure, a
  lead-paint form), stay the company's.
