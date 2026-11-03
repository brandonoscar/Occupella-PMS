# Changelog

Each release of the PMS, newest first: what changed for staff, owners and tenants, each new
database migration, anything a company must do to upgrade, and every security fix. How a release
is made and taken: docs/release.md. Installing for the first time: docs/install.md.

## v2026.11.0 (first release)

The first tagged release: a whole trust-accounting PMS for property management companies, free
under Apache-2.0, run on the company's own server (docs/install.md). Its database is at migration
`20261102000150`.

**For staff**
- **Trust accounting that can't go wrong quietly:** a double-entry ledger that is append-only (a
  mistake is fixed by a reversing entry), owners' money and deposits each in their own kind of
  trust bank account, no account but a bank's ever below zero, every trust account tying out at
  every commit, and every write in an audit log naming who made it.
- **Rentals:** properties in groups, units, leases with co-tenants, household members, pets and
  guarantors; the rent roll; rent, recurring charges, prorated months, late fees and NSF fees that
  post by themselves each night; rent changes with notice; each lease's own due day; payment holds
  for evictions; renters insurance; move-out with an itemized deposit return.
- **Money in:** Receive payment (check, money order, EFT, cash) matched to what is owed, rent first;
  a bank's or lockbox's payment file; receipts; bounces with the NSF fee; bank deposits with their
  slips.
- **Owners' money:** management agreements with reserves and approval limits; management, leasing
  and other fees; owners paid by ACH file or printed check, in batches, from a draw day; owner
  contributions; the company's earned fees taken out of trust in time.
- **Bills:** vendors with W-9s, insurance and 1099 status; bills split across expense accounts or
  shared across properties, with markups; owner approval in the portal; recurring bills; pay bills
  by check or ACH file.
- **Month-end:** bank statements imported (CSV or OFX), matched, and a three-way reconciliation
  prepared by one person and approved by another; journal entries with two-person approval; close
  a year.
- **Maintenance:** work orders with priority, photos, visit windows and a vendor's private link;
  tenants' requests, with emergencies emailed to whoever is on call; recurring jobs; charging a
  tenant for a repair.
- **Leasing:** listings on the company's public vacancy page, applications with households,
  showings, screening criteria, adverse action notices, and leases signed on the PMS's own signing
  page (no e-sign service needed); renewals.
- **Reports, on screen and to download** (PDF, CSV, Excel): rent roll, delinquency, trial balance,
  balance sheet, income statement, cash flow, general ledger, owner statements and year-end
  packages, 1099 summaries, vacancy, expirations, budgets against actuals, and more; a QuickBooks
  export; the whole ledger and every record as a zip.
- **Bringing a company over** from another system: CSV files, opening balances, the checks and
  deposits not yet cleared, unpaid bills, and history, with a tie-out staff sign off.
- **Staff roles** (admin, approve, disburse, and custom roles by area), each trust account's broker
  and signers, two-step sign-in with an authenticator app for every staff member.
- **Email** (with an SMTP provider): invites, password resets, receipts, statements, reminders and
  notices, each turned on by the company.

**For owners (owner portal):** each property's balance and statement, the year-end package and
income statement, bills to approve or decline, their documents, and **adding money to a property's
account online**, with no fee.

**For tenants (tenant portal):** what is owed and when, the lease, payments and receipts, rent paid
online by bank and autopay, maintenance requests with photos, renters insurance, renewals to sign.

**Payments online** (optional, with the company's own Stripe account): rent by bank (ACH) with
autopay, and owners paying in; the processing fee on an owner's payment is the company's. Tested
against Stripe's documented answers, not a live account (NOT VERIFIED).

**To install:** docs/install.md: `compose.selfhost.yaml`, `.env.example` with every key a company
brings, and `python -m pms.admin setup` for the company and its first admin.

**Not in this release:** texts, listing syndication, application fees, card payments, a screening
company, state rules beyond what docs/law.md records, and turning on Occupella AI from inside the
PMS.

**Known:** a test run that crosses midnight UTC can fail tests that fix their day when they load;
the PMS itself is not affected.
