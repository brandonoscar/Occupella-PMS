# State and city rules: research of 2026-10-23

The research behind ADR 0009's state rules (`docs/adr/0009-buildium-parity.md`). Eight topics were
researched on 2026-10-23; this file keeps the parts about the law: deposits, late and NSF fees,
rent increases, local rent control, notices and how they are served, what a tenant may be charged
to pay online, trust accounts, and how to build rules from a property's address. The parts about
the hosted edition's business, competitors' prices and renters insurance stay out of the repo.

**Every per-state value here came from search-engine excerpts, not from statute text. Each is a
lead, NOT VERIFIED, until read in the statute; nothing here drives a rule until then.** A rule
enters the rules table only from the statute's own text, cited and dated (`docs/law.md`'s method).
This is not legal advice.

## Every per-state value here is a lead, not law

Eight research notes were written on 2026-10-23, covering:

- deposits;
- late fees, NSF fees and rent increases;
- local rent control;
- notices and delivery;
- online payments;
- trust accounts;
- renters insurance;
- geocoding and rules design.

All eight hit the same wall:

- WebFetch failed DNS, or got a proxy 403, on every statute, regulator and vendor-docs site tried (leginfo, law.cornell.edu, nysenate.gov, dre.ca.gov, docs.stripe.com, census.gov and others).
- The shared web-search budget ran out after roughly 9 to 16 searches per topic.

**No statute page was opened.** Every figure rests on a search engine's summary of a page. The strongest are excerpts that quote official pages:

- Oregon DAS and Washington Commerce cap releases;
- California county and city rent pages;
- North Carolina's G.S. 25-3-506 PDF;
- ORS 90.222, Va. Code 55.1-1206, OAR 863-025-0028, 22 TAC 535.146 and 15 U.S.C. 7003;
- the San Francisco, Oakland, Berkeley, DC and Montgomery County rent pages.

The notes also hold 51-row working tables for deposits, late fees, NSF fees, rent-increase notice and nonpayment notices. Most of those cells are the researchers' recall, marked [K], R-H/R-M/R-L or "model knowledge", and every such cell is **NOT VERIFIED**.

This report cites only values that came with a source link and keeps the notes' NOT VERIFIED marks. The notes' 51-row working tables (most cells the researchers' recall) are kept outside the repo as a checklist for the statute pass, not as data. This is not legal advice.

When this was written, main (461cd3c, migration 0125) already showed each ended lease's deposit due-back day at the strictest value found (14 days after the last day, or 10 while the lease owes nothing), kept receipts for deductions and stored a staff-entered deposit-interest rate per property (`docs/law.md`, migrations 0120 and 0122); its notice templates (migration 0119) check no state's notice period or wording.

## Deposits: caps are converging on one month, but each state starts the clock differently

**Caps are moving toward one month.**

- **California** (AB 12): one month's rent, furnished or not, for deposits collected from **July 1, 2024**. A natural-person owner with no more than two properties and four units may ask two months, but never of a service member ([BHFS](https://www.bhfs.com/insights/alerts-articles/2024/california-security-deposit-limits-effective-july-1-2024); [AB 12 text](https://legiscan.com/CA/text/AB12/2023)).
- **Maryland**: one month **including any pet deposit**, for leases from October 1, 2024. An overcharge costs up to three times the excess ([MD DHCD FAQ](https://dhcd.maryland.gov/TurningTheKey/Documents/HB693-FAQ.pdf)).
- **Georgia**: a first-ever two-month cap from July 1, 2024 ([Georgia General Assembly](https://www.legis.ga.gov/api/legislation/document/20232024/214640)).
- **Colorado**: unresolved. Sources give one month, two months or none ([Ezel](https://ezel.ai/surveys/security-deposit-return-deadlines/colorado); [Captain Legal](https://www.captain.legal/us/blog/security-deposit-state-laws/)).
- **Washington**: whether HB 1217 as enacted kept its introduced one-month cap on deposit plus move-in fees is also unresolved. The AG's summary doesn't mention one ([WA AG](https://www.atg.wa.gov/landlord-tenant); [HB 1217](https://lawfilesext.leg.wa.gov/Biennium/2025-26/Htm/Bills/House%20Bills/1217.htm)).
- **Seattle**, from a commercial source only: caps the deposit plus non-refundable move-in fees at one month in total ([Utopia Management](https://utopiamanagement.com/seattle-security-deposit-laws)).

Main keeps pet and key deposits apart from the security deposit (migration 0092). A cap check therefore has to add them back in Maryland, and add non-refundable fees in Seattle.

| Jurisdiction | Cap | Return deadline, and what starts the clock | What else the screen needs | Source |
|---|---|---|---|---|
| California | 1 month from 7/1/2024; 2 for a small natural-person owner, never for a service member | 21 days after the tenant vacates | Move-out photos from 4/1/2025, move-in photos for tenancies from 7/1/2025; electronic refund when the tenant paid electronically, statement by email if agreed (from 1/1/2026) | [CAA](https://caanet.org/april-1-is-fast-approaching-get-ready-for-new-rent-reporting-and-security-deposit-photo-requirements/); [AAOC AB 414 FAQ](https://assets.noviams.com/novi-file-uploads/aaoc/AB_414_FAQs.pdf) |
| Maryland | 1 month including pet deposit (leases from 10/1/2024) | not sourced | Interest at the greater of 1.5% or the 1-year Treasury rate | [MD DHCD](https://dhcd.maryland.gov/TurningTheKey/Documents/HB693-FAQ.pdf); [Landlord Studio](https://www.landlordstudio.com/blog/security-deposit-laws-by-state) |
| Georgia | 2 months (from 7/1/2024) | 30 days after the landlord regains possession | No itemized list, no withholding; treble damages unless a bona fide error | [Nolo deadlines chart](https://www.nolo.com/landlord-tenant/state-deadlines-for-returning-security-deposit.html) |
| Colorado | conflict: 1, 2 or none | 30 days after the lease ends or surrender; a lease may set up to 60 (from 1/1/2026) | Documents behind a deduction within 14 days of the tenant's request | [Otten Johnson](https://www.ottenjohnson.com/news/changes-to-the-colorado-security-deposit-statute-to-take-effect-january-1-2026/) |
| DC | 1 month | 45 days to refund with interest or give notice of withholding, then 30 more for the balance and statement | Interest-bearing escrow at a DC institution; rate reset Jan 1 and Jul 1 | [Ezel DC](https://ezel.ai/surveys/security-deposit-return-deadlines/district-of-columbia) |
| Connecticut | not sourced | 21 days (from 10/1/2023), or 15 after a forwarding address if later; old tables still say 30 | Yearly interest; 2026 rate 0.49% per one site (NOT VERIFIED) | [securitydepositlaws.com](https://securitydepositlaws.com/states/) |
| Massachusetts | not sourced | not sourced | Separate interest-bearing account at a Massachusetts bank; 5% or the bank's rate; penalty listed as both 2x and 3x | [Landlord Studio](https://www.landlordstudio.com/blog/security-deposit-laws-by-state) |
| Florida | none | 15 days if nothing is claimed; a claim notice by **certified mail** within 30, then 15 days to object | The claim notice cannot go by email | [securitydepositlaws.com](https://securitydepositlaws.com/states/) |
| Delaware | 1 month on leases of a year or more | 20 days | No itemized list in time means nothing is owed | [Nolo](https://nolo.com/legal-encyclopedia/security-deposit-limits-deadlines-your-state-36186.html) |
| Arizona | 1.5 months | 14 days excluding weekends and holidays, after termination, possession and the tenant's demand | 2x what was wrongly kept | [Skybriz](https://www.skybriz.com/insights/security-deposit-return-deadline/) |
| South Dakota | not sourced | 21 days (was two weeks) after the tenancy ends **and** the landlord has a mailing address; 2026 SB 4, effective date NOT VERIFIED | Specific reasons in writing | [SD session law](https://mylrc.sdlegislature.gov/api/Documents/SessionLaw/300761.pdf?Year=2026) |
| Louisiana | not sourced | One month; from 8/1/2026 the itemized statement may follow within 15 more days | — | [Louisiana Legislature](https://www.legis.la.gov/Legis/ViewDocument.aspx?d=1442426) |
| Chicago | not sourced | not sourced | 2026 interest rate 0.01%; the Comptroller's rate summary "MUST be included with all Chicago leases" that take a deposit | [CAAPTS](https://www.caapts.org/news/2026-illinois-and-chicago-security-deposit-interest-rates) |

**Main's 14-day/10-day due-back day stays a safe floor.** Every sourced deadline is that long or longer, and several states start their clock later than the lease's last day:

- at possession (Georgia);
- at a forwarding or mailing address (Connecticut, South Dakota);
- at the tenant's demand (Arizona).

A per-state screen would therefore mostly let a company use its own, longer deadline lawfully. It can compute that deadline only if the PMS records the event that starts the clock. Today main stores the lease's last day and the tenant's contact mailing address, not the day possession came back, the day a forwarding address arrived, or a demand.

**Online payments make California's new rules computable.** AB 414 (from January 1, 2026) requires the remainder to go back electronically when rent or the deposit was paid electronically, unless both sides agree otherwise in writing ([Senate Judiciary analysis](https://sjud.senate.ca.gov/media/1823)). Once the PMS takes payments, it knows how each tenant paid. The refund can then ride the ACH payout file the founder just brought into scope. AB 2801's photos are similar: main already keeps move-out photos, but California now wants move-in photos for tenancies from July 1, 2025 and photos after repairs ([SF.gov](https://www.sf.gov/upcoming-changes-to-security-deposit-laws-photograph-requirements)).

**Interest needs a rate per regime, not just per property.** The rate sources differ:

- a fixed or floored rate (Maryland, Massachusetts);
- a rate set by a regulator (Connecticut, Chicago);
- the holding bank's rate, reset twice a year (DC).

Illinois adds interest for buildings of 25 or more units at a state-published rate ([IDFPR](https://idfpr.illinois.gov/news/2026/interest-rates-affecting-security-deposit-act.html)). Berkeley and San Francisco have their own interest ordinances ([Berkeley Rent Board](https://rentboard.berkeleyca.gov/elected-rent-board/news/2026-aga-published-and-2025-security-deposit-interest-payment-due-soon)). Main's per-property rate (migration 0122) is the right storage. The rules table should prefill it and flag a property whose rate doesn't match its jurisdiction's published one.

The PMS's separate `security_deposit` trust bank account already meets every separate-account rule. "Interest-bearing" and "at a bank in this state" are checks on which bank account the company opened, not ledger rules. Penalties belong on screen as information only.

**Inputs a deposit rule needs, which main lacks:**

- the owner's entity type and portfolio size, plus each tenant's service-member status (California);
- the deposit-collected and lease-signed dates (California, Maryland);
- the unit count per building (Illinois' 5-unit and 25-unit lines, per one source);
- the forwarding-address and possession dates.

## Late fees and NSF fees need five formula shapes and a local-time grace day

**What the surveys say.** A minority of states cap late fees by statute. None of the sources the notes found is official:

- Colorado: the greater of $50 or 5% of the overdue amount.
- Connecticut: the lesser of $5 a day (at most $50) or 5% of the late rent.
- Delaware: 5% of monthly rent.
- New York: the lesser of $50 or 5% of monthly rent.
- Hawaii: 8%.
- Iowa: a per-day formula with monthly caps.

([agreetemplate](https://www.agreetemplate.com/late-fees-by-state/); [rentceiling](https://rentceiling.com/blog/late-fee-laws-by-state-2026/); [hatchkeep](https://www.hatchkeep.com/laws/late-fee-index.html)).

The same surveys list **mandatory waits before any fee**: Massachusetts 30 days, Maine 15, Connecticut 9, Colorado 7, Oregon 4 (ORS 90.260), and 5 days in New York, North Carolina, Delaware, Tennessee, Washington and Virginia.

- California, Florida and Kansas apply a reasonableness test, and Arizona requires the fee in a written lease.
- The surveys contradict each other on Georgia, Maryland, Texas, Connecticut and Nevada. A 2026 guide says Nevada has no cap, against recall of NRS 118A.210's 5% ([Gerald guide](https://joingerald.com/learn/debt--credit/late-charge-for-rent-guide)).
- Chicago's RLTO adds a local limit ([tenantscreeningbackgroundcheck.com](https://tenantscreeningbackgroundcheck.com/late-rent-laws-by-state/)).

**Today's late-fee terms can't express these laws.** Main's terms are "flat, percent of unpaid, cap". Several recalled formulas are NOT VERIFIED, but the shapes they imply are clear:

- greater-of (Colorado, North Carolina);
- lesser-of (New York, Connecticut, Virginia);
- a per-day fee with a monthly maximum (Iowa, Connecticut);
- a percent of *monthly rent* (New York, Delaware, DC, Maine, Maryland) rather than of the unpaid amount.

The rules engine needs those five shapes. Until the values are verified, the safe default is one fee per late payment at no more than the lowest cap's value for that rent.

**The grace day is counted in the wrong time zone.** Main charges a late fee "at the end of its last grace day (UTC)". UTC midnight falls 4 to 10 hours before local midnight in US time zones. So in the "more than 5 days late" states and in Connecticut, the fee lands while the tenant's last grace day is still running locally. Grace should be counted in the property's own time zone, and in business days where a state says so. New Jersey's senior grace and Tennessee's weekend roll are recall, NOT VERIFIED.

**NSF caps run $20 to $40 and are poorly sourced.**

- North Carolina caps a returned-check fee at **$35** ([G.S. 25-3-506, ncleg.gov](https://www.ncleg.gov/enactedlegislation/statutes/pdf/bysection/chapter_25/gs_25-3-506.pdf)).
- California: **$25 for the first and $35 for later ones**. Texas: **$30** ([Nolo](https://www.nolo.com/legal-encyclopedia/bounced-check-fees-charged.html)).
- Florida tiers it by check size: $25, $30, $40, or 5% above $800 ([paidnice](https://www.paidnice.com/dictionary/returned-check-fee)).
- About 25 states have no reliable value.

Main charges "the fee in force the day it came back". California's tiers need a count of the lease's earlier returned payments.

**California's cash rule now matters for autopay.** California Civ. Code 1947.3 lets a landlord demand cash for up to three months after a bounced check, with written notice ([§ 1947.3](https://california.public.law/codes/ca_civ_code_section_1947.3)). With autopay in scope, sending that notice should turn online payment and autopay off for that lease for the period. That is an inference, but the PMS must not keep debiting a tenant the landlord has told to pay cash.

## Rent increases: three statewide caps on three calendars, and notice counted in days

Only Washington, Oregon and California cap increases statewide. Each uses "a fixed percent plus CPI, never above 10%", and each resets on a different calendar.

**Washington** (EHB 1217, May 2025):

- Cap: 7% plus the June Seattle-area CPI-U, or 10%, whichever is lower. That is **9.683% for 2026 and 10% for 2027**.
- **No increase in a tenancy's first 12 months.**
- 90 days' written notice.
- Manufactured-home spaces: 5%.
- Exempt: new construction for 12 years, public housing, low-income developments, and owner-occupied duplexes to fourplexes.
- Sources: [WA Commerce](https://www.commerce.wa.gov/commerce-announces-9-683-rent-cap-for-2026/); [WA Commerce 2027](https://content.govdelivery.com/accounts/WADOC/bulletins/420afb7).

**Oregon**:

- Cap: the lesser of 10% or 7% plus CPI. That is **9.5% for 2026** (after DAS corrected its first release) **and 10% for 2027**.
- Parks and marinas over 30 spaces: 6%.
- One increase per 12 months.
- A unit whose first certificate of occupancy is under 15 years old at the notice date is exempt, but the notice must state the facts.
- Subsidized units are exempt.
- The penalty is three months' rent.
- Sources: [Oregon DAS 2026](https://apps.oregon.gov/oregon-newsroom/OR/DAS/Posts/Post/2026-Rent-Stabilization-Percentages); [Oregon DAS 2027](https://apps.oregon.gov/oregon-newsroom/OR/DAS/Posts/Post/das-publishes-annual-maximum-rent-increase-2027); [Oregon Realtors FAQ](https://oregonrealtors.org/sites/default/files/Legislative%20Update%20for%20Oregon%20Landlords%20and%20FAQ%20%28003%29%20long%20logo.pdf).

**California** (AB 1482):

- Cap: 5% plus the regional April-to-April CPI, at most 10%.
- For August 1, 2026 to July 31, 2027: **8.7% in the Los Angeles area** and **8.8% in San Francisco and Marin**.
- Units under 15 years old are exempt.
- A single-family home or condo is exempt only if the owner is not a REIT, a corporation or an LLC with a corporate member, **and** the lease carried the written exemption notice.
- A stricter local ordinance wins.
- Sources: [SF.gov](https://sf.gov/information/california-tenant-protection-act-2019-ab-1482); [LA County DCBA](https://dcba.lacounty.gov/news/understanding-rent-increases/).

One 2026 aggregator still says only Oregon and California cap rents statewide, which shows how quickly secondary tables go stale ([TenantCloud](https://www.tenantcloud.com/rent-collection/rent-increase-laws)).

**Notice periods are longer in many places than one rental period:**

| Where | Notice | Source |
|---|---|---|
| Washington, Oregon | 90 days (Oregon: 7 for week-to-week tenancies) | [WA Commerce](https://www.commerce.wa.gov/commerce-announces-9-683-rent-cap-for-2026/); [getpropmanager](https://getpropmanager.com/guides/rent-increase-notice-periods-by-state) |
| California | 30 days, or 90 when this and other increases in 12 months total more than 10% | [ipropertymanagement](https://ipropertymanagement.com/laws/rent-increase-notice-requirements) |
| New York | 30, 60 or 90 days by tenancy length, for increases of 5% or more | [TenantCloud](https://www.tenantcloud.com/rent-collection/rent-increase-laws) |
| Rhode Island | 60 days, and 120 for tenants 62 or older; another guide gives no figure | [nationaltenantauthority](https://nationaltenantauthority.com/rent-increase-notice-requirements/); [Steadily](https://www.steadily.com/blog/rent-increase-laws-regulations-rhode-island) |
| Colorado, Georgia, Nevada | 60 days or more (Colorado's figure conflicts with 21 days in other surveys) | [ipropertymanagement](https://ipropertymanagement.com/laws/rent-increase-notice-requirements) |
| Seattle | 180 days for any housing-cost increase | [Seattle SDCI](https://www.seattle.gov/a/130803) |
| Montgomery County, Maryland | 90 days for every increase, regulated or exempt, by US mail or in person with a signed receipt | [DHCA](https://montgomerycountymd.gov/dhca/Tenants/RentStabilization.html) |

**Main's notice floor is short in February.** Main's floor is notice "by the first of the month before". For a March 1 increase that gives only 28 or 29 days, which fails 30-day states such as California's Civil Code 827. The rule should count days, not months, and take each jurisdiction's minimum from the rules table. California's extra five days for mailed notice under CCP 1013 is recall, NOT VERIFIED.

**Where the cap check runs.** It belongs at the moment staff record a rent change (`trust_rent_changes`). It needs:

- the rent in force 12 months earlier;
- the date of the last increase;
- the regime's number in force on the day the increase takes effect.

That Oregon and Washington tie the number to the effective date, not the notice date, is recall, NOT VERIFIED. The coverage tests need facts main doesn't hold: first certificate-of-occupancy date, owner entity type, owner-occupancy, units in the building, subsidy status, and space counts for parks.

## Local rent control: preemption stops local caps in 36 states, but not local notice, relocation or registration

**Who allows local caps.** As of February 2026, **36 states preempt local rent control** ([NAA](https://naahq.org/news/state-legislatures-return-rent-control-follows)). Five states let cities or counties cap rents with no statewide policy: Maine, Maryland, Minnesota, New Jersey and New York, plus DC. California has a statewide cap and lets cities go further. Oregon and Washington have statewide caps and preempt stricter local ones ([NAA Fall 2025](https://naahq.org/news/naas-rent-control-outlook-fall-2025)).

**Changes in flight:**

- Massachusetts' 1994 ban stands. The Supreme Judicial Court struck the 2026 repeal initiative from the November ballot on June 23, 2026 ([WBUR](https://www.wbur.org/news/2026/06/23/massachusetts-high-court-rent-control-ballot-question-struck)). The late-fee note's recall that the question would be on the November 3 ballot is superseded.
- Repeal bills are pending in Florida, New Mexico and Illinois, and Virginia defeated a 3% local-cap bill.
- Only two preemption statutes were confirmed by number: Texas Local Government Code 214.902 ([Caretaker](https://caretaker.com/learn/paying-rent/rent-control-in-texas), secondary) and Colorado C.R.S. 38-12-301 ([colorado.public.law](https://colorado.public.law/statutes/crs_38-12-301)).

**Preemption covers rent amounts only.** Washington preempts local caps, yet Seattle requires 180 days' notice. Illinois preempts, yet Chicago requires a deposit-interest summary with every lease. Colorado preempts, yet since HB24-1098 it requires cause to evict, with 90 days' notice for no-fault grounds ([Multifamily Dive](https://www.multifamilydive.com/news/colorado-for-cause-eviction-bill-signed/714839)). The PMS therefore needs separate switches per jurisdiction: cap, just cause, relocation, registration, and notice or disclosure.

Each regime publishes its number on its own cycle. This calendar is what a yearly refresh job must cover:

| Regime | Period the number covers | Formula | 2026 figure | Source |
|---|---|---|---|---|
| NYC Rent Guidelines Board (stabilized units) | leases from Oct 1, 2026 to Sep 30, 2027 | board vote in June | **0%** for 1- and 2-year leases | [NY1](https://ny1.com/nyc/all-boroughs/news/2026/06/25/rent-guidelines-board-final-vote-2026) |
| NY Good Cause (NYC and opt-in towns) | DHCR notice each summer | CPI + 5%, max 10% | NYC **8.38%**; 8.15% elsewhere per one law firm | [Rosenberg & Estis](https://www.rosenbergestis.com/media/blog/industry-updates/update-dhcr-revises-good-cause-eviction-law-notice); [DHCR](https://hcr.ny.gov/good-cause-eviction-law-notice-august-2026) |
| San Francisco | Mar 1 to Feb 28 | 60% of Bay Area CPI | **1.6%** | [SF Rent Board](https://www.sf.gov/news--annual-rent-increase-3126-22827-announced) |
| Oakland | Aug 1 to Jul 31 | 60% of CPI, max 3% | **2.3%** | [Oakland](https://www.oaklandca.gov/Community/Housing-Programs-Support/For-Landlords/Allowable-Rent-Increases/Learn-More-About-Allowable-Rent-Increases) |
| Berkeley | calendar year | 65% of CPI, 0–5% | **1.0%** | [Berkeley Rent Board](https://rentboard.berkeleyca.gov/sites/default/files/documents/AGA%20Public%20Notice.pdf) |
| Santa Monica | from Sep 1 | 75% of LA CPI | **2.6%** | [Santa Monica](https://www.santamonica.gov/topic-explainers/news-announcements) |
| Los Angeles City RSO | Jul 1 to Jun 30 | new formula 90% of CPI, 1–4% (start date disputed) | **3%** through 6/30/2027 (secondary) | [Nourmand](https://nourmand.com/blog/ab-1482-rent-increases-in-2026-what-la-landlords-can-actually-charge); [AAGLA](https://members.aagla.org/news/news-alert-la-city-passes-severely-reduced-rso-formula-ordinance) |
| LA County RSTPO (unincorporated) | Jul 1 to Jun 30 | 60% of CPI, max 3% | **1.919%** base (column headers unconfirmed) | [DCBA bulletin](https://dcba.lacounty.gov/wp-content/uploads/2026/03/Rent-Increase-Bulletin-RSTPO-June2026-July2027.pdf) |
| California AB 1482 | from Aug 1, by CPI region | 5% + CPI, max 10% | 8.7% LA area; 8.8% Bay Area | [LA County DCBA](https://dcba.lacounty.gov/news/understanding-rent-increases/) |
| Washington | calendar year | 7% + CPI, max 10% | 9.683% (2027: 10%) | [WA Commerce](https://www.commerce.wa.gov/commerce-announces-9-683-rent-cap-for-2026/) |
| Oregon | calendar year, published by Sep 30 | 7% + CPI, max 10% | 9.5% (2027: 10%) | [Oregon OEA](https://www.oregon.gov/das/OEA/Pages/rent-stabilization.aspx) |
| DC | from May 1 | CPI-W + 2% | **4.1%**; 2.1% for registered elderly or disabled tenants | [DC OTA](https://ota.dc.gov/node/1816006) |
| Montgomery County, MD | Jul 1 to Jun 30 | CPI-U + 3%, max 6% | **5.2%** | [DHCA](https://www.montgomerycountymd.gov/dhca/rental-housing-portal/home-page-announcements.html) |
| Prince George's County, MD | Jul 1 to Jun 30, adopted before May 1 | lower of 6% or CPI-U + 3% | not retrieved | [PG County DPIE](https://www.princegeorgescountymd.gov/departments-offices/permitting-inspections-and-enforcement/permanent-rent-stabilization-and-protection-act-prsa) |
| St. Paul | fixed | 3%, with exceptions | **3%** | [St. Paul](https://www.stpaul.gov/departments/safety-inspections/rent-buy-sell-property/renting-property/rent-stabilization) |

**Some regimes block an increase until a unit is registered:**

- San Francisco requires a **rent increase license**, which needs a Housing Inventory report first ([SF.gov](https://www.sf.gov/learn-about-rent-increases-san-francisco)).
- In DC, **an unregistered unit is rent-controlled automatically**. Exemptions are claimed on RAD Form 1, and increases go on Form 8 ([DHCD](https://dhcd.dc.gov/rentcontrol)).
- LA County requires every unit in its registry ([DCBA bulletin](https://dcba.lacounty.gov/wp-content/uploads/2026/03/Rent-Increase-Bulletin-RSTPO-June2026-July2027.pdf)).

**Some require an attachment with the increase notice:**

- Seattle requires an economic-displacement relocation notice whenever increases reach 10% within 12 months. A household at or below 80% of AMI that moves gets up to three times its monthly housing cost, collected from the landlord ([SDCI](https://www.seattle.gov/documents/Departments/SDCI/Resources/Home%20Fair%20Presentations/RentingInSeattle_HomeFair2025.pdf)).
- Oregon's notice must state the facts behind an exemption claim.
- AB 1482's single-family exemption depends on its notice being in the lease.

**Relocation is a payout, often from owner money held in trust:**

- Los Angeles: payments due within 15 days of the termination notice ([LAHD](https://housing.lacity.gov/rental-property-owners/relocation-assistance-information)).
- Portland: $2,900 to $4,500 by unit size when an increase reaches 10% in 12 months, with 31 days to pay after the tenant's request ([Portland Housing Bureau](https://www.portland.gov/phb/rental-services/renter-relocation-assistance)).

**Coverage depends on facts the PMS must record.** Coverage cutoffs are either fixed or rolling:

- fixed: LA City, certificate of occupancy on or before October 1, 1978 ([LAHD](https://housing.lacity.gov/residents/what-is-covered-under-the-rso)); DC, a permit after 1975; Prince George's, completed after 2000;
- rolling: Oregon 15 years; Montgomery County 23 years; New Jersey's new-construction exemption, 30 years or the mortgage term ([NJ DCA](https://www.nj.gov/dca/codes/publications/pdf_lti/new_const_m_dwell_law.pdf)).

Rolling cutoffs mean a property enters or leaves coverage on a date with no human action, so a nightly job should flag it. Where a coverage fact is unknown (the certificate-of-occupancy date, the owner's statewide holdings), the resolver should treat the unit as **covered** and show the stricter rule. It should never report "no rule".

**Warn, don't refuse.** Banked increases (San Francisco, Prince George's) and petitions (St. Paul, Prince George's) let a lawful increase exceed the yearly number. So the PMS can warn on a rent cap, with a recorded reason for any override, but should not refuse the increase.

## Notices: a balance email works everywhere, an emailed legal notice in three states

**Why the legal notice is different.** The founder's one button has to produce two different things. **E-SIGN excludes "any notice of default ... or eviction, or the right to cure, under ... a rental agreement for, a primary residence"** from its general rule that electronic records are valid ([15 U.S.C. 7003, govinfo](https://www.govinfo.gov/content/pkg/USCODE-2021-title15/html/USCODE-2021-title15-chap96-subchapI-sec7003.htm)). Validity then falls back on state law, and Wisconsin's UETA mirrors the exclusion ([WRA hotline memo](https://www.rfcity.org/DocumentCenter/View/3839/Wisconsin-Realtor-Association-Legal-Hotline-11-20-2018)).

Only three states were confirmed to accept email for the legal notice:

- **Florida** (Fla. Stat. 83.505, from July 1, 2025): only with a lease addendum both sides signed. Court papers still need traditional service ([Douglas Law Firm](https://douglasfirm.com/florida-electronic-rental-notices/)).
- **Texas** (SB 38, from January 1, 2026): only with the tenant's written agreement ([StopTXEviction](https://stoptxeviction.org/blog/can-a-landlord-send-an-eviction-notice-by-email-in-texas/)).
- **Michigan** (since 2015): only with written consent **and** the tenant's reply to a confirming email ([Curtis, Curtis & Brelinski](https://www.curtiscurtislaw.com/landlord-tenant-relations/2015/06/05/new-michigan-law-allows-landlords-to-serve-notices-by-email/)).

Virginia likely allows electronic notice when the rental agreement provides for it (NOT VERIFIED). Elsewhere, an eviction notice by email or text needs at least a signed written consent ([readyourlease.ai](https://www.readyourlease.ai/guides/notice-to-vacate)). Text messages, now in scope, fall under the same exclusion.

**Nonpayment notice periods vary widely.** They run from:

- none: Missouri, New Jersey and West Virginia follow a demand model;
- through 3 days in about a dozen states;
- to **14 days** in New York, Massachusetts and Minnesota ([VerticalRent](https://www.verticalrent.com/blog/eviction-notice-types-landlords); [ezel.ai](https://ezel.ai/surveys/eviction-notice-requirements)).

Minnesota gives 30 days for leases over 20 years ([Nolo](https://www.nolo.com/landlord-tenant/state-laws-termination-nonpayment-rent.html)). Arizona is 5 days under A.R.S. 33-1368(B) ([ipropertymanagement](https://ipropertymanagement.com/laws/eviction-process)). A landlord-side firm reads Arizona's June 2026 CARES Act rulings as restoring the 5-day notice from July 1, 2026 ([Scott Clark](https://scottclarklaw.com/the-end-of-an-era-what-arizonas-latest-cares-act-rulings-mean-for-landlords/)).

**Day counting is its own rule.** One source names California, Florida, Alabama, Delaware and Utah as excluding weekends and holidays. Michigan counts from the day after service. The period runs from proper service, not mailing, and many states roll a deadline that falls on a weekend to the next business day ([docugov.ai](https://docugov.ai/en/cases/eviction/eviction-nonpayment-notice)). An "earliest filing day" display therefore needs a holiday calendar per state.

**The rest of the 51-row table is NOT VERIFIED.** It covers service methods (hand delivery, an adult at the home, post and mail, certified mail; a state marshal in Connecticut) and the states that prescribe a form (California, New York, Washington, Oregon, Minnesota, Nevada, Ohio, Illinois, Florida, Connecticut and others).

| What staff press | Email allowed? | What the PMS does | Basis |
|---|---|---|---|
| **Send balance reminder** (courtesy) | Every state | Itemize open charges by due date from `trust_open_charges`, link online payment, say plainly it is a reminder and not a notice to quit; no "vacate", "eviction" or a deadline that reads like a notice period | The E-SIGN exclusion reaches only notices of default, eviction and cure ([govinfo](https://www.govinfo.gov/content/pkg/USCODE-2021-title15/html/USCODE-2021-title15-chap96-subchapI-sec7003.htm)) |
| **Prepare legal notice**, Florida / Texas / Michigan | Only with the state's consent record on file | Keep the signed addendum or agreement (and Michigan's reply) as an append-only document; keep the exact PDF and the delivery attempt; offer mail as a second channel | [Douglas](https://douglasfirm.com/florida-electronic-rental-notices/); [StopTXEviction](https://stoptxeviction.org/blog/can-a-landlord-send-an-eviction-notice-by-email-in-texas/); [Curtis](https://www.curtiscurtislaw.com/landlord-tenant-relations/2015/06/05/new-michigan-law-allows-landlords-to-serve-notices-by-email/) |
| **Prepare legal notice**, every other state | No | Print and mail through the paid add-on; show the state's period, day count and service methods as cited data, and say when the state also needs hand delivery, posting or an officer | [readyourlease.ai](https://www.readyourlease.ai/guides/notice-to-vacate) |
| **Federally covered property** (voucher, LIHTC, federally backed mortgage; the coverage definition is NOT VERIFIED) | As the state allows | Show a 30-day notice to vacate unless the state's courts say otherwise | [NAA, Arizona](https://naahq.org/news/arizona-court-rules-cares-act-notice-expired); [NAA, Washington](https://naahq.org/news/washington-supreme-court-sides-housing) |

**The product assumption is backwards.** The founder pictured mailing as a paid add-on "where a state requires it". In fact the legal notice can be emailed in only three states, and only with consent on file. So the add-on is the default path for every legal notice, and should be priced and built as a core line, not an edge case. It also serves:

- deposit statements: Florida's certified-mail claim notice; main emails statements, which law.md admits "not every state counts";
- rent-increase notices: Montgomery County's mail-or-signed-receipt rule.

It cannot satisfy every state alone. Where a state needs posting, hand delivery or a marshal, the screen must say so and record that staff did it.

**New York needs an early mailed notice.** From model knowledge (NOT VERIFIED), Real Property Law 235-e(d) requires a certified-mail notice when rent is unpaid 5 days after its due date. That would make New York the clearest case where the mail add-on is needed before any legal notice, and where an email reminder must never be presented as satisfying it.

**Legal notice text.** The legal notice should come only from a company's notice template (main's migration 0119), with the state's required inserts (legal-aid or right-to-counsel information, form attachments) supplied by the company. The PMS writes no notice text today, and the notes' list of states requiring such inserts is NOT VERIFIED.

**Federal overlays are in flux.**

- **Arizona:** Barrera v. Sedona Pointe (June 12, 2026) held that the CARES Act's 30-day notice ended with the 2020 moratorium ([NAA](https://naahq.org/news/arizona-court-rules-cares-act-notice-expired)).
- **Washington:** the Supreme Court confined the 30-day notice to nonpayment cases ([NAA](https://naahq.org/news/washington-supreme-court-sides-housing)).
- **HUD:** on February 26, 2026 HUD moved to revoke its own 30-day nonpayment notice rule for public housing and project-based Section 8 ([NLIHC](https://nlihc.org/resource/hud-issues-interim-final-rule-rescinding-30-day-termination-notice-requirement-nonpayment)). A March 13 update says the effective date was delayed indefinitely ([Nixon Peabody](https://www.nixonpeabody.com/insights/alerts/2026/02/26/hud-rescinds-30-day-notice-requirement-for-nonpayment-evictions); [CRS R48955](https://www.everycrsreport.com/reports/R48955.html)).

The safe default is a per-property "federally covered" flag that shows 30 days. Main can derive it for voucher tenancies (migration 0095) and LIHTC units (0123). A federally backed mortgage has to be entered by staff.

## What a tenant may be charged to pay online

**No state bans online rent payment, but four require an alternative.**

- **California**: a landlord must allow "at least one form of payment that is neither cash nor electronic funds transfer", and a waiver is void ([Civ. Code 1947.3](https://california.public.law/codes/ca_civ_code_section_1947.3)).
- **Oregon** (SB 1523, effective **June 5, 2026**): a landlord "may not require a tenant to make payments by debit card, credit card, electronic check, tenant portal or any other form of electronic payment". The landlord must offer an alternative to the portal on written request ([SB 1523 enrolled](https://olis.oregonlegislature.gov/liz/2026R1/Downloads/MeasureDocument/SB1523/Enrolled)). Under ORS 90.302, fees are allowed only as listed in the written agreement. A card processing fee passes through only when the card company allows it **and** the landlord takes cash or check ([ORS 90.302](https://www.oregonlaws.org/ors/90.302)).
- **Colorado** (HB25-1090): no payment-processing charge unless a free way to pay exists. Banned fees are void. A tenant's written demand must be repaid within 14 days, or the fee comes back with 18% interest. The effective date is reported as both August 2025 and January 2026 ([Otten Johnson](https://www.ottenjohnson.com/news/hb25-1090-colorados-residential-landlords-must-eliminate-junk-fees-in-2026-including-cam-charges/)).
- **Illinois** (Public Act 103-0809): for leases executed from January 1, 2025, a portal that charges for e-check payments requires a paper-check, cash or no-fee alternative ([ILGA](https://www.ilga.gov/Legislation/publicacts/view/103-0809)). A separate junk-fee law is reported moved to January 1, 2027 ([KSN Law](https://www.ksnlaw.com/blog/illinois-landlords-new-rental-fee-transparency-law-takes-effect-january-1-2027/)).
- **New York**: electronic payment may not be the only method, and bills to ban online-payment fees are pending ([NY AG](https://ag.ny.gov/publications/residential-tenants-rights-guide); [A56-A](https://www.nysenate.gov/legislation/bills/2025/A56/amendment/A)).

**Card surcharges are a second, separate layer.**

- **Connecticut, Massachusetts and Maine ban credit card surcharges** ([LawPay](https://www.lawpay.com/about/blog/credit-card-surcharge-rules/); [Nickel](https://www.nickel.com/surcharge-laws)).
- Colorado caps them at **2%**, and so does Oklahoma since November 1, 2025 ([Merchant Cost Consulting](https://merchantcostconsulting.com/lower-credit-card-processing-fees/credit-card-surcharge-laws-by-state/)).
- New York requires the total card price to be posted ([GBL § 518](https://newyork.public.law/laws/n.y._general_business_law_section_518)).
- Texas is contested.
- Visa caps surcharges at 3% and the merchant's cost, and requires 30 days' notice to the acquirer. **Debit and prepaid cards can never be surcharged** ([Global Payments](https://www.globalpayments.com/insights/everything-you-need-to-know-about-visas-surcharging-rules)).

**Autopay rules out a convenience fee.** Processor summaries of Visa's rules say a convenience fee must be flat, charged for a genuine alternative channel, and is **not allowed on recurring payments** ([Paycove](https://www.paycove.io/learn/convenience-fee-vs-surcharge); [Pay Theory](https://www.paytheory.com/card-fees)). With autopay now in scope, a card fee on scheduled rent would have to be a surcharge where state law allows one, not a convenience fee. Visa's rulebook itself was not read (NOT VERIFIED).

The Visa/Mastercard merchant settlement won preliminary approval on June 9, 2026. One source lists a final hearing on November 16, 2026. Once final, the cap becomes 3% or the cost of acceptance, whichever is lower, and state bans still apply ([America's Credit Unions](https://www.americascreditunions.org/news-media/news/court-grants-preliminary-approval-interchange-lawsuit-settlement); [Beancount](https://beancount.io/blog/2026/07/21/visa-mastercard-swipe-fee-settlement-credit-card-surcharge-pos-guide)). Whether the three surcharge bans reach rent at all was not found.

**The floor every state can live with:**

- tenant card fees **off by default**;
- a fee only on credit cards, never above the processor's actual cost;
- none on debit;
- none in Connecticut, Massachusetts or Maine;
- at most 2% in Colorado and Oklahoma;
- the fee written into the lease (Oregon);
- the total shown before the tenant pays;
- **a free ACH option and a non-electronic option always on offer**. Main already records checks and money orders taken by staff.

## Trust accounts: the ledger meets the floor; deadlines, reconciliation and online money need screens

**The floor.** Every state with a result requires:

- a separate, labelled trust account at an insured institution;
- no commingling;
- a ledger per owner;
- paying out only from what that owner holds;
- records open to the regulator.

Main's trust bank accounts, per-owner ledger accounts, no-negative rule and append-only audit log already sit on that floor.

**The strictest values found, each a candidate screen:**

- **Deposit by the close of the second working day** (Texas, 22 TAC 535.146) ([Cornell](https://law.cornell.edu/regulations/texas/22-Tex-Admin-Code-SS-535-146)). Others allow 3 business or banking days (California, Florida, Arizona) and 5 (Alaska; Iowa and Virginia in banking days). Maryland's 7 business days was found only for sales contracts.
- **A monthly three-part reconciliation**: adjusted bank balance, journal, and the sum of positive owner ledgers, all agreeing. It must be **signed and dated within 30 days of the statement**, for each account including the security-deposit account (Oregon OAR 863-025-0028) ([oregon.public.law](https://oregon.public.law/rules/oar_863-025-0028)).
- **Records kept 7 years** (Connecticut C.G.S. 20-325m) ([FindLaw](https://codes.findlaw.com/ct/title-20-professional-and-occupational-licensing-certification-title-protection-and-registration-examining-boards/ct-gen-st-sect-20-325m/)). Main keeps everything forever, which already meets it.
- **Security deposits in an account of their own**: Nevada, Oregon, Louisiana ("Security Deposit Trust Account"), Missouri and, in practice, Colorado ([NAC 645.655](https://law.cornell.edu/regulations/nevada/NAC-645-655); [LAC Rule 2701](https://state-rules.com/louisiana/lac/LXVII.2701/); [20 CSR 2250-8.220](https://regulations.justia.com/states/missouri/title-20/division-2250/chapter-8/section-20-csr-2250-8-220)).
- **An in-state bank**: Alabama, Delaware, Kansas, Kentucky, Louisiana, New Hampshire, North Dakota, South Carolina and Washington ([Ala. 790-X-3-.03](https://admincode.legislature.state.al.us/api/rule/790-X-3-.03); [Del. Rule 6.0](https://regulations.justia.com/states/delaware/title-24/department-of-state/division-of-professional-regulation/2900/section-2900-6-0); [KRS 324.111](https://law.justia.com/codes/kentucky/chapter-324/section-324-111/); [RCW 18.85.285](https://lawfilesext.leg.wa.gov/Law/RCWArchive/2023/htm/RCW%20%2018%20%20TITLE/RCW%20%2018%20.%2085%20%20CHAPTER/RCW%20%2018%20.%2085%20.285.htm)).

**Smaller rules that become screens:**

- Utah: all trust money handed over within 30 days of a management contract ending ([Utah bulletin](https://www.rules.utah.gov/publicat/bulletin/2015/20150515/39305.htm)).
- Alaska: the management fee taken "on a regular monthly schedule" ([12 AAC 64.240](https://regulations.justia.com/states/alaska/title-12/part-1/chapter-64/article-5/section-12-aac-64-240)).
- Ohio: an accounting to each owner at least quarterly, and trust interest to owners at least quarterly ([OAC 1301:5-5-11](https://codes.ohio.gov/ohio-administrative-code/rule-1301:5-5-11)).
- Delaware: interest belongs to the owner.
- Nevada: reconciliations filed with the Division on Form 546 ([Form 546](https://red.nv.gov/uploadedfiles/rednvgov/Content/Forms/546.pdf)).
- Kentucky: an audit permit signed at each licensure and renewal.

**Licensing.** DC, South Carolina and South Dakota issue a separate property manager license. Idaho's sources conflict on whether managing for a fee needs one. Nothing was found for Indiana, Mississippi, Oklahoma, Rhode Island, Vermont or Wyoming.

**What the screens should add:**

- a "received, not yet deposited" age, flagged after two business days;
- a reconcile-by date of statement date plus 30, with the approver's signature date recorded;
- a separate reconciliation for each security-deposit account;
- a flag when a trust bank account's state differs from its properties' state;
- a 30-day handover deadline when an owner's agreement ends.

**Two flags for law.md.** First, its **Texas 30-day fee removal** is stated without a NOT VERIFIED mark, but this pass found it only in a study guide. Second, "$0 of broker money in trust" meets every cap, but a bank fee taken from trust is paid with owners' money unless the company puts it back. Iowa requires exactly that.

**Online payments sharpen both flags into open questions.** Stripe's ACH settlement takes up to four business days. Is rent "received" for Texas's two-day clock when the tenant pays, or when Stripe pays out? And may a payout arrive net of fees at all? No regulator guidance was found. A 2025 California DFPI/DRE bulletin shows regulators watching money leaving trust for third parties ([DFPI/DRE](https://dfpi.ca.gov/wp-content/uploads/2025/06/CDA-Joint-Bulletin.pdf)).

**HOA management, now in scope, brings rules the PMS has no model for.** Georgia folds community-association management into the broker license with "assessment" trust accounts, paying a bill only when the association's balance covers it ([Ga. 520-1-.08](https://regulations.justia.com/states/georgia/department-520/chapter-520-1/rule-520-1-08)). The community-association-manager licenses in Florida, Nevada, Virginia and Illinois are unverified leads.

## Building it: geocode to FIPS once, then resolve cited rules in one database function

### The Census geographies endpoint is the only free default that returns FIPS

**Today's call returns no jurisdiction.** Main calls `https://geocoding.geo.census.gov/geocoder/locations/address` with `benchmark=Public_AR_Current` (`src/pms/geocoding.py` on origin/main). That endpoint returns coordinates only.

**The fix is to switch endpoints.** The Census Geocoder's `geographies` endpoints return GEOIDs for state, county and tract. Extra layers come back by TIGERweb layer ID or name, and `layers=all` is accepted ([Census Geocoding Services API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html); [TIGERweb layers](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer)). Requesting incorporated places, census designated places and county subdivisions gives the city layer.

**Existing properties can be backfilled without re-sending addresses.** Main already stores coordinates, so the `/geographies/coordinates` endpoint can resolve those properties directly.

**Why Census suits both editions:**

- It needs no key, and costs nothing.
- It takes batches of up to 10,000 ([Census](https://www.census.gov/programs-surveys/geography/technical-documentation/complete-technical-documentation/census-geocoder.html)).
- The vintage chosen decides which geography version is assigned ([Iowa worksheet](https://opportunityiowa.gov/media/4140/download)), so the vintage must be stored.

**Its limits:**

- It matches street addresses only.
- It has no reverse geocoding.
- In batch mode it returns nothing on tied candidates ([tidygeocoder](https://github.com/jessecambon/tidygeocoder/blob/main/vignettes/geocoder_services.Rmd)).
- Its rate limits and terms were not found, and the 17 U.S.C. 105 public-domain basis was not fetched (NOT VERIFIED).

| Geocoder | Returns FIPS | Storage terms | License / cost | Fit |
|---|---|---|---|---|
| U.S. Census | yes | government output (public-domain basis NOT VERIFIED) | free, no key | default, both editions |
| Nominatim / OSM | no | ODbL; attribution required; one result is an insubstantial extract | GPL software; public server bars bulk use | not a default ([OSMF guideline](https://osmfoundation.org/wiki/Licence/Community_Guidelines/Geocoding_-_Guideline)) |
| Google | no | coordinates cached at most 30 days | paid | unsuitable ([Google terms](https://cloud.google.com/archive/maps-platform/terms/maps-service-terms-20250501)) |
| Mapbox | no | temporary results may not be stored; permanent needs a card | paid | possible, not needed ([Mapbox](https://docs.mapbox.com/help/dive-deeper/understand-temporary-vs-permanent-geocoding/)) |
| Geocodio | not researched (search budget ran out) | stored permanently, per its own comparison | paid; pricing not researched | optional adapter ([Geocodio](https://www.geocod.io/geocoding-terms-of-use-comparison)) |

### Store every layer, and treat "no city" as an answer

**FIPS must come from a point-in-polygon answer, never from the ZIP code or the postal city name.** The design note proposes an append-only `trust_property_jurisdictions`, the latest row holding and naming who confirmed it. It carries:

- state (2 digits), county (5), county subdivision (10), place (7), tract (11);
- a place kind: incorporated, CDP or none;
- the source (Census API, local TIGER, or manual with a reason);
- the vintage;
- the matched point.

The GEOID lengths are background, NOT VERIFIED.

**Unincorporated areas.** When the place layer returns a census designated place or nothing, the city layer of the rules is empty and county rules govern. The UI says "Unincorporated X County", with the mailing city shown separately and labelled. LA County's RSTPO, which covers only unincorporated areas, is the concrete case.

**Special geographies, both NOT VERIFIED:**

- In New England, New York, New Jersey, Pennsylvania, Michigan, Minnesota and Wisconsin, towns are general-purpose governments, so the county-subdivision layer must be a rules layer too.
- Independent and consolidated city-counties need a "same as" link so one rule isn't applied twice.

**An offline fallback needs no PostGIS.** Postgres 16's core `polygon @> point` operator and GiST `point_ops` and `poly_ops` indexes support the lookup ([func.sgml](https://github.com/postgres/postgres/blob/REL_16_STABLE/doc/src/sgml/func.sgml); [gist.sgml](https://github.com/postgres/postgres/blob/REL_16_STABLE/doc/src/sgml/gist.sgml)). The core polygon type is one ring with no holes ([datatype.sgml](https://github.com/postgres/postgres/blob/REL_16_STABLE/doc/src/sgml/datatype.sgml)), so TIGER multipolygons have to be split into rings. A simpler option keeps the polygons out of the ledger database entirely: do the point-in-polygon in the app and store only the GEOIDs.

**Points near a boundary** (within about 1e-6 degrees) go to staff for confirmation.

**Boundaries move.** A new vintage re-resolves every property and shows annexation changes for staff to confirm, rather than switching rules silently. Test fixtures stay synthetic rings and coordinates, per the repo's rule.

### An append-only, dated, sourced rules table with one resolver

**Three tables.** The rule data is global and read-only to `trust_app`:

- **`trust_jurisdictions`**: one row per federal, state, county, county-subdivision or place GEOID.
- **`trust_rule_topics`**: a key such as `deposit.max_months_rent`, `notice.rent_increase_days`, `late_fee.min_grace_days` or `payment.card_surcharge_max_percent`. Each topic has a value type and a combine policy:
  - `min` for caps, so the smaller wins;
  - `max` for notice periods, so the longer wins;
  - `most_specific` where a local rule replaces the state's;
  - `all` for disclosures and attachments, which add up.
- **`trust_rule_versions`**: append-only rows. Each carries:
  - effective-from and effective-to dates;
  - a typed value;
  - JSON conditions on property facts (units, year built, owner-occupied, subsidy, owner entity type);
  - a **mandatory citation, source URL, checked-on day and checker**;
  - a status (draft, reviewed, published, withdrawn);
  - the dataset release it shipped in.

JSON is core Postgres, so this stays within the pgcrypto-only rule.

**One resolver.** `trust_rules_for_property(p_pmc_id, p_property_id, p_topic, p_on)` walks the property's confirmed jurisdictions. It keeps published rows in force on that day whose conditions match, combines them by the topic's policy, and returns the value **with the trail of every row considered**. The screen and the enforcement triggers call the same function, so a hand-written API call can't bypass what the screen shows.

**Missing facts never turn into "no rule".** When a needed fact is missing, the resolver returns "unknown". The screen asks for the fact and shows the stricter value meanwhile. This is the "unknown is never all clear" rule both repos already follow.

**Strictest-found becomes the fallback.** law.md's "strictest found" method doesn't disappear: it becomes the resolver's answer when the jurisdiction or a fact is unknown. Geocoding earns its keep in three places:

- letting a company use its own state's longer deadline or higher cap lawfully;
- attaching jurisdiction-specific forms, consents and notices that a single national floor can't express;
- tracking the yearly numbers.

**Company house rules.** A company may add a stricter rule of its own, but a check must refuse any house rule that loosens the published one.

**Sensitive tenant facts.** Several rules turn on tenant age 62+ (Connecticut, Rhode Island), disability registration (DC), household income against AMI (Seattle) or service-member status (California). If stored, these facts belong in `AI_NEVER_READS`.

### Keeping it current is an editorial job with tooling around it

**Ship the data as reviewed releases.** Ship the rule data as versioned seed files in the repo, one per state and topic. CI fails any row that lacks a citation, URL, checked-on day or reviewer. A nightly report lists rules whose checked-on day is older than, say, 180 days.

**Run a calendar job for the yearly numbers.** It is due before each regime's period starts:

- Washington by mid-summer for January 1;
- Oregon by September 30 for January 1;
- California for August 1;
- San Francisco for March 1;
- DC for May 1;
- New York's Rent Guidelines Board for October 1;
- and the rest of the calendar table above.

**Track bills, but never auto-publish.** Track bills with LegiScan, whose data is CC BY 4.0 and needs attribution ([LegiScan](https://legiscan.com/legiscan)), or Open States, whose data is public domain ([Open States](https://open.pluralpolicy.com/data/)). Consume their data, not the GPL-3.0 scrapers ([openstates-scrapers](https://github.com/openstates/openstates-scrapers)). When a bill is enacted, open a review task naming the affected topics. A human writes the new row, and nothing auto-publishes from bill text.

**There is no openly licensed, current national dataset to import:**

- The LSC/LawAtlas eviction-law database is frozen at **January 1, 2021**, and its license couldn't be confirmed ([LSC](https://lsc.gov/initiatives/effect-state-local-laws-evictions/lsc-eviction-laws-database); [LawAtlas terms](https://legacy.lawatlas.org/terms)).
- NCSL owns its content ([NCSL terms](https://www.ncsl.org/terms-of-use)).

The seed must come from the statutes themselves, row by row. LSC's 30-city list is a reasonable first set of cities to re-verify for 2026.

### Deterministic where the law bites, AI only where a human reviews

**What competitors do.** Competitors deliver "state-specific" behavior mainly as attorney-reviewed lease templates picked by address. TurboTenant says it "cannot provide legal advice" ([TurboTenant](https://support.turbotenant.com/en/articles/10092076-turbotenant-state-specific-lease-agreements)), and Avail pulls the local lease by the unit's address ([Avail](https://www.avail.com/landlords/rental-lease-agreement)). Rentec Direct says its templates "are not state-specific" ([Rentec](https://help.rentecdirect.com/article/157-forms-leases-agreements-and-eviction-notices)). One Avail user reported its Minnesota lease omitted required items such as a lead-paint disclosure ([Avail community](https://community.avail.com/t/avails-basic-lease-for-minnesota/2053)). That is the failure an AI-generated form would make harder to audit.

**The position this research supports:**

| Kind of output | How it is produced | Examples |
|---|---|---|
| Enforced in the database (refuse) | Deterministic resolver, called by triggers | Card surcharge where banned or on debit; a deposit above a verified cap; a legal notice marked emailed without a consent record in a consent state; the property's jurisdiction itself |
| Warned, with a recorded override reason | Deterministic resolver | Rent increase above a yearly number (banking and petitions exist); notice shorter than the jurisdiction's minimum; deposit return later than the state deadline; trust deposit or reconciliation dates |
| Shown as cited information | Deterministic resolver | Penalties, service methods, day-count rules, required inserts, registration status |
| AI-assisted, human-reviewed (paid upsell) | AI, with `trust_ai_agent` reading only | Explain a rule in plain words with its citation; summarize a tracked bill for the reviewer; draft a candidate rule row as `draft`; order which already-reviewed hint cards a dashboard shows |
| Never AI | — | Publishing a rule, computing a legal amount or date shown as authoritative, choosing which rule applies, reading notices, contact details or sensitive tenant facts |
