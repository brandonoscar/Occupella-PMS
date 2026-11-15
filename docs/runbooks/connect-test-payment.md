# Runbook: a $1 test-mode payment through Stripe Connect

For Brandon, before the hosted product takes real rent through Connect
(docs/adr/0014-stripe-connect.md). It shows, on Stripe's own dashboards and in the books, what a
payment of $1.00 by card leaves in the company's trust account, what Stripe kept and what
Occupella's application fee was. Every Stripe screen and test number below is from Stripe's
documentation as search excerpts showed it on 2026-11-13, not checked against a live account:
NOT VERIFIED. Nothing here runs from this repo's CI.

Test mode only. A live server (https at a host other than the machine itself) refuses Stripe's
test keys at start (docs/edge-cases.md P-048), so this runs on a laptop at
`http://localhost:8000`, with Stripe's CLI forwarding the platform's Connect events to it.

## 1. The platform account, in test mode

1. In Occupella's own Stripe account, switch to **Test mode** and turn on **Connect**
   (Connect, Get started; the platform's profile asks what it does: "Software for property
   managers", payments to the businesses themselves).
2. **Developers, API keys:** copy the test secret key (`sk_test_...`). Never paste it in a chat,
   a commit or a command line: put it in a file only your shell reads, as below.
3. Install the Stripe CLI and sign in (`stripe login`).

## 2. Run the PMS with the platform

```sh
export DATABASE_URL="postgres://postgres@localhost:5432/pms?sslmode=disable"
dbmate --no-dump-schema up
# The forwarding secret the CLI prints is the Connect endpoint's signing secret for this run.
stripe listen --forward-connect-to localhost:8000/api/payments/connect/webhook
```

In another shell, with the two secrets set from a file you `source` (not typed on the command
line, so they stay out of your shell's history):

```sh
# ~/.occupella-connect-test, readable by you alone (chmod 600):
#   export PMS_STRIPE_CONNECT_SECRET_KEY=sk_test_...
#   export PMS_STRIPE_CONNECT_WEBHOOK_SECRET=whsec_...   (the one `stripe listen` printed)
. ~/.occupella-connect-test
export PMS_CONNECT=stripe PMS_PUBLIC_URL=http://localhost:8000 PMS_COOKIE_SECURE=false
export PMS_STATIC_DIR=frontend/dist PMS_NIGHTLY=off
# Optional: Occupella's fee, percent,fixed,minimum. Unset: $0.50 a payment, bank or card.
# export PMS_CONNECT_FEE_CARD=0,0.50,0.50
(cd frontend && npm ci && npm run build)
PYTHONPATH=src python -m pms.api
```

Make a synthetic company with `python -m pms.admin setup ...` (docs/install.md, step 5) or the demo
(`python -m pms.demo`), and sign in as its admin.

## 3. Connect the company's account

1. **Settings, Company setup, Online payments:** turn on "Tenants and owners may pay online".
2. **Stripe Connect: Connect Stripe.** Stripe's onboarding opens. In test mode it offers test
   data (a test phone number, and the test bank account its form offers; NOT VERIFIED): use only
   test data, never a real person's or bank's.
3. Back in Company setup the card says **Connected ... Payments go through it.** (it asks Stripe at
   once; `stripe listen` also shows `account.updated` arriving).

## 4. Pay $1.00 by card

1. Sign in to the tenant portal as one of the company's synthetic tenants (invite them from their
   page). **Payments, Pay online:** amount `1.00`, **Pay by: Card**. The fee line shows what the
   tenant adds, if the company chose that (Company setup, Who pays the card fee).
2. **Pay online** opens Stripe's Checkout on the company's account. Pay with the test card
   `4242 4242 4242 4242`, any future date, any CVC.
3. `stripe listen` shows `checkout.session.completed` forwarded with a 200.

## 5. What to look at

With the company paying the fees and the defaults, a $1.00 card payment should read:

| Where | What | Expected |
|---|---|---|
| The connected account's dashboard (Connect, Accounts, the company, View dashboard as) | the payment | $1.00, fees $0.83: Stripe's $0.33 (2.9% + 30¢) and the application fee $0.50; net $0.17 |
| The platform's dashboard, Connect, Collected fees | the application fee | $0.50 from the company's account |
| The PMS, the lease's Ledger tab | the payment | a $1.00 rent payment, "Paid online" |
| The PMS, Banking, the operating trust account's register | the fees out of trust | "Payments partner's fee" $0.83 from the company's fee account, or waiting on Banking's Online payments waiting until that account holds it |

$0.17 is what Stripe pays out to the trust account; the books' cash moved by $1.00 in and $0.83
out. If Stripe's fee isn't $0.33, its price is not the one the ADR read: the books follow what
Stripe kept, but tell the founder, since a tenant's card fee is quoted from the published price.

## 6. A dispute, then winning it

Stripe's test card `4000 0000 0000 0259` opens a dispute after it pays (NOT VERIFIED). Pay $1.00
with it: the payment comes off the lease as a return, and the $15.00 dispute fee leaves trust from
the company's fee account (or waits on Banking). In the connected account's dashboard, submit the
evidence text `winning_evidence` (Stripe's test value to win, NOT VERIFIED): when Stripe reports
the dispute won, the payment is on the lease again and whatever Stripe gave back of the fee is
back in the company's fee account.

## 7. Before going live

- Read each figure in the ADR's table on Stripe's own pages and correct what differs.
- In live mode: the live secret key (`sk_live_...`), a Connect webhook endpoint in the platform's
  dashboard ("Listen to events on connected accounts") at
  `https://app.occupella.com/books/api/payments/connect/webhook` for the events Company setup's
  code lists (`pms.connect.CONNECT_EVENTS`), and its signing secret, each in the hosted app's
  environment (`PMS_CONNECT`, `PMS_STRIPE_CONNECT_SECRET_KEY`, `PMS_STRIPE_CONNECT_WEBHOOK_SECRET`,
  optionally `PMS_CONNECT_FEE_ACH` and `PMS_CONNECT_FEE_CARD`).
- Counsel's answers to the ADR's open questions (fees kept before payout from trust money, card
  surcharges by state).
