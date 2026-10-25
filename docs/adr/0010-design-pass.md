# ADR 0010: The design and behavior pass

Decided by the founder on 2026-10-24, after reviewing the PMS as a property manager. The review
set seven rules for every screen and four phases of work, one PR each (phase 1 in three). This
ADR records each decision, the founder's answers to the questions it raised, and every place the
code forced a different choice, with the reason. Later phases add their sections here.

## The rules for every screen

1. Every action button has one plain line under it, or in its confirm dialog, saying what happens
   and what doesn't ("Nothing is paid. The $240 hold goes back to Owner A's balance.").
2. No internal jargon in labels; "Set aside" is the worst case. Every PR lists the labels it
   renames.
3. Before anything sends (an email, an invite, a renewal), the screen shows exactly who gets it:
   names, emails, a count, and anyone left out and why.
4. Destructive actions live in the ⋯ menu, ask before acting, and are hidden when they don't
   apply.
5. No customization: no widgets, no rearranging. Rentvine's density, not Buildium's. Everything on
   screen is useful or reassures.
6. Phone numbers are `tel:` links and emails `mailto:` links. No calling from the app.
7. Synthetic and demo data only. The ledger rules hold everywhere: debits equal credits, no owner
   balance below zero, the bank equals the sum of the ledgers.

## The founder's answers (2026-10-24)

- **The company's own bills stay out.** A bill belongs to an owner's property account; Record
  bill refuses one with no owner and says the company pays its own bills outside the PMS. ("Bills
  for the company's own expenses come from company money, never an owner's.")
- **ADR 0006 decisions 2 and 3 are reversed** by phase 2: applications collect the details listed
  in part G (still no question about anything fair housing protects, and never a Social Security
  number or a date of birth from a tenant or applicant), and owners and vendors carry a tax ID,
  encrypted at rest, shown as its last four, revealed in full only to an admin, each reveal in the
  audit log.
- **Checks print on pre-printed stock**: payee, amount in figures and words, date, memo and stubs,
  onto stock that carries the bank's details and the MICR line.
- **Phase 1 is three PRs**: 1a the bill's life (A); 1b owner draws and payout files (B, C); 1c
  receive payment and the lease money rules (D, E). Phases 2 to 4 are a PR each.

## Phase 1a: a bill's life (part A)

Screens before and after: `docs/design-pass/1a/`.

A bill reads in plain words, as one of:

| Status | When |
|---|---|
| Entered | dated after today: nothing is held for it until its date |
| Waiting for owner | over the approval limit of the owner's agreement, not decided yet |
| Ready to pay | its amount is held from the owner's balance |
| Short | the owner's balance can't cover it |
| Partly paid | some of it is paid; it shows what is left |
| Paid | paid in full |
| Void | declined by its owner, or voided by staff (entered by mistake) |

Decisions, and what the code made of them:

- **"Set aside" is gone as a step.** Staff record a payment; nothing else. A bill not paid yet
  *holds* its amount and markup from the owner's property account without moving money
  (`trust_bill_holds`): each account's balance is laid over its open bills, the one due first
  first. That is the hold the founder asked for, and draws already skipped it: since migration
  0017 a draw pays only the balance less the reserve and every bill not yet paid
  (`trust_unpaid_bills`). So a draw never pays held money, and a hold never exceeds the balance.
- **Paying takes the whole bill from the owner at the first payment.** The ledger moves money
  only when staff record a payment. The first payment moves the whole bill (and its markup, into
  the company's fees) from the owner's account into the vendor's, as setting it aside did, and
  pays its part from the bank; later parts pay the rest from the vendor's account. *Forced by the
  code:* the reports (income statement, year-end, owner balances) count a bill when its money
  leaves the owner's account; moving it in one piece keeps them, and their golden cases,
  unchanged. A partly paid bill's remainder is already out of the owner's balance, held for the
  vendor. A Short bill can't be paid in part until the owner's balance covers it whole. A first
  payment posts twice in one transaction, so it locks every account it moves first, in the order
  pgledger locks one posting's accounts; the nightly soak found the deadlock without it.
- **Short** shows what the owner must send ("Owner A needs to send $X"): what the balance leaves
  uncovered of this bill and of those due before it. *Ask the owner* emails that request (once a
  day per bill, to their contact email, else their portal login's), after staff see who gets it.
  It clears by itself when the money arrives, since a hold is worked out, never stored.
- **Waiting for owner** is unchanged: the owner is emailed once (ADR 0004), approves or declines
  in the portal, and the bill's page shows when the email was written and whether it went.
- **Void** is for an owner's decline or a bill entered by mistake, and always says "Nothing was
  paid; the hold goes back to the owner's balance." *Forced by the code:* a void is a row of
  `trust_bill_declines` marked void, naming the staff member, so everything that already leaves
  out a declined bill (draws, every report, archiving) leaves out a void one. Like a decline, a
  void is refused once money was taken for the bill: a bill set aside before this change (its
  money already in the vendor's account), or one partly paid, is paid, not voided.
- **Bills set aside before this change** keep their money where it went. They read Ready to pay
  (or Partly paid), "held for this bill since" the day it was set aside, and pay as before. Nothing
  in the ledger is rewritten: that is the migration of set-aside data to the new hold.
- **Paid** is recorded by staff: the day, check (with its number) or ACH, the amount (all that is
  left, or a part), and a receipt. Every payment counts toward the vendor's 1099-NEC total
  (`trust_report_vendor_payments` sums each payment).
- **Matching the bank statement.** *Forced by the code:* trust books record a check when it is
  written, so a bill is paid when staff record the payment, and the bank shows later when it
  cleared. When a statement comes in, a bank line whose check number and amount exactly match a
  recorded bill payment's is matched to it by itself, and the match is logged (the audit log names
  the staff member who imported the statement). Anything less certain is a suggestion staff
  confirm on the month-end tab: a line of the same amount as a recorded payment, or a withdrawal
  of exactly what is left of an unpaid bill, which records the bill paid on the line's day.
- **Duplicates.** The database already refuses a vendor's reference twice. Record bill also warns,
  before saving, when the vendor has a bill whose reference is the same but for case, spaces and
  punctuation.
- **Kept as they were:** bills split across expense accounts, shared across properties, recurring
  bills and markups. Pay bills pays many vendors at once; a bill that is Short or waiting for its
  owner can't be picked, and says why.
- **The company's own bills** are refused, as the founder decided above.

## Phase 1b: owner draws and payout files (parts B and C)

Screens before and after: `docs/design-pass/1b/`.

- **The draw day** (Settings, Company setup, Owner draws; `trust_owner_draw_settings`,
  append-only, the latest row holding, naming who changed it): a day of the month, 1 to 28, or
  none. That night, or the first night after if the run missed it, the nightly run prepares every
  owner's draw, once a month (`trust_prepare_owner_draws`; `trust_draw_runs` and their lines):
  what each owner's property account has available then, as a draw counts it, and how its owner
  is paid. The Dashboard shows one "Owner draws ready" line and Pay owners lists them at the top;
  Use these draws chooses them, each at most what the account has now. **Nothing is recorded as
  paid until staff pay them**, and paying them marks the month's draws paid, once
  (`trust_draw_run_approvals`).
- **Bills due on or before the draw day are held before the draw is worked out.** *Forced by the
  code, and safer than asked:* draws already hold back every bill dated by the day of the draw and
  neither paid nor declined (`trust_unpaid_bills`, since migration 0017), whatever its due date.
  That holds every bill due by the draw day, as asked, and one due after it too; holding less would
  let a draw pay out money an entered bill needs. No separate rule was added.
- **Each owner has a payout method** (`trust_owner_payout_methods`): ACH or check, a check until
  staff change it on the owner's Summary tab. Pay owners pays each owner their own way unless
  staff pick one way for all. Screens say ACH; the database keeps the word EFT, as payout methods
  have since migration 0068.
- **Payee bank accounts** (`trust_payee_bank_accounts`, an owner's or a vendor's, on their
  page's Payouts card): the routing number (its check digit checked), checking or savings, the
  name on the account, and the account number sealed with AES-GCM under the server's
  `PMS_DATA_KEY` (`src/pms/sealed.py`; the row's id is bound in, so a sealed number can't be moved
  to another row). Only its last four come back to a screen; the whole number is opened only to
  write an ACH file. A server without the key keeps no account numbers and writes no ACH file, and
  says so. The key lives only in the server's environment, never in the repo or a workflow
  (ADR 0009); the browser run makes a throwaway one for itself. The AI role reads none of it.
- **Payout files** (part C). Pay owners and Pay bills take a Payout file: none (the record-only
  option, for payouts made outside the PMS, as before) or the ACH file and checks. With a file,
  each trust bank account and way paid becomes a payout run (`trust_payout_runs` and their items):
  - **A NACHA file** for the company to upload to its own bank: PPD credits to owners, CCD to
    vendors, service class 220, one entry per payee per run, with the trust bank account's ACH
    details from its bank (Banking, the account's Payouts tab: the bank's routing number and name,
    the origin and company ids and names, all of them or none).
  - **Checks to print** on the account's pre-printed stock, as the founder decided: payee, amount
    in figures and words, date, memo, the payee's mailing address when on file, and two stubs; one
    check to each payee per run, numbered on from the account's next check number (its first check
    number, or one past the highest it recorded).
  - Each run's file is listed under Payout files (Pay owners, Pay bills and the account's Payouts
    tab) and downloads again as often as needed. Only staff holding disburse take one, and each
    download is logged (`trust_payout_file_downloads`).
- **The PMS still never moves money itself.** It records payouts and writes their files; the
  company uploads the ACH file to its bank and mails the checks, and the bank moves the money.
  Nothing here calls a bank, and the AI role can neither record nor download a payout.
- **NOT VERIFIED:** no ACH file has been uploaded to a real bank and no check printed on real
  stock. The file follows the NACHA layout (tests check every record's length, counts, totals and
  entry hash) and the checks Letter-size three-part stock; a company's bank may ask for a test file
  first, and its stock may need the printing nudged.

## Phase 1c: receive payment and the lease money rules (parts D and E)

Screens before and after: `docs/design-pass/1c/`.

**Receive payment (D)**

- **One panel** from the rent roll, a lease's page and a housing authority's page. It takes a
  tenant's payment or, as the founder asked, a **housing authority's check**: one check split
  across the leases it pays, each lease's part paying what the authority owes there, oldest first,
  as before. It replaced the authority page's Record payment; that page's button is now Receive
  payment, opening the same panel with the authority chosen.
- **A note** (staff only; `trust_payment_notes`, once per payment, naming who wrote it) and **the
  check's photo or a PDF** (a document of kind `payment_attachment`, about the lease and the
  payment, listed on the lease's Files tab). Both come in the payment's own request, all or
  none: sent again, the payment keeps what it first came with, and a file that isn't what it says
  refuses the payment too. A tenant never sees the note; the AI role reads neither.
- **What it pays**: what is owed, rent first and oldest first (the existing order, unchanged), or
  the open charges staff choose first, each all of it or a part; what is left of the payment pays
  the rest in the usual order, and anything past what is owed is prepaid rent as before. A choice
  that doesn't fit (not open on the lease, chosen twice, more than is open, more than the payment)
  records nothing. *Forced by the code:* a lease whose tenants moved to it from another lease
  pays what each lease owes, oldest lease first (migration 0091), so no choice is offered there.
- **Save and add another** records the payment and keeps the panel open, cleared, for the next
  one: on the rent roll, the next lease; on a lease's page, the same lease.
- **A receipt for every payment.** Each tenant on the lease is emailed its receipt, the PDF
  attached, for every payment: recorded by staff, from a payment file, online and autopay, while
  the server sends email. The Automatic emails switch for receipts is gone and the card says
  "Every payment". *Kept:* the `payment_receipts` column of `trust_email_settings` stays, unread;
  a committed migration is never edited and dropping it gains nothing.

**The lease money rules (E)**

- **The rent due day stays independent of a lease's start and end.** Already so since migration
  0081: a lease starting mid-month is charged its first part month on its first day, then on its
  due day each month (`tests/test_rent_due_days.py` pins it). Unchanged.
- **Moving an end date earlier charges the last month only for the days covered.** Already so
  since migration 0051: `trust_end_lease` credits each rent and monthly charge that stood at the
  run's amount for the days after the new end (`tests/test_lease_end_credits.py`). Unchanged.
- **A fixed lease that passes its end with no renewal signed rolls to month to month at the same
  rent through the nightly run** (`trust_roll_over_leases`, migration 0154; `trust_lease_holdovers`,
  once per lease): on the first night after its last day, within a week, so a night the run missed
  still rolls it. Its end is cleared through `trust_end_lease`, which charges back the days of its
  last month credited when the term was set to end. An admin's Run rent now does the same first.
  The Dashboard notes each one for 30 days ("Leases gone month to month") while it stays so.
- **Which ends roll over: a decision the code forced.** A lease keeps one end date, so the PMS
  couldn't tell a term that ran out from a move-out staff recorded. From now on every end staff
  give (Move-out's end, and any lease's end from its ⋯ menu) is also an end notice naming them
  (`trust_lease_end_notices`), and a lease with one never rolls over; ends staff changed before
  this migration were found in the audit log and recorded as notices. Nor does a lease roll over
  when another lease follows it at its unit (a renewal or the next tenant), its tenants moved from
  it, its deposit was returned, or it was entered after its end had passed (a lease from the past,
  brought over by the switch or added late).

**Renamed labels (rule 2)**: on a housing authority's page, Record payment is now Receive
payment; Automatic emails' "Payment receipts to tenants: On/Off" is now "Every payment, the PDF
attached", with no switch. New: Receive payment's From, It pays, Note (staff only), Check photo or
PDF, Save and add another; the Dashboard's Leases gone month to month.
