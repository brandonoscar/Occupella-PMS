# ADR 0013: Signing in through a host app

Decided by the founder on 2026-11-07, for the hosted product built from this repo
(AgenticHelixis `docs/decisions/ADR-one-product.md`, phase 4: one login; the design note
AgenticHelixis `docs/plans/one-login-one-ui.md`). The founder's four calls of that day: the books
answer on the host app's own origin, through a rewrite; a staff member's second step is the
host's; every admin of the host's company is an admin of its books; and the books' own password
sign-in is off in the hosted product.

## Context

Occupella mounts the PMS's API at `/books` in its own app and signs people in itself, with its
own login and its own authenticator for staff. The PMS has logins of its own (passwords, and an
authenticator for staff), a session cookie on its own origin, and invites. Two logins for one
product is the thing the founder ruled out. A self-hosted PMS keeps its own sign-in unchanged.

## Decisions

1. **A host hands the PMS a verifier.** An app that mounts the PMS passes it a `Verifier`
   (`src/pms/external.py`) in its `Settings` (`verifier`). The verifier checks the host's own
   sign-in's token and says who it is (a `Person`): the host's own id of them, an email it has
   checked is theirs, their name, the companies they are staff of (and whether an admin of each),
   and whether a staff member passed the host's second step in this sign-in. A server of its own
   has none, and the endpoint below answers 404.
2. **The host's sign-in becomes a session here.** The host's frontend sends the token to
   `POST /api/external-sign-in`, on the books' own origin (so the PMS's cookie, its JSON-only and
   same-origin guard and its Content-Security-Policy hold as tested). The PMS makes the same
   session cookie a password sign-in makes. A token the host doesn't know gets the answer a wrong
   password gets, and counts toward the same limit of failed sign-ins per address. A verifier
   that fails (its keys can't be fetched) is answered 503, and the log names only the failure's
   kind, never the token.
3. **What the PMS still checks.**
   - A staff member signs in to a company only while the host says they are its staff, and only
     after the host's second step (403 otherwise: "staff sign in with two-step sign-in").
   - A login disabled here signs in no more, through the host or not.
   - A company that isn't active signs no one in (`python -m pms.admin status`).
   - An owner or a tenant joins only through the invite staff sent them, and only at the
     invite's own email. Their login is linked to the person, and the invite used up. A staff
     invite is never used this way.
   - A person is linked to one login per company and a login to one person per host
     (`trust_external_identities`, append-only). A company another than theirs reads as any
     refusal does.
4. **Staff join on their first sign-in.** They join as the staff login with their email, when
   there is one not linked yet, or as a new staff member of their own. A company whose books
   aren't open yet is opened by its admin's first sign-in: its id is the host's, with its
   starting chart and kinds of fee, as `python -m pms.admin setup` gives one. A staff member who
   isn't its admin is told an admin opens them first.
5. **The host's admins are the books' admins.** Each staff sign-in gives or takes the admin role
   as the host says (`trust_staff_roles.host_issuer`: a change naming no staff member but the
   host, of the admin role alone, of a staff member the host signs in). A company always keeps an
   admin: taking it from the last one is left undone, and logged. The books' other roles
   (approve, disburse, custom roles) and trust account signers stay the books' own, set at
   Settings, Staff.
6. **Password sign-in can be off.** `PMS_PASSWORD_SIGN_IN=off` turns off, with 403: signing in
   with a password, a password reset, accepting an invite by setting a password, a staff
   member's authenticator from their invite, an owner's or a tenant's own two-step sign-in, and
   staff invites (staff come from the host). The sign-in page says where to sign in instead.
   Owners and tenants are still invited by staff; with `PMS_INVITE_URL` their link goes to the
   host's own page (its token in the fragment, as always), whose frontend sends it with the
   host's token.

## What stays as it was

- The self-hosted PMS: password sign-in on, no verifier, and nothing of this reachable.
- Sessions: twelve hours, an hour idle, ended by signing out or disabling the login. The host
  signs people out of both (AgenticHelixis' part).
- Everything a session may do once it exists: the roles, the custom roles, the audit log naming
  the staff member, and the AI's key, which never signs in this way.

## Consequences

- A login linked through a host has no password, so every page and email that asks whether a
  login signs in counts a host's link as well as a password (`tests/test_external_identities.py`
  reads every query that reads the passwords).
- The host is trusted with who a person is and which companies they are staff of. A host that
  says wrongly that someone is an admin makes them one here. The PMS can't check that, so it
  checks everything else.
- Staff the host no longer counts keep their login here until it is disabled, but sign in no
  more through the host; nothing is deleted.

NOT VERIFIED: the hosted product's own verifier (AgenticHelixis), the rewrite carrying the
cookie, and the host's sign-up page. Those are AgenticHelixis' work. Here, the endpoint is tested
with a stand-in host (`tests/api/test_external_sign_in_api.py`).
