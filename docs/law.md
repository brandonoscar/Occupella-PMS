# The law each feature follows

Founder, 2026-10-15: "make sure what we are doing is by law like ones that apply in all states,
like commingling is in wv too". Each feature below meets the rule that applies in every state
(the floor) and, where states differ, the strictest one found, so a company in any state can use
it as it is. A state's own choices beyond that (deadlines, caps, notice periods) stay the
company's, as CLAUDE.md keeps state rules out of the PMS unless the founder adds one here.

**How this was checked (2026-10-15).** Research from search excerpts that quote the official
pages: the legal sites themselves (code.wvlegislature.gov, law.cornell.edu, dre.ca.gov,
leginfo) refused the container's requests, so no page was read in full. A point that rests on a
secondary source only says NOT VERIFIED. This is not legal advice: a company should have its own
broker or attorney confirm its state.

## The company's fees taken out of trust (migration 0059)

**The rule.** A management or leasing fee, once earned, is the company's own money. Every state's
license law forbids a broker to keep their own money mixed with what they hold for clients
(commingling), and lets only a small amount of it sit in a trust account, if any.

- West Virginia: W. Va. Code 30-40-18(f) forbids commingling and pledging the account; the broker
  may keep at most $100 of their own money in it, for a minimum balance. No deadline to take
  earned fees out was found in the Code or 174 CSR 1 (NOT VERIFIED: 174 CSR 1-15 could not be
  read in full). A third-party property manager needs a West Virginia broker license
  (W. Va. Code 30-40-3 and 30-40-4).
- California: Business and Professions Code 10176(e) forbids commingling; 10 CCR 2835 allows
  $200 of the broker's money for bank charges and requires the broker's share to be taken out
  within 25 days of its deposit, a disputed share staying until the dispute is settled.
- Texas: 22 TAC 535.146 requires money the broker comes to own to be taken out within 30 days.
- Oregon: OAR 863-025-0027 pays earned management fees out at least monthly; no broker money
  for bank fees may sit in the clients' trust account (OAR 863-025-0025(5)).
- Florida: 61J2-14.010 allows up to $5,000 of broker money in a property management escrow
  account.

**What the PMS does.**
- A fee is taken only from an owner's account, and never takes it below zero: the management fee
  from rent collected (by staff on Management fees, or, once the company turns it on, by the
  nightly run each month from a day it picks; migration 0082), the leasing fee once per lease, or
  another fee staff take (an inspection, a lease-up, another), saying what it is for, by a staff
  member who holds disburse, once per request, logged with who did it.
- `trust_withdraw_pmc_fees` takes the company's fees out of a trust bank account to the
  company's own account: never more than the fee account holds, once per request, by a staff
  member who holds the disburse role, logged with who did it.
- The owners page and Today show the fees still in each trust bank account, since when the
  oldest has waited and the day to take them out by: 25 days after it, California's deadline,
  the strictest found, which also meets Texas's 30 days and Oregon's month.
- No account holds the broker's own money in trust, so it holds $0 of it: within every state's
  cap, including Oregon's none.

**Left to the company.**
- A state deadline shorter than 25 days, should one exist (none was found).
- Taking each period's management fee soon after the period ends: the 25 days count from the day
  the fee was taken from the owner's account, while California counts them from the deposit the
  fee was earned on. Turning on the monthly fees in Company setup takes last month's from an early
  day of the month by itself.
- Taking only the fees the owner's management agreement allows. A broker pays trust money out
  only as the client authorized it; the PMS keeps no agreement text, so it doesn't check an
  inspection, lease-up or other fee against it (NOT VERIFIED: no state's rule on fees outside the
  agreement was read for this).
- Holding back a fee an owner disputes (California 10 CCR 2835(b)): the PMS records no dispute
  yet, so don't take that fee out until it is settled.

## Unpaid bills carried over at cutover

**The rule.** Every state requires a record of each trust receipt and disbursement and a ledger
for each owner, and forbids paying out more of an owner's money than that owner's balance.

- West Virginia: W. Va. Code 30-40-18(a) (maintain and reconcile trust accounts; how often is
  NOT VERIFIED) and 30-40-18(h) (records showing when money came in and from whom, when it went
  out, and whose it is); 30-40-19(a)(28) and (29) discipline a broker who keeps no records for
  five years or keeps them wrong.
- California: 10 CCR 2831 (the journal), 2831.1 (a record for each owner), 2831.2 (reconciled
  monthly), 2832.1 (no payment that takes the account below what all owners are owed without
  every owner's written consent); records kept three years (Business and Professions Code 10148).
- Oregon: OAR 863-025-0027 (no payment without enough money in that owner's ledger).
- No state rule about switching software was found (NOT VERIFIED that none exists).

**What the PMS does.**
- A bill the old system had not paid comes over from the switching tool as an ordinary bill
  against the owner's account for its property, with its old reference, bill date and due date,
  once (the file sent again adds nothing).
- From the first day, a draw holds it back, as it does any unpaid bill: the owner is never paid
  money a vendor is owed.
- One over the owner's approval limit keeps the approval the old system recorded (who and when);
  without one, it waits for the owner and the owner is emailed, as for a new bill.
- Every record is append-only and kept for good, past West Virginia's five years.

**Left to the company.** A bill the old system had set aside but not paid: carry its money over
on the owner's line and the bill as unpaid, then set it aside and pay it here. (Money carried
over on a vendor's line is paid out with a journal entry.)

## Who signs each trust bank account (migration 0060)

**The rule.** In every state the broker whose license the company runs under controls its trust
accounts and stays liable for them, whoever signs; anyone else withdraws only with the broker's
written authorization.

- West Virginia (the strictest found): W. Va. Code 30-40-18(g) makes the broker the account's
  designated trustee, with full authority over it; only one other licensed officer or member of
  the firm may be authorized to disburse, a second only where two signatures are required, and
  no unlicensed signer. The bank lists who may withdraw; the Real Estate Commission is told
  within 10 days of opening or changing a trust account (30-40-18(k)).
- California: 10 CCR 2834 lets the broker, a licensee, or an unlicensed employee bonded for at
  least what they can reach sign, with the broker's written authorization; the broker stays
  liable (2834(c)).
- Arizona: A.R.S. 32-2174 (the designated broker authorizes signers and stays liable).
- Florida: 61J2-14.010(1) (the broker signs every escrow account).
- Oregon: the property manager signs; an owner never does (OAR 863-025-0025(9)).

**What the PMS does.**
- Each trust bank account can have its broker on file, named by an admin, and the signers the
  broker authorized, each with the day of the written authorization and how they may sign: a
  license number, or a fidelity bond (its amount and the day it runs out). A change is a new row
  naming who made it; nothing is rewritten.
- Once an account has its broker on file, the database refuses a payout from it (an owner
  draw, a bill paid, a deposit returned, the company's fees taken out, a journal entry taking
  cash out) recorded by anyone but the broker or a signer, and by a bonded signer whose bond has
  run out. One broker per account; signers only while it has one.
- Today lists each account with no broker on file and each bond running out within 30 days or
  run out.

**Left to the company.**
- Naming the broker of each account: until then the roles alone decide who pays out, as before.
- Its state's limits: in West Virginia, the broker and at most one other licensed signer, and no
  bonded unlicensed one. The PMS records bonded signers because California allows them; a West
  Virginia company should not add one.
- Telling its regulator about a signer change (West Virginia: within 10 days).
- The bank's own signature card, which the PMS can't see: keep it the same as this list.

## Rent first (migration 0093)

**The rule.** Washington requires a landlord to apply any payment a tenant makes to rent before
late fees, damages, legal costs or other fees (RCW 59.18.283(1)), rent there being the recurring
charges the rental agreement sets for living there (RCW 59.18.030). It is the strictest rule
found; a Wisconsin legal aid FAQ says the same of Wisconsin without citing a statute. Paying rent
first is allowed everywhere, as it is in the tenant's favor, and a late fee figured on unpaid rent
should never grow because a payment went to a fee instead. Sources read: the RCW archive copy of
59.18.283 on lawfilesext.leg.wa.gov and the search snippets of wislawhelp.org and a North Carolina
Realtors note on G.S. 42-46 (a late fee may not be taken out of a later rent so as to make it
late). The current text of each statute was not read: NOT VERIFIED.

**What the PMS does.**
- Every payment, prepaid rent used, credit and deposit kept pays the lease's rent and its monthly
  charges first, oldest first, then its other charges (late, NSF, lease-break and other fees),
  oldest first. Before this, charges due the same day were paid in the order of their random ids,
  so the same lease could be charged a different late fee by chance.
- A late fee is worked out from the rent left once credits came off the rent first.
- A tenant who moved pays the rent of every lease they moved through before any of their fees,
  each owner still paid what was owed to them.

**Not built.** A payment a tenant earmarks for a particular month or charge (New York lets a
tenant do so, by Cornell's tenant advocacy notes, NOT VERIFIED): the PMS still pays rent first,
oldest first.

## A lease's rent changed from a month (migration 0061)

**The rule.** A periodic tenancy (month to month) changes rent only after written notice of at
least one full rental period, from the start of a period; a lease for a term changes rent during
it only as its own terms allow. Some states ask for more:

- West Virginia: no statute on rent increases and no rent control. Notice follows W. Va. Code
  37-6-5, which ends a tenancy of less than a year on one full period's written notice (three
  months for year to year); Legal Aid of West Virginia warns a plain 30 days often falls short.
- Washington: RCW 59.18.140 (90 days for every tenancy, 30 for subsidized housing; no increase
  during a term unless the lease allows it), with a cap since 2025.
- Oregon: ORS 90.323 (none in the first year, then 90 days' notice, once in 12 months) and
  90.324 (a cap).
- California: Civil Code 827 (30 days, 90 for over 10% within 12 months) and 1947.12 (a cap
  through 2030).
- New York: Real Property Law 226-c (30, 60 or 90 days for 5% or more, by how long the tenant
  has lived there).
- Colorado: 60 days without a written lease (C.R.S. 38-12-701), once in 12 months (38-12-702).
- Nevada: 60 days (NRS 118A.300).
- Cities with rent control have their own rules (NOT VERIFIED which, beyond the states above).

**What the PMS does.**
- A rent change starts on the first of a month, after the lease starts and within it, and is
  recorded with the day written notice was given and how it was served. The notice must have
  been given by the first of the month before, a full month: the floor every state shares.
- A lease with an end date changes rent during it only naming the lease term that allows it.
- A month the rent run charged already keeps its rent; a change starts from a later month.
- The rent run charges the rent in force each month (for the days in effect), a lease ended
  mid-month keeps its days at the rent in force, the reports show it, and the tenant sees the
  change coming in the portal with the day notice was given.

**Left to the company.** Longer notice where its state or city asks (up to 90 days), rent caps,
the first-year and once-a-year limits, extra days for notice served by mail, and the notice
itself, which the PMS records but does not write or send.

## Renters insurance on file (migration 0062)

**The rule.** No state requires renters insurance by statute; a landlord may generally require
it through the lease. Where states limit it:

- Oregon: ORS 90.222 allows it only in a written agreement, with coverage no higher than
  $100,000 or the local customary amount; the landlord may not require a particular insurer or
  being named an additional insured (an interested party is allowed); not for households at or
  below 50% of area median income or publicly subsidized units; 30 days' notice to add it to a
  month-to-month tenancy.
- Virginia: Code 55.1-1206 lets the landlord require it; a premium for coverage the landlord buys
  counts as rent, the tenant may buy their own instead, deposit plus premiums at most two months'
  rent, and the notice says it doesn't cover flood.
- West Virginia: no statute found (NOT VERIFIED).
- That most states forbid steering a tenant to one insurer is NOT VERIFIED; Oregon does.

**What the PMS does.**
- A lease's terms say whether it requires renters insurance and the least liability coverage it
  asks, from a day; the latest from that day or before holds.
- A policy names whatever insurer the tenant chose (free text), its number, the days it runs, its
  liability coverage, and whether the landlord is named an interested party. Staff record one,
  or the tenant records their own from the portal; the PMS never picks an insurer and charges
  nothing for it.
- The rent page, the portal and Today show how each lease stands: current, expiring within 30
  days, expired, none on file, or below what the lease asks.

**Left to the company.** Whether its lease may require insurance and how much (Oregon's cap and
exemptions), any premium it buys for a tenant and the rules on charging for it (Virginia), and
the notice to add the requirement to a tenancy.

## A lease's people: assistance animals, a tenant leaving, a lease broken (migration 0080)

**The rule.** An assistance animal (a service or support animal a person with a disability needs)
is not a pet under the federal Fair Housing Act, which applies in every state: a housing provider
waives a no-animal rule for it and charges no pet deposit or pet fee for it, though the tenant
still answers for damage it does. HUD set this out in Notice FHEO-2020-01 (January 2020, replacing
FHEO-2013-01), guidance without the force of law of its own. Sources read: a congressional fact
sheet on the Notice (mcgarvey.house.gov), an Idaho Legal Aid packet citing it, and a 2021 HUD
charge over a $400 deposit asked for an assistance animal (archives.hud.gov). The Notice's own
wording and the statute's subsection (42 U.S.C. 3604(f)(3)(B), reasonable accommodations) are
NOT VERIFIED: hud.gov was not read.

A tenant leaving a lease others stay on, and a lease ended early, have no rule every state shares
beyond the lease itself and the deposit staying in trust until it is returned (the deposit rules
above). Whether a landlord must try to re-rent a broken lease before charging for it, and how
much a lease-break fee may be, differ by state and stay the company's (NOT VERIFIED per state).

**What the PMS does.**
- A pet is marked as an assistance animal when it is one; the lease page then says that no pet
  rent, fee or deposit is charged for it. The PMS doesn't stop staff from adding a charge (it
  can't tell pet rent from other fees); it names the rule where the animal is recorded.
- A tenant leaving names the day; their deposit moves, inside the security-deposit trust account,
  to a tenant who stays, or stays under their name until the lease's deposit is returned. No money
  leaves trust.
- A lease-break fee is the property's terms in force on the lease's first day (what the lease was
  signed under), charged only when staff end a lease before its end date and tick it.

**Left to the company.** Whether a request for an assistance animal is granted (what it may ask
for, and the Act's exemptions), whether a tenant may leave and on what terms, and whether its
state lets it charge a lease-break fee and how much.

### Moving to another unit (migration 0091)

**The rule.**
- A security deposit is the tenant's money, held in trust for them, in every state; when a tenancy
  ends it goes back less what the tenant owes under the lease, with an itemized statement, by a
  deadline the state sets (California's 21 days, Civil Code 1950.5(g), the shortest found; West
  Virginia's is in W. Va. Code 37-6A-2). A tenant moving to another unit ends the tenancy of the
  old one. Keeping the deposit held for the new lease, instead of returning it, rests on the
  tenant's agreement; writing down the day they agreed is how a company shows it. No state's
  statute was found saying so in words: NOT VERIFIED.
- Money held in trust is the owner's it belongs to, in every state (the commingling rules above):
  rent the old lease owed its owner, paid after the move, is that owner's money, even when the
  tenant now rents from another owner.

**What the PMS does.**
- The move needs the day the tenants agreed in writing that their deposits stay held, whenever
  they hold one; the deposits then stay in the security-deposit trust account, theirs as before.
- What the old lease still owes stays owed to its owner: a payment on the new lease pays it first,
  into that owner's account, matched to the old lease's own charges (rent stays rent); the
  tenants' prepaid rent pays it first at the move. At final move-out the deposits pay it first,
  each lease's for its own owner, and the statement names each lease moved from and the home of
  each charge kept for.
- Prepaid rent moves with the tenants to the new owner's trust bank account, with its cash, and
  the page tells staff to move that money at the bank.

**Left to the company.** Whether its state treats the move as the end of the tenancy (and so asks
a statement and a deadline anyway), inspecting the old unit and charging what was damaged there on
the old lease, and the written agreement itself.

### A pet or key deposit (migration 0092)

**The rule.**
- A refundable deposit is held in trust and returned under the deposit rules above whatever the
  lease calls it: a pet or key deposit is money the tenant can get back, so it is a security
  deposit for the trust account, the itemized statement and the deadline. California's Civil Code
  1950.5(b) defines "security" as any payment, fee, deposit or charge meant to cover what the
  tenant may owe, and its cap (one month's rent for most landlords since AB 12, July 1, 2024)
  counts every such deposit together, a pet deposit included. Sources read: secondary summaries
  (turbotenant.com, landlordstudio.com, lucidrents.com, a LAFLA webinar deck); the statute's own
  text on leginfo.legislature.ca.gov was not read: NOT VERIFIED. Other states' caps differ and
  are not built (state rules beyond the no-negative rule are out of scope).
- An assistance animal takes no pet deposit (the Fair Housing Act, above).

**What the PMS does.**
- Staff record a security, pet or key deposit; each kind is held in a deposit account of its own
  in the security-deposit trust account, never the operating one. The lease, the register and the
  deposit return statement name each kind, and the lease shows what all of them come to, so a
  company can hold the total to its state's cap.
- A pet deposit is refused for a tenant whose only animals on their leases are assistance animals.
- A tenant leaving moves each kind to the staying tenant's of the same kind. At move-out the return
  keeps what is owed from each tenant's security deposit first, then the key and pet deposits, and
  returns the rest of each.

**Left to the company.** Its state's cap and whether it allows a separate pet or key deposit at
all, and any non-refundable fee (which the PMS records as a charge, not a deposit).

## A bill shared across properties, and the company's markup on a bill (migration 0083)

**The rule.** A manager is the owner's agent: it pays out an owner's trust money only for that
owner, never more than that owner holds (the record-keeping rules under "Unpaid bills carried
over at cutover"), and it takes from the owner only the compensation the owner agreed to,
disclosed, with no secret profit from anyone else.

- California: the Department of Real Estate has charged an undisclosed 5% markup on repairs and
  maintenance, and vendor discounts kept while billing owners the full price, as a "secret or
  undisclosed amount of compensation" under Business and Professions Code 10176(g) (accusations
  read in search excerpts of dre.ca.gov hearing files; the statute's own text is NOT VERIFIED).
- West Virginia: W. Va. Code 30-40-19(a)(14) disciplines a licensee who pays or receives any
  rebate, profit, compensation or commission from anyone other than their principal (read in
  search excerpts of code.wvlegislature.gov; the full section is NOT VERIFIED).
- No state was found that sets a markup's size, or forbids one the owner agreed to in writing
  (NOT VERIFIED that none does).

**What the PMS does.**
- One vendor invoice shared across properties is a bill for each owner's property account, each
  its share, set aside from that owner's money and held back from that owner's draws alone. No
  owner's money pays another owner's share, and each share waits for its own owner's approval
  over their limit.
- A markup is only the percent the owner's management agreement in force on the bill's date sets
  (none unless it sets one), added only to the bills staff mark up. It is taken with the bill's
  money into the company's fee account, on a line of its own on the owner's statement ("Markup of
  10% on bill ROOF-77/1"), with the management fees in the income statement and the year-end
  package. The owner sees it before: the approval email and the portal name it.
- The PMS records no rebate or discount from a vendor.

**Left to the company.**
- Putting the markup in the written management agreement, in words the owner reads as one (the
  PMS keeps only its percent, not the agreement's text).
- The approval limit counts the vendor's amount, not the bill with its markup; a company whose
  agreement counts both asks the owner on that basis.
- Taking the markup out of trust with the company's other fees (migration 0059).

## A property owned in shares (migration 0094)

**The rule.** Money a manager holds in trust is accounted for by whom it belongs to: a record for
each owner (each beneficiary) of what came in for them, what went out and what they hold, tied
each month to the bank. When two or more people own a property, each is an owner whose money is
theirs, and no owner's money pays what another owes.

- California: 10 CCR 2831.1 requires a separate record for each beneficiary or transaction of
  every deposit to and payment from the trust account, with a running balance, and 2831.2 a
  monthly reconciliation of those records against the account's own record (read in search
  excerpts of the regulation on regulations.justia.com and of dre.ca.gov; the regulation's text
  as now in force is NOT VERIFIED).
- West Virginia: W. Va. Code 30-40-18 requires a broker who holds others' money, rental receipts
  and security deposits among it, to keep it in trust fund accounts and reconcile them (read in
  search excerpts of code.wvlegislature.gov). That its rules (W. Va. CSR 174-1) also ask a ledger
  for each owner rests on study guides only: NOT VERIFIED.
- No state was found that sets how co-owners divide rent or costs: that is the owners' agreement
  (NOT VERIFIED that none does).

**What the PMS does.**
- Each co-owner has an owner's account of their own at the property, so each one's money has its
  own record, statement and draws, as before.
- A property's ownership is each owner's percent from a day, together 100, all their accounts in
  one trust bank account; a new one from a later day takes over from it, and earlier days keep
  the shares they had. Staff set it on the property's page, naming who did.
- From that day a tenant's payment, prepaid rent used and a deposit kept at move-out go to the
  owners by their shares in force on the day: each owner's percent rounded down to the cent, and
  the cents left over one each to the largest remainders, so the parts always come to the amount
  and none is more than a cent from its exact share. Each part is matched to the lease's charges
  in full; a bounce takes back from each owner exactly what the payment gave them.
- A bill for the property can be split between its owners by their shares on the bill's date,
  a bill for each owner's share, each following every bill rule on its own (that owner's money,
  approval limit and draws).
- Without shares nothing changes: staff choose which owner's account a payment goes to.

**Left to the company.**
- The co-owners' written agreement on how they share the property's income and costs: the PMS
  keeps only the percents staff enter.
- Management fees stay each owner's account's own terms; a fee split some other way is set in
  each owner's terms.

## A housing authority's part of the rent (migration 0095)

**The rule.** Under the Housing Choice Voucher program (Section 8) a public housing agency pays
the owner its housing assistance payment (HAP) under a contract with the owner, and the family
pays the rest of the rent. It is federal law, so it is the same in every state.

- 24 CFR 982.310(b): the family is not responsible for the housing assistance payment, and the
  owner may not end the tenancy because the agency did not pay it.
- 24 CFR 982.313: the owner may use the family's security deposit for unpaid rent or other
  amounts the family owes under the lease, so not for the agency's part.
- 24 CFR 982.451(b)(4): the agency pays the HAP promptly under the contract, and a late payment
  may carry the contract's late fee, which is the agency's to pay, not the family's.

Read in search excerpts of the eCFR and of agency guidance; the regulations' full text as now in
force is NOT VERIFIED.

**What the PMS does.**
- Staff keep the company's housing authorities (Rentals, Housing authorities) and set a lease's
  assistance from the first of a month whose rent isn't charged yet: what the authority pays of
  each month's rent, at most the rent, maybe its HAP contract's number. A later month's takes
  over; 0 ends it. Append-only, naming who recorded it.
- As each month's rent is charged (the nightly run, a renewal), the authority's part (its share
  of the month's rent to the cent, prorated as the rent is) becomes the authority's own charge,
  and the tenant is charged the rest, or nothing when the authority pays it all. So the tenant's
  balance, late fees, delinquency and the deposit kept at move-out count the tenant's part
  alone: no late fee is charged on the authority's part, and no tenant money pays it.
- A lease's end set, moved or cleared moves both parts to the days in effect. A renewal keeps
  the assistance in force on its first day.
- Staff charge an authority by hand (a month from before its assistance was on file) or credit
  one of its charges (an abatement), never past what is open of it.
- An authority's payment (one check or deposit for many leases) pays what it owes on each lease,
  oldest first, never more, into the property's owners' accounts (by their ownership shares), all
  or none, recorded once per request. It counts as rent collected in the management fee, the
  income statement and the year-end package (1099 rents).

**Left to the company.**
- The HAP contract itself and the agency's rent reasonableness review: the PMS keeps the amount
  staff enter and the contract's number, nothing more. The agency's inspections and the
  household's certifications are recorded since migration 0108 (below).
- Whether its HAP contract provides a late payment fee and how much: staff set the fee and grace
  days the contract says (since migration 0116, below).

### An authority's late fee, a returned payment and a payment hold (migration 0116)

**The rule.** 24 CFR 982.451(b)(4) (above): the agency pays the HAP promptly, and the HAP contract
may provide a penalty for a late payment, the agency's to pay, never the family's; the agency
isn't obligated to pay it when HUD finds the delay beyond the agency's control. (Full text NOT
VERIFIED, as above.) A payment hold (an eviction filed, migration 0025) is about whether accepting
rent waives the eviction, which state law decides; no state's rule is built in.

**What the PMS does.**
- Staff set an authority's late payment fee from a day: grace days and a flat fee, as its HAP
  contract says (0 charges none). The nightly run and Run rent now charge it to each part of a
  rent the authority still owes at the end of its last grace day (UTC), once, on the authority.
  The tenant is never charged it. A late fee paid is a late fee on the income statements, not
  rent for the management fee.
- A payment of the authority's that the bank returned is marked bounced from its ⋯ menu: its
  money moves back out of the owners' accounts, and what it paid of each charge is owed again.
- A lease on a payment hold takes the authority's payment only when staff accept it past the
  hold, as a tenant's, naming who did.

**Left to the company.** Whether its contract provides a late fee and collecting it from the
agency; whether accepting a payment during an eviction is right in its state.
- Payment holds (an eviction filed) hold the tenant's payments only; an authority's payment is
  not held.

## Keeping an assisted lease in compliance (migration 0108)

**The rule.** An assisted household's income and size are reexamined at least once a year, and
the home is inspected against the program's standards, both on a schedule the owner or manager
has to keep up with. It is federal law, the same in every state.

- 24 CFR 982.516(a): the agency reexamines family income and composition at least annually
  (with interim reexaminations when they change).
- 24 CFR 982.405(a): the agency inspects the unit at least biennially during the assisted
  tenancy (HQS; NSPIRE-V replaces HQS, which agencies adopt by a compliance date HUD has extended,
  read as February 1, 2027).
- HUD Handbook 4350.3, chapter 7 (project-based assistance): the owner recertifies each household
  every year and gives it a first reminder 120 days before the recertification is due, then
  reminders at 90 and 60 days.

Read in search excerpts of the eCFR, HUD's notices and the Handbook; their full text as now in
force is NOT VERIFIED.

**What the PMS does.**
- Staff record, on the lease's housing assistance card, each income certification (move-in,
  annual or interim: the day it takes effect, the household's size and annual income, and the
  day the next annual one is due, a year on unless given; an interim one keeps the due day of
  the one before) and each inspection for the authority (passed, the next due two years on unless
  given; failed, with the day the home must pass a reinspection by), only for a lease with
  assistance on file and within its days, an inspection only once it happened. Append-only,
  naming who recorded it.
- The card and the Housing assistance compliance report say where each stands on a day:
  current, due within 120 days (the Handbook's first reminder), overdue, none on file, or a failed
  inspection's reinspection due or overdue; the report counts what to certify and inspect now and
  within 120 days.
- A household's income is as private as an application's: the AI never reads a certification,
  and the report prints days and words, never income or household size.

**Left to the company.**
- The reexamination and the inspection themselves, the reminder letters, HUD's forms (50058,
  50059) and TRACS: the PMS files and sends nothing, and checks no income limit, rent limit or
  inspection standard.
- An agency that inspects every year, or a stricter schedule: staff give the due day.

## Utility billing: RUBS and submeters (migration 0096)

**The rule.** A tenant owes only what their lease says they owe, so a utility is billed back only
to a lease that says so. Where a state regulates billing a master-metered utility back to
tenants, the floor found is: the lease describes how the bill is shared, and the tenants together
are never charged more than the bill.

- Texas: Water Code 13.5031 has the utility commission set rules for sharing a master-metered
  bill among tenants, and asks the lease for a clear written description of the method and the
  last year's average bill per unit. Its rules (16 TAC chapter 24, earlier 30 TAC 291) are said
  to keep tenants from being charged more than the property's bill and to leave the common areas
  and empty units' use to the owner (read in search excerpts and a commission help page: NOT
  VERIFIED).
- California: Civil Code 1954.201 and the sections after it set the rules for water billed by
  submeter in rental units (each submeter measuring only its unit's water, readable by the tenant
  and the landlord); a summary says they leave ratio billing (RUBS) as it was (read in search
  excerpts of the bill, SB 7 of 2016: NOT VERIFIED).
- Other states may forbid billing some utilities back, cap a fee for it or ask more of the lease:
  NOT VERIFIED; the strictest found is built below.

**What the PMS does.**
- A property's plan for each utility from a day: by unit (each unit's days), by square feet, by
  occupants (the tenants who haven't left and others living there, for their days) or by
  submeter (each unit's reading of the master meter's), and the owner's share for the common
  areas. 'Not billed back' stops it.
- A lease is billed for a utility only from the day staff record that its lease says so.
- A bill is shared by the plan in force on its first day. The owner's share comes off first; each
  billed lease's share is its part of the rest, rounded down to the cent, and charged to it as a
  fee naming the utility, the period and the method. So the tenants together are never charged
  more than the bill less the owner's share. A vacant unit's days (by unit and by square feet), a
  lease not billed for it, the common areas and every part of a cent stay with the owner, never
  shifted onto another lease.
- No two bills of a property for a utility cover the same day. A preview shows each lease's share
  before anything is charged. No fee for billing is added.

**Left to the company.**
- The lease's own words on how the bill is shared, the average bill a state asks it to state, and
  any registration with a state commission.
- Whether a utility may be billed back where the property is.

## Maintenance: emergencies, photos, entry and the visit (migration 0084)

**The rule.** A landlord keeps a home fit to live in and fixes what makes it unfit within a
reasonable time, sooner when it is an emergency (no heat, no water, a leak, a gas smell). What is
reasonable is the state's, not the PMS's:

- West Virginia: W. Va. Code 37-6-30 sets the landlord's duty to keep the home fit and in repair
  (read in search excerpts; the section's text is NOT VERIFIED). Ch. 37, art. 6 has no
  entry-notice section, per a survey of the chapter; the Attorney General's renters' guide, as
  quoted, says a landlord enters only with the tenant's consent or reasonable notice, unless an
  emergency repair requires it (both NOT VERIFIED against the code).
- California: Civil Code 1954 lets a landlord enter only for the reasons it lists (an emergency,
  repairs, showings...), with written, reasonable notice (24 hours presumed reasonable) except in
  an emergency or when the tenant agrees at the time (read in secondary sources; the statute's
  text is NOT VERIFIED). The strictest found.
- That every state shares a duty of habitability and an emergency exception to entry notice is
  NOT VERIFIED state by state; no state was found without them.

**What the PMS does.**
- A tenant marks a request an emergency and may file up to three photos with it (JPEG, PNG or
  PDF, 5 MB each, kept as documents about the request; the AI never reads a file).
- An emergency's work order opens urgent, and each staff member on call (Work orders, On call;
  append-only, naming who changed it) is emailed at once while the server sends email. The work
  order says whom it went to. No text message is sent (out of scope).
- The tenant's word on entry ("someone may come in while I'm out", or not) is copied to the work
  order's entry ("may let themselves in", or "call the tenant first"), and the vendor's email says
  it.
- A scheduled visit names its day and, if staff say, its window ("8-10 am"); the tenant sees both
  in the portal, and the vendor's email gives the window.
- Each work order counts its days open, to the day it closed.

**Left to the company.** Whether a request is an emergency in its state's terms and how fast it
must act; the written entry notice its state asks (the portal's visit day and window may not be
one); and anything a tenant may do when a repair waits too long (repair and deduct, withholding).

### Charging a tenant for a repair, and recurring work (migration 0085)

**The rule.** A tenant answers for damage beyond normal wear and tear that they, their household
or their guests cause; a landlord may charge them for its repair, and may take it from the
security deposit at move-out under the deposit rules above. What counts as normal wear and tear,
and what a lease may shift to a tenant, is each state's (NOT VERIFIED state by state; West
Virginia's W. Va. Code 37-6A-1 counts "damages" beyond ordinary wear and tear against a deposit,
read in search excerpts, NOT VERIFIED). No state was found that limits recurring upkeep a landlord
schedules itself.

**What the PMS does.**
- A charge for a work order is an ordinary one-off fee of a lease at the work order's unit (or at
  a unit of its property when it is the whole property's), naming the work order; never a credit.
  The owner still pays the vendor's bill; what the tenant pays of the charge goes to the owner's
  account as any charge does. The work order lists what tenants were charged and what is open.
- A recurring work order opens on its day every so many months, once per day, with its priority
  and vendor (none when the vendor's insurance ran out); it moves no money.

**Left to the company.** Whether the damage is the tenant's and not wear and tear, and any notice
or itemization its state asks before charging it.

### A vendor's link (migration 0086)

**The rule.** No state rule was found on how a landlord tells a vendor about a job or hears back
from one. A tenant's privacy is: the vendor needs where to go, what is wrong and how to get in,
and no more (NOT VERIFIED as a rule of any state; it is the PMS's own care).

**What the PMS does.**
- A vendor with a link sees only the open work orders last assigned to them: the property and
  unit, what is wrong, priority, the day due, entry in the words their email uses, and the visit.
  No tenant's name, phone or email reaches them through it.
- They schedule a visit or say the work is done, and nothing else; the work order's timeline says
  it came from the vendor. Reassigned, a work order leaves their link; taken back, the link opens
  nothing. The token is kept only as its SHA-256.

**Left to the company.** The entry notice its state asks before the visit (the link's schedule
is not one), and checking the work before paying the vendor's bill.

## A listing's photos and days listed (migration 0087)

**The rule.** The federal Fair Housing Act, which applies in every state, forbids making, printing
or publishing any notice, statement or advertisement for renting a dwelling that indicates a
preference, limitation or discrimination based on race, color, religion, sex, handicap, familial
status or national origin (42 U.S.C. 3604(c); HUD's rule on advertisements, 24 CFR 100.75). A
listing's photos and description are an advertisement. Many states and cities add protected
classes (source of income, for one) (NOT VERIFIED state by state). How long a unit has been listed
is the company's own figure; no state rule on it was found.

**What the PMS does.**
- The public page shows the photos staff add to a listed unit (JPEG or PNG, twelve at most), and
  only while it is listed; a removed photo leaves it. The PMS reads nothing out of a photo.
- Listings shows how many days each listing has been up, counting the day it went up.

**Left to the company.** What its photos and words show and say (no people in a way that signals
who may live there, nothing that discourages anyone of a protected class), and its state's and
city's protected classes.

## Leads and showings (migration 0088)

**The rule.** The federal Fair Housing Act, which applies in every state, forbids refusing to rent
or negotiate, or making a dwelling unavailable, and setting different terms, conditions or
services, because of race, color, religion, sex, handicap, familial status or national origin,
and forbids saying a dwelling is not available for inspection or rental when it is (42 U.S.C.
3604(a), (b) and (d)). HUD's rule lists steering and discouraging prospects among the prohibited
practices (24 CFR 100.50 to 100.70; its sections read from memory, NOT VERIFIED against the CFR's
text). Many states and cities add protected classes (NOT VERIFIED state by state).

**What the PMS does.**
- Every lead is recorded the same way: a name, a way to reach them, what they asked about, where
  they came from, when they would move in. Nothing a fair housing law protects is asked or kept.
- Each showing and how it went, and why a lead was lost, are kept with who recorded them, so the
  company can show it treated prospects alike.

**Left to the company.** Treating every prospect alike (the same units offered, the same terms,
the same follow-up), and what its staff write in a lead's note or a closing's reason.

## Screening criteria, decisions against a screening and the notice (migrations 0089 and 0090)

**The rule.**
- The federal Fair Housing Act, which applies in every state, forbids refusing to rent, or setting
  different terms, because of race, color, religion, sex, handicap, familial status or national
  origin (42 U.S.C. 3604(a) and (b)). Applying the same written criteria to every applicant, and
  writing down why a decision departs from them, is how a landlord shows it did not; HUD's
  guidance says so for criminal-history screening in particular (HUD Office of General Counsel,
  April 4, 2016; read from memory, NOT VERIFIED against its text).
- The federal Fair Credit Reporting Act, in every state, asks a landlord who takes adverse action
  based in whole or in part on a consumer report to give the applicant notice: the reporting
  agency's name and contact, that the agency did not decide, and the right to a free copy within
  60 days and to dispute it (15 U.S.C. 1681m(a)). The PMS already records it (migration 0030) and
  emails it (migration 0077); the status page now shows it too.
- Some states and cities limit what screening may weigh (source of income, criminal history,
  credit for voucher holders) (NOT VERIFIED state by state; none is built in).

**What the PMS does.**
- A company sets its criteria once, and the screening partner applies them to every applicant;
  each screening names the criteria it applied. The apply page shows every applicant the same
  words before they apply.
- A decision that goes against the latest screening's recommendation is refused unless it says
  why, and the applicant's page marks it, so each departure is on the record.
- A declined or conditionally approved applicant reads the adverse action notice on their status
  page. Sending a lease for signing emails each signer their own link to the PMS's signing page
  (below).

**Left to the company.** Its criteria themselves (legal in its state and city), applying them the
same way to every applicant, and its reasons when it departs from them.

## Signing a lease electronically (migration 0097, ADR 0008)

**The rule.**
- Federal ESIGN, in every state: a signature or contract is not denied legal effect only because
  it is electronic, and an electronic signature is any sound, symbol or process attached to a
  record and adopted with the intent to sign it (15 U.S.C. 7001(a), 7006(5)).
- Before a record the law requires in writing is given to a consumer electronically, the consumer
  agrees after a clear and conspicuous statement of: their right to have it on paper, and to
  withdraw their agreement (with what follows and any fee); whether the agreement covers this
  transaction only; how to withdraw it and update their contact details; how to get a paper copy
  after agreeing, and any fee; and the hardware and software needed. They agree electronically in
  a way that reasonably shows they can open the electronic form (7001(c)(1)). Read in search
  excerpts of the code's text; the hardware and software clause's exact words are NOT VERIFIED.
- An electronic contract may be denied effect if it can't be kept and accurately reproduced by
  everyone entitled to it (7001(e)).
- ESIGN's rule leaves out notices of default, eviction or the right to cure under a rental
  agreement for a primary residence (7003(b)(2)(B)): those follow state law, and in practice go
  on paper.
- The Uniform Electronic Transactions Act, adopted by 49 states and DC, says the same for
  transactions between parties who agree to deal electronically; an electronic signature is the
  person's if it was their act, which may be shown by any means, including the security procedure
  used (UETA 9). New York, the one state without UETA, has its own Electronic Signatures and
  Records Act, under which an electronic signature binds as a handwritten one does. (Adoption
  count from search excerpts, Illinois the 49th in 2021: NOT VERIFIED against each state's code.)

**What the PMS does.**
- Each signer signs through a link of their own, sent to their email, which works for 30 days.
- They read the disclosures above, naming how to reach the company, and agree; the words they
  agreed to are kept. Paper is free and there is no fee to withdraw. "I'd rather sign on paper"
  is on the page, before and after agreeing, and ends the link.
- They open the lease (the very PDF sent) before they can sign, which shows they can open it.
- They type their name as it is on the offer, may draw their signature, and tick that they read
  the lease and sign it. The signature names the lease's SHA-256.
- Every step keeps its time, address and browser; nothing is rewritten. The signer downloads the
  lease and a signing certificate saying all of it; staff download the certificate too.
- The PMS sends no eviction or default notice electronically.

**Left to the company.**
- Keeping the promises the disclosures make for it: the lease on paper, and a paper copy, at no
  charge, to whoever asks.
- Whether its state asks more of a lease's signing (a separate signature beside a disclosure, a
  witness) and the lease's own words.

## The 1099 summary's threshold (migration 0113)

**The rule.** A business that pays a non-employee for services reports it on Form 1099-NEC (rents
and some other payments on 1099-MISC) once the year's payments reach a threshold. That threshold
was $600 for payments made through December 31, 2025. The One Big Beautiful Bill Act (Pub. L.
119-21, section 70433) raised it to $2,000 for payments made after December 31, 2025, adjusted
for inflation from 2027. (From tax practitioners' summaries of the Act, e.g. NATP and Sovos; the
statute's text and the IRS's Publication 1099 figures are NOT VERIFIED here.)

**What the PMS does.**
- The 1099 summary lists the vendors whose latest profile marks them for a 1099. Each paid the
  year's threshold or more (600.00 through 2025, 2000.00 for 2026) is on it and in its total;
  one paid less is listed under the threshold, so nothing marked disappears.
- A vendor with no profile is listed apart as having no 1099 setting, so staff can say; those
  marked for none are one line with how many and what they were paid.
- For 2027 and later it uses 2000.00 and says on its threshold line that the inflation
  adjustment is not known to it.
- It counts what the old system paid a vendor in the cutover's year.

**Left to the company.** Whether a vendor gets a 1099 at all (a corporation usually doesn't; the
W-9 says), the year's adjusted threshold from 2027, and filing: the PMS files nothing and keeps
no tax ID.
