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

The second step (migration 0077) adds the emails the PMS writes by itself. Each one is off until
the company turns it on in Settings, Company setup (Automatic emails), and none is written while
the server sends no email, so nothing piles up to go out late:

- **Rent reminders**, some days before the first of the month (1 to 15): to each tenant of each
  lease in effect on that first, with the monthly rent, what is owed already, and a lease ending
  in that month said so. Once per tenant, lease and due day.
- **Late fee notices**: to each tenant of a lease the run just charged a late fee, with what was
  unpaid and what is owed now. Once per tenant and fee; a fee charged before notices were on gets
  none.
- **Monthly owner statements**, from a day of the month the company picks (1 to 28): last month's
  statement in brief (opening balance, money in, money out, closing balance, all properties
  together) to each owner with a property account, with a link to the portal for the whole
  statement, or, with no portal login, a line to ask the company for it. Once per owner and month.
- **Work orders to vendors**: when one is assigned to a vendor or their visit scheduled, what and
  where it is, its priority, the day it is due, the day of the visit and whether they may let
  themselves in. Once per step. No tenant's contact details go to the vendor.
- **Adverse action notices to applicants**: when a decline or a conditional approval rests on a
  screening, its notice (the same text as its PDF: the reasons, the agency, that the agency did
  not decide and can't say why, the free copy within 60 days and the right to dispute) to each
  applicant at the email they applied with. Once per applicant and application; the applicant's
  page lists them with whether each went.

The nightly run (and an admin's Run rent now) writes the first three, in the run's own
transaction after it charges; the other two are written in the request that records the step or
the decision. A lease under a payment hold (an eviction filed) gets no reminder or late notice:
during an eviction, what goes to the tenant is for the company and its lawyer to decide. What each email is about is
on its row and a unique index keeps each once, so a run again, or two at once, writes nothing
new. Tenants and owners are written at their contact email, else their portal login's; vendors at
their contact email; anyone with none gets none.

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
