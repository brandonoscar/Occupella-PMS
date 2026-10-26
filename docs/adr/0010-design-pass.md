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

## Phase 2: tax IDs and what an application collects (parts F and G)

Screens before and after: `docs/design-pass/2/`. Migration 0155.

**Tax IDs (F)**

- **An owner's or a vendor's tax ID** (an EIN or an SSN) is sealed by the app with the server's
  key before it is kept (`trust_tax_ids`; `src/pms/sealed.py`, the same AES-GCM sealing as bank
  account numbers, with the row's id bound in so a sealed value copied onto another row doesn't
  open). Only its last four are kept in the clear. A server without `PMS_DATA_KEY` keeps none and
  says so. A new one replaces the one before from then on; the same again writes nothing.
- **Everyone sees the last four**: the owner's and the vendor's page carry a Tax ID card.
- **An admin reveals one whole** (Reveal, until they hide it or leave the page) and takes the
  year's **1099 export** from the 1099 summary report: each vendor marked for a 1099 and paid the
  year's threshold or more, and each owner whose rents collected came to it (1099-MISC box 1),
  with their tax ID whole, or "no tax ID on file: ask them for a W-9". The amounts are the vendor
  payments report's and the owner year-end package's, a test reading both. **Each time a tax ID
  is seen whole is a row naming the admin** (`trust_tax_id_reveals`), audit-logged, and the
  database refuses it for anyone else.
- **Never in logs**: the number travels only in a request's body and an admin's answer (no path,
  no-store), a refusal never repeats it, the audit log holds only the sealed bytes, and no other
  export holds it (the everything export keeps the sealed bytes, as it does bank account numbers).
  The AI role reads none of it.
- **Never from tenants or applicants**: the application asks for no Social Security number and no
  date of birth; the screening company asks for those itself, and the form says so.

**What an application collects (G)**

- **Each applicant**: a phone and their current address (asked of everyone), their previous
  landlord (a name, a phone and the days they rented) and employer if they have them, an emergency
  contact, and a proof of income (a PDF, JPEG or PNG). **The household**: others who would live
  there (names only), animals with the assistance animal flag kept (never charged pet rent, a fee
  or a deposit), and vehicles. Nothing else: no question about anything fair housing protects (a
  test reads the form's every label for it), and no field beyond the founder's list.
- **All or none**: a detail that doesn't fit refuses the whole application. The API still takes
  an application without them, as it did before; the form asks for them.
- **Staff see them** on the application's page, the proof of income opened from there; it is in
  no list of files and no search.
- **When an applicant becomes a tenant** (their application's offer is the lease), each tenant who
  was an applicant (the signer with the applicant's email and the tenant's name) is linked to them
  once (`trust_tenant_applicants`, checked by the database): their email and phone become the
  tenant's contact details, their emergency contact the tenant's, and the household the lease's
  occupants, animals and vehicles. The rest (the address they left, their previous landlord,
  employer and proof of income) stays with the application and shows on the tenant's page.
  *Forced by the code:* tenants have no columns for a previous landlord or employer, and copying
  them would make a second record of what the applicant gave; the link keeps one.
- **An emergency contact table**, as the founder asked: `trust_emergency_contacts`, a tenant's or
  an applicant's, the latest holding; staff change a tenant's on their page.

**Renamed labels (rule 2)**: none. New: the Tax ID card (Add tax ID, Change tax ID, Reveal, Hide);
the 1099 summary's "1099 export, with tax IDs"; the application's Phone, Where you live now, Your
previous landlord, Employer, Emergency contact, Proof of income and Your household; the tenant
page's Emergency contact and From their application; the application page's Household.

## Phase 3: the screens (parts H to O)

Screens before and after: `docs/design-pass/3/`. No migration: every part reads what the books
already keep, or writes through write paths that already exist.

**The Dashboard (H)**

- **Only numbers that change, each beside the same day a month before** ("Up 2 from 09/25/2026"):
  occupancy, rent collected against rent due this month, what is past due and how many tenants
  owe it (the delinquency report's own total), leases ending in the next 60 days, vacant units and
  their average days vacant, the next draw day with what a draw would pay then (and the last
  draw's), and trust bank accounts with a month ended and not reconciled. Each opens the page where
  that work is done (`src/pms/api/dashboard.py`). An archived unit counts in no occupancy.
- **Needs attention is one list**, the longest overdue first, then what comes due soonest, each
  row with its action right there: Send reminder (a past-due tenant), Pay bill, Approve draws,
  Approve reconciliation, Review application, or its page. **Send reminder** names who gets it,
  their emails, the count and anyone left out (no email on file) before it goes (rule 3), and
  goes once a day per lease (`/api/emails/rent-reminders/{lease}`).

**A tenant's page (I) and an owner's (J)**

- **A tenant's page** opens on their home, a `tel:` phone and a `mailto:` email (rule 6), and on
  Summary their lease: rent and due day, start and end (Change end date), the deposit held, their
  autopay and the balance, with Receive payment and Renew lease right there; then their household,
  emergency contact, notes, files and what their application gave, read only. Archive shows only
  once they have moved out or never had a lease (rule 4).
- **An owner's page** opens on how to reach them, how they are paid, their tax ID's last four (phase
  2), their balance, and the next draw day with what it would pay.
- **Invites in one click**, to the email in their contact details, named before it goes; with none
  on file the portal says so and opens their contact details. No email box.
- *Forced by the code:* **a lease's start date is not editable.** The app role only reads
  `trust_leases`; a lease's days move only through `trust_end_lease`, which adjusts every charge
  already made to the days in effect. Moving a start would need a new write path that re-prorates
  the first month's rent and recurring charges and moves a deposit's and a payment's dates. That
  touches money, so it waits for the founder's call (asked in this PR). The end date is editable.

**Email tenants (K)**

- Opens on the tenants living at the property today, each with their email and a box, all
  ticked; the count; who has no email on file; and "Current tenants only, not past or future."
  Only the ticked are written to (`GET /api/emails/property/{id}`, the preview; the email names the
  tenants chosen, each living there today, or is refused).

**Renewals (L)**

- **Renew lease** on the tenant's page and in the lease's ⋯ menu opens one side panel: the new
  rent and term, starting the day after the lease ends (the first of next month for month to
  month; a year long by default when the lease had an end), the company's lease template filled in
  or its own PDF, and the tenants who sign, with their emails; one with no email on file stops it
  and says where to add one. **Send for signing** asks first, naming everyone it goes to (rule 3).
  It goes through the PMS's own signing page (ADR 0008), and once everyone has signed it becomes
  the lease. The Renewals page loses "Draft a renewal"; an ended lease offers no Renew lease.
- **The notice warning**: before sending, the panel and its confirm say so when the new rent
  starts sooner than the written notice the property's state asks (`src/pms/rent_notice.py`; the
  property's address gives the state). Only the rules `docs/law.md` records are used, and nothing
  is invented: California Civil Code 827 (30 days, or 90 when the rent is more than 10% over the
  lowest charged in the 12 months before it starts), Oregon ORS 90.323 (90), Washington RCW
  59.18.140 (90), Nevada NRS 118A.300 (60), and New York Real Property Law 226-c (for 5% or more:
  30, 60 or 90 days by how long the tenant has lived there). Any other state gets no warning. A
  threshold compares the exact increase, so 4.99% is never taken for 5%. Left out, as `law.md`
  records them: Colorado's 60 days applies only without a written lease, and a renewal is one;
  West Virginia has no statute. Rent caps are not checked. The warning stops nothing: staff can
  Send anyway.
- *Forced by the code:* **"the lease updates" is a new lease.** A renewal offer becomes the lease
  from its first day and ends the one it renews the day before (`trust_complete_lease_offer`);
  its tenants, due day and assistance carry over. Rewriting a lease's terms in place would change
  charges already made, which the ledger rules forbid; the new lease keeps the old one's history
  whole.

**Ledgers (M)**

- **A lease's Ledger tab**: From and To (empty: from its first line, through today) with the
  balance brought forward; every line opens what is behind it in a side panel, a charge (what is
  still open of it, with Waive) or a payment (its receipt, It bounced; a bounce opens the payment
  that bounced); Export CSV of the lines shown; and "Updated hh:mm". The report's own download is
  still the whole ledger.
- **A trust bank account's register**: each move opens its lease, bill, owner or tenant; Export
  CSV of every move of the days, oldest first; and the same stamp.
- Both are **read again when the browser tab comes back into view**. A CSV's amounts are numbers,
  so a credit's minus sign isn't escaped as a formula; text still is.
- The owner's statement is left as it is: it already has its period and its downloads, and its
  lines are totals with no single payment or bill behind them to open.

**Utilities (N)**

- Accounting's "Utility billing" page becomes each **property's own Utilities page**
  (`/properties/<id>/utilities`, linked from the property's page), the only place a utility is
  billed back. No bill offers it: the portal never linked a vendor's bill to a bill-back, and
  still doesn't.

**Housing authorities (O)**

- **No Housing authorities menu page.** A lease's housing assistance stays on its Summary with the
  rent's split. Set housing assistance offers "A new housing authority" (the choice when none is
  on file), added by its name as the assistance is saved and kept chosen should the assistance be
  refused, so a retry adds no second one. Each authority the lease names links its own page (its
  leases, late fee and payments). Its checks go in through Receive payment (part D), on the rent
  roll or that page.

**Renamed labels (rule 2)**: "Draft a renewal" (Renewals) is gone; Renew lease opens a panel
instead of Draft leases. Accounting's "Utility billing" is the property's "Utilities". Rentals'
"Housing authorities" is gone. "Download the ledger" reads "The whole ledger, as a report". New:
the Dashboard's tiles and Needs attention actions (Send reminder, Pay bill, Approve draws, Approve
reconciliation, Review application); Renew lease's New rent a month, Send for signing and Send
anyway; the ledgers' From, To, Export CSV and Updated; the tenant's Lease card; the owner's top;
Email tenants' boxes; "A new housing authority".

## Phase 4: the finish (copy sweep, browser tests, goldens, property tests)

Screens before and after: `docs/design-pass/4/`. No migration.

**Rule 1, a plain line under every action button.** `Button` takes `says`, the line under it
saying what pressing it does and what it doesn't; the button is described by it, so a screen reader
reads it too. Every submit button in the portal has one (a report's Show and a Preview change
nothing and have none), and so does every button that changes something on click without asking
first. `src/buttonRules.test.ts` reads every `<Button>` in the portal's source and fails on a
submit button without a line. Each line was checked against the API route and the database
function it calls before it was written: one never says "nothing is sent" of a step that emails,
or "nothing is charged" of one that charges. A confirm question that only asked now says what
happens too. *Forced by the code:* `Button` defaults to `type="button"`, so only the submit
button submits its form: the notice card's Cancel, a button with no type inside its form, could
make the notice it was cancelling.

**Rule 2, no internal jargon.** Payment files' "Set aside" is Skip, Skipped and "Skip this line"
(the API's own messages too). A day inside a memo reads MM/DD/YYYY wherever a memo shows ("Rent
due 10/01/2026", "Management fee 09/01/2026 to 09/30/2026"): the lease ledger, the owner's
statement, Move-outs, the tenant's portal, month-end and the open charges. The owner's statement
on screen reads "Bill RF-1" where the ledger's memo says "Bill RF-1 set aside" (*forced by the
code:* phase 1a kept that write path and its memo, and a ledger memo is never rewritten). Month-end
reads "In the books" for what it called transfers; switching says "money recorded" for postings.
The browser sweep that checks every page of the demo for accessibility also fails a page whose
text carries a name from the code, "set aside", "PMC", "EFT", a value printed as nothing or a day
as YYYY-MM-DD (`frontend/e2e/words.ts`). **Left to the founder:** the reports, the general ledger
page and every download print the ledger's own words (`bank_cash:`, `owner_property:`, "PMC",
"posting", "Bill X set aside", times in UTC). An earlier decision keeps a report's text as the
report writes it, so the sweep leaves those pages out until the founder decides (asked in this PR).

**Rule 3, who gets it before it sends.** Write email names the address it goes to, or says the
person has no email on file and sends nothing (`GET /api/emails/to`). A portal invite says whether
it is emailed or only a link to hand over, as the server sends email or not. A work order's Next
step names the vendor's email before an assignment or a visit emails them, or why none goes (no
email on file, work order emails off, a server that sends none). A decision resting on a screening
names each applicant and their email, counted, before the adverse action notice goes. *Not done:*
a payment's receipt and an over-limit bill's approval email still go to the address on file
without the screen naming it first; both are written by themselves as the payment or bill is
recorded, and naming each address needs the API to answer it first.

**Rule 4, ask before what can't be taken back.** `AskFirst` asks a question naming what happens
before Post (a journal entry), Sign off the tie-out, Approve through (month-end), This is them (one
tenant at two properties), Remove logo, a file's and a listing photo's Remove, Unlist, Take away
(a signer) and Try again (an online payment that came back); a showing's outcome, ending a lease
from Move-outs and a vendor's The work is done ask too. Each shows only where it applies, as
before. *Not done:* they stay where they were rather than moving into a ⋯ menu (Unlist and a
file's Remove sit beside one; a signer's Take away, a photo's Remove and Remove logo have none to
go in). Undo on a month-end match stays a plain button: it changes nothing that can't be matched
again.

**Golden files for what the PMS hands people** (`tests/golden/test_golden_files.py`): a payment
receipt, an owner's statement as the PDF they download, the ACH file and the printed checks, each
from a fixed synthetic fixture and compared as the text it shows. *Found by them:* an ACH file
taken again read the company's bank details as they are now; it now reads them as the run had
them, as it already read each payee's account, so a file taken again is the file uploaded.

**Property tests for holds and draws** (`tests/properties/test_payables_properties.py`): holds
never come to more than the balance, a draw never pays held money, and now a void releases its
hold (the bill holds nothing, no other bill holds less, and a draw may take at least what it could
before).

**Renamed labels (rule 2)**: payment files' "Set aside" (⋯ menu), "Set it aside" and "Set aside"
(column, status) are Skip, Skip this line and Skipped; month-end's "Transfers not matched" is "In
the books, not matched", "Lines … · Transfers …" is "Bank lines … · Books …", and "No transfer" is
"Nothing in the books". New: the line under each action button; the confirm questions above.
