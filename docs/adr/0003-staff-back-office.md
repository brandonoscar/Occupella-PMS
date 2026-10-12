# ADR 0003: The staff back office

Decided by the founder on 2026-10-12, after ADR 0002's list (exports, owner and tenant
portals, leasing basics) was merged.

## Decision

The accounting the database already does gets the screens and API staff need to run a PMC
day to day, so nothing takes hand-written SQL. Built in this order, one PR at a time:

1. The back office for what is built: setting up the books; recording and matching tenant
   payments, the rent run, late fees and bounces; bills, fees, owner draws and contributions;
   approving the monthly reconciliation.
2. Bank statement import (CSV or OFX) and matching, for the three-way reconciliation.
3. A switching tool: CSV import of owners, properties, units, tenants, leases and vendors, and
   the opening balances with a tie-out report.
4. Staff roles, with disbursement limited to the staff a PMC authorizes.
5. Move-out: ending a lease and returning the security deposit with its itemized deductions.
6. Documents: W-9s, receipts, move-out photos, owner documents.

## What still holds

Everything ADR 0002 keeps out stays out: Stripe and online payments, bank files (ACH or
NACHA), email sending, 1099 filing, state rules beyond the no-negative rule, any deploy. The
back office records what happened at the bank; it never sends money anywhere. The AI role
gets no new grant.

## Setting up the books, as built

Staff set up their PMC at `/setup` (`src/pms/api/setup.py`): trust bank accounts (each with
its cash ledger account, and an operating one with the PMC's fee account), owners, properties
with an owner account per owner in an operating trust account, units, tenants, vendors,
management terms and late fee terms (each in force from a day), and the leases of tenants
already in place (a new tenant's lease comes through a lease offer). Every write is a plain
insert the app role may make or one of the database's write paths, so the database's rules
hold whoever calls, and each names the staff member to the audit log. Nothing there moves
money.

## Rent work, as built

Staff do the rent work at `/rent` (`src/pms/api/rent.py`):

- The rent run charges every lease in effect on a due date its rent (`trust_charge_rent_due`),
  once per lease and date; late fees are assessed as of today or an earlier day
  (`trust_assess_late_fees`). A fee or a credit is added to a lease by hand.
- A tenant's payment is recorded as it reached the operating trust account. The owner's account
  takes what the lease owes (rent and fees, less credits, less what was paid, plus what
  bounced), matched to the open charges oldest first; anything more is held as the first
  tenant's prepaid rent. Prepaid rent is used against new charges the same way. When the
  property has more than one owner account, staff choose which one the rent pays.
- A security deposit is recorded into the security deposit trust account, held for the tenant.
- A payment the bank returned is recorded as bounced: what it moved goes back to cash and its
  matches are reversed, once. It is refused when the owner's account (or the prepaid rent) no
  longer holds the money, since an owner's account never goes below zero.
- Each record carries a request id the page makes, its key in `trust_post_transfers`, so a
  double click or a retry records nothing new. Records are dated on or before today; a payment
  hold or a closed period refuses them, with nothing written.

