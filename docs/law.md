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
