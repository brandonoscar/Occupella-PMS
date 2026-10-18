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
