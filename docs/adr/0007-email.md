# ADR 0007: Email beyond a bill's approval

Decided by the founder on 2026-10-17. Asked "can we send emails from it" while reviewing the PMS
against 150 property-management questions, the founder marked every email question a must-have
and, offered email, online payments, text messages and bank payments out, chose email alone.
ADR 0004 brought in one email (a bill waiting for an owner's approval) and kept every other one
out "until the founder asks"; this is that ask.

## Decision

The outbox of ADR 0004 (`trust_emails`, written in the transaction that causes it, delivered
after the commit by the server's `PMS_EMAIL_SENDER`, every attempt recorded) carries more kinds
of email, built in two steps. This first step (migration 0076):

- **Portal invites are emailed.** When the server sends email, an invite staff make (from a
  tenant's or owner's Portal card, or a staff member's row) is emailed to the person with its
  link. The page still shows the link once, in case the email doesn't arrive.
- **A forgotten password, reset by email.** Sign-in offers "Forgot your password?" while the
  server sends email. An owner or a tenant gives their email and gets a link that sets a new
  password; until they use it, the old one still works. The answer is the same whether or not the
  email has a login, so it tells no one who does, and a login gets at most three resets an hour.
  Staff are not reset by email: an emailed link must not get past their authenticator, so an admin
  sends them a new invite, as before.
- **Staff write email.** A tenant's, owner's or vendor's page has an Email tab: Write email (a
  subject and the text, signed with the staff member's name and the company's) and every email
  the PMS wrote that person, newest first, with whether it went (sent, waiting to send, not sent
  and why). A property's page has Email tenants: one email to each tenant on a lease in effect
  there today, each on that tenant's tab; tenants with no email on file are named. An email goes
  to the person's contact email, else to their portal login's.
- **Who may.** Writing email is an area of its own for custom roles ("email to tenants, owners and
  vendors"); every staff member reads the tabs.

The second step adds the emails the PMS writes by itself: rent reminders, late notices, monthly
owner statements, vendor work-order notices and applicants' notices.

## How a sign-in link is kept

An invite's or a reset's link signs someone in, and the PMS keeps sign-in tokens only as their
SHA-256 (ADR 0002). So the link is never written to the outbox: the email's row holds a
placeholder, and the link waits in the memory of the server process that wrote it until the
email is handed to the sender, at most ten minutes. Another process delivering the outbox leaves
such an email to the one holding its link. One not sent in time is given up on, once, and says
so on the person's Email tab ("send a new one"). A server that sends no email writes no invite or
reset email at all: staff copy the invite link as before, and sign-in offers no reset.

## What stays the same

- The From address is the server's `PMS_EMAIL_FROM` (the company's own mail server, ADR 0004),
  with the company's name shown beside it.
- Emails and their deliveries hold people's addresses: they are not audited and the AI role reads
  neither. Records are kept for good.
- Still out: online payments, text messages, bank files (ACH or check printing), 1099 filing, the
  real screening and e-sign partners, listing syndication, and state rules beyond the no-negative
  rule.
