# ADR 0014: Stripe Connect for the hosted product

Asked by the founder on 2026-11-13: hosted Occupella takes rent online through Stripe Connect,
with Occupella's fee on every payment, by bank and by card. Built under ADR 0009 ("Hosted, free":
each company's own Stripe account, direct charges, the platform never holding rent) and its five
rules, which this keeps.

## Context

Until now a company pasted its own Stripe secret key and webhook secret in Company setup, or a
server used one account from its environment (`src/pms/payments.py`, `src/pms/api/payment_keys.py`).
Tenants and owners paid by bank (ACH) only, and Occupella earned nothing on a payment.

The founder's goal, in his words, numbered: a platform Stripe account whose secret comes only from
the environment; each company connects its own Stripe account through Stripe-hosted onboarding, no
keys pasted (1); money goes into the company's own Stripe account and out to its trust bank
account, never through Occupella's balance: direct charges with an application fee, on Standard or
Express accounts, chosen here (2); tenants, owners and autopay pay by bank or card, and a card
dispute is recorded as a bank return is (3); the fee always lands slightly above break-even (4);
who pays it follows `trust_payment_settings`, never an owner's money (5); card surcharges follow
docs/law.md and the card networks (6); one platform webhook maps each event's connected account to
its company (7); the self-hosted path keeps working (8); PR #237's guards hold (9); the fake
partner covers onboarding, card payments and disputes (10).

## What Stripe says, and how it was read

Stripe's own pages could not be opened from the build container on 2026-11-13: `stripe.com`,
`docs.stripe.com`, `edge-docs.stripe.com` and `support.stripe.com` don't resolve there (the
session's network allowlist). Every Stripe fact below is from web search result excerpts of the
named page, read on 2026-11-13, not from the page itself: **NOT VERIFIED**. Brandon checks each
figure on the named page before go-live; the runbook's $1 test payment
(docs/runbooks/connect-test-payment.md) shows the real fee Stripe keeps.

| Fact | Page | As read |
|---|---|---|
| Domestic cards 2.9% + 30¢; international +1.5%; currency conversion +1% | stripe.com/pricing | NOT VERIFIED |
| ACH Direct Debit 0.8%, capped at $5.00 | stripe.com/pricing | NOT VERIFIED |
| Dispute received fee $15.00; a $15.00 "countered" fee when responding by hand, given back for a won dispute | stripe.com/pricing | NOT VERIFIED (third-party pages disagree on whether the $15 comes back) |
| ACH failed payment $4.00 | third-party summaries only (dodopayments.com, superdupr.com) | NOT VERIFIED |
| "Stripe handles pricing for your users": no Connect fee for the platform | stripe.com/connect/pricing | NOT VERIFIED |
| "You handle pricing for your users": $2 per monthly active account, 0.25% + 25¢ per payout | stripe.com/connect/pricing; support.stripe.com/questions/monetizing-payments-with-stripe-connect | NOT VERIFIED |
| Standard = `controller.stripe_dashboard.type` full, `fees.payer` account, `losses.payments` stripe, `requirement_collection` stripe; Stripe carries the account's negative balances | docs.stripe.com/connect/migrate-to-controller-properties | NOT VERIFIED |
| Disputes on direct charges are debited from the connected account; a Standard account handles its own | docs.stripe.com/connect/disputes | NOT VERIFIED |
| Who pays a dispute fee on a direct charge follows `fees.payer` | docs.stripe.com/connect/direct-charges-fee-payer-behavior | NOT VERIFIED |
| A direct charge's application fee isn't refunded on a dispute unless the platform refunds it | no page states it either way | NOT VERIFIED |
| A Connect event carries a top-level `account`; the Connect endpoint has its own signing secret | docs.stripe.com/connect/webhooks | NOT VERIFIED |
| Checkout's `payment_method_options.card.restrictions.funding_types_blocked` (debit, prepaid) came in API version 2026-08-26.dahlia | docs.stripe.com/changelog/dahlia/2026-08-26/adds-card-funding-type-filtering-to-checkout-sessions | NOT VERIFIED |
| `charge.dispute.created`, `.closed` (won, lost), `.funds_reinstated` | docs.stripe.com/api/events/types | NOT VERIFIED |

No real Stripe call was made, in test mode or live: every Stripe answer the tests read is made up.

## Decision

**Standard accounts, direct charges, an application fee.** Each company's account is created by
the platform with Standard's controller properties (`pms.connect.account_form`) and onboarded on
Stripe's hosted pages through an Account Link that returns to Company setup. A tenant's or an
owner's payment is a Checkout Session on the company's account (`Stripe-Account`) with
`payment_intent_data[application_fee_amount]`; an autopay's is an off-session PaymentIntent on the
account the card or bank account was saved at, with `application_fee_amount`.

Why Standard, not Express:

- **Trust money never touches Occupella.** With direct charges, under either, the charge lands in
  the company's Stripe balance and pays out to the bank account the company gave Stripe, its
  operating trust account. Only the application fee reaches the platform.
- **Occupella carries no one's losses.** Under Standard Stripe carries a connected account's
  negative balances and the account its own disputes; under Express the platform does, so a
  company's chargebacks would be Occupella's debt.
- **No Connect fee.** Standard is "Stripe handles pricing": nothing per account or per payout. Express
  is "you handle pricing": $2 per active account a month, 0.25% + 25¢ a payout, and the platform
  pays Stripe's processing on a direct charge.
- **The company is the merchant of record**, with Stripe's full dashboard (its disputes, refunds,
  payouts and 1099-K are its own), as ADR 0009 and docs/law.md already require.

What Standard gives up: Stripe runs the onboarding and the dashboard, so the PMS can't style them,
and a company already on Stripe gets a new account under its Stripe login rather than linking the
old one (OAuth for Standard is Stripe's older path; not built).

### The fee (founder's item 4)

Occupella's fee is a server setting per method, `PMS_CONNECT_FEE_ACH` and `PMS_CONNECT_FEE_CARD`,
each `percent,fixed,minimum` (percent in percent: `0.25,0.30,0.50` is 0.25% + $0.30, at least
$0.50), on what goes to the lease or the owner. A setting below what a payment costs the platform
under the chosen model, at any amount, refuses to start the server (`pms.connect.read_fee`,
`below_cost`, which checks every point where the fee or the cost changes slope).

What a payment costs the platform (`pms.connect.cost`): Stripe's processing (if the platform pays
it), the active-account fee spread over the account's payments (one a month, the worst case), the
payout fee (one payout per payment, the worst case), and the dispute fee spread over payments (1 in
100 card payments, 1 in 200 bank payments; Stripe publishes no rate, card networks act at about
1%, NOT VERIFIED).

Worked for a $1,500.00 rent:

| | Standard (chosen) | Express (not chosen) |
|---|---|---|
| Bank: Stripe's 0.8% capped at $5.00 | $0.00 (the company's account pays $5.00) | $5.00 |
| Bank: active account, $2.00 / 1 payment | $0.00 | $2.00 |
| Bank: payout, 0.25% × $1,500 + $0.25 | $0.00 | $4.00 |
| Bank: dispute, 0.5% × $15.00 | $0.00 (the account's) | $0.075 |
| **Bank: Occupella's cost** | **$0.00** | **$11.08** (rounded up) |
| Card: Stripe's 2.9% × $1,500 + $0.30 | $0.00 (the company's account pays $43.80) | $43.80 |
| Card: active account, payout, dispute 1% × $15.00 | $0.00 | $2.00 + $4.00 + $0.15 |
| **Card: Occupella's cost** | **$0.00** | **$49.95** |

Under Standard a payment costs Occupella nothing at Stripe, so break-even is $0.00 and the default
is the margin alone: **$0.50 a payment, bank or card** (`MARGIN`, `DEFAULT_FEES`). That clears the
cost by $0.50 at every amount (`tests/test_connect.py::test_the_default_fees_land_above_break_even`
fails if a default ever falls below the computed cost; `tests/properties/test_connect_properties.py`
checks it at random amounts). The defaults would not cover Express, and a test says so: changing
the model means changing the fee. Brandon sets a bigger margin by the two settings.

What reaches the trust account of that $1,500.00, and who pays:

| | Company pays the fees | Tenant pays them on top |
|---|---|---|
| Bank | charge $1,500.00 − Stripe $5.00 − Occupella $0.50 = **$1,494.50**; the company's fee account pays $5.50 | tenant adds $5.50; charge $1,505.50 − $5.50 = **$1,500.00**; the company pays nothing |
| Card | charge $1,500.00 − Stripe $43.80 − Occupella $0.50 = **$1,455.70**; the company pays $44.30 | grossed up it would be $45.62, over the 3% cap: the tenant adds **$45.00**; charge $1,545.00 − Stripe $45.11 − Occupella $0.50 = $1,499.39; the company pays the $0.61 the cap left |

In the books every payment is recorded whole for the owner (or the lease's charges); the tenant's
fee, if any, goes into the company's fee account; and everything Stripe kept, its fee and
Occupella's together (the balance transaction's `fee` on a direct charge), leaves trust from the
company's fee account as the partner's fee always did (`online_payments.from_company`), waiting on
Banking when that account holds too little. **An owner's account never pays a fee**
(`tests/properties/test_connect_properties.py`).

### Who pays (items 5 and 6)

- **Bank:** the company's existing choice, `trust_payment_settings.ach_fee_payer`. A tenant who
  pays adds exactly what covers Stripe's fee and Occupella's, the least that does (`gross_up`).
- **Card:** `trust_card_fee_settings`, the company until it chooses the tenant. A tenant pays the
  card fee only where all of these hold, else the company does (`connect.card_fee_terms`):
  the property's state doesn't ban a surcharge (Connecticut, Massachusetts, Maine, Puerto Rico,
  per docs/law.md and 2026 compliance summaries, NOT VERIFIED against the statutes); it isn't
  autopay (Visa reportedly forbids a convenience fee on a recurring payment; a card fee on autopay
  is always the company's); and the tenant's part is never more than 3% of the payment (Visa's US
  cap; Mastercard's is 4%) or the state's lower cap (Colorado 2%; Oklahoma 2% per docs/law.md,
  unconfirmed). No debit or prepaid card is ever surcharged: when the tenant pays a card fee,
  Stripe's page takes credit cards only (`funding_types_blocked`, which needs the API version
  above; NOT VERIFIED). Company setup lists, for each state the company's properties are in, the
  rule recorded for it, the way rent notices warn (a warning, not legal advice).
- **Owners adding money:** the founder's rule of 2026-11-02 stands: the processing fee is always
  the company's. Occupella's fee is charged on an owner's payment too (it is "every payment") and
  is the company's as well: nothing is added to what the owner pays, and it leaves trust from the
  company's fee account, never the owner's.

### Disputes (item 3)

A dispute (`charge.dispute.created`, which is also how Stripe reports a late ACH return) is
recorded the way a bank return is: the payment comes off as a bounce with the property's NSF fee
(an owner's contribution comes back out), and the dispute fee Stripe took from the company's
balance leaves trust from the company's fee account (`src/pms/api/disputes.py`,
`trust_connect_disputes`). A dispute won (`charge.dispute.closed` won, or `funds_reinstated`)
records the payment again as it first was, the tenant's fee back into the company's account, and
whatever Stripe gave back of the dispute fee. A dispute lost moves nothing more. A close before
its opening is refused (409) so Stripe sends it again. Stripe's $15 dispute fee is the connected
account's under Standard: Occupella pays nothing on a dispute and keeps its fee.

### The webhook (item 7)

One endpoint for the platform, `POST /api/payments/connect/webhook`, signed with the Connect
endpoint's own secret (`PMS_STRIPE_CONNECT_WEBHOOK_SECRET`). Each event's `account` names the
company that connected it; an account no company connected is refused (404, so Stripe retries); a
report about a payment started on another account is acknowledged and never booked; a report
whose `livemode` isn't the platform key's mode is acknowledged and never booked. `account.updated`
keeps the account's state current; the admin's return from onboarding asks Stripe at once.

### Keeping what was there (items 8, 9)

A company pays through Connect only when the server has a platform (`PMS_CONNECT`) and the company
connected an account, through it, in its mode, that Stripe says takes charges
(`connect.ConnectResolver`). Any other company pays as before: its own keys, else the server's
partner. A live server refuses the platform's test key at start (as #237 refuses
`PMS_STRIPE_SECRET_KEY`'s), an account connected in test mode takes no payment on a live
platform, and the webhook books no test-mode report there. Connected-account rows, their states
and which account each payment started on are rows the AI role never reads (credential-adjacent).
No platform secret is ever in the database.

### The fake platform (item 10)

`PMS_CONNECT=fake` (refused over https, like every fake partner) plays Stripe's onboarding
(`/pay/fake-connect`: finished, or the bank account left for later), a bank or card payment with
Occupella's fee in it (`/pay/fake-connect-pay`: paid, failed, left, disputed, won, lost) and
autopay's save of a card or bank account (`/pay/fake-connect-save`). The browser test drives all of
it (`ci/features.toml`, "stripe-connect").

## Consequences

- Migration `20261113000150`: `trust_connected_accounts`, `trust_connected_account_states`,
  `trust_connected_payments`, `trust_card_fee_settings`, `trust_connect_disputes`; a payment may be
  by card (Connect only, at most $10,000.00) and names Occupella's fee (`platform_fee`); an
  autopay says how it pays.
- The hosted app sets `PMS_CONNECT=stripe`, `PMS_STRIPE_CONNECT_SECRET_KEY`,
  `PMS_STRIPE_CONNECT_WEBHOOK_SECRET`, and optionally `PMS_CONNECT_FEE_ACH` and
  `PMS_CONNECT_FEE_CARD`, and registers one Connect webhook endpoint at
  `$PMS_PUBLIC_URL/api/payments/connect/webhook` for the events `pms.connect.CONNECT_EVENTS` lists.
- Open, for the founder or counsel: Stripe's ACH failure fee ($4.00, NOT VERIFIED) and a partial
  dispute aren't read (the whole payment is treated as returned); the NSF fee charged at a dispute
  stays when it is won; a debit card's exclusion rests on the newer API version for one call;
  Express or OAuth for companies already on Stripe; refunds from Stripe's dashboard (P-028) apply
  here as to a company's own keys. docs/edge-cases.md lists each.
