# Edge cases

What can happen to a company on this PMS that it must be ready for, each case with what the PMS
does about it, whether a test proves it, and how much harm it would do. Eight areas, one table
each, 1126 cases in all, each table ranked by risk (high first), then open before guarded
before tested. The founder asked for it on 2026-11-05, toward a thousand cases and a graceful
answer in the portal for everything that can fail.

- **Status.** *Tested*: a test named in Evidence proves what the PMS does. *Guarded*: the code
  (a check, a constraint, a trigger) handles it, but no test pins it. *Open*: nothing handles it
  yet, or it is handled badly; what it does today is what the row says.
- **Risk.** *H*: money wrong, money moved twice or lost, a person's data shown to someone else, a
  company unable to work, or the law broken. *M*: a page wrong or a step refused that should go
  through, with a way around it. *L*: confusing, slow or untidy, nothing lost.
- **How it was made.** Read from the code, the migrations and the tests on 2026-11-05 by seven
  reviewers, one per area; every test cited was checked to exist by name. Nothing was run to make
  this list, so an Open row's behaviour is NOT VERIFIED by a test run, and a `src/` line number
  cited may drift as the code changes. Rows that rest on how an outside service or a library
  behaves say NOT VERIFIED where they do. The AI link's area was written as it was built, and
  each row closed since names the test that was run for it.
- **Keeping it.** A PR that closes an Open row changes it to Tested and names the test. A new
  case takes the next ID of its area and goes where its risk ranks it.

## Counts

| Area | Cases | Tested | Guarded | Open | Open, high risk |
|---|---|---|---|---|---|
| [Money and the ledger](#money-and-the-ledger) | 150 | 110 | 7 | 33 | 7 |
| [Dates, time zones and calendars](#dates-time-zones-and-calendars) | 157 | 79 | 15 | 63 | 17 |
| [Concurrency and retries](#concurrency-and-retries) | 142 | 61 | 30 | 51 | 4 |
| [Partners and outside services down, slow or wrong](#partners-and-outside-services-down-slow-or-wrong) | 160 | 59 | 8 | 93 | 29 |
| [Bad files and imports](#bad-files-and-imports) | 145 | 60 | 16 | 69 | 6 |
| [People doing the unexpected, permissions and security](#people-doing-the-unexpected-permissions-and-security) | 155 | 85 | 10 | 60 | 8 |
| [Hosting, operations and the portal's failure states](#hosting-operations-and-the-portals-failure-states) | 160 | 57 | 14 | 89 | 18 |
| [The AI link: drafts, events, webhooks and the MCP server](#the-ai-link-drafts-events-webhooks-and-the-mcp-server) | 57 | 40 | 10 | 7 | 0 |
| **All** | **1126** | **551** | **110** | **465** | **89** |

## Open, high risk: the work list

Every case of high risk with nothing handling it yet, by area. Each area's own section ends with
its ten most pressing, each with the test that would close it.

**Money and the ledger**

- M-049: Late fee terms added with a start day in the past
- M-050: A backdated late fee that falls in a closed year
- M-072: Prepaid rent left over at move-out
- M-073: A renewal while the old lease still owes
- M-081: A renewed lease: the old lease's interest paid through today and the new lease's from its first day
- M-082: A tenant on two leases at once (two units of one property)
- M-144: A cutover mid-month for leases already paid for the month in the old system

**Dates, time zones and calendars**

- D-016: Server down from the 28th to the month's end, lease due on the 28th
- D-017: Server down for a whole month, back on the 2nd of the next
- D-026: Run rent now with Rent due on next month or a later year
- D-055: A lease entered on the evening of its last day (after 8 pm Eastern)
- D-065: A rent change from 03/01 with notice given 02/01
- D-066: A rent increase recorded on the rent page in CA, OR, WA, NV or NY
- D-067: New York renewal of a 12-month lease (365 days, no February 29)
- D-068: New York tenant there exactly two years (730 days)
- D-069: California increase notice served by mail
- D-078: A check received on the last grace day, recorded the next morning dated back
- D-079: A lockbox file line dated within grace brought in after the fee
- D-080: Rent paid online by bank on the due day that settles after the grace
- D-081: Autopay started on the due day, settled 2 to 4 business days later
- D-082: Late fee terms added with a start day in the past
- D-083: Rent first charged after its own grace ran out (lease entered late, an admin's catch-up)
- D-128: A fee charged after the 10-day deadline passed
- D-129: Tenants who moved out before the lease's last day

**Concurrency and retries**

- C-055: A payout run's ACH file taken twice and uploaded to the bank twice
- C-087: A returned report arrives before paid (paid retried after a 502 or 503)
- C-088: The same out-of-order return for an owner's payment in
- C-090: Stripe gives up on a webhook after days of failed deliveries

**Partners and outside services down, slow or wrong**

- P-006: The company's secret key was rolled or revoked in Stripe, is a restricted key without a permission, or the account was restricted
- P-007: Keys saved that only look right: a typo'd key, or a secret key and a webhook secret from different Stripe accounts or modes
- P-010: The server's clock drifts more than 5 minutes
- P-011: The webhook signing secret changed in Stripe (endpoint recreated) but not in the PMS
- P-012: The Stripe endpoint isn't subscribed to the events the PMS reads (async_payment_succeeded, payment_intent.*, checkout.session.expired, charge.dispute.created)
- P-013: No report ever arrives for a started payment (webhook broken, PMS down past Stripe's retry window)
- P-014: Stripe's account API version is older than the shapes the PMS reads (it sends no Stripe-Version header)
- P-024: A return (dispute) arrives before the paid report lands (paid kept failing on its fee, or isn't subscribed)
- P-026: A late return Stripe reports by another event than a dispute (charge.failed after success; NOT VERIFIED which Stripe sends)
- P-027: A dispute the company won, funds reinstated (charge.dispute.closed, charge.dispute.funds_reinstated)
- P-028: Staff refund a tenant, all or part, from Stripe's dashboard (charge.refunded)
- P-030: Stripe takes a dispute fee from the balance (amount NOT VERIFIED)
- P-034: A company with two operating trust bank accounts and one Stripe payout account
- P-040: Paid online for a property with two owner accounts and no ownership shares
- P-048: Test-mode keys (sk_test_) on a live https server
- P-049: A company goes from test keys to live
- P-051: PMS_DATA_KEY lost while payments are in flight
- P-061: An owner's payment refunded in Stripe, or a dispute on it won
- P-065: The company changes Stripe accounts (server's to own, or new keys) with autopays saved at the old one
- P-067: Stripe leaves an off-session payment waiting on the tenant (requires_action; NOT VERIFIED for ACH)
- P-068: A hand payment left on Stripe's page whose expired report never comes
- P-075: SMTP server hangs
- P-087: The attachment or HTML fails with something other than a database or lookup error (fpdf, ValueError, HTTPException; NOT VERIFIED that any does today)
- P-111: PMS_DATA_KEY changed (one is set, but not the one that sealed the numbers)
- P-115: The bank returns an owner's ACH draw (account closed, no account)
- P-116: The bank returns a vendor's ACH payment
- P-121: A printed check is lost, misprinted or voided
- P-129: Every amount positive with a separate Type (Debit or Credit) column
- P-130: The wrong statement imported (another account's, wrong days, wrong signs)

**Bad files and imports**

- F-010: A PDF with JavaScript (OpenAction, /JS), uploaded by a tenant as their own lease document or by an anonymous applicant as proof of income
- F-061: Day-first dates from a UK or European file ("05/01/2026" meaning 5 January)
- F-077: A charge dated after a new lease started at the unit (the old tenant's move-out fee dated after the next move-in)
- F-122: An audit log report over a day a lease offer or a drawn signature was saved
- F-126: The everything export (records.zip) of a company with GBs of files
- F-129: Two tenants of the same name at different properties, in the QuickBooks export

**People doing the unexpected, permissions and security**

- U-010: An owner's or tenant's login is disabled by mistake
- U-014: A staff member uses the invite link shown on screen to become an owner or tenant who already has a password
- U-015: An admin re-invites another staff member (an approver, the trust account's broker) and uses the link
- U-033: PMS_TRUSTED_PROXIES "*" behind nginx set as the install guide says ($proxy_add_x_forwarded_for)
- U-051: The only admin with a working login drops admin while another admin's login is disabled
- U-085: A former owner (the property sold, or its ownership moved to another owner)
- U-099: Staff mark the wrong person's record as "the same person"
- U-100: A tenant left a lease others stay on

**Hosting, operations and the portal's failure states**

- H-008: A payment's answer is lost, staff close the panel and record it again
- H-016: The mail server is slow or down
- H-020: The nightly run fails on a server that sends no email (compose sets none by default)
- H-021: The nightly hasn't run for days (its thread died, it fails every night, or it is off)
- H-022: PMS_NIGHTLY=off with no cron running python -m pms.nightly
- H-027: The disk fills
- H-028: The disk fills, and the backups live on it
- H-031: The daily backup fails (password changed, pg_dump error, disk)
- H-041: A newer backup restored under an older tag, or the old tag checked out after migrations ran
- H-055: PMS_DATA_KEY changed while bank payments are on their way
- H-059: PMS_PUBLIC_URL isn't the address people use (www or not, http or https, another domain)
- H-060: PMS_PUBLIC_URL left as .env.example's https://pms.example.com
- H-067: PMS_TRUSTED_PROXIES empty behind a proxy on another host or network
- H-068: "*" trusted, with nginx's $proxy_add_x_forwarded_for as install.md says
- H-089: The server is down over a lease's own due day at a month's end (due the 28th, down the 28th to the 31st)
- H-097: The everything export of a large company
- H-115: A session signed out after an hour idle, page still open
- H-152: Any error while drawing a page (an answer of an unexpected shape after an upgrade, a status the page doesn't know)

## Money and the ledger

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| M-049 | Late fee terms added with a start day in the past | The next night charges a late fee on every rent since then not paid within its grace, even ones paid late since; nothing limits backdating | Open | `src/pms/api/setup.py:751` | H |
| M-050 | A backdated late fee that falls in a closed year | The closed-year trigger raises and the company's whole nightly run, rent included, fails every night | Open | `src/pms/nightly.py:115`; `db/migrations/20261021000106_year_close.sql:138` | H |
| M-072 | Prepaid rent left over at move-out | Not used for what the lease owes before the deposit is kept, and never refunded by the return | Open | `db/migrations/20261019000094_ownership_shares.sql:190` | H |
| M-073 | A renewal while the old lease still owes | New payments don't pay it, and the final return keeps only the renewal's debt; whichever lease is returned first takes the whole deposit | Open | `db/migrations/20261018000091_lease_transfers.sql:62` | H |
| M-081 | A renewed lease: the old lease's interest paid through today and the new lease's from its first day | The same days are paid twice: the old lease's through day is not capped at its end | Open | `db/migrations/20261022000122_deposit_interest.sql:152` | H |
| M-082 | A tenant on two leases at once (two units of one property) | Each lease's interest counts the tenant's whole deposit account | Open | `db/migrations/20261022000122_deposit_interest.sql:165` | H |
| M-144 | A cutover mid-month for leases already paid for the month in the old system | The first nightly run charges every imported lease the whole month again, and late fees if past grace | Open | `src/pms/nightly.py:115`; `src/pms/api/switching.py:399` | H |
| M-013 | Two payments with the same request id, then the same id with another amount | Recorded once; the different amount is refused 409 | Tested | `tests/api/test_rent.py::test_sending_a_payment_again_records_it_once` | H |
| M-015 | Two payments to one charge at the same moment | The second waits and never overpays the charge | Tested | `tests/test_leases_and_charges.py::test_two_payments_at_once_cannot_overpay_a_charge` | H |
| M-017 | A payment dated inside an approved month-end | Refused; nothing written | Tested | `tests/api/test_rent.py::test_a_closed_period_refuses_a_payment_dated_in_it` | H |
| M-018 | A payment dated before the trust bank account's cutover | Refused | Tested | `tests/test_cutover.py::test_nothing_is_dated_before_the_cutover_once_opened` | H |
| M-033 | Two rent runs at the same moment | Each lease is charged once | Tested | `tests/test_rent_automation.py::test_two_runs_at_once_charge_each_lease_once` | H |
| M-034 | Run rent now with a mid-month due date (the 15th) after the 1st was charged | Charged once a month whatever day the run is for: a month with any of its rent due already is not charged again (migration 20261105000400) | Tested | `tests/test_rent_once_a_month.py::test_a_mid_month_run_charges_no_month_twice`; `tests/properties/test_rent_once_a_month_properties.py` | H |
| M-035 | The same mid-month run for recurring charges (pet rent, parking) | Each monthly charge is charged once a month whatever day the run is for (migration 20261105000400) | Tested | `tests/test_rent_once_a_month.py::test_a_mid_month_run_charges_no_month_twice`; `tests/properties/test_rent_once_a_month_properties.py` | H |
| M-047 | Two late-fee runs at the same moment | One late fee per rent | Tested | `tests/test_rent_automation.py::test_two_runs_at_once_charge_one_late_fee` | H |
| M-057 | The same payment bounced twice | Owed again once; the second bounce is refused | Tested | `tests/api/test_rent.py::test_a_bounced_payment_is_owed_again_once` | H |
| M-058 | A bounce after the owner was already drawn | Refused: the owner's account would go below zero | Tested | `tests/api/test_rent.py::test_a_payment_the_owner_was_paid_out_of_cannot_bounce_into_the_negative` | H |
| M-059 | A bounce after the payment's prepaid part was used | Refused, nothing written | Tested | `tests/properties/test_rent_properties.py::test_rent_invariants_hold_under_random_charges_payments_and_bounces` | H |
| M-065 | A lease owes more than the deposits held | All kept, none returned | Tested | `tests/test_move_out.py::test_when_the_lease_owes_more_than_is_held_all_is_kept` | H |
| M-068 | A deposit return dated on or before the lease's last day | Refused | Tested | `tests/test_move_out.py::test_a_deposit_goes_back_only_once_the_lease_has_ended` | H |
| M-069 | A deposit return sent twice, or again under another key | Same answer once; another key refused | Tested | `tests/test_move_out.py::test_the_same_request_again_returns_nothing_twice_and_another_is_refused` | H |
| M-075 | A deposit kept for damages | Moves with its cash to the operating account in one transaction | Tested | `tests/test_tie_out.py::test_a_deposit_kept_for_damages_moves_with_its_cash` | H |
| M-086 | Two draws on one account at once | Never pay out more than is there | Tested | `tests/test_owner_money.py::test_two_draws_at_once_cannot_pay_out_more_than_is_there` | H |
| M-087 | A draw retried with its key | Returns the original draw | Tested | `tests/test_owner_money.py::test_a_draw_retried_with_its_key_returns_the_original` | H |
| M-088 | A draw when unpaid bills sit on top of the reserve | Keeps back both | Tested | `tests/test_vendor_bills.py::test_a_draw_keeps_back_unpaid_bills_on_top_of_the_reserve` | H |
| M-091 | A prepared draw run paid twice | Paid once | Tested | `tests/test_draws_and_payouts.py::test_a_prepared_run_is_paid_once` | H |
| M-098 | A part payment of more than what is left of a bill | Refused; one key pays one payment | Tested | `tests/test_bill_lifecycle.py::test_a_part_is_never_more_than_is_left_and_a_key_pays_one_payment` | H |
| M-099 | An owner short of the whole bill tries to pay part | Refused: no part is paid | Tested | `tests/test_bill_lifecycle.py::test_a_short_owner_pays_no_part` | H |
| M-101 | Shared invoice parts that don't come to the invoice | Refused at commit | Tested | `tests/test_shared_bills.py::test_the_parts_come_to_the_invoice` | H |
| M-116 | The company takes more of its fees out of trust than its fee account holds | Refused | Tested | `tests/test_company_fees.py::test_a_set_amount_is_taken_and_never_more_than_is_there` | H |
| M-117 | Two company fee withdrawals at once | Never more than is there | Tested | `tests/test_company_fees.py::test_two_withdrawals_at_once_cannot_take_more_than_is_there` | H |
| M-119 | Held money moved to another trust bank account without its cash | Refused at commit | Tested | `tests/test_tie_out.py::test_held_money_cannot_change_trust_account_without_its_cash` | H |
| M-120 | A batch with one overdraft among good transfers | The whole batch is refused | Tested | `tests/test_trust_model.py::test_batch_with_one_overdraft_is_refused_whole` | H |
| M-121 | An owner account taken below zero | Refused, nothing written | Tested | `tests/test_trust_model.py::test_transfer_that_would_take_owner_account_below_zero_is_refused_and_nothing_written` | H |
| M-122 | A tenant deposit opened in the operating account | Refused | Tested | `tests/test_deposit_account.py::test_money_held_in_trust_opens_only_in_its_own_kind_of_account` | H |
| M-123 | The table owner rewriting a ledger row | Refused | Tested | `tests/test_ledger_core.py::test_ledger_history_cannot_be_rewritten_even_by_the_owner` | H |
| M-124 | An idempotency key reused for a different posting | Refused; nothing written | Tested | `tests/test_idempotency.py::test_same_key_with_different_contents_is_refused_and_nothing_written` | H |
| M-125 | The same key used by two companies | Each company's own | Tested | `tests/test_idempotency.py::test_keys_belong_to_one_pmc` | H |
| M-126 | A transfer across two companies | Refused | Tested | `tests/test_trust_model.py::test_transfer_across_pmcs_is_refused` | H |
| M-128 | An authority's check for more than it owes | Refused, all or none | Tested | `tests/api/test_assistance_api.py::test_an_authority_pays_no_more_than_it_owes_and_all_or_none` | H |
| M-129 | One transfer paying a tenant's and an authority's charges | Refused | Tested | `tests/test_housing_assistance.py::test_a_transfer_pays_a_tenants_charges_or_an_authoritys_never_both` | H |
| M-131 | Two utility bills covering the same day | Refused | Tested | `tests/test_utility_billing.py::test_no_two_bills_cover_a_day` | H |
| M-132 | A journal entry posted by the staff member who proposed it | Refused | Tested | `tests/test_journal_entries.py::test_the_staff_member_who_proposed_it_does_not_post_it` | H |
| M-133 | A journal entry that would take an owner below zero | Not posted | Tested | `tests/test_journal_entries.py::test_an_entry_that_would_take_an_owner_below_zero_is_not_posted` | H |
| M-134 | A journal entry dated in an approved period | Not posted | Tested | `tests/test_journal_entries.py::test_an_entry_dated_in_an_approved_period_is_not_posted` | H |
| M-136 | A journal entry taking cash out by staff who isn't a signer | Refused once a broker is on file | Tested | `tests/test_trust_account_signers.py::test_a_journal_entry_that_takes_cash_out_takes_a_signer` | H |
| M-140 | Opening balances after a posting in the account | Refused | Tested | `tests/test_cutover.py::test_opening_balances_come_before_any_posting` | H |
| M-141 | Opening balances that don't tie out to the old book cash | Refused | Tested | `tests/api/test_switch.py::test_opening_balances_that_dont_tie_out_are_refused` | H |
| M-146 | The payments partner reports one payment paid twice | Recorded once; trust ties out | Tested | `tests/properties/test_online_payment_properties.py::test_online_payments_record_what_the_partner_reports_once_and_keep_trust_whole` | H |
| M-150 | The same payment file brought in twice | Comes in once | Tested | `tests/api/test_payment_files_api.py::test_a_file_comes_in_once` | H |
| M-023 | A tenant left the lease holding prepaid rent under their name | Use prepaid rent spends it on the staying tenants' later rent; the departure neither moves nor refunds it | Open | `src/pms/api/rent.py:626` | M |
| M-025 | A tenant holds enough prepaid rent but the company left prepaid rent by itself off (the default) | Rent stays open and a late fee is charged after grace | Open | `db/migrations/20261031000200_late_fees_skip_paid_rents.sql:139` | M |
| M-026 | A balance autopay when the tenant holds prepaid rent | Debits the tenant's bank for the whole balance; prepaid rent is not counted | Open | `src/pms/autopay.py:139` | M |
| M-027 | Refunding a tenant's overpayment while the lease runs | No refund action; only a journal entry by two staff moves prepaid rent out, with no receipt | Open | `src/pms/api/journal.py:178` | M |
| M-036 | Run rent now with next month's due date | Charges next month's rent today; only the late-fee day is checked | Open | `src/pms/api/rent.py:1257` | M |
| M-037 | A second rent in one month on an assisted lease (as in the mid-month run) | The authority's part is skipped by ON CONFLICT yet the tenant's rent is still cut by it: rent under-billed | Open | `db/migrations/20261020000095_housing_assistance.sql:251` | M |
| M-043 | A lease end set on a month whose rent staff partly waived | Credits the days after the end at full rent on top of the waiver; the extra credit pays other charges | Open | `db/migrations/20261020000095_housing_assistance.sql:523` | M |
| M-051 | Unpaid rent carried over at the cutover, due weeks ago, with late fee terms in force then | Charged a late fee the first night, maybe on top of the old system's | Open | `src/pms/api/switching.py:432` | M |
| M-060 | The bank already took back a payment whose bounce the books refuse | No guided way to record it (an owner contribution first, or a journal entry for prepaid rent); the bank line stays unmatched | Open | `src/pms/api/rent.py:657` | M |
| M-078 | A property archived while its past tenants' deposits are still held | Allowed: the property check looks only at its owners' accounts | Open | `db/migrations/20261024000152_bill_lifecycle.sql:611` | M |
| M-080 | Staff return a deposit before paying the interest it earned | The return goes ahead with no warning; the interest can never be paid after | Open | `db/migrations/20261022000122_deposit_interest.sql:264` | M |
| M-090 | A draw, fee or bill payment dated before money that arrived later | Checked against today's balance only; the owner's statement can show a negative balance between those days | Open | `db/migrations/20261010000017_vendor_bills.sql:352` | M |
| M-094 | A bill at the limit whose markup takes the owner's cost over it | Set aside without approval: only the bill amount is compared, not bill plus markup | Open | `db/migrations/20261024000152_bill_lifecycle.sql:223` | M |
| M-097 | Bill holds read in the evening, Eastern time | Use the UTC date: a bill dated the company's tomorrow already holds, and tomorrow's reserve applies | Open | `db/migrations/20261104000300_shortfall_keeps_the_reserve.sql:67` | M |
| M-105 | A recurring bill of a property or owner that was archived | Keeps entering a bill every month: archiving checks running recurring bills only for vendors | Open | `db/migrations/20261024000152_bill_lifecycle.sql:556` | M |
| M-111 | Bounces in a period larger than its collections | The fee is 0, not negative; the shortfall is not carried on, so the fee taken earlier on the bounced rent stays | Open | `tests/test_owner_money.py::test_a_bounce_in_the_period_counts_against_it` | M |
| M-112 | Collect fees for a period that hasn't ended yet | Takes the fee on what came in so far and locks the period against later fees | Open | `src/pms/api/payables.py:1932` | M |
| M-135 | A journal entry dated next year | Proposed and posted with a future date: balances move now while reports as of today leave it out | Open | `src/pms/api/journal.py:167` | M |
| M-066 | A lease owes exactly the deposits held | All kept; no return transfer is made | Guarded | `db/migrations/20261019000094_ownership_shares.sql:349` | M |
| M-005 | Three owners at 33.3333% splitting $100.00 | Each floored to the cent; spare cents go to the largest remainders, then the first account | Tested | `tests/test_ownership_shares.py::test_the_cents_left_over_go_to_the_largest_remainders_then_the_first_account` | M |
| M-012 | A partial payment | Pays the oldest open charge first, rent first | Tested | `tests/api/test_rent.py::test_a_payment_smaller_than_what_is_owed_pays_the_oldest_charge_first` | M |
| M-016 | A payment dated tomorrow | Refused: money is recorded no later than the company's today | Tested | `tests/api/test_rent.py::test_money_is_recorded_no_later_than_today` | M |
| M-019 | A payment on a lease under an eviction payment hold | Refused unless staff accept it past the hold, naming who | Tested | `tests/api/test_payment_holds_api.py::test_staff_accept_one_payment_past_the_hold` | M |
| M-020 | A property with two owner accounts and none chosen | Refused 422 asking staff to choose | Tested | `tests/api/test_rent.py::test_a_property_with_two_owner_accounts_needs_one_chosen` | M |
| M-021 | Charges chosen to pay first that add up to more than the payment | Refused; nothing recorded | Tested | `tests/api/test_receive_payment_api.py::test_a_choice_that_does_not_fit_is_refused_and_records_nothing` | M |
| M-022 | An overpayment after one co-tenant left the lease | The extra is held for a tenant who stays | Tested | `tests/api/test_lease_people_api.py::test_a_payment_beyond_what_is_owed_is_held_for_a_tenant_who_stays` | M |
| M-024 | Prepaid rent by itself on a held lease or one whose owner account staff must choose | Left for staff | Tested | `tests/api/test_prepaid_by_itself_api.py::test_a_lease_on_hold_or_whose_owners_account_staff_choose_is_left_for_staff` | M |
| M-028 | A payment to a property with no owner account | Refused 409 | Tested | `tests/api/test_rent.py::test_a_property_without_an_owner_account_takes_no_rent` | M |
| M-029 | A payment when a late fee is older than the rent | Pays the rent first | Tested | `tests/api/test_rent_first_api.py::test_a_payment_pays_the_rent_before_an_older_fee` | M |
| M-030 | A credit when a fee is older than the rent | Comes off the rent first | Tested | `tests/test_rent_first.py::test_credits_come_off_the_rent_before_an_older_fee` | M |
| M-031 | A tenant who moved units owes on both leases | Pays every lease's rent, oldest lease first, before any fee | Tested | `tests/api/test_rent_first_api.py::test_a_moved_tenant_pays_the_rent_of_every_lease_before_any_fee` | M |
| M-039 | A rent change for a month already charged | That month keeps its old rent | Tested | `tests/test_rent_changes.py::test_a_month_already_charged_keeps_its_rent` | M |
| M-040 | A rent change without a full month's written notice | Refused | Tested | `tests/test_rent_changes.py::test_written_notice_of_a_full_month_comes_first` | M |
| M-041 | A lease end set mid-month after the month was charged | Credits the days after the end | Tested | `tests/test_lease_end_credits.py::test_an_end_set_mid_month_after_the_run_credits_the_days_after_it` | M |
| M-042 | A lease end moved later or cleared | Charges the days back as a fee | Tested | `tests/test_lease_end_credits.py::test_a_later_end_or_none_charges_the_days_back` | M |
| M-045 | A fixed-term lease that runs out with no renewal | Goes month to month at the same rent, once | Tested | `tests/test_lease_holdovers.py::test_a_term_that_ran_out_goes_month_to_month_once` | M |
| M-046 | A payment dated on the last day of grace | Counts; one dated the day after does not | Tested | `tests/test_rent_automation.py::test_a_payment_counts_only_if_dated_before_the_grace_runs_out` | M |
| M-048 | A catch-up run after nights were missed | Charges what a run every night would | Tested | `tests/test_rent_automation.py::test_one_run_charges_what_a_run_every_day_would` | M |
| M-053 | A payment that bounced before the grace ran out | The rent counts as unpaid; a late fee is charged | Tested | `tests/test_rent_automation.py::test_a_payment_that_bounced_before_the_grace_ran_out_does_not_count` | M |
| M-055 | An assisted tenant paid their part but the authority is late | The tenant is charged no late fee | Tested | `tests/test_housing_assistance.py::test_a_tenant_who_paid_their_part_is_not_late_while_the_authority_owes` | M |
| M-056 | A bounce dated before the payment | Refused | Tested | `tests/test_payment_reversals.py::test_a_reversal_dated_before_the_payment_is_refused` | M |
| M-061 | A bounce of a payment split by ownership shares | Takes back each owner's part | Tested | `tests/api/test_ownership_api.py::test_a_bounced_split_payment_takes_back_each_owners_part` | M |
| M-062 | A bounce of a payment that paid an old lease's owner and a new one's | Comes back from both owners | Tested | `tests/api/test_lease_transfers_api.py::test_a_payment_on_the_new_lease_that_bounces_comes_back_from_both_owners` | M |
| M-063 | An online payment returned by the bank | A bounce with the NSF fee; the tenant's fee goes back out | Tested | `tests/api/test_online_payments_api.py::test_a_return_is_a_bounce_and_the_tenants_fee_goes_back_out` | M |
| M-064 | A bounce after part of the charge was waived | Owes again only what was not waived | Tested | `tests/test_charge_waivers.py::test_a_bounce_after_a_waiver_leaves_owed_what_was_not_waived` | M |
| M-067 | Credits larger than the charges at move-out | Owes nothing, never less than nothing; all returned | Tested | `tests/test_move_out.py::test_a_bounced_payment_is_owed_again_and_credits_never_make_it_owe_less_than_nothing` | M |
| M-070 | Security, key and pet deposits at move-out | Kept security first, then key, then pet, as the model says | Tested | `tests/properties/test_deposit_kind_properties.py::test_each_kind_of_deposit_is_held_moved_and_returned_as_the_model_says` | M |
| M-071 | A tenant still on another lease (a renewal running) | The deposit stays with it; the return is refused | Tested | `tests/test_move_out.py::test_a_tenant_still_on_another_lease_keeps_their_deposit` | M |
| M-074 | A deposit kept on a property owned in shares | Goes to the owners by their shares | Tested | `tests/api/test_ownership_api.py::test_a_deposit_kept_at_move_out_goes_to_the_owners_by_their_shares` | M |
| M-076 | A deposit return when a charge was waived | Keeps nothing for what was waived | Tested | `tests/test_charge_waivers.py::test_a_deposit_kept_at_move_out_leaves_a_waived_charge_alone` | M |
| M-077 | A deposit short of two leases a tenant moved through | Pays the older lease first | Tested | `tests/test_lease_transfers.py::test_a_deposit_short_of_both_pays_the_older_lease_first` | M |
| M-079 | Deposit interest asked for after the deposit was returned | Refused | Tested | `tests/test_deposit_interest.py::test_an_ended_lease_takes_it_with_its_deposit_and_nothing_after_the_return` | M |
| M-083 | Deposit interest added to the deposit when the owner is short | Refused | Tested | `tests/test_deposit_interest.py::test_an_owner_short_of_it_is_refused_and_a_staff_member_named_must_disburse` | M |
| M-084 | A draw of the last cent above the reserve | Paid | Tested | `tests/test_owner_money.py::test_a_draw_pays_the_last_cent_above_the_reserve` | M |
| M-085 | A draw with nothing above the reserve | Refused | Tested | `tests/test_owner_money.py::test_a_draw_with_nothing_available_is_refused` | M |
| M-089 | A draw when a bill carries a markup | Keeps back the bill with its markup | Tested | `tests/test_bill_markups.py::test_draws_hold_a_bill_back_with_its_markup_until_it_is_set_aside` | M |
| M-093 | A bill exactly at the owner's approval limit, and one 0.01 over | At the limit needs no approval; over it waits | Tested | `tests/test_vendor_bills.py::test_a_bill_within_the_approval_limit_is_set_aside_without_approval` | M |
| M-095 | An owner's balance at or below the reserve | Holds nothing; the bill reads Short | Tested | `tests/test_bill_lifecycle.py::test_a_balance_at_or_below_the_reserve_holds_nothing` | M |
| M-096 | Two bills and money for only one | Held for the bill due first; the other is Short | Tested | `tests/test_bill_lifecycle.py::test_the_balance_is_held_for_the_bill_due_first_and_the_rest_is_short` | M |
| M-100 | A void after money was taken for the bill | Refused | Tested | `tests/test_bill_lifecycle.py::test_no_void_once_money_was_taken_for_the_bill` | M |
| M-103 | A markup added twice, or on a declined bill | Once, before set aside, never on a declined bill | Tested | `tests/test_bill_markups.py::test_a_markup_comes_once_before_set_aside_and_never_on_a_declined_bill` | M |
| M-104 | An owner short of the markup but not the bill | Nothing is set aside | Tested | `tests/test_bill_markups.py::test_an_owner_short_of_the_markup_has_nothing_set_aside` | M |
| M-108 | A recurring bill's month already entered by hand | Left as it is | Tested | `tests/test_recurring_bills.py::test_a_month_already_entered_by_hand_is_left_as_it_is` | M |
| M-110 | A fee period overlapping one already taken | Refused; the same period again returns it | Tested | `tests/test_owner_money.py::test_a_period_overlapping_one_taken_is_refused` | M |
| M-113 | Co-owners whose fee terms differ | No fee until they agree | Tested | `tests/test_ownership_shares.py::test_owners_whose_terms_differ_take_no_fee_until_their_terms_agree` | M |
| M-118 | The partner's fee when the company's fee account is empty | Waits until the company covers it | Tested | `tests/api/test_online_payments_api.py::test_the_company_covers_the_partners_fee_its_account_cant` | M |
| M-127 | The housing authority pays the whole rent | The tenant is charged nothing | Tested | `tests/test_housing_assistance.py::test_when_the_authority_pays_all_of_the_rent_the_tenant_is_charged_nothing` | M |
| M-130 | Submeter readings adding up to more than the master meter | Refused | Tested | `tests/test_utility_billing.py::test_a_submetered_bill_reads_the_master_meter_and_the_units_meters` | M |
| M-137 | Closing a year not over, or with nothing reconciled | Refused | Tested | `tests/test_year_close.py::test_a_year_not_over_or_with_nothing_reconciled_is_not_closed` | M |
| M-138 | A charge dated in the year while it is being closed | Waits, then is refused | Tested | `tests/test_year_close.py::test_a_charge_dated_in_the_year_waits_for_a_close_under_way_and_then_is_refused` | M |
| M-142 | An overdrawn owner's opening balance (below 0) | Refused: each balance is positive | Tested | `tests/test_cutover.py::test_each_balance_is_a_positive_amount_in_cents` | M |
| M-143 | A cutover dated in the future | Refused; today is allowed | Tested | `tests/test_cutover.py::test_a_cutover_date_still_to_come_is_refused_and_today_is_not` | M |
| M-145 | Unpaid bills from the old system | Come over and hold back draws | Tested | `tests/api/test_switch.py::test_unpaid_bills_come_over_and_hold_back_draws` | M |
| M-147 | An online payment paid during a hold that would all be prepaid rent | Waits for staff | Tested | `tests/api/test_online_payments_api.py::test_a_payment_paid_during_a_hold_waits_even_when_it_would_all_be_prepaid_rent` | M |
| M-148 | A balance autopay bigger than what is owed less what is on its way | Pays no more than either | Tested | `tests/test_autopay.py::test_a_balance_autopay_pays_no_more_than_its_amount_or_than_is_owed_less_what_is_coming` | M |
| M-149 | Two roommates' equal payments on one day in one payment file | The second waits as recorded already, for staff | Tested | `tests/api/test_payment_files_api.py::test_a_payment_that_looks_recorded_already_waits` | M |
| M-008 | A charge or bill of $10.005 written by a path other than the API or the CSV parser | Kept: the tables check only amount above 0, not whole cents | Open | `db/migrations/20261010000012_leases_and_charges.sql:66` | L |
| M-010 | Deposit interest paid monthly instead of once a year | Each payment rounds per deposit, so twelve payments can differ by cents from one; nothing evens it out | Open | `db/migrations/20261022000122_deposit_interest.sql:165` | L |
| M-014 | The same request id resent with a different check number or method | Taken as a retry: the first payment comes back and the new reference is silently dropped | Open | `src/pms/api/rent.py:362` | L |
| M-054 | Rent still open past grace after the deposit statement went out | A late fee is still charged to the ended lease after the return | Open | `db/migrations/20261031000200_late_fees_skip_paid_rents.sql:139` | L |
| M-107 | A bill dated after today paid today | Allowed: nothing checks the payment is on or after the bill's date | Open | `src/pms/api/payables.py:1255` | L |
| M-114 | A leasing fee on a renewal or a move to another unit | Can be taken again: each is a new lease | Open | `db/migrations/20261010000016_owner_money.sql:305` | L |
| M-115 | A management or other fee when the owner's money is held for bills or the reserve | Taken anyway (only the balance is checked); the bill turns Short | Open | `db/migrations/20261010000016_owner_money.sql:183` | L |
| M-139 | Year end at 7 pm Eastern on Dec 31 (UTC is already Jan 1) | The year under way and the year having ended are read in UTC, not the company's day | Open | `db/migrations/20261021000106_year_close.sql:147` | L |
| M-002 | A negative payment, such as -$5.00 | Refused by the request model (greater than 0); never reaches the ledger | Guarded | `src/pms/api/rent.py:35` | L |
| M-007 | A CSV amount with a fraction of a cent (switching, statements, payment files) | Refused with its line: not an amount in cents | Guarded | `src/pms/statements.py:70` | L |
| M-038 | A rent due day of 29, 30 or 31 | Refused (1 to 28) | Guarded | `db/migrations/20261018000081_rent_due_days.sql:30` | L |
| M-044 | A lease end moved back into a closed year | Refused by the closed-year check on its adjustment charges | Guarded | `db/migrations/20261021000106_year_close.sql:138` | L |
| M-092 | Two Pay owners runs printing checks from one bank account at once | Check numbers wait on a lock per bank account | Guarded | `src/pms/api/payouts.py:448` | L |
| M-102 | A shared invoice of 21 parts | Refused (parts 1 to 20) | Guarded | `db/migrations/20261018000083_shared_bills_and_markups.sql:57` | L |
| M-001 | A payment of $1.001 (three decimals) | Refused 422 before anything is posted | Tested | `tests/api/test_rent.py::test_bad_input_is_refused` | L |
| M-003 | Part-month rent landing on half a cent (1000.01 x 15/30) | Rounds half up to 500.01 | Tested | `tests/test_prorated_rent.py::test_half_a_cent_rounds_up` | L |
| M-004 | Part-month rent that rounds to $0.00 (rent 0.01 for one day) | No charge is written | Tested | `tests/test_prorated_rent.py::test_a_part_month_that_rounds_to_nothing_is_not_charged` | L |
| M-006 | A utility bill whose shares have fractions of a cent | Each share rounded down; leftover cents stay with the owner | Tested | `tests/test_utility_billing.py::test_a_share_is_rounded_down_never_up` | L |
| M-009 | Deposit interest across a leap year (366 days) | Each day earns its rate over that year's own number of days | Tested | `tests/properties/test_deposit_interest_properties.py::test_deposit_interest_is_each_days_deposit_at_its_rate_paid_once` | L |
| M-011 | A payment $0.01 more than the lease owes | Owner gets what is owed; the cent becomes the first staying tenant's prepaid rent | Tested | `tests/api/test_rent.py::test_a_payment_pays_what_is_owed_oldest_first_and_holds_the_rest_as_prepaid_rent` | L |
| M-032 | A lease starting Feb 15 in a leap year | Charged 15 of 29 days | Tested | `tests/test_prorated_rent.py::test_a_month_is_its_own_number_of_days` | L |
| M-052 | A payment that bounced after the grace ran out | No late fee follows | Tested | `tests/test_rent_automation.py::test_a_bounce_after_the_grace_ran_out_changes_nothing` | L |
| M-106 | A vendor's credit memo (a bill of 0 or below) | Refused: bills are above 0 and there is no credit path | Tested | `tests/test_vendor_bills.py::test_a_bills_details_stay_in_range` | L |
| M-109 | Little rent collected in a fee period | The minimum fee applies | Tested | `tests/test_owner_money.py::test_the_minimum_applies_when_little_rent_came_in` | L |

### Most pressing open, with the test that would close each

- M-034: a test that charges a month's rent on the 1st, runs `trust_run_rent` again with the 15th of that month as the due date, and asserts no second rent is charged (and that `POST /api/rent/run` refuses or ignores a due date that is not the 1st).
- M-035: the same mid-month second run, asserting each recurring charge (pet rent, parking) is charged once in the month.
- M-049: a test that adds late fee terms starting before rents that were paid late or are still open, runs the nightly run, and asserts no fee is charged for rents whose grace ended before the terms were added.
- M-050: a test that closes a year, adds late fee terms backdated into it with a rent there still open, and asserts the next nightly run still charges this month's rent (no fee is written into the closed year, and the run doesn't fail).
- M-144: a switching test that brings in a lease on the 15th of a month whose rent the old system collected, runs the nightly run, and asserts no full-month rent and no late fee are charged for that month.
- M-072: a move-out test where the tenant holds prepaid rent and owes a fee, asserting the prepaid rent pays the fee before the deposit is kept and the rest is refunded (or that the return refuses until it is).
- M-073: a renewal test where the old lease still owes, asserting a later payment pays the old lease first and the final deposit return keeps for both leases.
- M-081: a property test that renews a lease and pays interest on both the old and the new lease, asserting no day of a deposit earns interest twice.
- M-082: a test with one tenant on two leases at once, asserting each lease's deposit interest counts only that lease's deposit.
- M-094: a bill test with the approval limit equal to the bill and a markup on top, asserting it waits for the owner's approval (or pinning that the markup is outside the limit on purpose).

## Dates, time zones and calendars

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| D-016 | Server down from the 28th to the month's end, lease due on the 28th | The next run charges only the new month (trust_run_rent from the 1st of today's month): the missed month's due-day rent and recurring charges are never charged and nothing says so | Open | src/pms/nightly.py:115 | H |
| D-017 | Server down for a whole month, back on the 2nd of the next | That month's rent is never charged for any lease; only an admin's Run rent now with that month's date would catch it up | Open | src/pms/nightly.py:115 | H |
| D-026 | Run rent now with Rent due on next month or a later year | Not checked (only late fees as of is): next month's rent is charged today | Open | src/pms/api/rent.py:1300 | H |
| D-055 | A lease entered on the evening of its last day (after 8 pm Eastern) | created_at::date reads the next day in a UTC session, so it counts as entered after its end and never rolls over; its rent stops | Open | db/migrations/20261024000154_receive_payment_and_lease_rules.sql:163 | H |
| D-065 | A rent change from 03/01 with notice given 02/01 | Allowed: one month before is only 28 days in February, short of a 30-day rule (California's least), and no state warning runs | Open | db/migrations/20261015000061_rent_changes.sql:44 | H |
| D-066 | A rent increase recorded on the rent page in CA, OR, WA, NV or NY | No state notice check runs there (rent_notice is called only by the renewal plan); the database asks a month | Open | src/pms/api/rent.py:1504; src/pms/api/renewals.py:190 | H |
| D-067 | New York renewal of a 12-month lease (365 days, no February 29) | years = 365 x 4 // 1461 = 0, so the panel asks 30 days; RPL 226-c asks 60 for a lease term of at least a year | Open | src/pms/api/renewals.py:203 | H |
| D-068 | New York tenant there exactly two years (730 days) | Counts as one year: 60 days asked instead of 90 | Open | src/pms/api/renewals.py:203 | H |
| D-069 | California increase notice served by mail | The 5 extra days for mail (CCP 1013) are not added; days are counted from today | Open | src/pms/rent_notice.py:83 | H |
| D-078 | A check received on the last grace day, recorded the next morning dated back | The night already charged the fee; nothing undoes a late fee when a payment dated within grace comes in later | Open | db/migrations/20261031000200_late_fees_skip_paid_rents.sql:208 | H |
| D-079 | A lockbox file line dated within grace brought in after the fee | Same: the fee stays until staff waive it | Open | src/pms/api/payment_files.py:109 | H |
| D-080 | Rent paid online by bank on the due day that settles after the grace | Dated when the partner reports it paid (the event's created time), so a short grace charges a late fee though the tenant paid in time | Open | src/pms/api/online_payments.py:533; src/pms/payments.py:292 | H |
| D-081 | Autopay started on the due day, settled 2 to 4 business days later | Same as online: every autopay tenant whose grace is shorter than settlement is charged a late fee | Open | src/pms/autopay.py:371; src/pms/api/online_payments.py:533 | H |
| D-082 | Late fee terms added with a start day in the past | The next night charges a fee on every old open rent back to that day; one dated in a closed year is refused and the company's whole run fails every night, and append-only terms can't be undone | Open | src/pms/api/setup.py:751; db/migrations/20261021000106_year_close.sql (trust_refuse_closed_year) | H |
| D-083 | Rent first charged after its own grace ran out (lease entered late, an admin's catch-up) | Charged a late fee the same night, before the tenant could see the rent | Open | db/migrations/20261031000200_late_fees_skip_paid_rents.sql:208 | H |
| D-128 | A fee charged after the 10-day deadline passed | The deadline moves to 14 days, hiding that the 10-day one was missed (the test pins the move) | Open | src/pms/api/moveout.py:134; tests/api/test_deposit_statements_api.py::test_a_lease_shows_when_its_deposit_is_due_back_and_its_fees_with_their_receipts | H |
| D-129 | Tenants who moved out before the lease's last day | The deadline still counts from the lease's last day; New York counts from vacating (docs/law.md) | Open | src/pms/api/moveout.py:129 | H |
| D-054 | A term ending on the last day of the month | Rolled over before that night's rent, so the new month is charged | Guarded | src/pms/nightly.py:112 | H |
| D-086 | Staff record a payment at 9 pm Eastern dated today (UTC already tomorrow) | Accepted and dated the company's day; the database's UTC checks never refuse it; tests use UTC companies only | Guarded | src/pms/api/rent.py:1720 | H |
| D-119 | A vendor paid at 9 pm Eastern on 12/31 | Dated 12/31 (the company's day at 00:00 UTC), so in that 1099 year; no test of the evening case | Guarded | src/pms/api/payables.py:56 | H |
| D-003 | 9 pm Eastern on 10/31 (01:00 UTC on 11/01), in each allowed zone | trust_pmc_day gives 10/31 in every US zone and 11/01 in UTC | Tested | tests/test_nightly.py::test_a_companys_day_is_the_date_in_its_time_zone | H |
| D-004 | A payment the partner reports paid at 11:30 pm Pacific on the 31st | Dated the 31st (the company's day) and on that month's owner statement only | Tested | tests/properties/test_company_day_properties.py::test_a_payment_is_on_the_statement_of_the_companys_own_day | H |
| D-010 | At 05:30 UTC on 11/01 it is still 10/31 in Hawaii | Each company runs for its own day: Hawaii gets no November rent until its midnight | Tested | tests/test_nightly.py::test_each_company_runs_for_its_own_day | H |
| D-011 | Scheduler start, then each company once its day begins | Runs all at start, then only companies whose day changed since its last run | Tested | tests/test_nightly.py::test_the_scheduler_runs_each_company_once_its_day_has_begun | H |
| D-014 | Two API servers, or the command while the API runs | One run at a time under an advisory lock; the other leaves the work | Tested | tests/test_nightly.py::test_one_run_at_a_time_another_leaves_the_work_to_it | H |
| D-021 | A fixed term ran out and nightly runs were missed | Still goes month to month within 7 days of its end; after that it stays ended and rent stops | Tested | tests/test_lease_holdovers.py::test_a_missed_night_still_rolls_it_within_a_week_but_no_later | H |
| D-024 | One run after several missed nights | Charges the late fees a run every day would have | Tested | tests/test_rent_automation.py::test_one_run_charges_what_a_run_every_day_would | H |
| D-025 | Run rent now with Rent due on set mid-month (the 15th) after the 1st was charged | Charged once a month whatever day the run is for: a month with any of its rent due already is not charged again (migration 20261105000400) | Tested | `tests/test_rent_once_a_month.py::test_a_mid_month_run_charges_no_month_twice`; `tests/properties/test_rent_once_a_month_properties.py` | H |
| D-029 | A lease starting mid-month | Its part month, due on its first day | Tested | tests/test_prorated_rent.py::test_a_lease_starting_later_in_the_month_is_charged_its_part_month_due_its_first_day | H |
| D-030 | A lease ending mid-month | Charged to its last day | Tested | tests/test_prorated_rent.py::test_a_lease_ending_in_the_month_is_charged_to_its_last_day | H |
| D-031 | A lease starting and ending in one month | Charged its days | Tested | tests/test_prorated_rent.py::test_a_lease_starting_and_ending_in_one_month_is_charged_its_days | H |
| D-032 | February in a leap year (29 days) and a 30-day April | Each month divides by its own days | Tested | tests/test_prorated_rent.py::test_a_month_is_its_own_number_of_days | H |
| D-036 | A lease that started this month, entered after the month's run | Charged at the next run | Tested | tests/test_prorated_rent.py::test_a_lease_entered_after_the_months_run_is_charged_when_it_runs_again | H |
| D-043 | Rent due on the 10th | Charged on the 10th, once | Tested | tests/test_rent_due_days.py::test_rent_due_on_the_tenth_is_charged_on_the_tenth_once | H |
| D-046 | A due day before the lease starts or after it ends in a part month | Kept within the lease's days that month | Tested | tests/test_rent_due_days.py::test_part_months_keep_the_due_date_within_the_leases_days | H |
| D-047 | Late fee grace for a lease due on the 10th | Counted from the 10th | Tested | tests/test_rent_due_days.py::test_late_fees_count_their_grace_from_the_due_date | H |
| D-052 | A fixed term that ran out with no renewal | Goes month to month the night after its last day, once | Tested | tests/test_lease_holdovers.py::test_a_term_that_ran_out_goes_month_to_month_once | H |
| D-053 | A term ending mid-month goes month to month | The rest of the month is charged back at the same rent | Tested | tests/test_lease_holdovers.py::test_the_days_of_its_last_month_come_back_at_the_same_rent | H |
| D-056 | A signed renewal | Ends the old lease the day before it starts | Tested | tests/test_lease_offers.py::test_a_renewal_ends_the_lease_the_day_before_and_keeps_its_tenants | H |
| D-057 | A renewal completed mid-month after the month was charged | The month stays whole across both leases | Tested | tests/test_lease_end_credits.py::test_a_renewal_mid_month_after_the_run_keeps_the_month_whole | H |
| D-058 | An end set mid-month after the run | Credits the days after it | Tested | tests/test_lease_end_credits.py::test_an_end_set_mid_month_after_the_run_credits_the_days_after_it | H |
| D-061 | A new lease starting on the old one's last day | Refused: one lease at a time per unit | Tested | tests/test_leases_and_charges.py::test_two_leases_of_one_unit_never_overlap | H |
| D-062 | A rent change with less than a month's written notice | Refused: notice by the 1st of the month before | Tested | tests/test_rent_changes.py::test_written_notice_of_a_full_month_comes_first | H |
| D-063 | A rent change for a month already charged | Refused; that month keeps its rent | Tested | tests/test_rent_changes.py::test_a_month_already_charged_keeps_its_rent | H |
| D-072 | California 30 versus 90 days at 10% | As recorded in docs/law.md | Tested | tests/test_rent_notice.py::test_california_asks_30_days_up_to_ten_percent_and_90_over_it | H |
| D-073 | New York tenant in their first year | 30 days | Tested | tests/api/test_renewal_plan_api.py::test_new_york_asks_30_days_in_the_first_year | H |
| D-074 | A payment dated the last grace day versus the day after | The first counts, the second doesn't (cutoff at the next UTC midnight; money is dated at 00:00 UTC of the company's day) | Tested | tests/test_rent_automation.py::test_a_payment_counts_only_if_dated_before_the_grace_runs_out | H |
| D-075 | Late fee terms changed after a rent's due date | The terms in force on the due date apply | Tested | tests/test_rent_automation.py::test_the_terms_in_force_on_the_rents_due_date_apply | H |
| D-085 | Staff record a payment dated tomorrow | Refused: money is recorded no later than the company's today | Tested | tests/api/test_rent.py::test_money_is_recorded_no_later_than_today | H |
| D-087 | A payment dated in a reconciled period | Refused | Tested | tests/test_reconciliation.py::test_a_posting_dated_inside_a_closed_period_is_refused | H |
| D-088 | An online payment whose day month-end closed before the report came | Recorded on the first open day | Tested | tests/api/test_online_payments_api.py::test_a_payment_whose_day_month_end_closed_is_recorded_in_the_next_open_one | H |
| D-092 | A payment dated on a payment hold's first day | Refused unless allowed | Tested | tests/test_payment_holds.py::test_a_payment_dated_from_the_holds_first_day_is_refused | H |
| D-101 | A bill dated before a new approval limit took effect | The limit in force on the bill's date applies | Tested | tests/test_vendor_bills.py::test_the_limit_in_force_on_the_bills_date_applies | H |
| D-106 | Preparing a month whose last day is the company's today | Refused until the company's day ends | Tested | tests/api/test_reconcile.py::test_a_period_is_prepared_once_the_books_lack_no_line_and_approved_by_another | H |
| D-107 | Approving a period whose end has not come | Refused | Tested | tests/test_reconciliation.py::test_a_period_is_approved_only_once_it_has_ended | H |
| D-108 | A posting dated on a closed period's end | Belongs to the open period | Tested | tests/test_reconciliation.py::test_a_posting_dated_at_or_after_the_period_end_is_accepted | H |
| D-112 | A charge or bill dated in a closed year | Refused; date it in the open year | Tested | tests/test_year_close.py::test_after_a_close_nothing_is_charged_or_billed_in_the_closed_year | H |
| D-114 | Opening balances on the cutover date | From cash at the start of the cutover date (UTC) | Tested | tests/test_cutover.py::test_opening_balances_are_posted_from_cash_at_the_start_of_the_cutover_date | H |
| D-116 | An owner statement's period | Includes its first day, not its end | Tested | tests/test_owner_statement.py::test_the_period_includes_its_start_and_not_its_end | H |
| D-118 | The 1099 threshold by year (600.00 through 2025, 2000.00 from 2026, 2027 unknown) | Each year's threshold, and 2027's line says it isn't known | Tested | tests/test_recurring_bills.py::test_the_threshold_is_the_irss_for_the_year | H |
| D-122 | A management fee over a period whose terms changed | The agreement in force on the period's first day | Tested | tests/test_owner_money.py::test_the_agreement_in_force_on_the_periods_first_day_sets_the_terms | H |
| D-123 | Automatic management fees before their day of the month | Nothing taken until it comes | Tested | tests/test_owner_fees.py::test_nothing_is_taken_until_a_company_turns_it_on_and_its_day_comes | H |
| D-124 | The draw day | Draws prepared on or after it, once a month, none paid | Tested | tests/test_nightly.py::test_a_night_on_or_after_the_draw_day_prepares_the_owners_draws_once_and_pays_none | H |
| D-126 | The company's earned fees left in trust | Take-by day 25 days after the oldest | Tested | tests/api/test_company_fees_api.py::test_staff_take_the_companys_fees_out_oldest_first_and_once | H |
| D-127 | A deposit's return deadline | 14 days after the lease's last day, 10 while it owes nothing | Tested | tests/api/test_deposit_statements_api.py::test_return_by_is_the_strictest_deadline_found_from_the_last_day | H |
| D-006 | The database server's session TimeZone is not UTC (an operator's Postgres set to a local zone) | Reports name UTC explicitly, but trust_roll_over_leases casts created_at::date and trust_check_autopay_run casts saved_at::date in the session's zone; a zone east of UTC makes the autopay trigger stricter than the Python check and fails that company's whole nightly run | Open | db/migrations/20261024000154_receive_payment_and_lease_rules.sql:163; db/migrations/20261023000151_autopay.sql:146 | M |
| D-015 | One company's run fails just after its midnight (a deadlock, a refused insert) | Logged and its admins emailed, but the day is marked done: nothing retries until the next night, so that day's late fees, reminders and autopay start a day late | Open | src/pms/nightly.py:308 | M |
| D-018 | Server down from the draw day to the month's end | Draws are prepared only on or after the draw day within the same month, so that month's draws are never prepared | Open | db/migrations/20261024000153_draws_and_payouts.sql:398 | M |
| D-019 | Server down from the automatic management fee day to the month's end | The next month takes only its own last month; the missed month waits for staff with nothing on the Dashboard | Open | db/migrations/20261018000082_owner_fees.sql:86 | M |
| D-022 | An operator schedules python -m pms.nightly once a day at 00:xx UTC | At that hour every US company is still on the day before, so each runs a day late (the 1st's rent and autopay go the evening of the 1st) | Open | src/pms/nightly.py:197; docs/backup.md:51 | M |
| D-023 | The app server's clock is a minute or more ahead of the database's | The scheduler works out each day from the app's clock; it can run late fees for the new day while the database still dates payments yesterday | Open | src/pms/nightly.py:197 | M |
| D-035 | A lease that prorates by a 30-day month or by yearly rent over 365 | Always the calendar month's days, so a February day costs more than a March day; no per-lease choice | Open | db/migrations/20261013000045_prorated_rent.sql:33 | M |
| D-038 | Pet rent added on the 15th of a lease already running | Charged only from the next due date, in full; the days from the 15th are never charged | Open | src/pms/api/rent.py:1459 (from the first due date on or after starts_on) | M |
| D-039 | A recurring charge stopped on the 2nd | The whole month stays charged; no credit for the days after it stopped (the test pins this) | Open | tests/test_recurring_charges.py::test_a_charge_stopped_the_day_after_a_due_date_is_charged_for_it | M |
| D-045 | A lease whose terms say rent is due on the 30th or the last day of the month | Cannot be recorded; staff must pick 28 or the 1st, so the due date and its grace are off by days | Open | db/migrations/20261018000081_rent_due_days.sql:30 | M |
| D-070 | A renewal plan read today, sent next week | The warning counts from the day the plan is read, not the day it goes out | Open | src/pms/api/renewals.py:211 | M |
| D-077 | The last grace day falls on a Sunday or a legal holiday | Calendar days only; a lease or state that moves it to the next business day is charged a day early | Open | db/migrations/20261031000200_late_fees_skip_paid_rents.sql:208 | M |
| D-094 | Autopay's bank account saved in the evening of a due day (after 8 pm Eastern) | Whether this month pays depends on UTC's date of the save, not the company's: the same evening goes either way by the hour | Open | src/pms/autopay.py:371; db/migrations/20261023000151_autopay.sql:146 | M |
| D-095 | A payments partner's report checked against the server's clock | Refused when signed more than 300 seconds from the server's clock; a server clock off by minutes refuses every report until fixed, and nothing alerts | Open | src/pms/payments.py:445 | M |
| D-096 | An owner draw dated back before a bill's date | The draw counts bills dated by its own day but today's balance, so it can pay out what a later-dated bill needs | Open | db/migrations/20261010000017_vendor_bills.sql:321; src/pms/api/payables.py:2119 | M |
| D-098 | A bill dated tomorrow, between 8 pm and midnight Eastern | trust_bill_holds counts bills dated by UTC's today: it holds the money and reads Ready to pay or Short hours early instead of Entered | Open | db/migrations/20261104000300_shortfall_keeps_the_reserve.sql:67 | M |
| D-099 | A management agreement starting tomorrow, the same evening window | Its reserve already applies to the holds tonight | Open | db/migrations/20261104000300_shortfall_keeps_the_reserve.sql:35 | M |
| D-103 | A recurring bill added with a first day years back | Every month since is entered at once as a bill to pay; one in a closed year refuses the whole add | Open | db/migrations/20261015000052_expense_accounts_and_income_statement.sql:113 | M |
| D-104 | Staff record an owner's decline with any date (tomorrow, or before a past draw) | declined_on is not checked against today or the bill; a past day changes what draws on those days could take | Open | src/pms/api/payables.py:1141 | M |
| D-117 | A payment dated back into last month after that month's statement email went | The email and its PDF stay as sent; nothing tells the owner the month changed | Open | src/pms/notices.py:353 | M |
| D-130 | A roommate leaves a lease others stay on | Their deposit stays under their name until the lease's return; no deadline at their leaving | Open | src/pms/api/moveout.py:157 | M |
| D-135 | A showing typed by staff whose browser is in another zone than the property | The time is read in the viewer's own zone (a Los Angeles browser booking 10:00 books 1 pm Eastern) and shown with no zone named; the test pins the browser's zone | Open | frontend/src/leads.ts:73; frontend/src/pages/LeadPages.test.tsx::reads what a lead asked about and a showing's time | M |
| D-139 | The signing page's You signed on | The UTC date of the signature: a tenant signing at 9 pm Pacific sees tomorrow's date | Open | frontend/src/pages/SignPage.tsx:111 | M |
| D-147 | Tests that count days in UTC | make_pmc gives every test company UTC's day, so most money paths never run for a New York company between 00:00 and 05:00 UTC; only the nightly and company-day tests do | Open | tests/helpers.py:109 | M |
| D-150 | A staff code entered at a 30-second step boundary, or an app clock off by a minute | The step before and after are accepted; the app's own clock is used, so a skew past 30 seconds refuses good codes | Open | src/pms/accounts.py:432 | M |
| D-153 | A vendor assigned at 9 pm Eastern on the last day of their insurance | The check uses the step's UTC date, so the vendor reads as expired and is refused while still covered that day | Open | db/migrations/20261016000072_work_order_details.sql:80 | M |
| D-012 | Daylight saving starts (Eastern midnight moves from 05:00 to 04:00 UTC) | The look a minute past every hour still catches the new day; no test runs the scheduler across a DST change | Guarded | src/pms/nightly.py:64 | M |
| D-064 | A rent change whose notice is dated after today | Refused by the API | Guarded | src/pms/api/rent.py:1504 | M |
| D-076 | Grace running into the next month or year (due the 28th, 5 days) | Plain date arithmetic; the fee is due the day after grace | Guarded | db/migrations/20261031000200_late_fees_skip_paid_rents.sql:208 | M |
| D-109 | The database's own preparation check counts UTC | trust_prepare_bank_reconciliation would take a period through today after 8 pm Eastern; only the API's company-day check stops it | Guarded | src/pms/api/reconcile.py:999; db/migrations/20261012000034_staff_roles.sql:264 | M |
| D-111 | Closing the year at 7 pm Eastern on 12/31 | trust_close_year counts the year ended in UTC, but December can't be prepared until the company's 01/01, so the close waits | Guarded | db/migrations/20261021000106_year_close.sql:69; src/pms/api/reconcile.py:999 | M |
| D-133 | Deposit interest paid through a day still to come | Refused | Guarded | db/migrations/20261022000122_deposit_interest.sql:287 | M |
| D-152 | A tenant's notice to move out for today, given at 9 pm Eastern | The trigger uses the company's day, so it is accepted; the API test runs for a UTC company only | Guarded | db/migrations/20261104000900_company_time_zone.sql:55 | M |
| D-001 | A company that never picked a time zone | Its day is New York's: trust_pmc_time_zone falls back to America/New_York and /api/me says so | Tested | tests/api/test_company_api.py::test_a_company_that_picked_no_time_zone_is_in_new_york | M |
| D-002 | A zone ahead of UTC (Europe/London, Pacific/Kiritimati) or a made-up one | Refused by the API's list and by the column's CHECK, so a company's day is never after UTC's | Tested | tests/api/test_company_api.py::test_a_bad_company_is_refused | M |
| D-007 | Owner statement run under a session in another time zone | Reads the same days: reports cut days at UTC midnight explicitly | Tested | tests/test_owner_statement.py::test_dates_read_the_same_in_any_time_zone | M |
| D-027 | Run rent now with late fees as of tomorrow | Refused | Tested | tests/api/test_rent.py::test_late_fees_are_assessed_up_to_today | M |
| D-033 | A part month that comes to half a cent | Rounds up | Tested | tests/test_prorated_rent.py::test_half_a_cent_rounds_up | M |
| D-037 | A lease that started last month, entered this month | Last month's part is never charged by the run (by design, for leases brought over); nothing asks staff to charge it | Tested | tests/test_prorated_rent.py::test_the_run_never_reaches_back_into_an_earlier_month | M |
| D-044 | A due day of 29, 30, 31 or 0 | Refused (1 to 28) | Tested | tests/test_rent_due_days.py::test_a_due_day_starts_after_the_last_month_charged_within_the_lease | M |
| D-048 | A renewal of a lease due on the 15th | Keeps the due day | Tested | tests/api/test_offers.py::test_a_renewal_keeps_the_rent_due_day_of_the_lease_it_renews | M |
| D-059 | Leases ending in 0-30, 31-60 and 61-90 days | Grouped by days left from the as-of day | Tested | tests/test_lease_expirations.py::test_leases_ending_in_the_next_90_days_are_listed_by_days_left | M |
| D-084 | A returned payment's NSF fee | The terms in force on the UTC day it came back (money is dated by the company's day at 00:00 UTC) | Tested | tests/test_nsf_fees.py::test_the_day_it_came_back_is_the_utc_day | M |
| D-089 | A payment file line dated tomorrow or 10/32/2026 | The whole file is refused with the line | Tested | tests/api/test_payment_files_api.py::test_a_file_that_is_not_payments_received_is_refused_whole | M |
| D-090 | A bank statement with dates as DD/MM/YYYY or two-digit years | Only YYYY-MM-DD and MM/DD/YYYY read; anything else is refused with its line | Tested | tests/test_statement_files.py::test_a_csv_that_is_not_a_statement_says_where_and_why | M |
| D-093 | Autopay saved after this month's due date | Pays from next month | Tested | tests/test_autopay.py::test_an_autopay_saved_after_this_months_due_date_pays_from_next_month | M |
| D-097 | A draw under a reserve that changed | The reserve in force on the draw's day applies | Tested | tests/test_owner_money.py::test_the_reserve_in_force_on_the_draws_day_applies | M |
| D-100 | A bill dated later than today (UTC company) | Holds nothing yet | Tested | tests/test_bill_lifecycle.py::test_a_bill_holds_its_markup_and_a_bill_dated_later_holds_nothing_yet | M |
| D-102 | A recurring bill on day 1 to 28 | Each month's bill once, from its first day through the day asked | Tested | tests/test_recurring_bills.py::test_each_months_bill_is_entered_from_the_first_day_through_the_day_asked | M |
| D-113 | A cutover date still to come | Refused, today allowed (UTC's today, so a New York company's tomorrow passes in its evening) | Tested | tests/test_cutover.py::test_a_cutover_date_still_to_come_is_refused_and_today_is_not | M |
| D-120 | A 01/01 cutover | Carries over the whole year before for 1099s | Tested | tests/test_vendor_payments_carried_over.py::test_a_january_first_cutover_carries_over_the_whole_year_before | M |
| D-121 | A payment collected in December that bounces in January | Comes off the year it bounced in | Tested | tests/test_owner_year_end.py::test_a_bounce_comes_off_the_year_it_bounced_in | M |
| D-132 | Deposit interest across a leap year (366 days) | Each day's rate over the days in its own year | Tested | tests/properties/test_deposit_interest_properties.py::test_deposit_interest_is_each_days_deposit_at_its_rate_paid_once | M |
| D-138 | A browser in Kiritimati (UTC+14) or Los Angeles signed in to a New York company | Default dates come from the company's zone, not the browser's | Tested | frontend/src/money.test.ts::is the date in the company's time zone | M |
| D-148 | A session past 12 hours | Refused | Tested | tests/api/test_sign_in.py::test_a_session_lasts_twelve_hours | M |
| D-149 | A session left alone for an hour | Signed out | Tested | tests/api/test_idle_sessions.py::test_a_session_left_alone_for_the_hour_is_signed_out | M |
| D-151 | A signing link more than 30 days old | Signs nothing | Tested | tests/test_esign.py::test_a_link_signs_nothing_once_it_ran_out_or_its_offer_was_voided | M |
| D-155 | An annual certification due a year after 02/29 | 02/28 the next year | Tested | tests/api/test_assistance_api.py::test_a_year_on_is_the_same_day_or_february_28_for_a_29th | M |
| D-157 | Vendor insurance running out within 30 days | Counted from the company's today | Tested | tests/api/test_vendors.py::test_expiring_insurance_waits_on_today | M |
| D-005 | The browser's time zone data and the database's disagree after a DST rule change | Default dates come from the browser's Intl data, today's checks from Postgres's tzdata; nothing compares them outside CI | Open | frontend/src/money.ts:38; db/migrations/20261104000900_company_time_zone.sql:39 | L |
| D-008 | A company moves its zone backwards after UTC midnight (New York to Hawaii at 1 am Eastern) | Its day goes back to yesterday; the scheduler only checks that the day differs from its last run, so it runs yesterday again (parts are once per date) and payments staff record next are dated yesterday | Open | src/pms/nightly.py:293 | L |
| D-009 | A company moves its zone forwards in its evening (Hawaii to New York at 10 pm Hawaii) | Its day jumps to tomorrow at once: payments recorded tonight are dated tomorrow and the new day's run waits for the next hourly look | Open | src/pms/api/company.py:219 | L |
| D-028 | The list of rent runs shows a run's day | Sliced from the UTC timestamp: a run by hand at 9 pm Eastern reads as tomorrow | Open | frontend/src/pages/RentPage.tsx:203 | L |
| D-049 | A rent reminder for a first part month due on a move-in day (the 20th) | Reminders look for leases whose due-day number is that day, so the first rent due on the 20th gets none | Open | src/pms/notices.py:186 | L |
| D-050 | The due day moved from the 10th to the 15th after the 10th's reminder went | Reminders are once per due date, not per month, so a second one goes for the 15th | Open | src/pms/notices.py:186 | L |
| D-060 | A renewal sent or signed at 9 pm Eastern on the report's day | Renewal status is as of UTC's end of day, so it shows tomorrow's status; the test pins UTC | Open | tests/test_lease_expirations.py::test_the_status_is_as_of_the_end_of_the_day | L |
| D-071 | California's 10% test over the 12 months before, across February 29 | The window is 365 days, a day short in a leap year | Open | src/pms/api/renewals.py:195 | L |
| D-105 | Asking an owner for a Short bill's money once a day | The day is UTC's, so two asks can go in one company evening | Open | src/pms/email.py:434 | L |
| D-110 | A bank statement whose last day is the company's tomorrow, imported after 8 pm Eastern | Accepted: the database's check is UTC's today and the API adds none; the test covers a UTC company only | Open | db/migrations/20261012000033_switching.sql:159; tests/api/test_reconcile.py::test_a_statement_out_of_turn_twice_or_ahead_of_today_is_refused | L |
| D-115 | A history line from the old system dated the company's tomorrow, in its evening | Allowed: never after UTC's today | Open | db/migrations/20261021000107_history.sql:92 | L |
| D-136 | A showing at 2:30 am on the spring-forward day | That local time doesn't exist; the browser shifts it without saying | Open | frontend/src/leads.ts:73 | L |
| D-137 | A tenant who left a lease others stay on | Their feed still shows its rent due days until 30 days after they left | Open | src/pms/api/calendar.py:233 | L |
| D-140 | When an online payment started or a payment file came in | Shown as the UTC date of the timestamp | Open | frontend/src/pages/PayOnlineCard.tsx:100; frontend/src/pages/PaymentFilesPages.tsx:94 | L |
| D-142 | A work order's days open | Counted between UTC dates, so one opened at 9 pm Eastern reads a day less | Open | src/pms/api/work_orders.py:146 | L |
| D-143 | Days a unit has been listed | From the UTC date it was listed against the company's day: one listed at 9 pm Eastern reads 0 days | Open | src/pms/api/listings.py:209 | L |
| D-144 | Daily caps (20 messages, 10 requests or files a lease) | Reset at UTC midnight, 8 pm Eastern | Open | src/pms/api/messages.py:110; src/pms/api/tenant.py:448 | L |
| D-145 | A unit added in the evening that never had a lease | The vacancy report's since day is the UTC date it was added | Open | db/migrations/20261022000124_unit_details.sql:88 | L |
| D-146 | The Dashboard's occupancy the evening a unit is added | Counts units added by the day in UTC, so tonight's unit is left out until the company's tomorrow | Open | src/pms/api/dashboard.py:135 | L |
| D-154 | Archiving a unit at 9 pm Eastern on its lease's last day | The check counts UTC's today, so the lease reads as ended and the archive goes through while it is in effect | Open | db/migrations/20261024000152_bill_lifecycle.sql:509 | L |
| D-013 | Daylight saving ends (1 to 2 am repeats) | Midnight happens once; the day is the date in the zone; no test | Guarded | db/migrations/20261104000900_company_time_zone.sql:39 | L |
| D-040 | A lease ending the day before daylight saving starts | Leases, charges and proration are plain dates; no clock time is involved | Guarded | db/migrations/20261013000045_prorated_rent.sql:33 | L |
| D-041 | A renewal starting 02/29/2028 with the default one-year term | yearFrom gives 02/28/2029; no test names February 29 | Guarded | frontend/src/offers.ts:167 | L |
| D-091 | An OFX date with a time and zone (20261031T200000[-5:EST]) | The first 8 characters, the bank's own day | Guarded | src/pms/statements.py:151 | L |
| D-131 | A deadline on a Sunday or a holiday | Not moved: the stricter reading | Guarded | src/pms/api/moveout.py:134 | L |
| D-020 | Server down from the owner statement day to the month's end | That month's statement email is never written; the property test's model expects exactly that | Tested | tests/properties/test_automatic_email_properties.py::test_each_automatic_email_is_written_once_exactly_when_its_settings_and_day_call_for_it | L |
| D-034 | A one-day part month that rounds to nothing (start on the 31st, end on the 1st) | Not charged | Tested | tests/test_prorated_rent.py::test_a_part_month_that_rounds_to_nothing_is_not_charged | L |
| D-042 | The day after 12/31 and after 02/28 of a leap year in the portal | Worked out in UTC from the ISO day | Tested | frontend/src/offers.test.ts::finds the next day across a month and a year | L |
| D-051 | Calendar rent due events when a due day changes | Each event's UID is the lease and month, so it moves in place | Tested | tests/api/test_calendar.py::test_each_event_keeps_its_id_and_a_whole_day_lasts_a_day | L |
| D-125 | The Dashboard's figure for a month before the 31st | That month's last day (03/31 to 02/28) | Tested | tests/api/test_dashboard.py::test_month_before_is_the_same_day_or_the_months_last | L |
| D-134 | A calendar app fetching the feed again | Each event keeps its UID; an all-day event lasts one day | Tested | tests/api/test_calendar.py::test_each_event_keeps_its_id_and_a_whole_day_lasts_a_day | L |
| D-141 | A day typed that doesn't exist (02/30) | Refused | Tested | tests/api/test_renewal_plan_api.py::test_bad_terms_are_refused | L |
| D-156 | A recurring work order added with a first day in the past | Every one already due opens at once | Tested | tests/api/test_recurring_work_orders_api.py::test_staff_add_a_recurring_work_order_and_the_due_ones_open_at_once | L |

### Most pressing open, with the test that would close each

- D-067: A new test in tests/api/test_renewal_plan_api.py: a NY lease of exactly 365 days (and one of 730) asks 60 (and 90) days for a 5% increase.
- D-025: A new test in tests/api/test_rent.py: Run rent now with due_on the 15th after the 1st was charged leaves one rent per lease and month, or is refused.
- D-016: A new test in tests/test_nightly.py: a lease due on the 28th, no run from the 28th to the 31st, run_pmc on the 1st still charges (or flags) last month's rent.
- D-082: A new test in tests/test_rent_automation.py: backdated terms charge no fee on rents due before they were entered, and an old open rent in a closed year leaves run_pmc working.
- D-080: A new test in tests/api/test_online_payments_api.py: a payment started within grace and reported paid after it charges no late fee.
- D-078: A new test in tests/test_rent_automation.py: a payment dated within grace recorded after the fee was charged undoes (or flags) that fee.
- D-066: A new test in tests/api/test_rent.py: a 03/01 change with 02/01 notice in California warns or is refused, as the renewal panel would.
- D-129: A new test in tests/api/test_deposit_statements_api.py: return_by counts from the notice's leaving day, and a fee charged after the 10-day day keeps the missed deadline.
- D-055: A new test in tests/test_lease_holdovers.py: a lease with created_at at 01:00 UTC the day after its end (its last day in New York) still rolls over.
- D-026: A new test in tests/api/test_rent.py: Run rent now with a due_on after the company's month is refused.

## Concurrency and retries

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| C-055 | A payout run's ACH file taken twice and uploaded to the bank twice | Each download is logged and the page says Taken N times, but nothing asks before a second take and the file is the same (NOT VERIFIED whether a bank refuses it) | Open | src/pms/api/payouts.py:601 (take_file) | H |
| C-087 | A returned report arrives before paid (paid retried after a 502 or 503) | The trigger refuses it, the webhook answers 200 so Stripe stops, and the return is never applied: the books keep money the bank took back (the property test pins this drop) | Open | src/pms/api/online_payments.py:570; db/schema.sql:3327 | H |
| C-088 | The same out-of-order return for an owner's payment in | Dropped the same way; the owner's account keeps money that came back | Open | src/pms/api/owner_payments.py:269; db/schema.sql:3375 | H |
| C-090 | Stripe gives up on a webhook after days of failed deliveries | Nothing polls for it; the payment never reaches the books, and if staff record it by hand a later resend records it twice | Open | src/pms/api/online_payments.py:622 (receive only; no event poll in src/pms/payments.py) | H |
| C-041 | Staff void a bill while another staff member pays it | The decision trigger locks the owner's account; a void after money was taken is refused | Guarded | db/schema.sql:2496 (trust_check_bill_decision) | H |
| C-050 | Two staff pay the night's prepared draws under different request ids | One approval; the second gets 409 paid already | Guarded | src/pms/api/payables.py:2208 | H |
| C-063 | A journal entry posted twice at once | Entry lock; the second returns the transfers already posted | Guarded | db/schema.sql:6939 (trust_post_journal_entry) | H |
| C-076 | Autopay started by the nightly run and by Run rent now at once | Lease lock and one run per autopay and due date | Guarded | src/pms/autopay.py:375 | H |
| C-078 | One autopay payment sent by two processes at once | Same Stripe retry key, a lookup by payment id, and the submission row ON CONFLICT | Guarded | src/pms/payments.py:242 (StripePartner.charge) | H |
| C-079 | The process dies after Stripe took the payment, before the submission row | The next send finds it on Stripe by payment id instead of making a second | Guarded | src/pms/payments.py:248 | H |
| C-086 | Two paid reports (session completed and intent succeeded) at the same moment | Lease lock and the event's own lock serialize them; each posting has a key | Guarded | src/pms/api/online_payments.py:560 | H |
| C-001 | The same idempotency key posted twice in a row | The second call returns the first call's transfers and posts nothing | Tested | `tests/test_idempotency.py::test_same_key_twice_posts_once_and_returns_the_original` | H |
| C-002 | A key reused with a different amount, account or day | Refused as used for a different posting; nothing is written | Tested | `tests/test_idempotency.py::test_same_key_with_different_contents_is_refused_and_nothing_written` | H |
| C-003 | Two calls with one key at the same instant | One posts; the other waits on the key and gets the original | Tested | `tests/test_idempotency.py::test_two_calls_with_one_key_at_once_post_once` | H |
| C-004 | Many transfers on the same accounts at once | All finish and every balance equals its transfers | Tested | `tests/test_ledger_core.py::test_concurrent_transfers_all_finish_and_balances_add_up` | H |
| C-005 | Two batches touching the same accounts in opposite order | Accounts are locked in sorted order before any change, so no deadlock | Tested | `tests/test_ledger_core.py::test_batches_lock_accounts_in_sorted_order_before_changing_anything` | H |
| C-007 | A posting from a snapshot taken before a period was approved | Refused at the close date; it cannot slip into the closed period | Tested | `tests/test_reconciliation.py::test_a_posting_from_an_older_snapshot_cannot_slip_into_a_period_closed_since` | H |
| C-008 | Receive payment double-clicked or resent with the same request id | Recorded once; the same answer comes back | Tested | `tests/api/test_rent.py::test_sending_a_payment_again_records_it_once` | H |
| C-012 | A payment's connection killed just before commit, then resent | Nothing stands; the resend lands once | Tested | `tests/test_faults.py::test_a_payment_cut_off_before_it_commits_leaves_nothing_and_its_retry_lands_once` | H |
| C-013 | Two different payments at once on one open charge | The charge lock keeps them from paying past it together | Tested | `tests/test_leases_and_charges.py::test_two_payments_at_once_cannot_overpay_a_charge` | H |
| C-016 | A match while a payment hold is being placed | The match waits for the hold, then is refused | Tested | `tests/test_payment_holds.py::test_a_match_waits_for_a_hold_being_placed_and_is_then_refused` | H |
| C-018 | Two reversals of one payment at once | Together they never undo more than was matched | Tested | `tests/test_payment_reversals.py::test_two_reversals_at_once_cannot_undo_more_than_the_match` | H |
| C-019 | A payment while a reversal on its charge is in progress | Waits, then counts what was reversed | Tested | `tests/test_payment_reversals.py::test_a_payment_waits_for_a_reversal_in_progress_and_then_counts_it` | H |
| C-022 | A deposit recorded twice with one request id | Held once; a different amount under it is refused | Tested | `tests/api/test_rent.py::test_a_security_deposit_is_held_for_the_tenant_once` | H |
| C-024 | A housing authority's check resent | Records nothing new; another amount under its id is refused | Tested | `tests/api/test_assistance_api.py::test_the_same_payment_again_records_nothing_new` | H |
| C-028 | A payment file brought in again later | 409 this file came in on its day; it comes in once | Tested | `tests/api/test_payment_files_api.py::test_a_file_comes_in_once` | H |
| C-035 | Two staff pay one bill at once | Money moves once | Tested | `tests/test_vendor_bills.py::test_the_same_step_taken_twice_at_once_moves_money_once` | H |
| C-036 | A bill payment resent with its request id | Returns the payment already recorded | Tested | `tests/properties/test_payables_properties.py::test_payables_invariants_hold_under_random_bills_contributions_and_draws` | H |
| C-039 | A first part payment while another posting holds the owner's account | Waits before taking any account | Tested | `tests/test_bill_lifecycle.py::test_a_first_payment_waits_for_a_posting_before_taking_any_of_its_accounts` | H |
| C-040 | An owner declines while staff set the bill aside | Waits, then refused | Tested | `tests/test_bill_decisions.py::test_a_decline_waits_for_a_set_aside_in_flight_and_is_then_refused` | H |
| C-044 | Pay bills sent again | Pays nothing twice | Tested | `tests/api/test_bills_api.py::test_pay_bills_pays_each_bill_once_by_check_or_eft` | H |
| C-048 | Two draws of one account at once | Never pay out more than is there | Tested | `tests/test_owner_money.py::test_two_draws_at_once_cannot_pay_out_more_than_is_there` | H |
| C-049 | Pay owners cut off, then resent | Pays no one, then each once | Tested | `tests/test_faults.py::test_pay_owners_cut_off_pays_no_one_and_its_retry_pays_each_once` | H |
| C-052 | One management fee taken twice at once | Posted once | Tested | `tests/test_owner_money.py::test_the_same_fee_taken_twice_at_once_posts_once` | H |
| C-053 | An owner fee resent | Taken once; another amount under its id is refused | Tested | `tests/api/test_owner_fees_api.py::test_staff_take_a_fee_from_an_owner_once_per_request` | H |
| C-054 | Two withdrawals of the company's fees at once | Never more than the fee account holds | Tested | `tests/test_company_fees.py::test_two_withdrawals_at_once_cannot_take_more_than_is_there` | H |
| C-056 | A posting while an approval is in progress | Waits, then refused | Tested | `tests/test_reconciliation.py::test_a_posting_waits_for_an_approval_in_progress_and_is_then_refused` | H |
| C-057 | An approval while a posting is in flight | Waits and counts it | Tested | `tests/test_reconciliation.py::test_an_approval_waits_for_a_posting_in_flight_and_counts_it` | H |
| C-059 | Approve after the books moved or the period was prepared again | 409 in words | Tested | `tests/api/test_reconcile.py::test_approving_takes_the_role_and_the_figures_as_prepared` | H |
| C-062 | A charge dated in a year while it is being closed | Waits, then refused | Tested | `tests/test_year_close.py::test_a_charge_dated_in_the_year_waits_for_a_close_under_way_and_then_is_refused` | H |
| C-064 | A journal post and a decline at once | They wait for each other | Tested | `tests/test_journal_entries.py::test_a_post_and_a_decline_wait_for_each_other` | H |
| C-066 | Two rent runs at once | Each lease charged once | Tested | `tests/test_rent_automation.py::test_two_runs_at_once_charge_each_lease_once` | H |
| C-067 | A lease end while a run is under way | Waits and credits what the run charged | Tested | `tests/test_lease_end_credits.py::test_an_end_waits_for_a_run_under_way_and_credits_what_it_charged` | H |
| C-069 | The nightly run started on two servers at once | The one that finds the lock held runs nothing | Tested | `tests/test_nightly.py::test_one_run_at_a_time_another_leaves_the_work_to_it` | H |
| C-072 | A night cut off midway | Nothing stands; the next run does it all once | Tested | `tests/test_faults.py::test_a_nightly_run_cut_off_midway_leaves_the_night_undone_and_the_next_run_does_it_all_once` | H |
| C-085 | A paid webhook delivered twice | Recorded once | Tested | `tests/api/test_online_payments_api.py::test_paid_is_recorded_as_staff_record_a_payment_and_only_once` | H |
| C-096 | An owner's online payment reported paid twice | One contribution | Tested | `tests/api/test_owner_online_payments_api.py::test_paid_is_the_owners_contribution_once_and_the_company_pays_the_fee` | H |
| C-097 | Reports in any order, repeated, with holds placed and released | Trust stays whole and each payment counts once | Tested | `tests/properties/test_online_payment_properties.py::test_online_payments_record_what_the_partner_reports_once_and_keep_trust_whole` | H |
| C-119 | Sixteen staff at random on the same leases and owner accounts | Books right at the end and no 500 | Tested | `tests/tools/test_contention.py::test_a_short_soak_leaves_the_books_right` | H |
| C-123 | Two offers for one application complete at once | One lease | Tested | `tests/test_lease_offers.py::test_two_offers_for_one_application_completing_at_once_make_one_lease` | H |
| C-129 | A lease on a unit while another is being made there | Waits, then refused | Tested | `tests/test_leases_and_charges.py::test_a_lease_waits_for_one_in_progress_on_the_unit_and_is_then_refused` | H |
| C-134 | A deposit return resent | Returned once; another key is refused | Tested | `tests/test_move_out.py::test_the_same_request_again_returns_nothing_twice_and_another_is_refused` | H |
| C-135 | A deposit paid in while the return runs | The return waits for it | Tested | `tests/test_move_out.py::test_a_deposit_paid_in_meanwhile_waits_for_the_return` | H |
| C-136 | Move to another unit resent | The same new lease comes back | Tested | `tests/api/test_lease_transfers_api.py::test_the_new_lease_keeps_the_old_one_s_due_day_unless_staff_pick_another` | H |
| C-014 | A tenant leaves the lease while a payment waits for the lease lock | The tenant list was read before the lock, so the prepaid part can go to the tenant who just left | Open | src/pms/api/rent.py:468 (lease_of before lock_lease at 471) | M |
| C-025 | Enter charge resent after its answer was lost | No request id: the tenant is charged twice | Open | src/pms/api/rent.py:1372 (NewCharge has no request id) | M |
| C-026 | Add recurring charge resent after its answer was lost | No request id: two recurring charges, so the tenant is billed twice every month | Open | src/pms/api/rent.py:1446 (NewRecurringCharge) | M |
| C-029 | The same payment file submitted twice at the same moment | Both pass the read check; the unique key stops the second, answered as a 500, not in words | Open | src/pms/api/payment_files.py:387 | M |
| C-032 | A tenants file imported by two staff or two tabs at once | Names are looked up with no lock or unique key, so every tenant is added twice | Open | src/pms/api/switching.py:213 (Names.tenant) | M |
| C-045 | Two staff run Pay bills on overlapping bills listed in different orders | Owner accounts are locked in the order sent; a deadlock is answered as a 500 | Open | src/pms/api/payables.py:1423 | M |
| C-065 | A journal entry proposed again after its answer was lost | No request id: two identical entries wait, and an approver may post both, moving money twice | Open | src/pms/api/journal.py:178 | M |
| C-070 | A scheduler that found the lock held by another run | Still records every company's day as run here, so it won't look again that day; if the holder ran an earlier day or died, today waits for tomorrow | Open | src/pms/nightly.py:308 | M |
| C-071 | One company's run fails on a passing cause (a deadlock, a lock a staff request held) | Logged and admins told, but its day is marked and the scheduler won't retry it until the next day | Open | src/pms/nightly.py:202 and :308 | M |
| C-074 | A large company's nightly run holds its lease locks for its whole transaction | Staff requests on those leases wait while holding pool connections and can reach 503 (NOT VERIFIED by a run) | Open | src/pms/nightly.py:109 (run_pmc in one transaction) | M |
| C-075 | No statement_timeout or lock_timeout for the app's connections | A slow query or a stuck lock holds a connection with no limit; ten of them and every request gets 503 | Open | src/pms/api/app.py:302 (configure sets only the role and jit) | M |
| C-081 | A tenant turns autopay off between the still-unsent check and the charge | The bank is debited after the stop, though the stop email says nothing more will be taken | Open | src/pms/autopay.py:479-483 | M |
| C-084 | Every write request starts, after its answer, autopay sends for every company and email delivery on pooled connections | During a Stripe or SMTP outage each holds a connection for every call's timeout, starving requests and webhooks into 503 | Open | src/pms/api/app.py:389-391; src/pms/api/autopay.py:118 | M |
| C-089 | A paid report after failed for one payment | Dropped as out of order; if the bank did pay, the money is in the bank and not in the books | Open | src/pms/api/online_payments.py:570 | M |
| C-093 | Two staff record the company's deposit for fees at once under different request ids | Both see the same need and both deposit it, leaving the company's own money in trust | Open | src/pms/api/online_payments.py:909 | M |
| C-095 | Webhooks and checkout call Stripe while holding a pool connection | A slow Stripe (20 s timeout per call) pins connections and can turn staff requests into 503 | Open | src/pms/api/online_payments.py:335 and :668 | M |
| C-099 | The server dies after the SMTP server took an email, before its delivery row commits | It is sent again on the next delivery | Open | src/pms/email.py:667 | M |
| C-104 | A restart within ten minutes of a password reset | The link is lost; the person was told it is on its way, nothing arrives and no one is alerted (a lost link is not a failure) | Open | src/pms/email.py:678 (LINK_LOST left out of the give-up alert) | M |
| C-107 | One person's requests at the same moment (two tabs, a page loading several lists) | Each request's activity upsert locks the session row until commit, so they run one at a time; a long one blocks the rest while each holds a pool connection (NOT VERIFIED by a run) | Open | src/pms/accounts.py:472 | M |
| C-110 | Behind a proxy not listed in PMS_TRUSTED_PROXIES | Every visitor shares the proxy's address; 20 failures lock everyone out of sign-in for 15 minutes | Open | src/pms/api/throttle.py:84 | M |
| C-118 | A deadlock or serialization failure on a path the soak doesn't run (autopay, deposits, move-out, journal) | No handler for them: answered as a 500 | Open | src/pms/api/app.py:406-433 (handlers stop at privileges) | M |
| C-127 | Screen sent twice (two tabs, or a resend after a slow partner) | The partner screens twice: two reports, maybe two fees and two credit pulls | Open | src/pms/api/leasing.py:656 | M |
| C-130 | A unit archived while a lease is being added to it | The archival check takes no lock; both commit, leaving an archived unit with a lease | Open | db/schema.sql:1951 (trust_check_archival) | M |
| C-131 | A tenant archived while a payment adds prepaid rent for them | Both commit: an archived tenant holding money | Open | db/schema.sql:1951 (trust_check_archival) | M |
| C-133 | End lease with the lease-break fee sent twice at once (two tabs, or a resend while the first runs) | Both read the old end date before the lease lock, so the fee is charged twice | Open | src/pms/api/moveout.py:410-433 | M |
| C-011 | A payment resent after other payments changed what the lease owes | New postings differ so the key refuses them; the replay compares metadata and returns the original | Guarded | src/pms/api/rent.py:369 (earlier, replay) | M |
| C-020 | It bounced pressed twice with the same day | The bounce key replays its postings, each reversal returns its original, one NSF fee | Guarded | src/pms/api/rent.py:689 (bounce key) and trust_reverse_payment's retry | M |
| C-021 | The night's prepaid rent and staff's Use prepaid rent on one lease at once | Both take the lease lock; the night's is keyed by lease and day | Guarded | src/pms/api/rent.py:1878 | M |
| C-023 | Two housing authority checks at once that share leases | Leases are locked in sorted order, so no deadlock | Guarded | src/pms/api/assistance.py:832 | M |
| C-030 | A waiting line recorded by one staff member and skipped by another at once | Line lock; recorded or skipped once, never both | Guarded | src/pms/api/payment_files.py:577 | M |
| C-042 | The owner approves in the portal as staff record an approval | One decision; the second gets 409 in words | Guarded | src/pms/api/owner.py:424 | M |
| C-043 | A shared bill and a single bill under one vendor reference at once | Advisory lock on vendor and reference; one is refused | Guarded | db/schema.sql:2563 (trust_check_bill_reference) | M |
| C-058 | Two approvers approve one preparation at once | Statement lock; the second is refused as approved already (409) | Guarded | db/schema.sql:783 (trust_approve_bank_reconciliation) | M |
| C-060 | Two staff press Match the sure ones at once | Bank account match lock; the second's matches are refused as matched and skipped | Guarded | db/schema.sql:5858 (trust_match_bank_lines) | M |
| C-061 | Year close pressed twice at once | Company row lock; the second returns the first close | Guarded | db/schema.sql:4758 (trust_close_year) | M |
| C-068 | A rent change or due day recorded while the run charges | Company-wide lease-days lock | Guarded | db/schema.sql:3797 (trust_check_rent_change) | M |
| C-073 | An admin's Run rent now while the nightly run does the same company | No shared lock, but each part charges once per date | Guarded | src/pms/api/rent.py:1291 | M |
| C-092 | Staff accept a payment past the hold while the webhook settles it | Lease lock and a posted-key check | Guarded | src/pms/api/online_payments.py:869 | M |
| C-112 | Wrong passwords on one login sent in parallel | The login's secret row is locked, so each failure counts toward the lockout | Guarded | src/pms/accounts.py:374 | M |
| C-113 | One authenticator code used in two sign-ins at once | Row lock; the second sees the step used and is refused | Guarded | src/pms/accounts.py:374 | M |
| C-114 | An invite accepted twice at once | The invite row is locked; the second can't use it | Guarded | src/pms/accounts.py:274 | M |
| C-137 | Deposit interest paid twice at once | Lease lock, once per request key | Guarded | db/schema.sql:6606 (trust_pay_deposit_interest) | M |
| C-142 | A utility bill billed back twice at once | Advisory lock; no two bills cover a day | Guarded | db/schema.sql:1280 (trust_bill_back_utility) | M |
| C-006 | A money write path called at REPEATABLE READ | Refused, so its sums always see what committed while it waited | Tested | `tests/test_move_out.py::test_a_return_is_made_at_read_committed` | M |
| C-009 | A payment's request id resent with a different amount | 409 this request id was used for a different payment; nothing new | Tested | `tests/api/test_rent.py::test_sending_a_payment_again_records_it_once` | M |
| C-031 | A tenants import cut off, then retried | Nothing, then each tenant once | Tested | `tests/test_faults.py::test_an_import_cut_off_brings_in_nothing_and_its_retry_brings_each_tenant_once` | M |
| C-034 | A bank statement file imported twice | 409 imported for this trust bank account already | Tested | `tests/api/test_reconcile.py::test_a_statement_out_of_turn_twice_or_ahead_of_today_is_refused` | M |
| C-077 | The partner is down when an autopay payment is sent | Tried again at later runs for three days | Tested | `tests/test_autopay.py::test_each_payment_goes_to_the_partner_once_and_waits_while_it_is_down` | M |
| C-082 | A stop that commits while another autopay payment is being sent | The stopped one is never sent | Tested | `tests/test_autopay.py::test_a_stopped_autopays_payment_is_never_sent` | M |
| C-083 | An autopay payment given up while a send might still be trying it | Not before four days; a later paid report still counts | Tested | `tests/test_autopay.py::test_a_payment_the_partner_never_took_is_given_up_and_the_tenant_told` | M |
| C-091 | A payment reported paid during a payment hold | Waits for staff to accept it | Tested | `tests/api/test_online_payments_api.py::test_a_payment_paid_during_a_hold_waits_for_staff_to_accept_it` | M |
| C-094 | Stripe doesn't answer while a report is read | 502, so Stripe sends it again | Tested | `tests/api/test_payment_keys_api.py::test_stripe_not_answering_through_the_companys_webhook_asks_for_the_report_again` | M |
| C-098 | Two email deliveries at once | Each email sent once (an advisory lock per email) | Tested | `tests/test_email.py::test_two_deliveries_at_once_send_each_email_once` | M |
| C-101 | An email fails five times | Given up; the company's admins are told once a day | Tested | `tests/test_reserve_and_failure_alerts.py::test_admins_hear_once_a_day_when_an_email_is_given_up_on` | M |
| C-103 | An invite or reset whose link another process holds | Left to that process, then given up once after ten minutes | Tested | `tests/test_email_everywhere.py::test_a_link_another_process_holds_is_left_to_it_then_given_up_on_once` | M |
| C-111 | An address that failed too often tries another email | Refused with Retry-After | Tested | `tests/api/test_throttle.py::test_an_address_that_failed_too_often_is_refused_whatever_it_tries` | M |
| C-117 | Every connection busy for 30 seconds | 503 in words with Retry-After | Tested | `tests/api/test_refusals.py::test_a_request_that_finds_every_connection_busy_is_told_so_in_words` | M |
| C-122 | A signature while the offer is being voided | Waits, then refused | Tested | `tests/test_esign.py::test_a_signature_waits_for_a_void_and_then_is_refused` | M |
| C-125 | A decision while a withdrawal is on its way | Refused in words | Tested | `tests/api/test_leasing.py::test_a_decision_made_while_a_withdrawal_was_on_its_way_refuses_it_in_words` | M |
| C-139 | Two admins take each other's admin role at once | One stays an admin | Tested | `tests/test_staff_roles.py::test_two_admins_taking_each_others_role_leave_one` | M |
| C-140 | Two brokers named for one trust bank account at once | The second waits and is refused | Tested | `tests/test_trust_account_signers.py::test_two_brokers_named_at_once_the_second_waits_and_is_refused` | M |
| C-015 | Ownership shares change while a payment waits for the lease lock | Owners and shares were read before the lock; the payment splits by the old shares | Open | src/pms/api/rent.py:469 (owners_of before lock_lease) | L |
| C-033 | A history file submitted twice at once | Read check then insert; the unique key stops the second as a 500 | Open | src/pms/api/switching.py:621 | L |
| C-037 | A bill payment resent with the same request id but a new amount | The first payment comes back as success with no comparison; staff believe the new amount was paid | Open | src/pms/api/payables.py:1271 | L |
| C-038 | One bill payment with a receipt sent twice at the same moment | The payment posts once but its receipt is kept twice | Open | src/pms/api/payables.py:1296 | L |
| C-046 | Two Pay bills file runs both include one bill | The payout item is kept once per transfer; the second run reports the bill as its own, its file lacks it and a check number is skipped | Open | src/pms/api/payouts.py:480 | L |
| C-051 | Two Pay owners runs with accounts in different orders | Owner accounts are locked in the order sent; a deadlock is answered as a 500 | Open | src/pms/api/payables.py:2152 | L |
| C-080 | A customer with more than 100 payment intents | The lookup reads one page; a resend after Stripe forgets its retry key could charge twice | Open | src/pms/payments.py:248 (limit 100, no paging) | L |
| C-100 | The SMTP server takes the message but the connection drops before its reply | Recorded as failed and sent again | Open | src/pms/email.py:667-672 | L |
| C-102 | Two deliveries racing on an email failing its last tries | The attempt count is read before the lock, so a sixth attempt and a second give-up are possible | Open | src/pms/email.py:678 | L |
| C-105 | Send reminder sent twice at once on a past-due lease (two tabs, or a resend while the first runs) | Both read not sent today, so two reminders go to each tenant | Open | src/pms/api/emails.py:377 | L |
| C-106 | A staff email resent after its answer was lost | Two emails go out | Open | src/pms/api/emails.py:183 | L |
| C-108 | Several API processes behind one address | Each counts failed sign-ins and resets on its own, so N processes allow N times the limit, and a restart forgets every count | Open | src/pms/api/throttle.py:14 | L |
| C-109 | A burst of wrong passwords sent in parallel from one address | All are checked before any is counted, so more than 20 get through | Open | src/pms/api/app.py:488-492 | L |
| C-115 | Two invites at once for a person with no login | Both create a login; the unique key stops the second as a 500 | Open | src/pms/accounts.py:211-219 | L |
| C-116 | Per-day limits (requests, messages, files, applications) | Counted, then inserted without a lock, so parallel posts go past the limit | Open | src/pms/api/tenant.py:691 | L |
| C-120 | A deferred check (shared bill whole, payment file lines, a document's file) fails at commit | Raised after the handler returned, answered as a 500 without words | Open | src/pms/api/deps.py:125-134 | L |
| C-124 | Send on a draft offer sent twice at once (two tabs, or a resend while the first runs) | Both see a draft; the second hits the request's key and is answered as a 500 | Open | src/pms/api/offers.py:414 | L |
| C-126 | Two staff decide one application at once | Both pass the check; the key stops the second as a 500 | Open | src/pms/api/leasing.py:731 | L |
| C-128 | An applicant submits the apply form twice | Two applications for one household | Open | src/pms/api/leasing.py:198 | L |
| C-132 | A vendor archived while a bill is entered for them | Both commit: an archived vendor with an open bill | Open | db/schema.sql:1951 (trust_check_archival) | L |
| C-138 | A vendor schedules through a link as staff take it back | No lock between them; the step can land on a link just taken back | Open | db/schema.sql:4592 (trust_check_vendor_link_step) | L |
| C-141 | Two staff save one setting at once (latest row holding) | The row whose transaction began later wins even if it committed first; the same values saved twice write two rows | Open | src/pms/notices.py:75 | L |
| C-010 | A payment's request id resent with a different check number or method | Returns the first payment as a retry; the new reference is dropped without a word | Guarded | src/pms/api/rent.py:362 (DESCRIBED left out of the comparison) | L |
| C-017 | Two staff place a payment hold on one lease at once | Lease lock; the second is told the lease is on a hold already | Guarded | src/pms/api/rent.py:2044 | L |
| C-027 | Waive pressed twice on one charge | The waiver trigger locks the charge; never past what is left of it | Guarded | db/schema.sql:2647 (trust_check_charge_waiver) | L |
| C-047 | A bill entered while a draw works out what is available | The draw doesn't see the uncommitted bill and pays out; the bill then reads Short (by design, no money moved twice) | Guarded | db/schema.sql:5782 (owner account lock on draws) | L |
| C-121 | A restart drops the after-request email and autopay work | Picked up by the next hourly look or the next write | Guarded | src/pms/nightly.py:311-313 | L |

### Most pressing open, with the test that would close each

- C-087: an API test that sends returned, then paid, through the fake partner and asserts the payment is in the books and then undone (or that the early return is kept and applied once paid lands).
- C-088: the same test for owner payments: returned then paid leaves the owner's account as it was before the payment.
- C-090: a test that a paid payment with no report after N days is found by a poll of the partner and recorded once, and that a staff-recorded copy and a late report don't double it.
- C-055: an API test that a second take of a run's ACH file asks for confirmation (or carries a new file id modifier) and is logged as a repeat.
- C-065: an API test that proposing with the same request id twice leaves one entry waiting.
- C-026: an API test that the same request id twice makes one recurring charge and the next rent run charges it once.
- C-014: a timing test: a departure holds the lease lock, a payment waits, and its prepaid part goes to the tenant who stays.
- C-081: a test with a partner whose charge sees a stop committed after the check, asserting the payment is not sent (lock the autopay across the send).
- C-130: a timing test: an archival and a new lease on one unit at once, asserting one of them is refused.
- C-133: a timing test: two ends with break_fee at once charge one lease-break fee.

## Partners and outside services down, slow or wrong

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| P-006 | The company's secret key was rolled or revoked in Stripe, is a restricted key without a permission, or the account was restricted | Every checkout and autopay fails as "couldn't reach"; Settings still says "Your Stripe account, key ending in ..."; no admin alert, no check of the key | Open | src/pms/payments.py:203; src/pms/api/payment_keys.py:73-104 (view never calls Stripe) | H |
| P-007 | Keys saved that only look right: a typo'd key, or a secret key and a webhook secret from different Stripe accounts or modes | check_keys checks only their shape; payments start on one account and its reports fail the signature, so paid payments never reach the books | Open | src/pms/payments.py:477-487; src/pms/api/payment_keys.py:117-142 | H |
| P-010 | The server's clock drifts more than 5 minutes | Every Stripe report is refused 400 as "signed too long ago"; Stripe retries then stops; payments stay "Waiting for your bank"; no alert | Open | src/pms/payments.py:429-447 (tolerance 300) | H |
| P-011 | The webhook signing secret changed in Stripe (endpoint recreated) but not in the PMS | Every report answers 400 "the report's signature isn't this company's"; after Stripe's retries (about 3 days, NOT VERIFIED) they are gone; nobody is told | Open | src/pms/api/online_payments.py:686-693 | H |
| P-012 | The Stripe endpoint isn't subscribed to the events the PMS reads (async_payment_succeeded, payment_intent.*, checkout.session.expired, charge.dispute.created) | Settings says only "In Stripe, add this as a webhook endpoint." with no list of events; paid bank payments never reach the books | Open | frontend/src/pages/PaymentKeysCard.tsx:90; src/pms/payments.py:337-351 | H |
| P-013 | No report ever arrives for a started payment (webhook broken, PMS down past Stripe's retry window) | Nothing asks Stripe; the tenant's line says "Waiting for your bank" forever; staff have no list of old started payments, since Waiting lists only paid ones | Open | src/pms/api/online_payments.py:781-790; src/pms/nightly.py (no sweep) | H |
| P-014 | Stripe's account API version is older than the shapes the PMS reads (it sends no Stripe-Version header) | latest_charge or its balance transaction missing: every paid report answers 502 and is never recorded | Open | src/pms/payments.py:191-200 (no Stripe-Version), 324-335 | H |
| P-024 | A return (dispute) arrives before the paid report lands (paid kept failing on its fee, or isn't subscribed) | The trigger refuses it, the webhook answers 200 and drops it; Stripe never resends; when paid lands later the payment stays in the books though the money went back | Open | src/pms/api/online_payments.py:568-570; the drop is pinned by tests/api/test_online_payments_api.py::test_a_return_is_a_bounce_and_the_tenants_fee_goes_back_out | H |
| P-026 | A late return Stripe reports by another event than a dispute (charge.failed after success; NOT VERIFIED which Stripe sends) | Not in STRIPE_KINDS: ignored, the payment stays in the books | Open | src/pms/payments.py:337-351 | H |
| P-027 | A dispute the company won, funds reinstated (charge.dispute.closed, charge.dispute.funds_reinstated) | Not read: the payment stays bounced, the tenant owes it again plus an NSF fee though the money came back | Open | src/pms/payments.py:337-351 | H |
| P-028 | Staff refund a tenant, all or part, from Stripe's dashboard (charge.refunded) | Not read: the books keep the whole payment; trust cash at the bank is short of the books | Open | src/pms/payments.py:337-351 (no refund kind) | H |
| P-030 | Stripe takes a dispute fee from the balance (amount NOT VERIFIED) | Nothing records it; bank cash falls short of the books by it and month-end can't tie | Open | src/pms/api/online_payments.py:524-546 (settle has no dispute fee) | H |
| P-034 | A company with two operating trust bank accounts and one Stripe payout account | Books put each payment in its property's trust bank account; Stripe pays everything into the one bank set in Stripe, so cash lands in the wrong account | Open | src/pms/api/online_payments.py:366-369; src/pms/payments.py:517-525 | H |
| P-040 | Paid online for a property with two owner accounts and no ownership shares | Checkout lets it start; paid, it waits as refused; then GET /api/payments/waiting fails whole (owner_account raises 422) so Banking shows nothing, and Record can't name an account; the tenant sees Paid while the lease still owes and may be charged a late fee | Open | src/pms/api/online_payments.py:813; src/pms/api/rent.py:201-202; frontend/src/pages/OnlinePaymentsCards.tsx:105 | H |
| P-048 | Test-mode keys (sk_test_) on a live https server | A Test badge in Settings, but test payments go into the trust books as real money; the event's livemode is never checked and https doesn't refuse a test key | Open | src/pms/payments.py:471-487; src/pms/deployment.py:30 (fakes only); no livemode in src | H |
| P-049 | A company goes from test keys to live | every_own keeps the old test keys, so test-mode reports still land in the real books | Open | src/pms/payments.py:543-548; src/pms/api/online_payments.py:680-693 | H |
| P-051 | PMS_DATA_KEY lost while payments are in flight | The company webhook answers 404 until keys are entered again; reports past Stripe's retry window are lost and nothing asks for them | Open | src/pms/api/online_payments.py:682-684 | H |
| P-061 | An owner's payment refunded in Stripe, or a dispute on it won | Same gaps as P-027 and P-028: not read, the books keep the contribution or the return | Open | src/pms/payments.py:337-351 | H |
| P-065 | The company changes Stripe accounts (server's to own, or new keys) with autopays saved at the old one | The partner name is still "stripe", so the new account is asked to charge a customer it doesn't know; fails three days, given up, tenant emailed every month; a give-up never counts toward stopping it and staff aren't told | Open | src/pms/autopay.py:162-165, 476-478 | H |
| P-067 | Stripe leaves an off-session payment waiting on the tenant (requires_action; NOT VERIFIED for ACH) | Recorded as sent, and no report follows; it stays "on its way", so every later balance autopay pays less and the tenant falls behind into late fees | Open | src/pms/autopay.py:128-137, 484-501 | H |
| P-068 | A hand payment left on Stripe's page whose expired report never comes | Counted on its way forever, lowering every balance autopay | Open | src/pms/autopay.py:128-137 | H |
| P-075 | SMTP server hangs | Each email waits up to 20 s inside an open transaction on a pool connection; a delivery runs after every successful change, so several fill the pool and everyone gets 503 busy | Open | src/pms/email.py:128, 646-675; src/pms/api/app.py:383-391 | H |
| P-087 | The attachment or HTML fails with something other than a database or lookup error (fpdf, ValueError, HTTPException; NOT VERIFIED that any does today) | Not caught: the pass stops at that email, which is first again next time, so every later email waits behind it unrecorded | Open | src/pms/email.py:662-672 | H |
| P-111 | PMS_DATA_KEY changed (one is set, but not the one that sealed the numbers) | The pay-time check only sees that a key exists, so the draws are recorded as paid by ACH; then the file answers 409 "doesn't open under this server's key" | Open | src/pms/api/payouts.py:394-431, 656-661 | H |
| P-115 | The bank returns an owner's ACH draw (account closed, no account) | No way to record it: the draw stays paid and the owner's balance wrong until two staff post a journal entry | Open | src/pms/api/payouts.py (no return path); src/pms/api/journal.py | H |
| P-116 | The bank returns a vendor's ACH payment | The bill stays Paid and can't be voided once money was taken | Open | src/pms/api/payables.py:1309-1328 (void refused once money was taken) | H |
| P-121 | A printed check is lost, misprinted or voided | Reprinting gives the same numbers again; there is no void and reissue, and the money stays recorded as gone | Open | src/pms/api/payouts.py:699-728 | H |
| P-129 | Every amount positive with a separate Type (Debit or Credit) column | Read as all money in; a CSV needs no closing balance, so it goes in silently | Open | src/pms/statements.py:92-140 | H |
| P-130 | The wrong statement imported (another account's, wrong days, wrong signs) | Permanent: statements are append-only and there is no undo; its lines the books lack block month-end; Import statement asks nothing first | Open | tests/test_bank_statements.py::test_statements_and_lines_are_append_only pins it; frontend/src/pages/ReconcilePage.tsx:370-371 | H |
| P-004 | Stripe slow (up to 20 s a call) while many tenants pay at once | Each checkout holds one of the pool's 10 connections and an open transaction for the whole Stripe call; staff pages then get 503 busy | Open | src/pms/payments.py:183; src/pms/api/app.py:311 (max_size=10); src/pms/api/online_payments.py:334-337 | M |
| P-005 | Stripe refuses an amount over the account's ACH limit, or another 4xx about the payment itself | Any HTTP refusal reads to the tenant as "We couldn't reach our payments partner"; the real reason is lost and the PMS lets a tenant start up to 100,000.00 | Open | src/pms/payments.py:203-204; src/pms/api/online_payments.py:46, 336-337 | M |
| P-029 | A dispute whose charge has no payment_intent, or the lookup of it fails | Asks Stripe for /payment_intents/None, fails as PartnerFailed and answers 502 on every retry | Open | src/pms/payments.py:309-311 | M |
| P-031 | Stripe charges a fee on a failed ACH debit (NOT VERIFIED that it does) | Nothing records it | Open | src/pms/api/online_payments.py:530-531 (failed moves nothing) | M |
| P-032 | Stripe pays out net of fees, several payments at once | Books hold each payment gross and each fee out apart; auto-match pairs a line with one transfer or one deposit only, so a payout never matches by itself and staff match it by hand | Open | src/pms/api/reconcile.py:697-750 | M |
| P-033 | Stripe pays out days after the paid day, or holds a reserve | The cash is in the books on the paid day; month-end shows it uncleared with no word that it sits in Stripe's balance | Open | src/pms/api/online_payments.py:530-533 | M |
| P-036 | Stripe's real price differs from the PMS's fixed 0.8%, at most $5 (NOT VERIFIED) | The tenant's fee comes from the constant: if Stripe charges less the tenant pays more than the cost (a surcharge some states limit); if more, the company covers it silently | Open | src/pms/payments.py:54-55, 128-134 | M |
| P-037 | Stripe's paid amount differs from the amount the PMS started | Never compared; the PMS records its own amount | Open | src/pms/payments.py:292-322 (amount ignored); src/pms/api/online_payments.py:438-448 | M |
| P-043 | A return months later, after the lease ended, its deposit was returned and the owner was paid out | Reopens the ended lease's charges plus an NSF fee, or waits with no end in sight; nothing says the tenant is gone | Open | src/pms/api/online_payments.py:505-518; src/pms/api/rent.py:657-703 | M |
| P-044 | Paid is reported after the lease ended and its deposit was returned | Recorded to the ended lease; with nothing owed it becomes prepaid rent of a tenant who left | Open | src/pms/api/online_payments.py:438-448 | M |
| P-057 | A tenant pays in two tabs | Two Stripe sessions; both paid, both are recorded (the second as prepaid rent); nothing warns that a payment is already on its way | Open | src/pms/api/online_payments.py:320-357 | M |
| P-058 | The fee quote call fails while the tenant pays the fee | The fee line goes blank but Pay online stays enabled, so the tenant leaves without the PMS showing the fee | Open | frontend/src/pages/PayOnlineCard.tsx:50, 88 | M |
| P-069 | Bank account verification fails (setup_intent.setup_failed, micro-deposits never confirmed) | Not read; autopay reads "Waiting for a bank account to be saved" forever and the tenant isn't told it failed | Open | src/pms/payments.py:282-291 | M |
| P-070 | A tenant changes the autopay's bank account and leaves Stripe's page | The old autopay is already stopped as replaced; the new one waits for an account, so rent stops paying itself | Open | src/pms/api/autopay.py:186-188 | M |
| P-072 | The email before a debit can't be delivered | The debit goes ahead; a balance autopay's changing amount reaches the bank unannounced | Open | src/pms/autopay.py:12-14 (email written, delivery separate) | M |
| P-076 | A short outage burns all five attempts | No backoff: every change sends a pass over everything waiting, so five failures come in minutes and the email is given up for good | Open | src/pms/email.py:57, 592-599; src/pms/api/app.py:386-391 | M |
| P-077 | Greylisting or another temporary 4xx | Counted as a failure like any other; given up as quickly | Open | src/pms/email.py:671-672 | M |
| P-078 | The provider's rate limit during a big batch (the night's rent reminders) | Each refusal counts toward five; many reminders given up | Open | src/pms/email.py:592-599 | M |
| P-081 | Port 465 (implicit TLS) configured | No SMTP over TLS option: a plain connect then STARTTLS fails or times out, and the error gives no hint | Open | src/pms/email.py:128-129, 147 | M |
| P-083 | Mail broken for everyone, so the admins' alert fails the same way | The alert is given up and told to nobody; no Dashboard line; only per-person Not sent badges and the server log | Open | src/pms/email.py:686-688; pinned by tests/test_reserve_and_failure_alerts.py::test_an_alert_given_up_on_is_told_to_nobody | M |
| P-085 | A bounce after the server took it (mailbox full, DMARC reject) | Recorded Sent; no bounce is read | Open | src/pms/email.py:673 | M |
| P-090 | A sender is set up months later | Everything waiting goes at once, months-old bill approvals and staff messages included; nothing limits an email's age | Open | src/pms/email.py:580-601 | M |
| P-092 | A password reset email fails to arrive | The screen gives the same answer either way; the owner or tenant can only ask again (three an hour) or call the manager | Open | src/pms/api/app.py:531-540; src/pms/email.py:671 | M |
| P-104 | Staff on such a server press Screen | The button is always offered; it can only fail | Open | frontend/src/pages/ApplicantPage.tsx:85-87 | M |
| P-105 | A real screening partner down or slow | The Partner interface has no failure type and no timeout; any exception is a 500 | Open | src/pms/leasing/screening.py:96-99; src/pms/api/leasing.py:656-664 | M |
| P-106 | A real partner answers later (consent on its page, report in hours) | The interface only answers at once | Open | src/pms/leasing/screening.py:96-99 | M |
| P-112 | The bank wants a balanced file (an offsetting debit to the trust account; NOT VERIFIED per bank) | Only credits are written | Open | src/pms/payouts/nacha.py:73-165 | M |
| P-113 | The effective date is the paid day: already past, a weekend or a holiday | Written as it is | Open | src/pms/api/payouts.py:683 | M |
| P-114 | Two runs paid in the same minute (Pay owners, then Pay bills) | Same creation time and the same file id "A", which a bank's duplicate check may refuse | Open | src/pms/payouts/nacha.py:73, 85-86; src/pms/api/payouts.py:686 | M |
| P-120 | A payout file refused (no disburse role, key changed, payee account missing) | It's a plain link, so the browser leaves the app for a page of raw JSON | Open | frontend/src/pages/PayoutFiles.tsx:69 | M |
| P-122 | A check never cashed (stale) | Outstanding forever; no stale-check list or void | Open | src/pms/api/reconcile.py (no stale items) | M |
| P-123 | The bank's positive pay wants a file of the checks issued | The PMS writes none, so the bank may refuse the checks | Open | src/pms/api/payouts.py (no positive pay) | M |
| P-124 | The payee's name has letters the font lacks (Arabic, Chinese) | "?" printed on the check's payee line | Open | src/pms/payouts/checks.py:121 | M |
| P-134 | An OFX whose DTEND is the next day at midnight | Its last day reads a day late, so the next month's statement is refused | Open | src/pms/statements.py:141-148, 211-216 | M |
| P-135 | An OFX holding several accounts | All their transactions go into one statement, with the first LEDGERBAL | Open | src/pms/statements.py:189-216 | M |
| P-136 | An OFX with both FITID and CHECKNUM | FITID wins, the check number is lost, and checks don't match bill payments by number | Open | src/pms/statements.py:209 | M |
| P-139 | A statement file over 2,000,000 characters | 422 that reads only "check what you entered and try again" | Open | src/pms/api/reconcile.py:222; frontend/src/api.ts:66 | M |
| P-141 | A name longer than QuickBooks takes (Desktop's limit per part, Online's 100; NOT VERIFIED) | Written as it is; the PMS allows 200 | Open | src/pms/quickbooks.py:103-111; src/pms/api/quickbooks.py:37 | M |
| P-142 | Two ledger accounts with the same default name (two tenants with one name, two bank accounts with one name) | One QuickBooks account: QuickBooks merges them | Open | src/pms/quickbooks.py:105-127 | M |
| P-143 | QuickBooks Online refuses accounts that don't exist yet | No chart of accounts comes with the export | Open | src/pms/quickbooks.py:181-191 | M |
| P-147 | The database down, asked by any page | Each request waits 30 s, then says "the PMS is busy just now", which isn't why | Open | src/pms/api/deps.py:121-132 | M |
| P-148 | The database restarted under the app | The pool doesn't check connections, so dead ones give 500s until replaced | Open | src/pms/api/app.py:310-311 | M |
| P-149 | A query waits on a lock, or runs long (no statement_timeout) | The request waits until the proxy times out, then the page shows "the server answered 504" | Open | src/pms/api/app.py:302-308 (no statement_timeout) | M |
| P-152 | The network is down, so fetch itself fails | Pages show "TypeError: Failed to fetch" | Open | frontend/src/pages/useRecord.ts:19; frontend/src/api.ts:36-41 | M |
| P-153 | /api/me fails on load (503, 500) | A bare red line with no sidebar, no sign-out and no Try again | Open | frontend/src/App.tsx:99-108 | M |
| P-154 | A GET never answers | "Loading" forever: fetch has no timeout | Open | frontend/src/api.ts:36-41 | M |
| P-155 | Downloads (ledger CSV, statements, receipts, QuickBooks, 1099) refused or failing | Plain links: the browser leaves the app for raw JSON or the proxy's error page | Open | frontend/src/pages/QuickBooksPage.tsx:60; frontend/src/pages/LeaseForms.tsx:56; frontend/src/pages/OwnersPage.tsx:476 | M |
| P-156 | GET /api/payments/waiting fails | Banking hides the card, as if nothing waited | Open | frontend/src/pages/OnlinePaymentsCards.tsx:105 | M |
| P-157 | Online payments wait for staff | Only Banking lists them; the Dashboard's Needs attention doesn't | Open | src/pms/api/today.py (no online payments) | M |
| P-158 | The staff lease page's autopay list fails to load | Says "No tenant set up autopay." | Open | frontend/src/pages/AutopayCards.tsx:131 | M |
| P-159 | The prepared draws fail to load on Pay owners | Hidden, as if none were prepared | Open | frontend/src/pages/PayOwnersPage.tsx:73 | M |
| P-160 | The company's fees in trust fail to load on Banking | Hidden, so fees past their day to take out don't show | Open | frontend/src/pages/ReconcilePage.tsx:71 | M |
| P-119 | The same ACH file downloaded and uploaded twice | The same file each time, logged "Taken N times"; the bank's duplicate check is all that stops a second upload | Guarded | src/pms/api/payouts.py:621-624; tests/api/test_payouts_api.py::test_an_ach_file_taken_again_is_the_one_made_when_the_run_was_paid | M |
| P-025 | A paid ACH payment comes back (charge.dispute.created) | Undone as a bounce with the NSF fee, the tenant's fee back out of the company's account | Tested | tests/api/test_online_payments_api.py::test_a_return_is_a_bounce_and_the_tenants_fee_goes_back_out; tests/test_payments.py::test_stripe_reports_a_failure_a_return_and_nothing_else | M |
| P-050 | PMS_DATA_KEY lost or changed, so the company's keys don't open | Never falls back to the server's account; tenants see How to pay; admins see "Your keys can't be read on this server. Enter them again." | Tested | tests/api/test_payment_keys_api.py::test_keys_that_dont_open_here_take_no_payment_and_never_fall_back; frontend/src/pages/PaymentKeysCard.test.tsx > says keys that can't be read here need entering again | M |
| P-151 | A 5xx with no JSON body (proxy 502, 500) | "the server answered 502", with no retry offered | Tested | frontend/src/api.test.ts > names the status when the answer has no reason | M |
| P-008 | PMS_PUBLIC_URL wrong (http, old host) so Stripe refuses success_url or sends the tenant to a dead page | Stripe's refusal reads as "couldn't reach"; after paying, the tenant may land on an unreachable page though the payment still lands by webhook; nothing checks the URL | Open | src/pms/api/online_payments.py:333 | L |
| P-017 | Stripe sends thin (v2) event notifications instead of snapshot events | The body lacks data.object: 400 every time | Open | src/pms/payments.py:292-300 | L |
| P-045 | The lease's trust bank account has no company fee account | company_account raises 409 outside the savepoint, so the whole webhook answers 409 and Stripe keeps resending | Open | src/pms/api/online_payments.py:384-393, 479-489, 524-536 | L |
| P-094 | Staff read a failed email | The raw error ("SMTPServerDisconnected: ...") under Not sent, and no Send again | Open | frontend/src/components/EmailsCard.tsx:165-171 | L |
| P-096 | The real failure text is Python's ("the Census geocoder did not answer: <urlopen error ...>") | Shown verbatim to staff | Open | src/pms/geocoding.py:103 | L |
| P-099 | Several matches, or a match in another ZIP or state | The first is kept, never compared with the address | Open | src/pms/geocoding.py:113 | L |
| P-100 | A new build, PO box or rural route Census doesn't know | No way to place the point by hand | Open | src/pms/api/addresses.py:184-187 | L |
| P-107 | A partner's recommendation the PMS doesn't know, or a decline with no reasons | The database refuses the row and the API answers 500 | Open | src/pms/api/leasing.py:665-680 | L |
| P-118 | The first ACH to a new payee, never prenoted | No prenote is written; a bad account shows only as a return | Open | src/pms/payouts/nacha.py | L |
| P-125 | A payee with no mailing address on file | The check prints with no address and no warning | Open | src/pms/api/payouts.py:711-716 | L |
| P-128 | Dates as DD/MM/YYYY | Days 1 to 12 read silently as month and day | Open | src/pms/statements.py:79 | L |
| P-137 | An OFX amount with a decimal comma ("100,50") | Read as 10050.00 | Open | src/pms/statements.py:61 | L |
| P-003 | Stripe made the session but its answer was lost, and the tenant presses Pay again | Each press is a new payment id and a new Idempotency-Key, so a second session; the first just expires on Stripe's side and nothing is recorded for it | Guarded | src/pms/api/online_payments.py:329-337; src/pms/payments.py:196-199 | L |
| P-079 | STARTTLS not offered, or a wrong SMTP password | Recorded as a failure with its error | Guarded | src/pms/email.py:129-131, 671 | L |
| P-084 | The recipient is refused (550) at send | Retried five times anyway, then the admins are alerted; staff see the raw SMTPRecipientsRefused text | Guarded | src/pms/email.py:671-672; frontend/src/components/EmailsCard.tsx:171 | L |
| P-088 | A deposit statement's receipt PDF is corrupt | An "unreadable" page in its place | Guarded | src/pms/deposit_statement.py:185-195 | L |
| P-089 | The outbox grows while no sender is set | Emails wait; pages say the server sends none | Guarded | frontend/src/components/EmailsCard.tsx:154 | L |
| P-117 | The bank sends a notification of change (new account number) | Staff save the new account by hand | Guarded | src/pms/api/payouts.py:206-235 | L |
| P-127 | Dates like "Nov 1, 2026" or "11/01/26"; amounts like "100.00-" or "100.00 CR" | Refused with the line and the value | Guarded | src/pms/statements.py:60-84 | L |
| P-001 | Stripe times out (20 s) or refuses when a tenant presses Pay online | Checkout catches PartnerFailed, answers 502 "We couldn't reach our payments partner. Try again in a few minutes." and records no payment | Tested | tests/api/test_online_payments_api.py::test_a_partner_that_does_not_answer_starts_no_payment | L |
| P-002 | Stripe answers 5xx, HTML, or JSON missing the session's id or url | _call and checkout turn each into PartnerFailed, so the tenant gets the same 502 in words | Tested | tests/test_payments.py::test_stripe_refusing_or_not_answering_is_the_partner_failing; tests/test_payments.py::test_an_answer_from_stripe_missing_what_the_pms_needs_is_the_partner_failing | L |
| P-009 | Webhook with a forged or wrong-endpoint signature, a changed body, no header, or signed over 5 minutes ago | 400, nothing recorded; during a secret roll either v1 signature is accepted | Tested | tests/test_payments.py::test_a_report_counts_only_with_this_endpoints_signature_and_while_fresh | L |
| P-015 | An event type the PMS doesn't read, or a report about a payment it didn't start (another app or server on the same Stripe account) | 200 with recorded false, so Stripe stops sending | Tested | tests/api/test_online_payments_api.py::test_stripes_signed_report_records_the_payment | L |
| P-016 | Body signed but not JSON, or missing type, created or data | 400 "can't be read" | Tested | tests/test_payments.py::test_stripes_report_must_be_signed_and_readable | L |
| P-018 | The same report delivered twice, or two copies at once | Recorded once per payment and kind (unique index and advisory lock); each posting has its own key | Tested | tests/api/test_online_payments_api.py::test_paid_is_recorded_as_staff_record_a_payment_and_only_once | L |
| P-019 | checkout.session.completed with payment_status unpaid (bank still clearing) | Ignored; the async events say how it ends | Tested | tests/test_payments.py::test_stripe_reports_a_failure_a_return_and_nothing_else | L |
| P-020 | Paid reported but Stripe can't give its fee yet | 502 "send it again", nothing recorded; the next delivery records it | Tested | tests/api/test_online_payments_api.py::test_stripes_signed_report_records_the_payment | L |
| P-021 | The bank payment failed | Moves nothing; the tenant sees "Didn't go through" | Tested | tests/api/test_online_payments_api.py::test_a_failed_payment_moves_nothing | L |
| P-022 | The tenant left Stripe's page without paying | Expired: reads "Not finished" and moves nothing | Tested | tests/api/test_online_payments_api.py::test_a_payment_the_tenant_left_unpaid_reads_not_finished_and_moves_nothing | L |
| P-023 | Paid after failed, or failed after paid | Kept as it first came, answered 200; nothing settled | Tested | tests/properties/test_online_payment_properties.py::test_online_payments_record_what_the_partner_reports_once_and_keep_trust_whole | L |
| P-035 | Stripe's fee is more than the company's fee account holds | The partner fee waits; Banking says what to deposit; the company's deposit settles it | Tested | tests/api/test_online_payments_api.py::test_the_company_covers_the_partners_fee_its_account_cant | L |
| P-038 | Paid while the lease is on a payment hold | Waits for staff to accept it past the hold | Tested | tests/api/test_online_payments_api.py::test_a_payment_paid_during_a_hold_waits_for_staff_to_accept_it | L |
| P-039 | Paid on a day month-end already closed | Goes in on the first day left open | Tested | tests/api/test_online_payments_api.py::test_a_payment_whose_day_month_end_closed_is_recorded_in_the_next_open_one | L |
| P-041 | The books refuse a waiting payment when staff press Record | 409 in words | Tested | tests/api/test_online_payments_api.py::test_a_refusal_from_the_books_reaches_staff_in_words | L |
| P-042 | A return the owner's account can't cover yet | Waits as a return; Try again once it can | Tested | tests/properties/test_online_payment_properties.py::test_online_payments_record_what_the_partner_reports_once_and_keep_trust_whole | L |
| P-046 | A server with no payments partner, or a webhook for a partner it doesn't run | Settings say so, tenants see How to pay, the webhook answers 404 | Tested | tests/api/test_online_payments_api.py::test_a_server_without_a_partner_takes_no_payment_online | L |
| P-047 | The server's own Stripe keys on a server holding many companies | No company takes payments online until it enters its own keys | Tested | tests/api/test_server_keys_api.py::test_a_real_server_partner_serves_no_company_on_a_server_with_many | L |
| P-052 | An admin removes or changes the keys with payments started | Back to the server's partner if any; reports for payments started under the old keys still land | Tested | tests/api/test_payment_keys_api.py::test_taking_the_keys_away_goes_back_to_the_servers_partner; tests/api/test_payment_keys_api.py::test_a_payment_started_under_keys_since_changed_or_taken_away_still_reports_in | L |
| P-053 | Keys of the wrong shape or too long, or a server with no PMS_DATA_KEY | 422 without echoing the key; 409 "This server can't keep keys" and the card says so | Tested | tests/api/test_payment_keys_api.py::test_what_isnt_a_stripe_key_is_refused_without_saying_it_back; tests/api/test_payment_keys_api.py::test_a_server_without_a_data_key_keeps_none | L |
| P-054 | Stripe not answering while a company's webhook fetches the fee | 502 "send it again" | Tested | tests/api/test_payment_keys_api.py::test_stripe_not_answering_through_the_companys_webhook_asks_for_the_report_again | L |
| P-055 | A report to one company's webhook about another company's payment | Acknowledged, recorded false | Tested | tests/api/test_payment_keys_api.py::test_a_companys_webhook_records_none_of_another_companys_payments | L |
| P-056 | The fake partner on a public server | Refused at start over https; its page has no sign-in, so over plain http anyone could mark a payment paid | Tested | tests/test_deployment.py::test_a_server_over_https_refuses_each_fake_partner | L |
| P-059 | Stripe down when an owner adds money | 502 in words, nothing started | Tested | tests/api/test_owner_online_payments_api.py::test_a_partner_that_does_not_answer_starts_no_payment | L |
| P-060 | An owner's payment comes back after their account was paid out | Waits for staff | Tested | tests/api/test_owner_online_payments_api.py::test_a_return_the_owners_account_cant_cover_waits_for_staff | L |
| P-062 | Stripe down when autopay charges | Waits, tried each run for three days | Tested | tests/test_autopay.py::test_each_payment_goes_to_the_partner_once_and_waits_while_it_is_down | L |
| P-063 | Stripe never takes an autopay payment within three days | Given up, marked expired, the tenant emailed | Tested | tests/test_autopay.py::test_a_payment_the_partner_never_took_is_given_up_and_the_tenant_told | L |
| P-064 | The PMS stops between Stripe's charge and recording it | The next run lists the customer's payments and finds it: no second debit | Tested | tests/test_payments.py::test_stripe_charges_a_saved_account_once_finding_the_payment_it_made_before | L |
| P-066 | Two autopay payments in a row don't go through | Stops by itself and emails | Tested | tests/test_autopay.py::test_an_autopay_stops_by_itself_after_two_payments_in_a_row_did_not_go_through | L |
| P-071 | Stripe down when a tenant sets up autopay | 502, nothing saved, the old autopay kept | Tested | tests/api/test_autopay_api.py::test_a_partner_that_does_not_answer_saves_no_autopay | L |
| P-073 | Payments online off, or a hold on the due date | The run records why it started none | Tested | tests/test_autopay.py::test_an_autopay_records_why_it_started_no_payment | L |
| P-074 | SMTP server down (connection refused) | The attempt is recorded with its error and tried again later | Tested | tests/test_email.py::test_a_failure_then_a_delivery | L |
| P-080 | The mail server's certificate isn't trusted | Nothing sent, recorded why | Tested | tests/test_email_mailpit.py::test_a_server_whose_certificate_is_not_trusted_gets_nothing | L |
| P-082 | An email failed five times | Given up; each admin emailed once a day | Tested | tests/test_reserve_and_failure_alerts.py::test_admins_hear_once_a_day_when_an_email_is_given_up_on | L |
| P-086 | An attachment can't be made (no such payment) | A failed delivery saying why | Tested | tests/test_email_attachments.py::test_a_receipt_whose_file_cant_be_made_is_a_failed_delivery_saying_why | L |
| P-091 | An invite's or reset's link lost (restart before it was sent) | Given up once as link lost after ten minutes | Tested | tests/test_email_everywhere.py::test_a_link_another_process_holds_is_left_to_it_then_given_up_on_once | L |
| P-093 | Two deliveries at once | Each email goes once | Tested | tests/test_email.py::test_two_deliveries_at_once_send_each_email_once | L |
| P-095 | The Census geocoder down or timing out | 502 with its text, nothing kept | Tested | tests/api/test_property_addresses_api.py::test_a_geocoder_that_fails_is_said_in_words_and_keeps_nothing | L |
| P-097 | Census answers in another shape or with HTML | GeocoderFailed, 502 | Tested | tests/test_geocoding.py::test_the_census_geocoder_says_when_it_found_nothing_or_failed | L |
| P-098 | No match | 422 "found no match ... check the address" | Tested | tests/api/test_property_addresses_api.py::test_finding_a_property_needs_its_address_and_an_address_the_geocoder_knows | L |
| P-101 | No geocoder on the server | 503 in words, and the card offers no map | Tested | tests/api/test_property_addresses_api.py::test_a_server_without_a_geocoder_says_so; frontend/src/pages/AddressCard.test.tsx > offers no map without a geocoder, and says why one was refused | L |
| P-102 | The address changes after it was found | The old point no longer counts | Tested | tests/api/test_property_addresses_api.py::test_the_same_address_again_writes_nothing_and_a_new_one_is_not_found_yet | L |
| P-103 | No screening partner (every https server today: the fake is refused and no real one exists) | Screen answers 503 "no screening partner is set up on this server" | Tested | tests/api/test_leasing.py::test_without_a_partner_screening_is_refused | L |
| P-108 | A routing number typo or an account number that isn't digits | Refused at entry by the check digit and pattern | Tested | tests/api/test_payouts_api.py::test_a_bank_account_that_cant_be_paid_is_refused | L |
| P-109 | An ACH payout without the bank's details or a payee's account | Refused before any money is recorded | Tested | tests/api/test_payouts_api.py::test_an_ach_run_needs_the_banks_details_and_every_payees_account | L |
| P-110 | No PMS_DATA_KEY at pay time | ACH payouts refused | Tested | tests/api/test_payouts_api.py::test_a_server_without_its_key_records_no_ach_payout | L |
| P-126 | A CSV statement whose header isn't the first line, or with no date or amount column | Refused saying the line and why | Tested | tests/test_statement_files.py::test_a_csv_that_is_not_a_statement_says_where_and_why | L |
| P-131 | The same file imported twice | 409 "imported for this trust bank account already" | Tested | tests/test_bank_statements.py::test_a_file_is_imported_once | L |
| P-132 | An overlapping or out-of-turn re-export | Refused as not the next statement | Tested | tests/api/test_reconcile.py::test_a_statement_out_of_turn_twice_or_ahead_of_today_is_refused | L |
| P-133 | Lines that don't come to the stated closing balance | Refused with both figures | Tested | tests/api/test_reconcile.py::test_a_statement_that_does_not_add_up_or_fit_is_refused | L |
| P-138 | A statement saved by Excel on Windows (Windows-1252) | Read without losing letters | Tested | frontend/src/fileText.test.ts > reads a file Excel on Windows saved as CSV (Windows-1252) without losing its letters | L |
| P-140 | A QuickBooks name containing a colon or tab | Refused, or replaced in the PMS's own names | Tested | tests/test_quickbooks.py::test_a_name_quickbooks_cant_read_is_refused | L |
| P-144 | A memo that starts with "=" | Never a formula | Tested | tests/test_exports.py::test_csv_neutralizes_formulas_and_keeps_everything_else | L |
| P-145 | The database unreachable, asked by /health | 503 "database unavailable" within 2 s | Tested | tests/api/test_sign_in.py::test_health_says_when_the_database_is_unavailable | L |
| P-146 | Every pool connection busy | 503 in words with Retry-After | Tested | tests/api/test_refusals.py::test_a_request_that_finds_every_connection_busy_is_told_so_in_words | L |
| P-150 | The connection drops before the commit | Nothing lands and the retry lands once | Tested | tests/test_faults.py::test_a_payment_cut_off_before_it_commits_leaves_nothing_and_its_retry_lands_once | L |

### Most pressing open, with the test that would close each

1. P-040: an API test that a paid online payment on a two-owner property without shares appears in GET /api/payments/waiting (not a 422) and that Record takes an owner account; a vitest that the Waiting card shows an error notice when the GET fails.
2. P-024: a webhook test that a return arriving before paid is kept (or answered 5xx so Stripe resends) and settles once paid lands.
3. P-027 and P-028: Stripe event tests for charge.refunded and charge.dispute.closed (won), each read and recorded, or at least listed for staff.
4. P-013 (with P-011, P-012, P-051): a nightly sweep that lists payments started more than a few days ago with no report, shown on Banking and the Dashboard, and a test that one appears there.
5. P-048 and P-049: a test that an https server refuses sk_test_ keys (or that a report whose livemode doesn't match the keys is refused), and a test that old test keys stop counting once live keys are saved.
6. P-130: a preview of the statement's lines and totals before Import statement, confirmed with AskFirst, and a browser test; plus a way to set aside a statement imported in error.
7. P-087: a test that a sender or attachment raising a ValueError records a failed delivery and the next email still goes.
8. P-075: a test that deliveries never hold more than one pool connection at once, with SMTP set to hang, and that staff pages still answer.
9. P-111: an API test that Pay owners by ACH refuses before recording when a payee's sealed number doesn't open under the current PMS_DATA_KEY.
10. P-065: an autopay test that a company changing Stripe accounts stops (or flags) autopays saved at the old one and tells the tenant to save their bank again.

## Bad files and imports

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| F-010 | A PDF with JavaScript (OpenAction, /JS), uploaded by a tenant as their own lease document or by an anonymous applicant as proof of income | Kept and served byte for byte. It goes out as an attachment with nosniff and a CSP sandbox, which only protects the browser, so a desktop reader opening it may run its script. Staff and co-tenants receive it | Open | src/pms/api/documents.py:452-469; src/pms/api/tenant.py:535-548; src/pms/api/applicant_details.py:141-150 | H |
| F-061 | Day-first dates from a UK or European file ("05/01/2026" meaning 5 January) | Read as May 1 with no warning; "13/01/2026" is refused. A payment file records a payment in the wrong month, and charges get wrong due days | Open | src/pms/statements.py:77 | H |
| F-077 | A charge dated after a new lease started at the unit (the old tenant's move-out fee dated after the next move-in) | Goes to the newest lease started by its due day, so the new tenant owes the old tenant's charge. The file has no tenant or lease column to say otherwise | Open | src/pms/api/switching.py:441-447 | H |
| F-122 | An audit log report over a day a lease offer or a drawn signature was saved | to_jsonb of the row carries the offer's 5 MB PDF as 10 million hex characters into one detail cell. The PDF export wraps it a character at a time, copying the rest each line, and effectively never finishes; the Excel cell runs far past 32,767. NOT VERIFIED by running | Open | db/schema.sql trust_audit (to_jsonb(NEW)) and trust_report_audit_log (jsonb_each_text detail); src/pms/exports.py:279-286 | H |
| F-126 | The everything export (records.zip) of a company with GBs of files | Built whole in memory: each table fetched in full, every document's bytes, the zip in a BytesIO, one response. Each table is sorted by row_to_json(r)::text, which turns every 5 MB file into 10 MB of hex to sort. May exhaust memory or never finish; not timed | Open | src/pms/exports.py:433-491 | H |
| F-129 | Two tenants of the same name at different properties, in the QuickBooks export | Both get the default account "Prepaid rent:Pat Sample" (and "Tenant deposits:Pat Sample"), so QuickBooks posts both people's money to one account | Open | src/pms/quickbooks.py:42-48,109 | H |
| F-088 | The same payments again under other bytes (renamed, re-exported, a trailing newline) | Another SHA, but each line looks recorded already (same lease, day and amount) and waits for staff | Guarded | src/pms/api/payment_files.py:160-214 (the nearest test is tests/api/test_payment_files_api.py::test_a_payment_that_looks_recorded_already_waits) | H |
| F-015 | A PNG bomb: a 5 MB PNG declaring 30000 x 30000 pixels, kept as a move-out photo or a receipt | The statement still goes: the photo's page says it could not be added here and is with the lease's files, whatever error the image library raises | Tested | tests/test_deposit_statement.py::test_a_photo_that_says_it_is_huge_leaves_the_letter_whole | H |
| F-016 | That same statement emailed (deposit_statement) | Any error making an email's file is that attempt's failed delivery, with the error; the emails behind it go out in the same run | Tested | tests/test_email_attachments.py::test_an_attachment_that_fails_in_any_way_is_a_failed_delivery_and_stops_nothing_behind_it | H |
| F-055 | Formula text in an imported cell ("=HYPERLINK(...)" as a tenant name) | Kept as text and never run by the PMS. Every CSV download puts an apostrophe before it, and Excel downloads hold it as text | Tested | tests/test_exports.py::test_csv_neutralizes_formulas_and_keeps_everything_else | H |
| F-064 | A decimal comma ("1234,56" or "1200,50") | Refused saying the line: a comma only separates thousands (1,234.56), so a decimal comma is never read as 100 times the amount; in bank statements, payment files, every switching file and a units file's details | Tested | tests/test_amounts.py; tests/api/test_payment_files_api.py::test_a_file_that_is_not_payments_received_is_refused_whole; tests/api/test_unit_details_api.py::test_a_units_file_brings_their_details | H |
| F-076 | Two identical charges in one file (two $25.00 fees due the same day with the same memo) | Each line is matched to one charge alike already there: two alike in one file are two charges, and the file sent again adds nothing | Tested | tests/api/test_switch.py::test_two_alike_charges_in_one_file_both_come_in_and_once | H |
| F-083 | The uncleared checks and deposits file (cutover items) sent twice | Its lines are matched to the items carried over already, each once: sent again it adds nothing; two alike in one file both come in; one import at a time per account | Tested | tests/api/test_switch.py::test_an_items_file_sent_again_carries_nothing_over_twice | H |
| F-084 | Opening balances sent again, or sent differently | Once per trust bank account: the same again returns the first; a different one is refused | Tested | tests/test_cutover.py::test_the_same_opening_again_returns_the_original_transfers; tests/test_cutover.py::test_a_different_opening_of_the_same_trust_bank_account_is_refused | H |
| F-085 | Opening balances that don't add up to the book cash, or one account on two lines | Refused | Tested | tests/api/test_switch.py::test_opening_balances_that_dont_tie_out_are_refused | H |
| F-087 | A payment file sent twice | 409 "this file came in on ...; it comes in once" | Tested | tests/api/test_payment_files_api.py::test_a_file_comes_in_once | H |
| F-100 | An OFX with a DOCTYPE or an external entity | Never expanded: plain text matching, no XML parser | Tested | tests/test_statement_files.py::test_an_ofx_entity_or_doctype_is_never_expanded | H |
| F-109 | A report cell starting =, +, -, @, a tab or a carriage return, or one already starting "'=" | CSV puts one apostrophe before it and reads back as it was | Tested | tests/properties/test_export_properties.py::test_csv_holds_every_cell | H |
| F-110 | The same cells in an Excel download | Never a formula: stored as text | Tested | tests/properties/test_export_properties.py::test_excel_holds_every_value_and_no_formula | H |
| F-128 | Logins' secrets, sessions and token hashes in records.zip, in a table or in the audit log's copies | Left out | Tested | tests/test_records_export.py::test_no_credential_comes_out | H |
| F-138 | An ACH file downloaded again | Byte for byte the file made when the run was paid; each download is logged. The bank pays twice if it is uploaded twice | Tested | tests/api/test_payouts_api.py::test_an_ach_file_taken_again_is_the_one_made_when_the_run_was_paid | H |
| F-139 | A payee routing number whose check digit fails | Refused when the bank account is saved | Tested | tests/api/test_payouts_api.py::test_a_bank_account_that_cant_be_paid_is_refused | H |
| F-009 | A POST body of hundreds of MB, to any endpoint including the public apply and sign-in pages | No body size limit. uvicorn and FastAPI read the whole body into memory before the 7,000,000-character field limit refuses it, so a few such requests can exhaust the worker's memory | Open | src/pms/api/__main__.py:11-16; src/pms/api/app.py (no body-size middleware) | M |
| F-011 | A PDF carrying an embedded file (an .exe or .html attachment inside it) | Kept and served unchanged. The PMS reads nothing out of a file, so nothing flags or strips it | Open | src/pms/api/documents.py:386-409 | M |
| F-012 | A receipt PDF with link actions or scripts on its pages, attached to a deposit statement | pypdf's append copies its pages, annotations included, into the statement that is emailed to the former tenant. NOT VERIFIED which actions survive | Open | src/pms/deposit_statement.py:183-186 | M |
| F-014 | A JPEG with GPS and EXIF data (camera serial, time, owner name) used as a listing photo, request photo or move-out photo | Served unchanged: to anyone for listing photos, to staff for request photos, and passed into the deposit statement PDF for move-out photos. No metadata is stripped | Open | src/pms/api/listings.py:327-345; src/pms/api/documents.py:11-12 | M |
| F-017 | A PNG just under Pillow's bomb limit (about 13000 x 13000) | Decoded in full, about 500 to 700 MB per image, while the statement is built in the request | Open | src/pms/deposit_statement.py:157-159 | M |
| F-021 | A malformed PDF that makes pypdf raise something other than PyPdfError (KeyError, RecursionError on a deep page tree) | Not caught. The statement download fails with a server error and its email blocks the queue as in F-016. NOT VERIFIED which inputs do it | Open | src/pms/deposit_statement.py:184-188; src/pms/email.py:669-671 | M |
| F-022 | A receipt PDF of 10,000 tiny pages within 5 MB | Every page is appended into the statement in memory and emailed. No page cap | Open | src/pms/deposit_statement.py:185 | M |
| F-023 | A lease with 200 move-out photos of 5 MB each | All of them load into memory (PHOTOS has no limit). The statement email runs to hundreds of MB, mail servers refuse it, and each of the five attempts re-renders it | Open | src/pms/api/moveout.py:749-757,794-797; src/pms/email.py:657-671 | M |
| F-032 | Anyone with a public listing sends 50 applications a day, each with 6 applicants and a 5 MB proof of income each | Up to about 1.5 GB a day per link, kept for good (append-only, never purged), from people with no login | Open | src/pms/api/leasing.py:36-38; src/pms/api/applicant_details.py:69-72 | M |
| F-033 | A tenant opens a document a co-tenant uploaded to their shared lease | Served to them through the portal. An untrusted file passes from one tenant to another, with no scan | Open | src/pms/api/tenant.py:535-548 | M |
| F-037 | A Mac Roman CSV (old Excel for Mac "CSV (Macintosh)") | Not valid UTF-8, so it is read as Windows-1252: "José" comes in as "JosŽ" with no warning | Open | frontend/src/fileText.ts:5-11 | M |
| F-038 | A Windows-1250 or ISO-8859-2 CSV (Central European Excel) | Read as Windows-1252: "Łukasz" comes into the books as "£ukasz" with no warning | Open | frontend/src/fileText.ts:5-11 | M |
| F-039 | A UTF-8 file with one stray Windows-1252 byte on one line | The whole file falls back to Windows-1252, so every correct UTF-8 name elsewhere becomes "JosÃ©" with no warning | Open | frontend/src/fileText.ts:7-10 | M |
| F-044 | An unclosed quote partway through a file | The rest of the file becomes one cell (measured): later rows vanish, and e.g. one owner named "Owner A, / Owner B / Owner C" is added; payment and statement lines are dropped | Open | src/pms/api/switching.py:98-100; src/pms/statements.py:94-96 | M |
| F-047 | Blank lines or a multi-line quoted cell before a bad row | The "line N" in the refusal counts only the rows kept, so it points staff at the wrong line | Open | src/pms/api/switching.py:100,113; src/pms/statements.py:101,119 | M |
| F-052 | An unquoted comma in a name ("Smith, Jr" in a one-column owners file) | The row has more cells than headers; the extra is dropped and owner "Smith" is added with no warning | Open | src/pms/api/switching.py:112 | M |
| F-057 | A file over 2,000,000 characters (a 50,000-line charges, statement or payment file) | pydantic refuses the field before any line is read, and the portal says "check what you entered and try again", never the limit or the row count | Open | src/pms/api/switching.py:39; src/pms/api/reconcile.py:223; frontend/src/api.ts:56-67 | M |
| F-059 | A 20,000-row tenants or charges file | Several queries a row, all in one request transaction. Never timed (tools/speed.py doesn't import), so a proxy's timeout may cut it off. NOT VERIFIED | Open | src/pms/api/switching.py:552-555; tools/speed.py | M |
| F-065 | Misplaced thousands commas ("1,2,3", "12,50,0") | Commas stripped, read as 123 or 12500 with no warning | Open | src/pms/statements.py:62 | M |
| F-072 | Names that differ only in case or inner spacing ("tenant a" against "Tenant A", "Pat  Sample") | Exact matching fails. An owners or tenants file adds a second person, and a leases file adds a new tenant onto the lease with no warning | Open | src/pms/api/switching.py:135-240,403-405 | M |
| F-073 | A name in decomposed Unicode (NFD, as macOS writes it) against the same name in NFC | They look identical but match nothing, so duplicate records come in | Open | src/pms/api/switching.py:135-240 | M |
| F-074 | Two different people with the same name (two "Pat Sample" tenants at a property, two owners of one name) | Merged into one record: their leases, deposits and opening balances land on one person | Open | src/pms/api/switching.py:127-133,214-240 | M |
| F-075 | A leases row whose Tenants cell is only ";" | No tenant goes to trust_open_lease, which raises invalid_parameter_value. Only ExclusionViolation is caught and the DataError handler re-raises, so the answer is a server error. NOT VERIFIED by running | Open | src/pms/api/switching.py:403-428; src/pms/api/app.py:424-431 | M |
| F-092 | A payer written in capitals with accents ("JOSÉ SAMPLE") | lower() under a database with C ctype leaves É as it is: the line matches on one server and waits on another | Open | src/pms/api/payment_files.py:149-153 | M |
| F-098 | An OFX file holding several accounts' statements (checking and savings) | Every STMTTRN is read into one statement under the first LEDGERBAL; a first statement's "Balance brought forward" line absorbs the difference | Open | src/pms/statements.py:189-215; src/pms/api/reconcile.py:271-288 | M |
| F-099 | An OFX file whose lines don't come to its own LEDGERBAL | The opening is worked out as closing less the lines, so the file's balance never refuses it; only the next-statement rule catches it, from the second statement on | Open | src/pms/api/reconcile.py:271-283 | M |
| F-103 | A bank CSV of unsigned amounts with a separate Type (Debit or Credit) column | Every withdrawal comes in as money in. The closing balance is optional for CSV, so nothing refuses it | Open | src/pms/statements.py:105-130; frontend/src/pages/ReconcilePage.tsx:330-338 | M |
| F-106 | A history file sent again with one line added, or other line endings | Another SHA, so every earlier line is kept twice in each person's history | Open | src/pms/api/switching.py:612-624 | M |
| F-107 | A history file of a header only | The history file row has 0 lines, which its check (line_count > 0) refuses. That is outside the try, so the answer is a server error. NOT VERIFIED by running | Open | src/pms/api/switching.py:619-624 | M |
| F-108 | A vendor's or tenant's name mistyped in a history file | A new vendor (who then shows in pickers and lists) or a tenant who "left before the switch" is added with no warning | Open | src/pms/api/switching.py:582-590 | M |
| F-112 | Phones kept as E.164 ("+15555550100") and any text starting "-", in the records.zip and ledger zip CSVs | Each gets an apostrophe, so a system importing the "data for another system" export reads "'+15555550100" | Open | src/pms/exports.py:139; src/pms/exports.py:486 | M |
| F-113 | A CSV download opened by double-click in Excel on Windows | UTF-8 without a BOM, so Excel reads it as Windows-1252: "Nguyễn" shows as "Nguyá»…n" | Open | src/pms/exports.py:125-131 | M |
| F-118 | An Arabic or Chinese payee on a printed check | The payee line prints "?????": a check payable to question marks | Open | src/pms/payouts/checks.py:125,131-133 | M |
| F-121 | A cell taller than a page (a long memo or detail) | The row is drawn past the bottom margin; its text below the page edge is lost | Open | src/pms/exports.py:220-230,243-255 | M |
| F-123 | Any Excel cell over 32,767 characters | Written as is, and Excel asks to repair the file or cuts the cell | Open | src/pms/exports.py:158-160 | M |
| F-124 | A report of more than 1,048,576 rows (years of a big company's audit log or general ledger) as Excel | openpyxl refuses the row number, so a server error. NOT VERIFIED | Open | src/pms/exports.py:155-157 | M |
| F-130 | An owner's account for one property in two trust bank accounts | One default QuickBooks name for both, so their money merges in QuickBooks | Open | src/pms/quickbooks.py:44,109 | M |
| F-131 | Cyrillic or Vietnamese names in the IIF file | Windows-1252 with errors replaced: "Дмитрий Иванов" becomes "??????? ??????". Names can collide, or QuickBooks makes new accounts | Open | src/pms/quickbooks.py:169 | M |
| F-132 | A holder name longer than QuickBooks Desktop allows per account level (about 31 characters, NOT VERIFIED) | Not shortened, so QuickBooks may refuse the import or cut names until two accounts merge | Open | src/pms/quickbooks.py:96-99,109 | M |
| F-136 | An ACH account holder named only in Cyrillic, Arabic or Chinese | The receiver name is 22 spaces; the bank may reject the entry or not know who it pays. Nothing warns when the account is saved | Open | src/pms/payouts/nacha.py:55-57,126 | M |
| F-137 | Two ACH files for one bank on one day (two Pay owners runs) | Every file's File ID Modifier is "A", and banks commonly reject a second file with the same one as a duplicate | Open | src/pms/payouts/nacha.py:73; src/pms/api/payouts.py:686 | M |
| F-141 | A long payee name or memo on a printed check | fpdf's cell doesn't clip, so the name runs past its 130 mm box into the amount figure | Open | src/pms/payouts/checks.py:124-127,135 | M |
| F-096 | A line dated in a period month-end has closed | The books refuse it; it waits with their reason | Guarded | src/pms/api/payment_files.py:398-405 | M |
| F-035 | A CSV Excel on Windows saved as Windows-1252, with "José O’Brien" | Read as Windows-1252; the letters survive | Tested | frontend/src/fileText.test.ts "reads a file Excel on Windows saved as CSV (Windows-1252) without losing its letters" | M |
| F-036 | A file whose letters arrive as the replacement character | Refused whole with its line: "save the file as CSV UTF-8" | Tested | tests/api/test_switch.py::test_a_file_whose_letters_didnt_read_is_refused_with_its_line | M |
| F-063 | Amounts written "$2,750.00", "(12.05)", "$ 1 234.56" or " -0.10 " | Read to the cent; parentheses mean money out | Tested | tests/test_statement_files.py::test_an_amount_is_read_to_the_cent; tests/test_statement_files.py::test_a_csv_with_an_amount_column_is_read | M |
| F-070 | Fractions of a cent, NaN, a billion or more | Refused: not an amount in cents | Tested | tests/test_statement_files.py::test_a_csv_that_is_not_a_statement_says_where_and_why | M |
| F-071 | The same roster file sent twice | Nothing is added twice; the counts say kept | Tested | tests/api/test_switch.py::test_the_roster_comes_in_and_a_file_sent_again_adds_nothing | M |
| F-078 | A second rent of another amount for the same lease and day | Refused with its line | Tested | tests/api/test_switch.py::test_a_rent_charged_twice_is_refused_with_its_line | M |
| F-080 | A lease over another at the same unit | Refused with its line | Tested | tests/api/test_switch.py::test_a_lease_over_another_is_refused_with_its_line | M |
| F-081 | A bills file sent again, or a reference reused with other details | Kept the same; refused when different | Tested | tests/api/test_switch.py::test_unpaid_bills_come_over_and_hold_back_draws; tests/api/test_switch.py::test_a_bills_file_that_cant_go_in_says_where_and_why_and_adds_nothing | M |
| F-086 | A vendor payments file sent again, or with another amount | Kept; another amount for that vendor's year is refused | Tested | tests/api/test_switch.py::test_what_the_old_system_paid_vendors_comes_over_once; tests/api/test_switch.py::test_another_amount_for_a_year_carried_over_is_refused | M |
| F-089 | The same line twice in one file (two roommates each paying $400.00 that day) | The first is recorded and the second waits as "recorded already" for staff to record by hand | Tested | tests/api/test_payment_files_api.py::test_a_payment_that_looks_recorded_already_waits | M |
| F-093 | Units sharing a name at two properties, with no property column | Waits: "2 leases in effect ...; name the property too" | Tested | tests/api/test_payment_files_api.py::test_a_file_records_each_line_to_the_lease_it_names_and_the_rest_wait | M |
| F-094 | A negative or zero amount, a day still to come, an unknown method | The whole file is refused, saying the line | Tested | tests/api/test_payment_files_api.py::test_a_file_that_is_not_payments_received_is_refused_whole | M |
| F-097 | The same bank statement file sent twice, or an overlapping download | 409: imported already, or not the next statement | Tested | tests/api/test_reconcile.py::test_a_statement_out_of_turn_twice_or_ahead_of_today_is_refused; tests/test_bank_statements.py::test_a_file_is_imported_once | M |
| F-101 | An OFX of 200,000 tags that never close | Read in linear time | Tested | tests/test_statement_files.py::test_an_ofx_reads_tags_left_open_and_never_slows_on_them | M |
| F-105 | A history file sent twice | Nothing added twice (by its SHA-256) | Tested | tests/api/test_history_api.py::test_staff_bring_each_persons_history_in_once_and_read_it_newest_first | M |
| F-114 | Vietnamese, Polish, Russian and Greek names in a report PDF | Printed as written in IBM Plex Sans | Tested | tests/test_pdf_fonts.py::test_a_report_prints_its_names_as_written | M |
| F-115 | Arabic or Chinese names in a PDF | Each letter prints "?", never an empty box | Tested | tests/test_pdf_fonts.py::test_a_letter_the_font_has_prints_and_one_it_lacks_reads_a_question_mark | M |
| F-127 | A document's title used for its name inside records.zip | Never: files are named files/table/n-column.ext by their first bytes, so a title can't add a path | Tested | tests/test_records_export.py::test_a_files_name_ends_as_its_first_bytes_say | M |
| F-133 | A colon in a holder's name; a tab, quote or line break in a memo | The colon becomes a dash; the others become spaces in IIF | Tested | tests/test_quickbooks.py::test_each_account_has_the_pms_name_until_the_company_names_it; tests/test_quickbooks.py::test_desktop_reads_balanced_general_journal_entries | M |
| F-143 | A tenant named "{{balance_due}}" in a notice or lease template | Stays as text: blanks are filled in one pass | Tested | tests/test_notice_letters.py::test_each_blank_is_filled_once_with_its_value | M |
| F-144 | An email attachment that can't be made (no such payment, a deposit not returned yet) | A failed delivery saying why, tried again, and given up on after five | Tested | tests/test_email_attachments.py::test_a_receipt_whose_file_cant_be_made_is_a_failed_delivery_saying_why; tests/api/test_deposit_statements_api.py::test_a_statement_that_cannot_be_made_is_a_failed_delivery | M |
| F-004 | A real PDF that the browser reports as "application/x-pdf", "application/octet-stream" or "" (no extension, an odd OS mapping) | The portal picks the type from the browser, not from the bytes, and refuses a good file with "A file is a PDF, a JPEG or a PNG." | Open | frontend/src/documents.ts:85 | L |
| F-005 | A 0-byte file | Its base64 is "", so pydantic's min_length=4 refuses it and the portal shows "check what you entered and try again". The API's own message would wrongly say "at most 5 MB" | Open | src/pms/api/documents.py:335,366; frontend/src/api.ts:66 | L |
| F-008 | A 2 GB video picked in Files or Upload | The page reads the whole file into memory and base64-encodes it before showing "A file is at most 5 MB", so the tab may freeze or crash | Open | frontend/src/pages/DocumentsPage.tsx:162-167; frontend/src/pages/FilesCard.tsx:134 | L |
| F-027 | A title holding a right-to-left override (U+202E), e.g. "invoice[RLO]fdp.exe" | Kept as typed and shown reversed in the Files lists, so it looks like another file type. The download name is ASCII | Open | src/pms/api/documents.py:57 | L |
| F-028 | A file whose name runs past 200 characters | The title defaults to the file name, and the input's maxLength doesn't cut a value set by code. The API refuses it and the toast says "check what you entered and try again" | Open | frontend/src/pages/DocumentsPage.tsx:167; frontend/src/pages/TenantPortalCards.tsx:136 | L |
| F-029 | The same file uploaded twice (same SHA-256) | Kept twice and listed twice. A duplicate listing photo counts toward the twelve | Open | src/pms/api/documents.py:386-409 | L |
| F-040 | A UTF-16 file (Excel "Unicode Text") | Read as Windows-1252 full of NULs and refused as "line 1: no name column", without naming the encoding | Open | frontend/src/fileText.ts:5-11; src/pms/api/switching.py:105-108 | L |
| F-043 | A line break inside a quoted name | Kept: a tenant or owner name holding a line break goes into the books. Nothing refuses it | Open | src/pms/api/switching.py:63-68 | L |
| F-051 | The same header twice (two Tenant or Amount columns) | The switching tool keeps the last one and the statement reader the first, both without a word | Open | src/pms/api/switching.py:112; src/pms/statements.py:85-89 | L |
| F-067 | Scientific notation ("1e3", "1.5E+03") or Arabic-Indic digits ("١٢٣") | Read as 1000.00, 1500.00 and 123.00: Decimal accepts both (measured) | Open | src/pms/statements.py:67-70 | L |
| F-068 | "(-5.00)" | The minus and the parentheses cancel, so it reads as +5.00 | Open | src/pms/statements.py:62-72 | L |
| F-082 | A bills row the database refuses for a reason the importer doesn't check first (dated in a closed year) | The INSERT has no try, so the answer is a server error, not the line. NOT VERIFIED | Open | src/pms/api/switching.py:515-520 | L |
| F-090 | The preview of a file holding the same line twice | Shows both lines as "to record"; bringing it in records one and holds the other, so the preview's count is wrong | Open | src/pms/api/payment_files.py:347-372 | L |
| F-091 | A payment file of a header only | Refused as "line 1: no unit or payer column" although it has one: the headers are read from the first data row | Open | src/pms/api/payment_files.py:88-95 | L |
| F-102 | A QuickBooks Web Connect .qbo file (OFX inside) | Read as CSV by its extension and refused: "no date column" | Open | frontend/src/reconcile.ts:152-154 | L |
| F-111 | Text with a space before "=" (" =1+1"), or what LibreOffice or Google Sheets might evaluate | No apostrophe. NOT VERIFIED whether any spreadsheet runs it | Open | src/pms/exports.py:55,139 | L |
| F-117 | A name in decomposed form (e plus combining marks) in a PDF | The marks pass printable but fpdf2 sets them with no shaping, so accents may sit beside their letter. NOT VERIFIED | Open | src/pms/pdf_fonts.py:65-69 | L |
| F-134 | A formula memo in the QuickBooks Online CSV, or a company-named account starting "-" or "+" | The memo gets an apostrophe (tested). So does the account name, which QuickBooks Online then reads as another account's name | Open | src/pms/quickbooks.py:183-191; tests/test_quickbooks.py::test_online_reads_two_lines_an_entry_under_its_journal_number | L |
| F-140 | A bank that asks for CRLF line ends, or an effective day on a weekend or holiday | LF only, and the day as given. NOT VERIFIED against a bank | Open | src/pms/payouts/nacha.py:105,165 | L |
| F-142 | One check paying more than 12 bills | Its stubs list only the first 12; the rest go unnamed (the total is right) | Open | src/pms/payouts/checks.py:103 | L |
| F-145 | A year of general ledger or audit log as a PDF for a 1,000-unit company | Built in memory in the request. tools/speed.py times a month's PDFs only (10 s p95 ceiling), never a year's. NOT VERIFIED | Open | src/pms/exports.py:197-231; ci/thresholds.toml [speed] | L |
| F-003 | An iPhone HEIC photo renamed .jpg | Its first bytes aren't JPEG's, so it is refused with "the file isn't a JPG", which doesn't say it is HEIC | Guarded | src/pms/api/documents.py:368 | L |
| F-006 | A file of exactly 5 MiB (5,242,880 bytes) | Accepted: its base64 is 6,990,508 characters, under 7,000,000. Only the over-limit side has a test | Guarded | src/pms/api/documents.py:58,335,366 | L |
| F-020 | A password-protected receipt PDF | pypdf's FileNotDecryptedError is a PyPdfError, so the statement prints the could-not-be-added page | Guarded | src/pms/deposit_statement.py:184-188 | L |
| F-026 | A title in Cyrillic, all emoji or all symbols | The download name loses every non-ASCII letter and becomes "document.pdf" or "Nguy-n-V-n-An.pdf". The title itself is kept | Guarded | src/pms/api/documents.py:419-422 (the nearest test is the "***" case in tests/api/test_documents_api.py::test_a_download_is_named_for_its_title) | L |
| F-053 | A semicolon- or tab-separated file (European Excel), or Excel's "sep=," hint on the first line | Read as one column, or the hint taken as the header, and refused: "line 1: no ... column" | Guarded | src/pms/api/switching.py:105-108 | L |
| F-054 | A bank CSV with account details above the header, or a "Total" row at the end | The header is refused ("no date column") or the total row is refused as not a date: the whole file, saying where | Guarded | src/pms/statements.py:82,108-109 | L |
| F-056 | A NUL character in a cell | Postgres can't store it; the answer is "text can't contain a NUL character", without the line | Guarded | src/pms/api/app.py:424-431 | L |
| F-062 | "1/5/26", "Jan 5, 2026", "2026/01/05", "2026-01-05 00:00", an Excel serial "46027", or February 29 of a non-leap year | Refused: "is not a date (YYYY-MM-DD or MM/DD/YYYY)" | Guarded | src/pms/statements.py:75-82 (tests cover only 'June' and '2026-13-01') | L |
| F-066 | "1.234,56" (European thousands and decimal) | Refused: not an amount in cents | Guarded | src/pms/statements.py:70-71 | L |
| F-069 | A trailing minus "50.00-", a "CR" or "DR" suffix, "USD 50", a Unicode minus "−50", a non-breaking-space separator | Refused: "is not an amount" | Guarded | src/pms/statements.py:66-69 | L |
| F-079 | A credit (negative amount) in a charges file | Refused: "rent or a fee, of more than 0". A tenant's credit has to come over as prepaid rent | Guarded | src/pms/api/switching.py:438-440 | L |
| F-095 | A payment file of 5,001 lines | Refused: "at most 5000 lines". No test names it | Guarded | src/pms/api/payment_files.py:36,98-99 | L |
| F-104 | Two identical bank lines (two $50.00 deposits on one day) | Both kept as lines of their own | Guarded | src/pms/statements.py:131-140 | L |
| F-116 | Emoji in a name in a PDF | "?" (no glyph in the font) | Guarded | src/pms/pdf_fonts.py:65-69 | L |
| F-001 | A PNG renamed .pdf (the browser sends it as application/pdf) | The server checks the first bytes and refuses: "the file isn't a PDF" | Tested | tests/api/test_documents_api.py::test_a_file_is_what_it_says_and_at_most_5_mb | L |
| F-002 | A PDF renamed .jpg or .png | Refused by the first-bytes check in the API and again by the database trigger | Tested | tests/test_documents.py::test_a_file_is_the_type_its_document_says | L |
| F-007 | A file 5 bytes over 5 MiB | Refused: "a document is a file of at most 5 MB". The database checks the size too | Tested | tests/api/test_documents_api.py::test_a_file_is_what_it_says_and_at_most_5_mb (over-5-mb); tests/test_documents.py::test_a_documents_details_stay_in_range | L |
| F-013 | A polyglot: starts "%PDF-" but holds HTML and script | The browser never runs it as a page: attachment, application/pdf, nosniff, CSP "default-src 'none'; sandbox" | Tested | tests/api/test_documents_api.py::test_staff_keep_each_kind_of_document_and_download_it (asserts the CSP) | L |
| F-018 | A photo whose first bytes are an image's but the rest is cut short | The statement names it on a page of its own: "could not be added here: it is with the lease's files" | Tested | tests/test_deposit_statement.py::test_each_file_follows_the_letter_on_pages_of_its_own | L |
| F-019 | A 5-byte "PDF" (just "%PDF-") | The upload takes it. The statement names it as could not be added. Owners and staff download a broken file | Tested | tests/test_deposit_statement.py::test_each_file_follows_the_letter_on_pages_of_its_own (Broken PDF) | L |
| F-024 | A signature drawing PNG that declares a huge size within 100 KB | Refused before it is kept: the header must say at most 2000 x 1000 | Tested | tests/test_esign.py::test_a_drawing_fits_only_as_a_png_of_a_signatures_size | L |
| F-025 | A document title with path characters, quotes or a line break ("../../Väry Odd name!!") | The download name is cut to plain letters, digits and dashes (V-ry-Odd-name.png), so Content-Disposition can't be broken | Tested | tests/api/test_documents_api.py::test_a_download_is_named_for_its_title | L |
| F-030 | A 13th listing photo, two uploads at once, or a PDF as a listing photo | 409 at twelve; the uploads wait for each other; a PDF is refused | Tested | tests/api/test_listing_photos_api.py::test_a_listing_photo_is_an_image_and_a_unit_shows_at_most_twelve | L |
| F-031 | A tenant sends an 11th file in a day | 429 "You can send 10 files a day." | Tested | tests/api/test_portals_two_api.py::test_a_lease_takes_so_many_files_a_day_from_its_tenants | L |
| F-034 | A CSV saved as UTF-8 with a byte order mark | The portal drops the BOM, and the server strips one too | Tested | frontend/src/fileText.test.ts "reads a UTF-8 file as written, its byte order mark dropped"; tests/test_statement_files.py::test_a_csv_with_an_amount_column_is_read | L |
| F-041 | An empty file, or only blank lines | "the file is empty" | Tested | frontend/src/fileText.test.ts "reads an empty file as nothing"; tests/test_statement_files.py::test_a_csv_says_why_in_full | L |
| F-042 | Quotes and commas inside fields ("$1,250.00", "Smith, Jr.") | Read by the csv module | Tested | tests/api/test_switch.py::test_the_roster_comes_in_and_a_file_sent_again_adds_nothing | L |
| F-045 | A field over 131,072 characters | Refused in words with its line | Tested | tests/api/test_switch.py::test_a_file_that_is_not_csv_is_refused_in_words_not_a_server_error; tests/test_statement_files.py::test_a_field_longer_than_csv_reads_is_refused_in_words | L |
| F-046 | Blank lines in a CSV | Skipped (rows of only commas likewise, untested) | Tested | tests/test_statement_files.py::test_a_csv_with_an_amount_column_is_read | L |
| F-048 | Headers in another case, with underscores or extra spaces ("Starts_On", " name ") | Matched | Tested | tests/api/test_switch.py::test_the_roster_comes_in_and_a_file_sent_again_adds_nothing | L |
| F-049 | Columns in another order | Found by header name | Tested | tests/test_statement_files.py::test_a_csv_with_columns_for_money_out_and_in_is_read | L |
| F-050 | A required column missing | "line 1: no name column" and the like; nothing comes in | Tested | tests/api/test_switch.py::test_a_file_that_cant_go_in_says_where_and_why_and_adds_nothing | L |
| F-058 | 20,001 data rows in a roster file | "a file has at most 20000 rows" | Tested | tests/api/test_switch.py::test_a_file_has_at_most_so_many_rows | L |
| F-060 | Dates as 2025-06-01 and 06/01/2025 in one file (single digits like 1/5/2026 too) | Both read by strptime | Tested | tests/api/test_switch.py::test_the_roster_comes_in_and_a_file_sent_again_adds_nothing | L |
| F-119 | A report with no rows | CSV and Excel hold the header alone and the PDF builds (the generated tables include empty ones) | Tested | tests/properties/test_export_properties.py::test_the_pdf_always_builds | L |
| F-120 | A long name or a word with no spaces in a PDF cell | Wraps inside the word only when it must | Tested | tests/test_exports.py::test_a_cells_text_wraps_at_spaces_and_inside_a_word_only_when_it_must | L |
| F-125 | Control characters (a bell) in an Excel cell | Read as "?" | Tested | tests/test_exports.py::test_excel_keeps_types_and_never_holds_a_formula | L |
| F-135 | Accented letters in an ACH name ("Bö & Sons") | Become spaces: "B  & SONS, LTD." | Tested | tests/test_payout_files.py::test_an_ach_file_writes_names_in_capitals_with_its_own_characters_only | L |

### Most pressing open, with the test that would close each

1. F-015 / F-016: build a deposit statement whose move-out photo is a PNG declaring 30000 x 30000 pixels. Assert the statement still downloads, with a could-not-be-added page. Then queue its email ahead of a receipt and assert deliver_pending records a failed delivery and still sends the receipt (tests/test_deposit_statement.py, tests/test_email_attachments.py).
2. F-064: an API test bringing in a payment file line "1234,56" (and a charges file with a decimal comma). Assert it is refused saying the line, and that nothing is recorded (tests/api/test_payment_files_api.py, tests/test_statement_files.py).
3. F-061: a parametrized test that a day-first date ("05/01/2026" written for 5 January) is refused or asked about, never read as May 1, in payment files and charges (tests/test_statement_files.py::read_date cases).
4. F-083: send the same cutover items file twice. Assert the second adds nothing ({"added": 0, "kept": n}) and that the tie-out is unchanged (tests/api/test_switch.py).
5. F-076: one charges file with two identical $25.00 fees due the same day. Assert both come in, or that the file is refused saying it can't tell them apart from a resend (tests/api/test_switch.py).
6. F-077: a unit with an ended lease and a new one, and a charges row dated after the new move-in. Assert it is refused for naming no tenant, or goes to the lease a tenant column names (tests/api/test_switch.py).
7. F-129: a property test in tests/properties/test_quickbooks_properties.py. Two tenants of one name at two properties, and two owners' accounts. Assert every ledger account has a QuickBooks name of its own, in IIF and in the Online CSV.
8. F-122: export trust_report_audit_log as PDF and Excel over a day a lease offer was made, under a time limit marked timing. Assert it finishes, no Excel cell passes 32,767 characters, and the offer's PDF bytes never appear in the detail (tests/test_exports.py).
9. F-126: build records.zip for a company holding, say, 200 MB of documents under a memory and time ceiling (tools/speed.py or a nightly test). Assert it streams, or stays under the ceiling.
10. F-010: an API test where a tenant uploads a PDF holding /JavaScript or /EmbeddedFile, and the applicant apply page sends one as proof of income. Assert it is refused, or served with the script stripped, per whatever rule the founder picks (tests/api/test_portals_two_api.py, tests/api/test_applicant_details_api.py).

## People doing the unexpected, permissions and security

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| U-010 | An owner's or tenant's login is disabled by mistake | No way back: nothing re-enables it, a new invite is refused at its link, and a second login for the person is impossible (one per person) | Open | src/pms/accounts.py:487 disable_login only sets disabled_at; db/migrations/20261011000028_logins.sql:43-46 UNIQUE (pmc_id, owner_id) and (pmc_id, tenant_id) | H |
| U-014 | A staff member uses the invite link shown on screen to become an owner or tenant who already has a password | Possible: whoever has the area makes an invite, sees its link once, sets a password and acts as the owner (approves bills over the limit by typing the owner's name) | Open | src/pms/api/app.py:508 invite returns the link; src/pms/api/owner.py:433 approve_bill trusts the session | H |
| U-015 | An admin re-invites another staff member (an approver, the trust account's broker) and uses the link | The admin sets their password and authenticator and acts as them, defeating prepared-by-one-approved-by-another and the signer rules; the audit log shows only who made the invite | Open | src/pms/accounts.py:303 accept_invite replaces the password and TOTP secret; db/migrations/20261012000034_staff_roles.sql trust_check_staff_invite | H |
| U-033 | PMS_TRUSTED_PROXIES "*" behind nginx set as the install guide says ($proxy_add_x_forwarded_for) | The app takes the leftmost X-Forwarded-For, which the visitor writes: the sign-in limit is bypassed and a signature's evidence keeps a made-up address | Open | src/pms/api/app.py:338; uvicorn 0.54.0 picks the first host when every host is trusted; docs/install.md:94; tests/api/test_signing.py::test_behind_a_trusted_proxy_the_evidence_keeps_the_signers_address sends one value only | H |
| U-051 | The only admin with a working login drops admin while another admin's login is disabled | Allowed: the disabled admin still counts, so nobody can change roles, and make-admin refuses because an admin "exists" | Open | db/migrations/20261012000034_staff_roles.sql:209-219 (no login check); src/pms/admin.py make-admin names nobody | H |
| U-085 | A former owner (the property sold, or its ownership moved to another owner) | Still sees the property's units, tenants' names, rents, what each lease owes, repairs and files, as their account there remains | Open | src/pms/api/owner_portal.py:63 owned_properties (any owner_property account, no share or date) | H |
| U-099 | Staff mark the wrong person's record as "the same person" | Nothing undoes a link: the wrong person signs in to the other's lease, ledger, receipts and files, and may pay or file under it | Open | src/pms/api/directory.py:463 same_person; db/migrations/20261021000105_tenant_records.sql:25 (no removal table) | H |
| U-100 | A tenant left a lease others stay on | Still reads it: the staying tenant's later payments, rent changes, files and the property's announcements | Open | src/pms/api/tenant.py:268 lease_of ignores departures; tests/api/test_lease_people_api.py::test_a_tenant_who_left_reads_the_lease_and_files_nothing_more; src/pms/api/announcements.py:145 | H |
| U-078 | An owner approves in one tab and declines in another at the same moment | One decision stands; the other is 409 | Guarded | src/pms/api/owner.py:421 decide; db/migrations/20261013000041_owner_bill_decisions.sql:90 trust_bill_decision | H |
| U-002 | Company A's session cookie used with company B's record ids in a path or body | Every route answers not found or refuses, and changes nothing of B's | Tested | tests/api/test_isolation.py::test_a_route_refuses_another_companys_record_in_its_path; ::test_a_write_refuses_another_companys_record_in_its_body | H |
| U-003 | A's staff, owner or tenant lists, searches or runs reports hoping to see B's rows | Every list, page and report is swept; nothing of B's shows | Tested | tests/api/test_isolation.py::test_a_list_or_page_shows_nothing_of_another_companys; ::test_no_report_shows_another_companys_books | H |
| U-009 | A staff member without admin, or whose custom role lacks "who can sign in", disables an owner's or tenant's login | Disabling an owner's or a tenant's login takes the area their invite takes, who can sign in (403 without it); a staff login still takes admin | Tested | tests/api/test_custom_roles.py::test_disabling_an_owners_or_tenants_login_takes_who_can_sign_in | H |
| U-013 | Two invites (or a reset email and an invite) are open for one login; the person uses the newer | Only a login's newest invite opens: a newer invite or password reset retires the older links, which read as can't be used | Tested | tests/api/test_invites.py::test_a_link_that_cant_be_used_says_so | H |
| U-039 | Another site posts to the API (CSRF) | Refused when the browser says cross-site or same-site, with or without an Origin | Tested | tests/api/test_sign_in.py::test_a_browser_saying_another_site_is_refused_with_or_without_an_origin | H |
| U-049 | The last admin takes away their own admin role | Refused "the company keeps an admin: give another staff member admin first" | Tested | tests/api/test_staff.py::test_an_admin_adds_a_staff_member_and_changes_roles; tests/test_staff_roles.py::test_a_pmc_keeps_an_admin | H |
| U-050 | Two admins take each other's admin role at once | One admin is left | Tested | tests/test_staff_roles.py::test_two_admins_taking_each_others_role_leave_one | H |
| U-053 | The staff member who prepared month-end approves it | Refused | Tested | tests/test_staff_roles.py::test_whoever_prepared_it_does_not_approve_it | H |
| U-054 | A journal entry's proposer posts it | Refused | Tested | tests/test_journal_entries.py::test_the_staff_member_who_proposed_it_does_not_post_it | H |
| U-055 | Staff without disburse pay a bill or a draw | Refused by the database | Tested | tests/api/test_staff.py::test_paying_out_money_takes_the_disburse_role | H |
| U-056 | Staff who aren't the account's signers pay out once a broker is on file | Refused | Tested | tests/test_trust_account_signers.py::test_once_an_account_has_its_broker_only_its_signers_pay_out | H |
| U-057 | A signer whose bond ran out pays out | Refused | Tested | tests/test_trust_account_signers.py::test_a_bonded_signer_pays_out_only_while_the_bond_is_in_force | H |
| U-062 | A staff member who isn't an admin downloads everything | 403 | Tested | tests/api/test_downloads.py::test_only_an_admin_downloads_everything | H |
| U-063 | A staff member who isn't an admin reveals a whole tax ID | Last four only; the database refuses the reveal | Tested | tests/api/test_tax_ids_api.py::test_staff_who_are_not_admins_see_the_last_four_only | H |
| U-064 | A staff member who isn't an admin invites a staff member | Refused | Tested | tests/api/test_staff.py::test_only_an_admin_invites_or_disables_a_staff_login | H |
| U-066 | An API key taken back | Opens nothing from its next request | Tested | tests/api/test_ai_keys.py::test_a_key_taken_back_opens_nothing_from_its_next_request | H |
| U-067 | A key sent with a browser's cookie, or a cookie on a key endpoint | Neither opens the other's endpoints | Tested | tests/api/test_ai_keys.py::test_a_key_is_no_session_and_a_session_is_no_key | H |
| U-068 | A key names another company's ids in a report's arguments | The report's company is always the key's | Tested | tests/api/test_ai_keys.py::test_a_reports_company_is_always_the_keys | H |
| U-069 | The AI role selects a table it must never read | It holds no grant on it | Tested | tests/test_privileges.py::test_ai_role_reads_only_what_the_app_reads | H |
| U-070 | The AI role writes a table or moves money | No grant; refused | Tested | tests/test_ai_role.py::test_the_ai_role_cannot_move_money_or_change_a_record; ::test_the_ai_role_writes_no_table | H |
| U-071 | The AI reads the audit rows of tables it may not read | Row-level security hides them | Tested | tests/test_privileges.py::test_row_level_security_only_hides_the_audit_rows_the_ai_may_not_read | H |
| U-072 | The AI runs a report that runs as the owner over tables it can't read | Gets the report's words only (renewal status, compliance standing) | Tested | tests/test_ai_role.py::test_the_ai_role_runs_every_report_and_sees_what_the_owner_sees; tests/test_assistance_compliance.py::test_records_are_never_rewritten_and_the_ai_reads_no_income | H |
| U-081 | An owner approves another owner's bill by guessing its id | 404 | Tested | tests/api/test_owner.py::test_another_owners_bill_reads_as_not_found | H |
| U-082 | An owner sends who approved in the request body | Refused | Tested | tests/api/test_owner_approval_names_api.py::test_who_approves_comes_from_the_session_never_the_request | H |
| U-087 | An owner opens another owner's statement | Never reachable: no request names an owner | Tested | tests/api/test_owner.py::test_another_owners_statement_is_never_reachable | H |
| U-088 | An owner opens another owner's property page | 404 | Tested | tests/api/test_portals_two_api.py::test_another_owners_property_reads_as_not_found | H |
| U-089 | An owner reads a tenant's messages | Never; their own conversation only | Tested | tests/api/test_portals_two_api.py::test_an_owner_has_their_own_conversation_and_never_reads_a_tenants | H |
| U-090 | An owner downloads documents | Their own, their properties' and their bills' receipts only | Tested | tests/api/test_documents_api.py::test_an_owner_reads_their_own_documents_only | H |
| U-093 | A tenant guesses another tenant's receipt id | Not found | Tested | tests/api/test_receipts_api.py::test_a_payment_not_theirs_reads_as_not_found | H |
| U-094 | A tenant opens a lease they aren't on | Not found | Tested | tests/api/test_tenant.py::test_a_lease_that_isnt_theirs_reads_as_not_found | H |
| U-095 | A tenant looks for a neighbour's repair requests | Never shown | Tested | tests/api/test_tenant.py::test_a_neighbours_requests_are_never_shown | H |
| U-109 | The 11th request in 24 hours is an emergency | An emergency goes through past the day's ten requests, up to five emergencies a day; the sixth is told to call the manager, and 911 first for gas, fire or flooding | Tested | tests/api/test_tenant.py::test_an_emergency_goes_through_past_the_days_requests_up_to_its_own_limit | H |
| U-113 | A tenant pays online for a lease that isn't theirs | Refused | Tested | tests/api/test_online_payments_api.py::test_a_tenant_pays_only_a_lease_they_are_on_and_a_sane_amount | H |
| U-118 | A withdrawn application's offer gets every signature | It never becomes a lease | Tested | tests/test_application_withdrawals.py::test_no_offer_of_a_withdrawn_application_becomes_a_lease | H |
| U-119 | A withdrawal and making the lease at the same moment | One waits and is refused | Tested | tests/test_application_withdrawals.py::test_a_withdrawal_waits_for_a_lease_being_made_and_then_is_refused | H |
| U-139 | The same payment sent twice (a double click, a retry) | Recorded once | Tested | tests/api/test_rent.py::test_sending_a_payment_again_records_it_once | H |
| U-006 | Someone types a long repair request, email or journal entry for over an hour without a request | The session ends with no warning; Save gets a "sign in first" toast and the typed work is lost (WCAG 2.2.1 asks for a warning) | Open | src/pms/accounts.py:43 IDLE_MINUTES; frontend/src/api.ts:36 call() has no 401 handling | M |
| U-007 | A login is disabled while its tab is open | Its next request gets 401; the page keeps showing what it loaded and each click toasts "sign in first" instead of going to sign-in | Open | frontend/src/App.tsx:100 checks 401 only when the page first loads; frontend/src/api.ts:36 | M |
| U-011 | Staff re-invite a disabled login | The invite is made (201) and, while the server sends email, emailed; the link then reads "can't be used" | Open | src/pms/accounts.py:192 create_invite has no disabled check; :267 _open_invite refuses it | M |
| U-016 | A staff invite accepted in two tabs before the code is confirmed | The second accept makes a new authenticator secret; codes from the first QR are refused as wrong | Open | src/pms/accounts.py:303 (a new totp_secret each accept, invite stays open until confirm) | M |
| U-017 | A staff reset half done: password set, authenticator not yet confirmed | The new password is in place, sign-in says "finish setting up from your invite link first", and old sessions stay open until the code is confirmed | Open | src/pms/accounts.py:303 and :347 (sessions end only at confirm) | M |
| U-019 | An admin's re-invite of a staff member is emailed | Whoever reads that inbox sets the password and a new authenticator: the emailed link gets past the authenticator, which request_reset says must not happen | Open | src/pms/api/app.py:508 emails every kind of invite while the server sends email; src/pms/accounts.py:236 docstring | M |
| U-023 | A tenant lost the mailbox they sign in with | A login's email never changes; resets go to the old address; staff must re-invite with the old email and hand over the shown link | Open | db/migrations/20261011000028_logins.sql:106 (no UPDATE of email); src/pms/accounts.py:192 Conflict "this tenant signs in as" | M |
| U-024 | A tenant changes their contact email in the portal and later forgets their password | Emails go to the new address, but sign-in and resets stay on the old one | Open | src/pms/api/tenant.py:649 "Their sign-in email stays as it is" | M |
| U-025 | An owner who also rents a unit, with one email | The second login is refused "already signs in to this PMC as someone else"; they need two emails and two portals | Open | tests/api/test_invites.py::test_an_email_signs_in_as_one_person; db/migrations/20261011000028_logins.sql:43 UNIQUE (pmc_id, email) | M |
| U-026 | A staff member who also owns a property or rents a unit | Nothing ties their staff record to it: they can record their own owner's approval, waive their own fees or pay their own bill | Open | src/pms/api/deps.py:241 (areas only); no conflict-of-interest check found | M |
| U-028 | Someone who knows a tenant's email keeps them locked out | Ten wrong tries every 15 minutes keep the login locked; staff can't unlock it, only a new invite clears it | Open | src/pms/accounts.py:417 _count_failure; :303 accept_invite clears locked_until | M |
| U-030 | An office of many staff behind one address, a few of them mistyping | 20 failures in 15 minutes refuse everyone at that address, right passwords too, until the oldest failure leaves the window | Open | src/pms/api/throttle.py:29; tests/api/test_throttle.py::test_an_address_that_failed_too_often_is_refused_whatever_it_tries shows a right password refused | M |
| U-032 | An attacker on IPv6 rotates addresses within their /64 | Each address counts alone, so the per-address limit doesn't slow them | Open | src/pms/api/throttle.py:82 address_of keys the whole address | M |
| U-034 | Behind a proxy with no trusted proxy set | Every visitor has the proxy's address: one person's failures lock out the whole company, and evidence keeps the proxy's address | Open | src/pms/api/app.py Settings.trusted_proxies defaults to ""; src/pms/api/signing.py:164 origin() | M |
| U-044 | A suspended company's open tab | The next request gets 401 "sign in first", then sign-in says suspended; the page itself never says why | Open | src/pms/accounts.py:454 session_for; src/pms/api/deps.py current() | M |
| U-046 | A suspended or closed company's calendar feeds, vendor links, signing links and public listings | Keep answering: only sign-in, sessions, resets and API keys check the status | Open | src/pms/api/calendar.py:282 FEED has no status; trust_pmc_status appears in src/pms/api only at ai_keys.py:186 | M |
| U-059 | A custom role that allows no area | Its holder changes nothing in an area, yet reads every page: contact details, notes, screenings, messages and the whole ledger download | Open | src/pms/api/deps.py:241 (reads skip the area check); src/pms/api/custom_roles.py:109 (no fewest areas) | M |
| U-065 | An API key made by an admin who later leaves or loses admin | The key keeps working until an admin takes it back | Open | src/pms/api/ai_keys.py:174 with_key checks only the revocation and the company's status | M |
| U-073 | An API key leaks | Reads every report of the company (tenants' names, balances) from anywhere: no expiry and no address allow list | Open | src/pms/api/ai_keys.py:174 | M |
| U-083 | The owner's name on file has a curly apostrophe or accents; they type a straight apostrophe, no accents, or a Mac's decomposed letters | Refused "That name doesn't match the name on your account": only case and spaces are folded | Open | src/pms/api/owner.py:397 same_name (casefold, no Unicode normalization) | M |
| U-084 | The owner on file is an LLC or a trust; the person approving types their own name | Refused; only the record's name matches | Open | src/pms/api/owner.py:397 | M |
| U-086 | An archived owner or tenant | Their login keeps signing in: archiving doesn't touch logins | Open | trust_check_archival (db/migrations/20261024000152_bill_lifecycle.sql) never names trust_logins; src/pms/accounts.py:454 | M |
| U-105 | A tenant in New York gives notice at 9 pm for that day | Refused as a past day: the database's today is UTC's, not the company's | Open | db/migrations/20261103000100_portals_two.sql:84 (clock_timestamp AT TIME ZONE 'UTC') | M |
| U-108 | One roommate uses up the lease's daily caps | Notices, files, requests and notes count per lease, so the other tenant is refused too | Open | src/pms/api/tenant.py:445 and :690-700 (per lease, not per tenant) | M |
| U-124 | A signer who asked for paper signs on paper | Nothing records a paper signature on that offer; staff must void it and add the lease by hand | Open | src/pms/api/signing.py:149; CLAUDE.md (a PMS e-sign offer takes its signature no other way) | M |
| U-127 | A signer types their name with accents dropped, another apostrophe, or a letter folded differently (ß, or É on a server whose database lowercases only ASCII) | The app compares with casefold and the database with lower(); they can disagree and the signature is refused | Open | src/pms/api/signing.py:305; db/migrations/20261020000097_esign.sql:239 | M |
| U-132 | A vendor link leaks | It never expires: it shows the addresses and which homes the vendor may let themselves into until staff take it back | Open | src/pms/api/vendor_links.py:1-10 (no expiry); db/migrations/20261018000086_vendor_links.sql | M |
| U-133 | A vendor, or whoever holds their link, says a job is done by mistake | The work order closes for good and the tenant's request with it; nothing reopens either | Open | tests/api/test_maintenance.py::test_nothing_follows_a_closed_work_order; db/migrations/20261018000086_vendor_links.sql | M |
| U-135 | Staff add a lease to a unit another staff member just archived (a stale page, or the API) | Nothing refuses it: the unit stays archived with a lease in effect and leaves the lists | Open | db/schema.sql:1951 trust_check_archival runs only on an archival; no archived check on trust_leases | M |
| U-137 | A bill entered for a vendor another staff member just archived | Only the picker hides archived vendors; the database takes the bill | Open | src/pms/api/setup.py:377-382; db/migrations/20261021000110_vendor_archivals.sql (no trigger on bills) | M |
| U-138 | Two staff enter the same tenant's check from two tabs | Two payments: each panel makes its own request id and nothing warns of a match, as bills and payment files do | Open | src/pms/api/rent.py (no duplicate check); compare src/pms/api/payment_files.py:161 | M |
| U-144 | A company name of about 180 characters (200 are allowed) | Invite and reset subjects pass the 200-character limit: the database refuses, a server error, and on reset only for an email that has a login (it tells who has one) | Open | src/pms/email.py:258 and :288 (not cut); db/migrations/20261013000042_owner_emails.sql:25; src/pms/api/setup.py:31 | M |
| U-154 | A screen reader opens a receipt, statement, adverse action notice or lease PDF | The PDFs carry no tags or language (NOT VERIFIED with a screen reader) | Open | no StructTreeRoot or set_lang anywhere in src/pms | M |
| U-052 | The only admin loses their authenticator | The server's operator prints a new invite with python -m pms.admin invite, which names nobody | Guarded | src/pms/admin.py:66; db/migrations/20261012000034_staff_roles.sql trust_check_staff_invite | M |
| U-079 | An owner approves a bill staff just voided | 409, a void being a decline row | Guarded | src/pms/api/owner.py:421 decide (UniqueViolation) | M |
| U-102 | A tenant who left pays online or sets up autopay | Refused | Guarded | src/pms/api/online_payments.py:321 and src/pms/api/autopay.py:181 still_on | M |
| U-115 | A tenant signs a renewal from the portal | Matched to its signer by the login's email; a signer listed under another address gets 403 "you aren't one of this renewal's signers" | Guarded | src/pms/api/offers.py:860 | M |
| U-128 | A signing link forwarded to someone else | They can sign by typing the name; the evidence keeps the address and browser | Guarded | src/pms/api/signing.py:164 origin(); tests/api/test_signing.py::test_the_evidence_keeps_the_address_and_browser_a_step_came_from | M |
| U-152 | A keyboard-only signer | The typed name is the signature; drawing is optional | Guarded | frontend/src/components/SignaturePad.tsx:5 | M |
| U-040 | A form posts text/plain to get past the JSON rule | 415 "send JSON" | Tested | tests/api/test_sign_in.py::test_a_change_must_be_sent_as_json | M |
| U-042 | Another site frames a portal page | CSP frame-ancestors none and X-Frame-Options DENY | Tested | tests/api/test_portal_pages.py::test_every_page_is_the_portal_with_a_strict_policy | M |
| U-043 | Sign-in to a suspended company | Refused with the reason, whatever the password | Tested | tests/api/test_company_status.py::test_nobody_signs_in_to_a_company_that_isnt_active | M |
| U-045 | A suspended company's API keys | Stop answering | Tested | tests/api/test_company_status.py::test_a_suspended_companys_api_keys_stop | M |
| U-058 | The server's command line pays out naming nobody | Not held to the roles, as designed | Tested | tests/test_staff_roles.py::test_with_nobody_named_money_is_not_held_to_a_role | M |
| U-061 | A new staff write path left out of the area map | The test fails until it is mapped or said to be no area's | Tested | tests/api/test_custom_roles.py::test_every_staff_write_is_in_an_area_or_said_not_to_be | M |
| U-075 | The calendar link of a disabled login | Reads as not found | Tested | tests/api/test_calendar.py::test_a_feed_stops_once_its_persons_login_is_disabled | M |
| U-077 | An owner approves the same bill from two tabs | The second gets "This bill was already approved or declined." | Tested | tests/api/test_owner.py::test_an_owner_decides_a_bill_once | M |
| U-080 | An owner declines a bill whose money was already taken | 409 "already being paid" | Tested | tests/api/test_owner.py::test_a_bill_whose_money_was_taken_is_not_declined_in_the_portal | M |
| U-091 | Staff record an approval for an owner with no login | Must name the bill's own owner, how and why, and the staff member | Tested | tests/api/test_staff_recorded_approvals_api.py::test_only_the_bills_owner_can_be_named | M |
| U-096 | A tenant renting at two properties (a linked record) | One sign-in shows both homes and acts on each lease as the record there | Tested | tests/api/test_tenant_records_api.py::test_the_tenant_acts_on_the_other_propertys_lease_as_their_record_there | M |
| U-097 | Staff invite the linked second record of a tenant | Refused | Tested | tests/api/test_tenant_records_api.py::test_a_record_linked_to_another_takes_no_invite_of_its_own | M |
| U-098 | A link and a login of one record made at the same moment | One waits and is refused | Tested | tests/test_tenant_records.py::test_a_login_waits_for_a_link_of_its_record_and_then_is_refused | M |
| U-101 | A tenant who left files a request, document, notice or note | Refused "You left this lease on ..." | Tested | tests/api/test_lease_people_api.py::test_a_tenant_who_left_reads_the_lease_and_files_nothing_more | M |
| U-111 | A tenant cancels a request as staff open its work order | One of them is refused in words | Tested | tests/api/test_request_details_api.py::test_a_cancel_as_staff_open_its_work_order_is_refused | M |
| U-112 | A tenant pays online during a payment hold | Refused, said as "paused" | Tested | tests/api/test_online_payments_api.py::test_a_lease_on_a_payment_hold_takes_no_payment_online | M |
| U-114 | A tenant looks for what staff wrote on their payment | Never shown | Tested | tests/api/test_receive_payment_api.py::test_a_tenant_never_sees_what_staff_wrote | M |
| U-116 | A tenant sends a file that isn't what it says, or for another lease | Refused | Tested | tests/api/test_portals_two_api.py::test_a_tenants_file_must_be_what_it_says_and_of_their_lease | M |
| U-117 | An applicant withdraws after being approved | Recorded; no screening, decision or offer after it | Tested | tests/test_application_withdrawals.py::test_an_approved_application_may_be_withdrawn_and_a_declined_one_not | M |
| U-121 | Another site submits an application for someone | Refused | Tested | tests/api/test_apply.py::test_another_site_cant_apply_for_someone | M |
| U-122 | A made-up application status token | Not found | Tested | tests/api/test_apply.py::test_a_made_up_status_token_is_not_found | M |
| U-123 | A signer asks for paper, then tries to sign | Refused through that link | Tested | tests/api/test_signing.py::test_a_signer_who_asks_for_paper_signs_nothing_through_that_link | M |
| U-125 | A signer signs twice, or uses a link past 30 days or of a voided offer | Refused | Tested | tests/api/test_signing.py::test_a_signer_signs_once; ::test_a_link_that_ran_out_or_whose_offer_was_voided_signs_nothing | M |
| U-126 | A void and a signature at the same moment | The signature waits and is refused | Tested | tests/test_esign.py::test_a_signature_waits_for_a_void_and_then_is_refused | M |
| U-129 | A vendor link taken back while the vendor has the page open | Their next step is refused | Tested | tests/api/test_vendor_links_api.py::test_a_new_link_replaces_the_old_and_a_taken_back_one_opens_nothing | M |
| U-130 | A work order reassigned while the first vendor has it open | Their step is refused | Tested | tests/test_vendor_links.py::test_only_the_vendor_last_assigned_and_only_while_the_link_stands | M |
| U-131 | A vendor opens another vendor's or a closed work order | Not found | Tested | tests/api/test_vendor_links_api.py::test_another_vendors_or_a_closed_work_order_is_not_found | M |
| U-134 | Staff assign a vendor whose insurance ran out | Refused | Tested | tests/test_work_order_details.py::test_a_vendor_whose_insurance_ran_out_is_not_assigned | M |
| U-141 | Names in Arabic, Chinese or another script the font lacks | Print as "?" on receipts, statements, notices, adverse action notices and certificates | Tested | tests/test_pdf_fonts.py::test_a_letter_the_font_has_prints_and_one_it_lacks_reads_a_question_mark | M |
| U-142 | A name starting with = or + in an export | Never a formula | Tested | tests/test_exports.py::test_csv_neutralizes_formulas_and_keeps_everything_else; ::test_excel_keeps_types_and_never_holds_a_formula | M |
| U-143 | A name or message with HTML in an email | Escaped | Tested | tests/test_email_html.py::test_text_is_escaped_so_a_name_or_a_message_adds_no_markup | M |
| U-150 | A screen reader in a side panel or confirm question | Focus stays inside and returns to what opened it | Tested | frontend/src/components/SidePanel.test.tsx "closes on Escape and on Close, giving focus back to what opened it" | M |
| U-151 | Keyboard-only use of a row's ⋯ menu | Opens from the keyboard, the arrow keys move, focus comes back | Tested | frontend/src/components/RowMenu.test.tsx "opens from the keyboard on its first action, moves with the arrow keys and gives focus back" | M |
| U-153 | Every page against WCAG 2.2 AA | axe checks every page the browser tests reach | Tested | frontend/e2e/portal.spec.ts "every page of the demo meets WCAG 2.2 AA as axe checks it, in plain words: staff's, an owner's, a tenant's and the public ones" | M |
| U-031 | A server run with several processes | Each process counts alone, so the sign-in limit is that many times larger | Open | src/pms/api/throttle.py docstring (counted per process) | L |
| U-037 | Sign out on one device while others stay signed in | Only that session ends; there is no list of sessions or "sign out everywhere" short of a new password | Open | src/pms/accounts.py:480 sign_out | L |
| U-038 | A shared office computer after sign-out | The browser keeps the company id and the last property scope | Open | frontend/src/App.tsx:101 PMC_KEY in localStorage; frontend/src/scope.ts | L |
| U-041 | People reach the server at an address other than PMS_PUBLIC_URL (www against bare) | Every change is refused "this request came from another site" | Open | src/pms/api/app.py:367 (Origin must equal settings.origin) | L |
| U-047 | Anyone with a company's id learns it is suspended | The status is told before any login is looked at or counted | Open | src/pms/accounts.py:360 sign_in | L |
| U-048 | Accepting an invite in a browser signed in as someone else | The cookie becomes the new person's; the old session stays open on the server | Open | src/pms/api/app.py:566 accept_invite sets a new cookie | L |
| U-074 | A draft proposed by a key that is taken back before staff approve it (uncommitted drafts work) | Approval doesn't look at the key, so it is done (NOT VERIFIED) | Open | src/pms/api/drafts.py has no trust_api_key_revocations check; db/migrations/20261105000300_drafts.sql checks the key only at insert | L |
| U-103 | A former tenant whose lease ended | Keeps signing in and sees every company-wide announcement from then on | Open | src/pms/api/announcements.py:145 FOR_TENANT (company-wide ones go to every login) | L |
| U-106 | Daily caps (messages, notices, files) reset at UTC midnight, 7 or 8 pm in New York | "Try again tomorrow" while it is still today for the tenant, then a fresh day hours early | Open | src/pms/api/tenant.py:445 given_today; src/pms/api/messages.py:106 | L |
| U-110 | Two requests sent at the same moment at the cap | Both pass: counted, then inserted, with no lock | Open | src/pms/api/tenant.py:690-700 | L |
| U-120 | The signer of a withdrawn application opens their link | Still agrees, opens the lease and signs; nothing comes of it and the page doesn't say why | Open | db/migrations/20261020000097_esign.sql:104 trust_signing_link_open; src/pms/api/signing.py:140 open_link (no withdrawal) | L |
| U-136 | An archival and a new lease at the same moment | The archival check takes no lock, so both can commit | Open | db/migrations/20261024000152_bill_lifecycle.sql trust_check_archival (no lock) | L |
| U-140 | Two staff save a person's contact details at once | The last save holds; neither is told | Open | src/pms/api/directory.py:108 record_contact (append-only, the latest holds) | L |
| U-145 | Property and unit names long enough | The deposit statement email's subject passes 200 characters: Send statement fails | Open | src/pms/notices.py:603 (one_line, not cut) | L |
| U-146 | A company name with a line break in it | Invite and reset subjects keep it; delivery fails on the header | Open | src/pms/email.py:258 (no one_line); src/pms/api/setup.py:31 Name allows line breaks | L |
| U-147 | A staff member added on the command line with a blank or very long name | add-staff takes it as typed | Open | src/pms/admin.py add-staff branch (no strip, no limit) | L |
| U-148 | Very long unbroken names in tables, on a phone | Most cells don't break words, so the page may scroll sideways (NOT VERIFIED) | Open | break-all is in only a few cells of frontend/src | L |
| U-149 | Staff search "Nguyen" for a name written with its accents | Not found: the search folds case, not accents | Open | src/pms/api/rentals.py:42 ILIKE pattern | L |
| U-155 | Right-to-left names beside figures in a table or receipt | Nothing sets the text's direction, so it may reorder around numbers (NOT VERIFIED) | Open | no dir attribute in frontend/src | L |
| U-035 | Wrong authenticator codes after a right password | Counted on the login (locks after ten), not on the address | Guarded | src/pms/accounts.py:390-396 | L |
| U-036 | Sign out, then the browser's back button | The page asks /api/me again, gets 401 and goes to sign-in; API answers are no-store | Guarded | frontend/src/App.tsx:100; src/pms/api/app.py Cache-Control no-store on /api/; frontend/src/components/Shell.test.tsx "signs out from the sidebar, back to the PMC's sign-in page" covers sign-out only | L |
| U-076 | A calendar link leaks | Anyone reads its homes, visits and work (no contact details) until it is taken back; it never expires | Guarded | src/pms/api/calendar.py:282 FEED; tests/api/test_calendar.py::test_a_new_link_takes_back_the_old_and_a_link_taken_back_stops | L |
| U-001 | One email signs in at two companies on one server | Each company has its own login; sign-in names the company (its link, else this browser's last); one company's password signs in to nothing else | Tested | tests/api/test_isolation.py::test_one_companys_login_doesnt_sign_in_to_another; frontend/src/pages/SignInPage.test.tsx "takes the PMC from the link, else from this browser's last sign-in" | L |
| U-004 | A made-up cookie, or a session past its 12 hours | 401 "sign in first" | Tested | tests/api/test_sign_in.py::test_no_session_and_a_made_up_cookie_are_refused; ::test_a_session_lasts_twelve_hours | L |
| U-005 | A tab left alone for an hour | Signed out at its next request | Tested | tests/api/test_idle_sessions.py::test_a_session_left_alone_for_the_hour_is_signed_out | L |
| U-008 | Staff disable a login | Its sessions end and it signs in no more, in one step | Tested | tests/api/test_invites.py::test_disabling_a_login_ends_its_sessions_and_sign_ins | L |
| U-012 | An invite link opened again after it was accepted, or after its 7 days | "this invite link can't be used: ask for a new one" | Tested | tests/api/test_invites.py::test_a_link_that_cant_be_used_says_so | L |
| U-018 | A staff member asks for a password reset at sign-in | Nothing is sent and the answer reads the same; an admin's new invite resets them | Tested | tests/api/test_password_reset.py::test_no_reset_goes_out_and_the_answer_is_the_same | L |
| U-020 | An owner resets a forgotten password from the email | A new link; the old password works until it is used | Tested | tests/api/test_password_reset.py::test_an_owner_resets_a_forgotten_password_from_the_emailed_link | L |
| U-021 | A reset asked for a disabled login, an unknown email or another company's | Nothing sent; the same answer | Tested | tests/api/test_password_reset.py::test_no_reset_goes_out_and_the_answer_is_the_same | L |
| U-022 | Someone fills a tenant's inbox with resets | Three per person an hour, ten per address an hour | Tested | tests/api/test_password_reset.py::test_at_most_three_resets_an_hour; tests/api/test_throttle.py::test_an_address_asks_for_only_so_many_password_resets | L |
| U-027 | Ten wrong passwords on one login | Locked 15 minutes, even for the right password | Tested | tests/api/test_sign_in.py::test_ten_failures_lock_the_login_even_for_the_right_password | L |
| U-029 | Sign-in refused for no login, a wrong password, a disabled or a locked login | One answer after the same work | Tested | tests/api/test_sign_in.py::test_every_refused_sign_in_reads_the_same | L |
| U-060 | An area taken from a role while its holder has a page open | Their next write is refused in words | Tested | tests/api/test_custom_roles.py::test_an_area_taken_from_a_role_is_taken_from_its_staff | L |
| U-092 | An owner changes their own contact details | Staff see the owner changed them | Tested | tests/api/test_portals_two_api.py::test_an_owner_changes_their_own_contact_details_and_the_record_says_they_did | L |
| U-104 | A tenant gives notice on a lease that already ended, or for a past day | Refused in words | Tested | tests/api/test_portals_two_api.py::test_the_database_takes_no_notice_on_an_ended_lease_or_from_a_tenant_who_left; ::test_a_notice_for_a_past_day_is_refused_in_words | L |
| U-107 | A tenant sends a 21st message, a sixth notice or a 21st note in a day | 429 with what to do | Tested | tests/api/test_portals_two_api.py::test_a_person_writes_at_most_so_many_messages_a_day; ::test_a_lease_takes_so_many_notices_a_day; tests/api/test_request_details_api.py::test_notes_come_twenty_a_day_at_most | L |

### Most pressing open, with the test that would close each

- U-009: a clerk whose custom role lacks "who can sign in" (and no admin) calls `POST /api/logins/<owner login>/disable` and is refused 403.
- U-010: an admin brings back a disabled owner's or tenant's login (or a new invite for it works), and the person signs in again.
- U-013: two invites for one login; the newer is accepted; the older then reads "can't be used" at view, accept and confirm.
- U-014 and U-015: an invite for a login that already has a password is never returned to staff in the answer (emailed only, or the person confirms), so staff can't sign in as an owner, tenant or another staff member.
- U-033: with `PMS_TRUSTED_PROXIES` set to the proxy's own address and `X-Forwarded-For: 198.51.100.9, 203.0.113.7`, the signature's evidence and the sign-in limiter use 203.0.113.7, and a test pins what "*" does.
- U-051: admins A and B, B's login disabled; A taking away their own admin is refused as the last admin who can sign in.
- U-085: after a property's ownership moves wholly to another owner, the former owner's `GET /api/owner/properties/<id>` reads as not found and lists no tenants.
- U-099: staff undo a wrong "same person" link, and the person no longer reads the other record's lease, receipts or files.
- U-100: a payment, file or rent change recorded after a tenant's departure is not in what that tenant reads of the lease.
- U-109: after ten requests in a day under one lease, an eleventh marked emergency is still filed and its on-call email written.

## Hosting, operations and the portal's failure states

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| H-008 | A payment's answer is lost, staff close the panel and record it again | The reopened panel makes a new request id, so the payment is recorded twice; Receive payment has no same lease, day and amount warning (payment files have one) | Open | frontend/src/pages/LeaseForms.tsx:109; src/pms/api/payment_files.py:161 has the check, src/pms/api/rent.py none | H |
| H-016 | The mail server is slow or down | After every write a background delivery takes a pooled connection and works through up to 100 emails at a 20 s SMTP timeout each; a handful of them fill the pool and the whole PMS answers busy | Open | src/pms/api/outbox.py:25-32; src/pms/email.py:128,610 | H |
| H-020 | The nightly run fails on a server that sends no email (compose sets none by default) | Nobody is told; the log is the only place it shows | Open | tests/test_reserve_and_failure_alerts.py::test_no_alert_while_the_server_sends_no_email pins the silence; docs/support.md "What does not reach anyone" | H |
| H-021 | The nightly hasn't run for days (its thread died, it fails every night, or it is off) | The Dashboard and Needs attention never say when rent last posted; rent, late fees and draws are silently not posted | Open | src/pms/api/today.py and src/pms/api/dashboard.py read no rent run's age | H |
| H-022 | PMS_NIGHTLY=off with no cron running python -m pms.nightly | Nothing posts by itself and no page or log line says the run is off | Open | src/pms/api/app.py:318-326; .env.example ("off" stops it) | H |
| H-027 | The disk fills | Postgres refuses writes; every save reads "the server answered 500"; nothing says the disk is full | Open | src/pms/api/app.py:406-437 (no handler for DiskFull) | H |
| H-028 | The disk fills, and the backups live on it | The dump fails, its .partial stays, and old dumps are pruned only after a dump succeeds, so the disk never frees itself | Open | scripts/backup-daily.sh:54-56; scripts/backup.sh:51 | H |
| H-031 | The daily backup fails (password changed, pg_dump error, disk) | "FAIL" goes to the backup container's log only: no admin email, no health status, nothing on the Dashboard | Open | scripts/backup-daily.sh:77,81-84; docs/support.md ("A backup that failed") | H |
| H-041 | A newer backup restored under an older tag, or the old tag checked out after migrations ran | Nothing compares schema_migrations to the code: the old app starts and posts money on a schema it was never tested on | Open | no schema version check in src/ (only scripts/backup.sh:155 prints the version) | H |
| H-055 | PMS_DATA_KEY changed while bank payments are on their way | The company's webhook answers 404, Stripe retries then stops; rent the bank paid never reaches the books; one log line | Open | src/pms/api/online_payments.py:680-683; src/pms/payments.py:551-556 | H |
| H-059 | PMS_PUBLIC_URL isn't the address people use (www or not, http or https, another domain) | Every change, sign-in included, is refused "this request came from another site"; nobody can sign in; start-up doesn't check | Open | src/pms/api/app.py:364-368 | H |
| H-060 | PMS_PUBLIC_URL left as .env.example's https://pms.example.com | The same refusal, and emailed links point at example.com | Open | .env.example (PMS_PUBLIC_URL prefilled) | H |
| H-067 | PMS_TRUSTED_PROXIES empty behind a proxy on another host or network | Every visitor has the proxy's address: 20 failed sign-ins by anyone lock the whole company out for 15 minutes, 10 resets an hour for everyone, signatures record the proxy's address | Open | src/pms/api/throttle.py:84-86 | H |
| H-068 | "*" trusted, with nginx's $proxy_add_x_forwarded_for as install.md says | uvicorn takes the leftmost address, which the visitor writes: the sign-in throttle is bypassed with a new forged address each try, and a signer's address in the evidence can be forged | Open | uvicorn 0.54 ProxyHeadersMiddleware (always_trust takes the first host); docs/install.md step 4 | H |
| H-089 | The server is down over a lease's own due day at a month's end (due the 28th, down the 28th to the 31st) | The next run charges only the new month, so that rent is never charged by itself, and nothing says so | Open | src/pms/nightly.py:116-118; db/schema.sql:1688-1720 (only the run's month) | H |
| H-097 | The everything export of a large company | Every table and every document's bytes are fetched and zipped in memory in one request; it can exhaust the process and take the PMS down for everyone | Open | src/pms/exports.py:433-491 | H |
| H-115 | A session signed out after an hour idle, page still open | Only the first /api/me sends people to sign in; afterwards every load and save says "sign in first" in notices and toasts, nothing signs them in, and the page test pins it | Open | frontend/src/App.tsx:100; frontend/src/pages/RentPage.test.tsx > says when no lease is listed, and when the leases can't be read | H |
| H-152 | Any error while drawing a page (an answer of an unexpected shape after an upgrade, a status the page doesn't know) | No error boundary: the whole portal goes blank white | Open | frontend/src/main.tsx (no boundary); e.g. frontend/src/pages/PayOnlineCard.tsx:105 | H |
| H-072 | A very large JSON body (hundreds of MB) to /api/apply or /api/sign-in, no sign-in needed | Refused 413 above 10 MB before it is read: at once when it says its length, counted as it streams otherwise; a 5 MB file as base64 still fits | Tested | tests/api/test_body_limit.py | H |
| H-003 | The database is down while staff open pages | Each request waits 30 s for a pool connection, then gets 503 "the PMS is busy just now: try again in a moment": it says busy, not down, and every click takes 30 s | Open | src/pms/api/deps.py:121-132 (PoolTimeout always read as busy); no test of a page with the database down | M |
| H-004 | The database is down when the portal first loads | Home renders nothing while /api/me waits 30 s (a white page), then shows one error line with no menu, no retry button and no word of what to do | Open | frontend/src/App.tsx:96-109 | M |
| H-005 | The database restarts in the middle of the day | The pool has no connection check, so each idle pooled connection fails once after the restart: those requests answer 500 and the portal says "the server answered 500" | Open | src/pms/api/app.py:310-311 (ConnectionPool with no check=) | M |
| H-011 | A request waits on a lock (a big company's nightly run, another staff member's lease work, a migration) | No statement or lock timeout in the database session and no timeout on the portal's fetch: the button stays busy or the page says Loading with no end | Open | src/pms/api/app.py:298-306 (configure sets only the role and jit); frontend/src/api.ts:36-50 (no AbortController) | M |
| H-012 | A deadlock or serialization failure in a request | No handler for it: answered 500, read as "the server answered 500" instead of "try again" | Open | src/pms/api/app.py:406-437 (handlers for Refused, validation, DataError, InsufficientPrivilege only) | M |
| H-015 | Stripe answers slowly at checkout | The checkout request holds a pooled database connection for up to 20 s while it calls Stripe; ten tenants paying at once fill the pool of 10 and staff get "busy" | Open | src/pms/api/online_payments.py:333-337; src/pms/payments.py:183,201 | M |
| H-017 | The mail server blips for a few minutes | Attempts are not spaced: five can run back to back after five writes, then the email (a receipt, a notice) is given up for good | Open | src/pms/email.py:57,598 (counts attempts, no time between them) | M |
| H-023 | /health while the nightly, email or migrations are broken | Answers ok on SELECT 1, so an uptime check stays green | Open | src/pms/api/app.py:468-475 | M |
| H-029 | Years of month-first dumps | Each month's first is kept for good on the database's own disk; the folder grows without end | Open | scripts/backup-daily.sh:39-49 | M |
| H-030 | Container logs over months | compose sets no log size limit, so the access log grows on the server's disk | Open | compose.selfhost.yaml (no logging: section on any service) | M |
| H-035 | The dumps never leave the server | Copying them off, encrypted, is the company's job; nothing checks it was set up | Open | docs/backup.md "Where" | M |
| H-042 | Restoring the books on a new server | docs/backup.md never says the same PMS_DATA_KEY is needed; without it account numbers, tax IDs and the company's Stripe keys are lost | Open | docs/backup.md (no mention of PMS_DATA_KEY) | M |
| H-044 | An upgrade by install.md's `up -d --build` | The old app keeps serving, and its nightly may post, while migrations run, and may keep running if migrate fails; release.md says to stop the app first (NOT VERIFIED how compose acts) | Open | docs/install.md "Upgrading" against docs/release.md "Upgrading a company" step 4 | M |
| H-046 | The new app started on a schema without the release's migrations (app started alone) | Endpoints using new tables answer 500; /health stays ok | Open | src/pms/api/app.py:468-475 | M |
| H-050 | PMS_DB_PASSWORD changed in .env after the first start | Postgres keeps the first password; migrate can't sign in, so the app never starts; the docs don't warn | Open | compose.selfhost.yaml:15,31 | M |
| H-056 | The wrong PMS_DATA_KEY at start | Nothing checks at start that sealed values open; it shows only when an ACH file, a 1099 or a payment is needed | Open | src/pms/api/app.py create_app (no check) | M |
| H-058 | PMS_DATA_KEY leaked | No tool re-seals what it sealed under a new key | Open | src/pms/sealed.py:1-12 (docstring: no tool yet) | M |
| H-061 | PMS_PUBLIC_URL unset | Defaults to http://localhost:8000: changes refused, the fake partners not refused, emailed links to localhost | Open | src/pms/api/app.py:133; src/pms/deployment.py:58-60 | M |
| H-062 | PMS_PUBLIC_URL starts http:// on a real server | Fake partners are allowed and no HSTS is sent | Open | src/pms/deployment.py:58-60 | M |
| H-064 | The company moves to another web address | Links already emailed (invites, resets, signing), calendar feeds and Stripe's webhook keep the old one; nothing tells staff to update them | Open | src/pms/api/app.py invite URL from settings.public_url; docs/install.md "Payments online" | M |
| H-066 | Staff open the server over plain http (the cookie is Secure) | Sign-in answers OK, the browser drops the cookie, the page goes back to sign-in with no word why | Open | frontend/src/pages/SignInPage.tsx:55-56; frontend/src/App.tsx:100-102 | M |
| H-071 | An operator publishes the port on 0.0.0.0 to skip the proxy, keeping "*" | Anyone sets their own address; nothing refuses it | Open | compose.selfhost.yaml:51-56 (a comment only) | M |
| H-073 | nginx in front with its default 1 MB body limit | A 5 MB invoice, photo, lease PDF or statement file is refused by nginx; the portal says "the server answered 413" | Open | docs/install.md step 4 (no client_max_body_size) | M |
| H-074 | A long import or export past the proxy's timeout (nginx 60 s) | A 504 page while the server goes on and commits; the retry is refused as came in before | Open | docs/install.md step 4 | M |
| H-085 | The server clock jumps forward (VM resumed, NTP fix) | The scheduler runs a day that hasn't come: late fees, next month's rent and draws early; nothing compares it with the database's clock | Open | src/pms/nightly.py:288-300 | M |
| H-090 | The server is down for a whole month | That month's rent and recurring charges are never charged by the nightly; Run rent now with that month's date catches up, but nothing tells staff to | Open | db/schema.sql:13117 trust_run_rent; docs/support.md | M |
| H-091 | An admin's Run rent now with a future due date | That month's rent is charged now; only the late-fee day is checked | Open | src/pms/api/rent.py:1300-1301 | M |
| H-093 | A company with more than 200 open bills on Pay bills | Only the first 200 load and the page says nothing of the rest | Open | frontend/src/pages/PayBillsPage.tsx:75 | M |
| H-094 | A large company on Pay owners and the Owners page | Every owner account loads at once, unpaged | Open | frontend/src/pages/PayOwnersPage.tsx:63-66 | M |
| H-095 | A report preview of a large company | Every row is fetched into memory, then 500 are sent | Open | src/pms/api/reports.py:26,49; src/pms/exports.py:103 | M |
| H-096 | A report download (a year's general ledger as PDF) of a large company | Built whole in memory with no time limit; the speed ceilings were measured at 1,000 units only (NOT VERIFIED beyond) | Open | ci/thresholds.toml [speed] | M |
| H-098 | The ledger zip of years of transfers | Built in memory in one request | Open | src/pms/exports.py:359-375 | M |
| H-099 | The everything export's sort | Each table is sorted by its row as JSON, documents' bytes included, holding a connection for a long time | Open | src/pms/exports.py:460-462 | M |
| H-101 | A large company's nightly emails | Delivered 100 per look: overnight 100 an hour, so reminders may arrive late | Open | src/pms/email.py:610; src/pms/nightly.py:313 | M |
| H-102 | Ten staff at once beyond 1,000 units | Not measured (NOT VERIFIED) | Open | tools/speed.py; ci/thresholds.toml:131-135 | M |
| H-104 | A company suspended while people are signed in | Their pages answer "sign in first", with no word of the suspension until they sign in again | Open | src/pms/accounts.py:466; same test asserts the 401 | M |
| H-106 | A suspended company's tenants on autopay | Autopay goes on debiting them, and reminders and late notices go out, while they can't sign in to stop it | Open | src/pms/nightly.py:167; no status check in src/pms/autopay.py or src/pms/notices.py | M |
| H-107 | A closed or suspended company's public pages | The vacancy page, apply links, signing links, vendor links and calendar feeds keep working | Open | no trust_pmc_status in src/pms/api/listings.py, signing.py, vendor_links.py, calendar.py | M |
| H-109 | The network drops while saving | The toast and the notice say "TypeError: Failed to fetch" (the browser's words) | Open | frontend/src/api.ts:84-86; frontend/src/pages/useRecord.ts:19 | M |
| H-110 | The network drops while a page loads | Most pages show the same raw words in place of the list, with no retry | Open | frontend/src/pages/RentPage.tsx:32-34 (same helper on most pages) | M |
| H-112 | The sign-in page on a one-company server whose database is down | GET /api/sign-in fails and the page says "Open the sign-in link your property manager sent you" instead of that the server is down | Open | frontend/src/pages/SignInPage.tsx:25-27,91-96 | M |
| H-113 | A browser holding an old company id (server rebuilt, or a demo once opened at that address) | Every sign-in is refused as a wrong password; the server's own company is passed over | Open | frontend/src/pages/SignInPage.tsx:15-17 | M |
| H-116 | Signing in again after that | The page they were on is not kept: they land on the Dashboard | Open | frontend/src/App.tsx:101-102; frontend/src/pages/SignInPage.tsx:56 | M |
| H-117 | The lease page's re-read (on focus or after a save) fails once it has loaded | The error is kept but not shown; the old balance stays on screen | Open | frontend/src/pages/LeasePage.tsx:122,139-145 | M |
| H-120 | Run rent now's latest runs can't be read | Shown as no runs | Open | frontend/src/pages/RentPage.tsx:179 | M |
| H-125 | Pay bills' owner cash can't be read | The holds and after columns vanish silently; staff pay without seeing the cash (the books still refuse a short one) | Open | frontend/src/pages/PayBillsPage.tsx:81 | M |
| H-128 | Pay owners' prepared draws can't be read | The Owner draws ready card is silently missing | Open | frontend/src/pages/PayOwnersPage.tsx:72-73 | M |
| H-130 | Banking's company fees can't be read | Treated as none: the fee withdrawal and its take-out-by day vanish | Open | frontend/src/pages/ReconcilePage.tsx:71 | M |
| H-131 | Banking's online payments waiting can't be read | The card is hidden; payments paid during a hold or returns staff must act on look like none | Open | frontend/src/pages/OnlinePaymentsCards.tsx:105 | M |
| H-136 | A report's owner list can't be read | Its reason is wiped by the preview's reset; the Owner picker is empty and an owner's report says Choose what the report covers for good | Open | frontend/src/pages/ReportPage.tsx:52-66 | M |
| H-137 | A download refused (report PDF or Excel, ledger zip, receipt, statement, ACH file) after sign-out, a bad day, a server error or a proxy timeout | Each is a plain link, so the browser leaves the portal for a raw JSON or proxy error page; a non-admin is shown the everything export and gets a raw 403 | Open | frontend/src/pages/ReportPage.tsx:86-88; frontend/src/pages/DataExportPage.tsx:22,32 | M |
| H-138 | The owner portal's properties can't be read | Overview and Properties say Loading for good; the reason shows only on Statements | Open | frontend/src/pages/OwnerHome.tsx:88,125-127,188 | M |
| H-141 | A tenant's emergency request when the network drops | Raw browser words; a retry after a lost answer files it twice and emails on-call staff twice (no request id) | Open | frontend/src/pages/TenantHome.tsx:281-311; src/pms/api/tenant.py (no key on new requests) | M |
| H-142 | Pay online's fee quote can't be read | The fee line is blank and Pay online stays on; a tenant who pays the fee doesn't see it before leaving | Open | frontend/src/pages/PayOnlineCard.tsx:46-51 | M |
| H-145 | A checkout whose report never arrives (webhook wrong or keys changed) | It counts as on its way for good, so a balance autopay pays less every month | Open | src/pms/autopay.py:125-136 (no age limit) | M |
| H-146 | The tenant's lease card can't read their autopays | Shown as none: the tenant doesn't see an autopay that is on | Open | frontend/src/pages/TenantLeaseCard.tsx:67-68 | M |
| H-149 | An applicant's answer lost after it committed | A retry makes a second application and the first status link is lost; network errors read raw; the form is lost on reload | Open | frontend/src/pages/ApplyPage.tsx:44-61 (no request id) | M |
| H-159 | Ending a lease when the break fee can't be read | No break fee is offered, silently, and the lease ends without it | Open | frontend/src/pages/LeasePeople.tsx:80-82 | M |
| H-026 | A company's run fails at 00:01 (the database restarting) | That company waits for its next day unless an admin uses Run rent now; support.md says so | Guarded | src/pms/nightly.py:200-206; docs/support.md "What to do" | M |
| H-043 | A migration fails halfway through an upgrade | dbmate rolls that one back, the ones before stay; migrate exits 1 and the new app doesn't start | Guarded | compose.selfhost.yaml:62-64; docs/release.md "Migrations" | M |
| H-053 | PMS_DATA_KEY changed, then a tax ID is revealed or the 1099 export taken | 409 "that tax ID doesn't open under this server's key" | Guarded | src/pms/api/tax_ids.py:115-119 | M |
| H-019 | The mail server is down, so the admins' alert can't go either | The alert is given up too; only the log says so | Tested | tests/test_reserve_and_failure_alerts.py::test_an_alert_given_up_on_is_told_to_nobody | M |
| H-024 | One company's run fails every night with the same bug | Its admins are emailed once a day while the server sends email; its rent waits | Tested | tests/test_reserve_and_failure_alerts.py::test_each_admin_hears_once_a_day_when_the_nightly_run_fails | M |
| H-032 | PMS_BACKUP_AT isn't HH:MM | Refused in words; the container then restarts in a loop and nobody is told | Tested | tests/tools/test_backup_daily.py::test_a_time_of_day_that_isnt_hh_mm_is_refused | M |
| H-052 | PMS_DATA_KEY changed, then an ACH file is asked for | Refused, naming the payee whose number doesn't open | Tested | tests/api/test_payouts_api.py::test_an_ach_file_opens_its_numbers_only_under_the_key_they_were_sealed_with | M |
| H-054 | PMS_DATA_KEY changed, and the company entered its own Stripe keys | No payment online; it never falls back to the server's keys | Tested | tests/api/test_payment_keys_api.py::test_keys_that_dont_open_here_take_no_payment_and_never_fall_back | M |
| H-075 | The PMS restarting behind the proxy | 502 reads "the server answered 502", with no word that it is restarting | Tested | frontend/src/api.test.ts > names the status when the answer has no reason | M |
| H-014 | The portal gets a 503 with Retry-After | Shows the words; never retries by itself, the person clicks again | Open | frontend/src/api.ts:36-50 | L |
| H-045 | A migration takes a table lock while the app serves | Requests to that table wait with no lock_timeout; pages spin until it finishes | Open | src/pms/api/app.py:298-306 | L |
| H-082 | The process is killed after an email went out and before its delivery committed | It is sent again later | Open | src/pms/email.py:653-682 | L |
| H-087 | The app's clock and the database's differ (Postgres on another host) | The nightly uses the app's clock, pages use the database's now(); near midnight they name different days | Open | src/pms/nightly.py:288; src/pms/days.py today() | L |
| H-100 | A large company's nightly run | One transaction; staff ending a lease or running rent at that time wait | Open | src/pms/nightly.py:107 | L |
| H-108 | A closed company's outbox | Emails already waiting still go out | Open | src/pms/email.py:610 (no status filter) | L |
| H-118 | The rent roll's refresh after a save fails | The old list stays with no word | Open | frontend/src/pages/RentPage.tsx:104 | L |
| H-132 | Month-end's bill suggestions can't be read | Silently none | Open | frontend/src/pages/ReconcilePage.tsx:524-525 | L |
| H-134 | The Deposits tab can't be read | The reason at the top while both cards keep saying Loading | Open | frontend/src/pages/BankAccountTabs.tsx:186-206,239 | L |
| H-140 | The tenant portal's leases can't be read | The reason at the top and Loading below for good | Open | frontend/src/pages/TenantHome.tsx:258-264,330-331 | L |
| H-151 | An invite's answer lost after the password was set | The retry says "this invite link can't be used: ask for a new one" though they can sign in | Open | src/pms/accounts.py:278 | L |
| H-157 | A server error (500) | Plain "the server answered 500" with no reference to give an admin to find its traceback | Open | src/pms/api/app.py (no handler or request id) | L |
| H-160 | The docs of the nightly's time | .env.example and app.py's Settings still say 06:00 UTC; it runs just after each company's midnight | Open | .env.example (PMS_NIGHTLY); src/pms/api/app.py Settings.nightly comment | L |
| H-034 | The backup time comes before a Pacific or Hawaii company's night | Docs say to set PMS_BACKUP_AT=11:00; a server with companies in several zones has one time for all | Guarded | docs/install.md "Every day: backups"; compose.selfhost.yaml:79 | L |
| H-039 | A restore with Postgres 17 client tools | Refused unless pg_dump, pg_restore and psql are 16 | Guarded | scripts/backup.sh:24-33 | L |
| H-040 | A backup taken on an older release restored under a newer one | Step 5 of the restore applies the newer release's migrations | Guarded | docs/backup.md "Getting the books back" step 5 | L |
| H-048 | A skipped release brought a required setting (PMS_APP_DB_PASSWORD) | compose refuses to start and names the setting | Guarded | compose.selfhost.yaml:32,44 | L |
| H-079 | Two processes and the throttles | Sign-in failures, resets and API key rates are counted per process, so each allows twice as much | Guarded | src/pms/api/throttle.py:13-16 | L |
| H-080 | A process finds the nightly lock held | It marks those companies done for the day; one the holder failed waits a day | Guarded | src/pms/nightly.py:300-308 | L |
| H-086 | The server clock jumps back | An earlier day charges nothing new (once per date) | Guarded | src/pms/nightly.py:296 | L |
| H-111 | Signing in with the network down | "the server can't be reached" | Guarded | frontend/src/pages/SignInPage.tsx:61 | L |
| H-124 | Receive payment's housing authorities can't be read | The reason in place of the form | Guarded | frontend/src/pages/ReceivePayment.tsx:100 | L |
| H-127 | Pay bills sent again after a lost answer | The request id is kept until success and a bill is paid in full once | Guarded | frontend/src/pages/PayBillsPage.tsx:135; src/pms/api/payables.py:1385 | L |
| H-148 | A signer's answer lost after they signed, and they press again | "you have signed this lease already" as an error, the form still showing until reload | Guarded | src/pms/api/signing.py:148 | L |
| H-001 | The database isn't up yet when the app starts | The app starts anyway (pool opened without waiting); /health answers 503 "database unavailable" | Tested | tests/api/test_sign_in.py::test_health_says_when_the_database_is_unavailable | L |
| H-002 | The database isn't up when the nightly scheduler starts | The look fails, is logged, and is tried again 300 s later | Tested | tests/test_nightly.py::test_a_scheduler_that_cannot_reach_the_database_tries_again | L |
| H-006 | A request's connection dies before it commits | The transaction rolls back whole; the same request sent again lands once | Tested | tests/test_faults.py::test_a_payment_cut_off_before_it_commits_leaves_nothing_and_its_retry_lands_once | L |
| H-007 | A payment's answer is lost after it committed and staff press Record payment again in the same panel | The panel kept its request id, so the second send records nothing new | Tested | tests/api/test_rent.py::test_sending_a_payment_again_records_it_once (id kept until success: frontend/src/pages/LeaseForms.tsx:109,147-148) | L |
| H-009 | The nightly run is cut off midway (server killed, connection dropped) | Nothing of the night stands; the next run does it all once | Tested | tests/test_faults.py::test_a_nightly_run_cut_off_midway_leaves_the_night_undone_and_the_next_run_does_it_all_once | L |
| H-010 | Pay owners is cut off before it commits | No one is paid; the retry with the same request id pays each once | Tested | tests/test_faults.py::test_pay_owners_cut_off_pays_no_one_and_its_retry_pays_each_once | L |
| H-013 | Every pool connection is busy (many staff on slow pages) | 503 in words with Retry-After 5; once one frees the same request is answered | Tested | tests/api/test_refusals.py::test_a_request_that_finds_every_connection_busy_is_told_so_in_words | L |
| H-018 | An email is given up after its fifth failure | Each admin with a login is emailed once a day | Tested | tests/test_reserve_and_failure_alerts.py::test_admins_hear_once_a_day_when_an_email_is_given_up_on | L |
| H-025 | One company's run fails | The others still run and the lock is let go | Tested | tests/test_nightly.py::test_one_company_that_fails_leaves_the_others_to_run | L |
| H-033 | A dump fails halfway | It never takes the place of a good one and nothing is removed | Tested | tests/tools/test_backup_daily.py::test_a_dump_that_cant_be_read_back_fails_and_removes_nothing | L |
| H-036 | A restore into a database that already holds something | Refused | Tested | tests/tools/test_check_backup.py::test_a_restore_never_writes_over_a_database_that_holds_anything | L |
| H-037 | A restore whose ledger doesn't tie out | Fails and says so | Tested | tests/tools/test_check_backup.py::test_a_restore_whose_ledger_does_not_tie_out_fails | L |
| H-038 | A restore on a new server that lacks the PMS's roles | The roles are made without logins, grants come from the backup | Tested | tests/tools/test_check_backup.py::test_a_restore_makes_the_roles_the_books_grant_to_when_the_server_lacks_them | L |
| H-047 | An upgrade that skips releases | The migrations upgrade a database holding money from earlier points of main | Tested | tests/tools/test_check_upgrade.py::test_migration_that_keeps_the_money_upgrades_cleanly | L |
| H-049 | Rolling back a ledger migration | Refused, nothing changed | Tested | tests/tools/test_check_rollback.py::test_irreversible_newest_migration_refuses_and_changes_nothing | L |
| H-051 | PMS_APP_DB_PASSWORD changed | The next start sets the new one on pms_app | Tested | tests/tools/test_app_login.py::test_run_again_it_takes_the_new_password_and_drops_any_other_membership | L |
| H-057 | No PMS_DATA_KEY | No account numbers or tax IDs are kept, and the PMS says so | Tested | tests/api/test_payouts_api.py::test_a_server_without_a_key_keeps_no_account_numbers | L |
| H-063 | PMS_PUBLIC_URL ends in a slash | Links come out without a double slash | Tested | tests/test_logins.py::test_the_command_line_adds_staff_and_prints_their_invite | L |
| H-065 | A fake partner named on an https server | The app refuses to start | Tested | tests/test_deployment.py::test_the_app_refuses_to_start_with_a_fake_partner_over_https | L |
| H-069 | Behind a trusted proxy that writes the header | The evidence keeps the signer's address | Tested | tests/api/test_signing.py::test_behind_a_trusted_proxy_the_evidence_keeps_the_signers_address | L |
| H-070 | compose publishes the app on 127.0.0.1 only | Trusting "*" is safe there | Tested | tests/tools/test_selfhost_compose.py::test_the_app_trusts_the_proxy_only_because_the_server_alone_reaches_it | L |
| H-076 | Two app processes, or the command while the API runs | One nightly run at a time; the other leaves the work to it | Tested | tests/test_nightly.py::test_one_run_at_a_time_another_leaves_the_work_to_it | L |
| H-077 | Two processes deliver email at once | Each email is sent once | Tested | tests/test_email.py::test_two_deliveries_at_once_send_each_email_once | L |
| H-078 | An invite link held by the other process | Left to it, then given up once | Tested | tests/test_email_everywhere.py::test_a_link_another_process_holds_is_left_to_it_then_given_up_on_once | L |
| H-081 | A restart loses the held invite, reset or signing links | Each such email is given up once with words; staff send a new one | Tested | tests/test_email_everywhere.py::test_a_link_is_held_no_longer_than_its_minutes | L |
| H-083 | A restart while autopay payments go to the partner | The partner's own key stops a second one; the rest go at the next run | Tested | tests/test_autopay.py::test_each_payment_goes_to_the_partner_once_and_waits_while_it_is_down | L |
| H-084 | A restart runs every company's night again | Nothing is charged twice | Tested | tests/test_nightly.py::test_the_scheduler_runs_at_start_then_waits_for_its_time | L |
| H-088 | Each company's midnight across time zones | The scheduler looks a minute past every hour and runs each company for its own day | Tested | tests/test_nightly.py::test_each_company_runs_for_its_own_day | L |
| H-092 | A 20,000-unit company's rent roll and tenants | Paged, each page worked out alone | Tested | tests/api/test_list_pages.py::test_the_rent_roll_a_lease_a_page_is_the_whole_roll_in_order | L |
| H-103 | Signing in to a suspended or closed company | Refused in words, the same whatever the password | Tested | tests/api/test_company_status.py::test_nobody_signs_in_to_a_company_that_isnt_active | L |
| H-105 | A closed company's nightly run | Stops; a suspended one's goes on | Tested | tests/api/test_company_status.py::test_a_closed_companys_nightly_run_stops_and_a_suspended_ones_goes_on | L |
| H-114 | Too many failed sign-ins from one address | 429 with the minutes to wait, shown on the sign-in page | Tested | tests/api/test_throttle.py::test_a_waiting_address_is_told_in_minutes_with_retry_after | L |
| H-119 | The rent roll can't be read | Says why in place of the list; the rest of the page works | Tested | frontend/src/pages/RentPage.test.tsx > says when no lease is listed, and when the leases can't be read | L |
| H-121 | One of the Dashboard's three reads fails | Its reason at the top, the other parts still show; only the last reason, no retry | Tested | frontend/src/pages/NeedsAttention.test.tsx > says what went wrong | L |
| H-122 | Receive payment's lease can't be read | Goes back to the search and says so | Tested | frontend/src/pages/RentPage.test.tsx > goes back to the search for another lease, and says when one can't be read | L |
| H-123 | Receive payment refused (a hold, a closed period) | The panel stays open with the reason | Tested | frontend/src/pages/LeasePage.test.tsx > keeps a refused payment's panel open with the reason, and says when the lease is on a hold | L |
| H-126 | Pay bills refused | Says why and pays none | Tested | frontend/src/pages/PayBillsPage.test.tsx > chooses every bill and none, and says why a payment was refused | L |
| H-129 | Pay owners refused | Says why and pays none | Tested | frontend/src/pages/PayOwnersPage.test.tsx > says why the payouts were refused, and pays none | L |
| H-133 | Month-end's prepare refused | Shows the refusal | Tested | frontend/src/pages/ReconcilePage.test.tsx > prepares a period whose books lack no line, and shows a refusal | L |
| H-135 | A report refused | Says why | Tested | frontend/src/pages/ReportPage.test.tsx > asks for what is missing, says how many lines are left out, and says why it was refused | L |
| H-139 | An owner's bill decision refused | Says why | Tested | frontend/src/pages/OwnerBills.test.tsx > says why a decision was refused | L |
| H-143 | The payments partner doesn't answer at checkout | "We couldn't reach our payments partner. Try again in a few minutes."; nothing started | Tested | tests/api/test_online_payments_api.py::test_a_partner_that_does_not_answer_starts_no_payment | L |
| H-144 | Pay online while off or on a hold | The company's own words, or why it is paused | Tested | frontend/src/pages/PayOnlineCard.test.tsx > says to contact the manager when the company wrote nothing, and why a lease on hold can't pay | L |
| H-147 | A signing step refused | Says why | Tested | frontend/src/pages/SignPage.test.tsx > says why a refused step was refused | L |
| H-150 | An apply link closed | Says so | Tested | frontend/src/pages/ApplyPage.test.tsx > says so when the link is closed | L |
| H-153 | A save clicked twice | The button is busy and sends once | Tested | frontend/src/components/Form.test.tsx > shows the submit button busy while the save runs, and can't send it twice | L |
| H-154 | A 422 from the API's own field checks | "check what you entered and try again", not naming the field | Tested | frontend/src/api.test.ts > says what to do about input the server couldn't read | L |
| H-155 | A refused change anywhere | Its reason as a toast too | Tested | frontend/src/components/Toaster.test.tsx > raises a refused change's reason, wherever the page shows it | L |
| H-156 | A staff write outside their custom role | 403 "your roles don't let you change ...: an admin can add it" | Tested | tests/api/test_custom_roles.py::test_a_role_limits_what_its_staff_change_and_not_what_they_see | L |
| H-158 | The search fails | Lists nothing, with no word it failed | Tested | frontend/src/components/SearchBox.test.tsx > leaves a new-tab click to the browser, clears on Escape, and lists nothing on a failure | L |

### Most pressing open, with the test that would close each

- H-115 (idle sign-out): send any 401 from api.get or api.post to /sign-in with the current path kept (and back after sign-in, H-116); a vitest that a 401 on a loaded page navigates to sign-in and returns, replacing the RentPage test that pins "sign in first".
- H-152 (blank portal): an ErrorBoundary around App with a reload button; a vitest rendering a page whose answer has an unknown status.
- H-059 and H-060 (wrong PMS_PUBLIC_URL): refuse to start, or log loudly, when PMS_PUBLIC_URL is the example's or unset with PMS_COOKIE_SECURE on; answer the origin refusal with both origins named; a test in tests/test_deployment.py.
- H-068 and H-067 (proxy addresses): trust named proxies, not "*", in compose (the proxy's own address), and tell nginx users to set X-Forwarded-For to $remote_addr; a test that a forged leftmost X-Forwarded-For doesn't reset the sign-in throttle.
- H-021, H-022 and H-020 (silent nightly): a Needs attention row when the company's latest trust_rent_runs is over a day old, and /health saying so; tests in tests/api/test_today.py.
- H-031 (silent backups): the backup job writes its last good dump's day where the app reads it, and the Dashboard or /health says when it is over a day old; a test in tests/tools/test_backup_daily.py.
- H-089 (rent missed over a month's end): the run charges last month's due days not yet charged when it runs early in the next month; a property test in tests/properties that a run after any gap charges what nightly runs would have.
- H-008 (payment recorded twice): warn before recording a payment of the same lease, day and amount already recorded, as payment files do; an API test and a vitest on Receive payment.
- H-016 (mail server fills the pool): deliver in one background worker per process on its own connection, with a time between attempts (H-017); a test that a slow sender leaves the pool free.
- H-072 and H-097 (memory): a request body limit in the app (beyond the 5 MB files) and the everything export streamed to a temporary file; tests in tests/api that an oversized body is 413 and that the export doesn't hold the documents in memory.

## The AI link: drafts, events, webhooks and the MCP server

| ID | Edge case | What the PMS does | Status | Evidence | Risk |
|---|---|---|---|---|---|
| A-030 | DNS that changes between the check and the connection (rebinding) | The request goes to the address checked, the certificate checked against the host | Guarded | src/pms/webhooks.py _Pinned | H |
| A-032 | A receiver that redirects (to an internal address) | Not followed: a 3xx is a failed attempt | Guarded | src/pms/webhooks.py post | H |
| A-001 | Software proposes a draft with a key that was taken back | 401 at the API; the database refuses a draft of a key taken back whatever the app does | Tested | tests/api/test_drafts.py::test_only_a_standing_key_proposes_and_a_key_opens_no_staff_page; tests/test_draft_rules.py::test_a_draft_needs_a_standing_key_of_an_active_company | H |
| A-002 | A suspended or closed company's key proposes a draft | Refused by the draft's trigger: nothing is proposed to a company that isn't active | Tested | tests/test_draft_rules.py::test_a_draft_needs_a_standing_key_of_an_active_company | H |
| A-003 | A draft names another company's tenant, lease, unit or work order | Refused 422 naming the record; an edit by staff is checked the same way | Tested | tests/api/test_drafts.py::test_a_draft_names_only_the_companys_own_records_each_once; tests/api/test_drafts.py::test_an_edit_keeps_the_drafts_kind_and_names_only_the_companys_records | H |
| A-004 | A draft carries an email address or a phone in an extra field | Refused: a proposal says only what its kind takes | Tested | tests/api/test_drafts.py::test_a_malformed_proposal_is_refused | H |
| A-005 | Software proposes a payment, a charge, a bill or a draw | No such kind: refused 422 by the API and by the table's check | Tested | tests/api/test_drafts.py::test_a_malformed_proposal_is_refused; tests/test_draft_rules.py::test_a_drafts_kind_is_one_that_moves_no_money_and_its_proposal_says_it | H |
| A-007 | A staff member without the area approves a draft (repairs only, approving an email) | Refused 403 naming the area; nothing is written; they may still decline it | Tested | tests/api/test_drafts.py::test_approving_takes_the_area_of_what_the_draft_does_and_declining_takes_none | H |
| A-014 | A bug approves a draft without doing it, or does it without approving | Refused at commit: approved means done, and only approved | Tested | tests/test_draft_rules.py::test_a_draft_is_decided_once_by_a_named_staff_member_and_approved_means_done | H |
| A-015 | The AI reads what staff changed a draft to (a note's words) | Never: the action table is out of its reach; it learns only that it was edited | Tested | tests/api/test_drafts.py::test_staff_change_a_draft_before_approving_it_and_it_is_done_as_changed; tests/test_privileges.py | H |
| A-020 | An event of a transaction still running when the reader asks | Not read yet, and read once it commits: the reader never skips one, even one that began first and finished last | Tested | tests/test_event_rules.py::test_a_reader_never_skips_an_event_of_a_transaction_that_finished_later; tests/properties/test_event_properties.py | H |
| A-021 | A write undone (rolled back) | Its event is undone with it | Tested | tests/test_event_rules.py::test_an_undone_write_writes_no_event | H |
| A-022 | The app or the AI's role writes an event | Refused: only the trigger, as the owner, writes them | Tested | tests/test_event_rules.py::test_neither_the_app_nor_the_ai_writes_an_event | H |
| A-028 | A webhook whose host resolves to a cloud's metadata address (169.254.169.254) or the server's own network | Nothing is sent; the attempt is recorded saying why | Tested | tests/test_webhooks.py::test_an_address_that_leads_inside_is_never_sent_to | H |
| A-029 | A host that resolves to a public and a private address | Refused: every address must be public | Tested | tests/test_webhooks.py::test_every_address_a_host_resolves_to_must_be_public | H |
| A-031 | http://, credentials, localhost, .internal, a private or loopback literal, IPv4 inside IPv6 | Refused when added and at every delivery | Tested | tests/test_webhooks.py::test_an_address_is_refused_saying_why; tests/api/test_events.py::test_a_webhook_goes_only_to_a_public_https_address | H |
| A-039 | A staff member who isn't an admin adds a webhook | Refused 403, and by the database | Tested | tests/api/test_events.py::test_only_an_admin_keeps_webhooks; tests/test_webhooks.py::test_the_database_takes_a_webhook_from_an_admin_only | H |
| A-041 | A webhook added today | Gets the company's events from then on, never before, never another company's | Tested | tests/test_webhooks.py::test_a_webhook_gets_its_own_companys_events_from_when_it_was_added_while_it_stands | H |
| A-043 | Each event's body | Its id, kind, time, company and ids, never a name or an amount; signed with HMAC-SHA256 over the time and the body | Tested | tests/test_webhooks.py::test_each_event_goes_signed_with_ids_only_and_once | H |
| A-045 | A tool's code raises something unexpected (a KeyError) | A plain 500, nothing of the error's words | Tested | tests/api/test_mcp.py::test_a_tool_that_breaks_tells_the_client_nothing_of_why | H |
| A-049 | A key taken back between two MCP calls | The next call is 401 | Tested | tests/api/test_mcp.py::test_only_a_standing_key_opens_the_server | H |
| A-051 | `lease_ledger` naming another company's lease | Refused: no such lease in this company | Tested | tests/api/test_mcp.py::test_reports_ledgers_and_events_read_the_keys_company | H |
| A-016 | A draft about a tenant who has since left the lease, or a record since archived | Approved as any other: the email goes, the note is written; nothing says the record changed since it was proposed | Open | src/pms/api/drafts.py act | M |
| A-023 | A writing transaction left open on the server (idle in transaction, or a long one) | Every company's events wait behind it, never skipped; with no idle-in-transaction timeout set, a stuck session holds them back until it ends | Open | src/pms/api/events.py EVENTS | M |
| A-024 | Events never cleared | trust_events grows for good (how long they are kept is not decided, ADR 0011) | Open | docs/adr/0011-ai-link.md | M |
| A-034 | A receiver that hangs | Ten seconds an attempt, one after another: 200 hung attempts hold the courier half an hour, every other webhook waiting | Open | src/pms/webhooks.py TIMEOUT | M |
| A-035 | An event never delivered within seven days | No longer tried; no admin is told, beyond the webhook's row saying it is failing | Open | src/pms/webhooks.py KEPT | M |
| A-006 | Two staff approve the same draft at the same moment | The second waits for the first (an advisory lock on the draft) and is told it was approved already; the decision's key backs it up | Guarded | src/pms/api/drafts.py waiting_draft; db/migrations/20261105000300_drafts.sql | M |
| A-018 | A notice draft whose template was removed before approval | The notice's own write path refuses it; the draft waits, saying why | Guarded | src/pms/api/notice_templates.py filled | M |
| A-027 | Writes of tables the AI never reads (portal messages, notices to move out, notes) | No event tells of them | Guarded | db/migrations/20261105000500_events.sql | M |
| A-038 | Two API processes deliver at once | One sends each event to each webhook (an advisory lock), the other skips it | Guarded | src/pms/webhooks.py deliver_pending | M |
| A-008 | An email draft where nobody named has an email on file | Refused 422 saying so; the draft waits | Tested | tests/api/test_drafts.py::test_an_email_no_one_named_can_get_is_refused_and_the_draft_waits | M |
| A-009 | An email draft where some named have no email | Sent to those who have one; the result names who had none, as staff saw before approving | Tested | tests/api/test_drafts.py::test_staff_see_who_a_proposed_email_goes_to_and_approving_it_writes_it_as_them | M |
| A-010 | Staff edit a draft into another kind before approving | Refused 422: an edit keeps its kind | Tested | tests/api/test_drafts.py::test_an_edit_keeps_the_drafts_kind_and_names_only_the_companys_records | M |
| A-011 | A visit drafted before any vendor is assigned | The work order's own rules refuse it at approval (409); the draft waits for an assignment | Tested | tests/api/test_drafts.py::test_work_order_drafts_open_assign_and_schedule_by_the_work_orders_own_rules | M |
| A-012 | An AI in a loop proposes thousands of drafts | At most 500 wait at once; past that, 429 until staff decide some | Tested | tests/api/test_drafts.py::test_drafts_waiting_have_a_limit_so_a_loop_cant_bury_the_inbox | M |
| A-013 | A decided draft is approved or declined again | 409, saying it was decided already; nothing more is done | Tested | tests/api/test_drafts.py::test_staff_see_who_a_proposed_email_goes_to_and_approving_it_writes_it_as_them | M |
| A-019 | The decision and the action are audited, naming the staff member; the proposal names its key's role | Audit rows: the draft as trust_ai_agent naming nobody, the decision and the action naming the approver | Tested | tests/api/test_drafts.py::test_a_drafts_proposal_and_decision_are_in_the_audit_log_with_who | M |
| A-025 | `after` names another company's event, 0, or text | 422 | Tested | tests/api/test_events.py::test_another_companys_events_are_never_read; tests/api/test_events.py::test_a_bad_place_or_page_is_refused | M |
| A-026 | A payment, a bounce, a late fee, a work order step, a repair request, a bill, a lease, a draft decided | Each its event, in the same transaction, ids only | Tested | tests/test_event_rules.py | M |
| A-033 | A receiver that answers 500, or refuses the connection | Tried again after 1 and 5 minutes, half an hour, 2 and 12 hours; given up after six | Tested | tests/test_webhooks.py::test_each_wait_is_longer_and_the_next_attempt_goes_once_it_passed; tests/test_webhooks.py::test_an_event_tried_every_time_is_given_up_on | M |
| A-036 | PMS_DATA_KEY changed, so a webhook's secret won't open | Each attempt fails saying so; nothing is sent unsigned | Tested | tests/test_webhooks.py::test_a_secret_that_wont_open_is_a_failed_attempt | M |
| A-040 | A webhook taken back | Nothing more is sent to it | Tested | tests/test_webhooks.py::test_a_webhook_gets_its_own_companys_events_from_when_it_was_added_while_it_stands | M |
| A-044 | A server without PMS_DATA_KEY | Keeps no webhook (409 saying why) and sends none; software still reads events with its key | Tested | tests/api/test_events.py::test_a_server_without_a_data_key_keeps_no_webhook | M |
| A-046 | A report given an argument it can't read, through MCP | A fixed sentence, never the database's message | Tested | tests/api/test_mcp.py::test_a_refusal_says_only_the_pmss_own_words | M |
| A-050 | `find` with % or _ in the name | Read as themselves | Tested | tests/api/test_mcp.py::test_find_gives_each_record_its_id_of_this_company_only | M |
| A-052 | An MCP client sends a body of hundreds of MB | 413 at 10 MB, as any request | Tested | tests/api/test_body_limit.py | M |
| A-056 | A proposal through MCP missing a field or with an extra one | The tool's result says which, in pydantic's words, nothing is proposed | Tested | tests/api/test_mcp.py::test_a_proposal_carries_what_its_kind_takes_and_nothing_else | M |
| A-017 | A draft no one decides | Waits for good, on the Dashboard until decided; nothing expires it | Open | src/pms/api/drafts.py | L |
| A-037 | A webhook's secret leaks | No rotation: take it back and add a new one (rotation is not decided, ADR 0011) | Open | docs/adr/0011-ai-link.md | L |
| A-042 | A receiver answering with a huge body | At most 64 KB is read and thrown away | Guarded | src/pms/webhooks.py post | L |
| A-054 | An MCP tool's day left out | The key's company's day, read before the role changes (the AI's role reads no time zone) | Guarded | src/pms/api/ai_keys.py with_key | L |
| A-055 | MCP calls and REST calls with one key | Share the key's 120 a minute | Guarded | src/pms/api/ai_keys.py too_soon | L |
| A-057 | `find` for leases | Those in effect or to come; an ended lease isn't found (its ledger still is, by its id) | Guarded | src/pms/api/mcp.py FIND | L |
| A-047 | A JSON-RPC batch, a message that isn't JSON-RPC, an unknown method or tool | 400 or the JSON-RPC error that says so | Tested | tests/api/test_mcp.py::test_a_message_it_doesnt_know_is_answered_as_json_rpc | L |
| A-048 | A GET to /mcp (an event stream asked for) | 405: the server holds no stream; with the portal served, NOT VERIFIED that a GET doesn't get the portal's page instead | Tested | tests/api/test_mcp.py::test_a_message_it_doesnt_know_is_answered_as_json_rpc | L |
| A-053 | An MCP client asks a protocol version the server doesn't speak | It answers with its own (2025-06-18) | Tested | tests/api/test_mcp.py::test_a_client_initializes_and_lists_the_tools | L |
