# ADR 0004: Email to an owner when a bill waits for them

Extended on 2026-10-17 by `docs/adr/0007-email.md`: invites, password resets and what staff
write to tenants, owners and vendors go through this outbox too.

Decided by the founder on 2026-10-13, with product item 2 of the second list (rental owners
approve or decline bills over their approval limit in the owner portal): "it's over the approval
limit it should send an email, like asking for approval". Asked whether to bring email into
scope with a pluggable sender, and whether the email decides anything: "Yes, pluggable sender";
"Link to sign in".

## Decision

Email comes into scope for one purpose: when a bill over an owner's approval limit is entered,
the PMS emails that owner, at their portal login's email, that it waits for them. The email
names the vendor, the bill's reference, property, amount, memo and due date, and the owner's
limit, and links to the portal. The owner signs in there to approve or decline it. Nothing in
the email decides anything or carries a token.

- **Pluggable sender.** The server's `PMS_EMAIL_SENDER` picks it, as `PMS_SCREENING_PARTNER`
  and `PMS_ESIGN_PARTNER` do: `fake` for development and CI (the email is kept in the outbox and
  sent nowhere), `smtp` for the PMC's own mail server, and none by default (nothing is sent; the
  email waits in the outbox).
- **SMTP settings are server settings.** `PMS_SMTP_HOST`, `PMS_SMTP_PORT` (587 by default),
  `PMS_SMTP_USERNAME`, `PMS_SMTP_PASSWORD` and `PMS_EMAIL_FROM` come from the environment of
  whoever runs the PMS. None is in the repo, a workflow or a log (hard rule 5). The connection
  always uses STARTTLS.
- **An outbox, written with the change that causes it.** Each email is a row of
  `trust_emails`, inserted in the same transaction as the bill, so an email exists only if its
  bill does. After the request commits, the app hands each email not yet delivered to the sender
  and records every attempt in `trust_email_deliveries`: delivered, or why not. One that failed
  is tried again on the next delivery, at most five times in all; `python -m pms.email deliver`
  delivers what waits, for a server that runs it on a schedule.
- **Once per bill.** A bill's approval email is written at most once.
- **Staff see it.** The bills page says whether the owner was emailed, and lists the outbox:
  each email, to whom, and whether it went.
- **Who gets none.** An owner with no portal login (or a disabled one) gets no email; the bills
  page says so, and staff ask them as before.

## What stays out

Every other email (invites, statements, notices to tenants, reminders) stays out until the
founder asks. So do payments, bank files, 1099 filing and the real screening and e-sign
partners (ADR 0002). The AI role reads no email and holds no grant on the outbox; emails and
their deliveries hold people's addresses, so they are not copied into the audit log either.
