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
- A fee is taken only from an owner's account, from rent collected (the management fee) or once
  per lease (the leasing fee), and never takes the owner's account below zero.
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
  fee was earned on.
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
