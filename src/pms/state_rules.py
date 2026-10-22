"""State rules as warnings (q142; the founder, 2026-10-22; docs/law.md, State rules as warnings).

Some states' statutes set numbers that terms staff set must meet: how long rent must go unpaid
after its due date before a late fee, the most a late fee may be, how many days' written notice a
rent increase needs (and whether one may come in a tenancy's first year, or twice in a year), and
how many hours' notice comes before entering a home. RULES holds those numbers, each with its
statute and how it was checked, and the checks below compare what staff set with them.

A check only warns. It decides nothing and refuses nothing: a company saves its terms either way.
A state not in RULES, a rule a state doesn't set as a number ("reasonable"), and a property with no
address on file get no warning. No attorney has reviewed this table, and no statute in it was read
on an official site (2026-10-22: those sites refused the research's requests); each says how it
was checked. A city's own rules are not in it, nor rent caps, nor anything about security
deposits (docs/law.md keeps those apart).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

# How a number was checked: its statute's text read on an official page (or a page quoting the
# code, such as law.cornell.edu), its words quoted in a search excerpt, or only a summary site said
# it (NOT VERIFIED).
How = Literal["official", "excerpt", "secondary"]

CHECKED_ON = date(2026, 10, 22)

# Shown wherever a warning is: what the table is and isn't.
NOT_ADVICE = (
    "No attorney has reviewed these state rules. They come from research into each state's"
    " statutes, none read on an official site, and may be out of date or incomplete; a city's own"
    " rules are not checked. The PMS only warns: confirm your state's rules with your own attorney"
    " or broker."
)

CENT = Decimal("0.01")
ONE_DOLLAR = Decimal("1.00")


@dataclass(frozen=True)
class Source:
    cite: str
    url: str
    how: How


# The most one late fee may be when `late` of a monthly `rent` is late, at a property of `units`
# units.
Cap = Callable[[Decimal, Decimal, int], Decimal]


@dataclass(frozen=True)
class LateFeeRule:
    # A fee only once rent is still unpaid at the end of its due day plus these days (0: no wait).
    grace_days: int
    wait_words: str | None
    # The most one late fee may be, in words and worked out; None when the state sets no number.
    cap_words: str | None
    cap: Cap | None
    source: Source
    # Amounts late at which the cap changes how it grows (the greater or lesser of a flat amount
    # and a percent of what is late): checked besides the whole rent and a dollar of it.
    kinks: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class Bigger:
    """A bigger increase needing longer notice: more than (or at least) `percent` of the lowest
    rent of the 12 months before (California Civil Code 827, Maine 14 M.R.S. 6015)."""

    percent: Decimal
    days: int
    at_least: bool = False


@dataclass(frozen=True)
class RentNoticeRule:
    # Days of written notice before a rent increase takes effect, and the rule in words.
    days: int
    words: str
    source: Source
    bigger: Bigger | None = None
    # An increase of at least this percent needs days by how long the tenant has lived there or
    # the lease's term, whichever is longer (New York RPL 226-c): (years at least, days), the
    # longest first.
    by_tenancy: tuple[Decimal, tuple[tuple[int, int], ...]] | None = None
    # A lease for a term longer than these months needs these days (Maryland).
    longer_term: tuple[int, int] | None = None
    # No increase within the tenancy's first year (Oregon), and at most one in 12 months
    # (Colorado): each the statute that says it.
    not_first_year: Source | None = None
    once_a_year: Source | None = None


@dataclass(frozen=True)
class EntryRule:
    hours: int
    words: str
    source: Source
    # Not for a repair the tenant asked for (Delaware, Virginia).
    unless_requested: bool = False


@dataclass(frozen=True)
class StateRules:
    name: str
    late_fee: LateFeeRule | None = None
    rent_notice: RentNoticeRule | None = None
    entry: EntryRule | None = None


def percent_of(amount: Decimal, percent: str) -> Decimal:
    return (amount * Decimal(percent) / 100).quantize(CENT, ROUND_HALF_UP)


def of_rent(percent: str) -> Cap:
    return lambda rent, _late, _units: percent_of(rent, percent)


def of_late(percent: str) -> Cap:
    return lambda _rent, late, _units: percent_of(late, percent)


def lesser_of_rent(flat: str, percent: str) -> Cap:
    return lambda rent, _late, _units: min(Decimal(flat), percent_of(rent, percent))


def greater_of_rent(flat: str, percent: str) -> Cap:
    return lambda rent, _late, _units: max(Decimal(flat), percent_of(rent, percent))


def lesser_of_late(flat: str, percent: str) -> Cap:
    return lambda _rent, late, _units: min(Decimal(flat), percent_of(late, percent))


def greater_of_late(flat: str, percent: str) -> Cap:
    return lambda _rent, late, _units: max(Decimal(flat), percent_of(late, percent))


def kink(flat: str, percent: str) -> tuple[Decimal, ...]:
    """Where a percent of what is late reaches a flat amount."""
    return ((Decimal(flat) * 100 / Decimal(percent)).quantize(CENT),)


def by_rent(up_to: str, low: str, high: str) -> Cap:
    """A flat most for a rent up to an amount, another above it (Iowa)."""
    return lambda rent, _late, _units: Decimal(low) if rent <= Decimal(up_to) else Decimal(high)


def by_units(most_units: int, small: str, large: str) -> Cap:
    """A percent of the rent at a property of up to `most_units` units, another above (Texas)."""
    return lambda rent, _late, units: percent_of(rent, small if units <= most_units else large)


# --- the table (researched 2026-10-22: docs/law.md, State rules as warnings) ----------------------

S = Source
EXCERPT: How = "excerpt"
SECONDARY: How = "secondary"

# Every state and DC by its USPS code, for the names the warnings use.
NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip

LATE_FEES = {
    "CO": LateFeeRule(
        7,
        "rent is at least seven days late",
        "the greater of $50 or 5% of the rent past due",
        greater_of_late("50", "5"),
        S("C.R.S. 38-12-105", "https://colorado.public.law/statutes/crs_38-12-105", EXCERPT),
        kink("50", "5"),
    ),
    "CT": LateFeeRule(
        9,
        "rent is nine days late",
        "the lesser of $5 a day up to $50, or 5% of the rent late",
        lesser_of_late("50", "5"),
        S(
            "Conn. Gen. Stat. 47a-15a",
            "https://ezel.ai/surveys/late-rent-fee-limits/connecticut",
            SECONDARY,
        ),
        kink("50", "5"),
    ),
    "DC": LateFeeRule(
        5,
        "rent is unpaid five days after its due date",
        "5% of the rent due",
        of_rent("5"),
        S(
            "D.C. Code 42-3505.31",
            "https://code.dccouncil.gov/us/dc/council/code/sections/42-3505.31",
            EXCERPT,
        ),
    ),
    "DE": LateFeeRule(
        5,
        "rent is unpaid five days after its due date (eight where the landlord has no office in"
        " the county)",
        "5% of the monthly rent",
        of_rent("5"),
        S(
            "25 Del. C. 5501(d)",
            "https://law.justia.com/codes/delaware/2016/title-25/chapter-55/section-5501/",
            SECONDARY,
        ),
    ),
    "HI": LateFeeRule(
        0,
        None,
        "8% of the rent not paid when due",
        of_late("8"),
        S(
            "Haw. Rev. Stat. 521-21(f)",
            "https://data.capitol.hawaii.gov/sessions/session2026/bills/SB347_.HTM",
            EXCERPT,
        ),
    ),
    "IA": LateFeeRule(
        0,
        None,
        "$12 a day up to $60 a month for a rent of $700 or less, else $20 a day up to $100",
        by_rent("700.00", "60.00", "100.00"),
        S("Iowa Code 562A.9(4)", "https://www.legis.iowa.gov/docs/code/2025/562A.9.pdf", EXCERPT),
    ),
    "MA": LateFeeRule(
        30,
        "rent is 30 days late",
        None,
        None,
        S("Mass. G.L. c. 186, 15B(1)(c)", "https://www.masslegalhelp.org/node/145", EXCERPT),
    ),
    "MD": LateFeeRule(
        0,
        None,
        "5% of the rent unpaid",
        of_late("5"),
        S(
            "Md. Code, Real Prop. 8-208(d)(3)",
            "https://mgaleg.maryland.gov/2025RS/chapters_noln/Ch_580_hb0273T.pdf",
            EXCERPT,
        ),
    ),
    "ME": LateFeeRule(
        15,
        "rent is unpaid 15 days after its due date",
        "4% of a month's rent",
        of_rent("4"),
        S(
            "14 M.R.S. 6028",
            "https://legislature.maine.gov/legis/statutes/14/title14sec6028.html",
            EXCERPT,
        ),
    ),
    "MN": LateFeeRule(
        0,
        None,
        "8% of the rent overdue",
        of_late("8"),
        S("Minn. Stat. 504B.177", "https://www.revisor.mn.gov/statutes/cite/504B.177", EXCERPT),
    ),
    "NC": LateFeeRule(
        5,
        "rent is five days late",
        "the greater of $15 or 5% of the monthly rent",
        greater_of_rent("15", "5"),
        S(
            "N.C. Gen. Stat. 42-46",
            "https://www.ncleg.gov/enactedlegislation/statutes/html/bysection/chapter_42/gs_42-46.html",
            EXCERPT,
        ),
    ),
    "NM": LateFeeRule(
        0,
        None,
        "10% of the rent for each period in default",
        of_rent("10"),
        S("N.M. Stat. 47-8-15(D)", "https://answers.uslegal.com/landlord-tenant/12156/", EXCERPT),
    ),
    "NV": LateFeeRule(
        3,
        "rent is three days late",
        "5% of the periodic rent",
        of_rent("5"),
        S(
            "NRS 118A.210",
            "https://law.justia.com/codes/nevada/chapter-118a/statute-118a-210",
            EXCERPT,
        ),
    ),
    "NY": LateFeeRule(
        5,
        "rent is unpaid five days after its due date",
        "the lesser of $50 or 5% of the monthly rent",
        lesser_of_rent("50", "5"),
        S(
            "N.Y. Real Prop. Law 238-a(2)",
            "https://www.nycourts.gov/reporter/3dseries/2021/2021_00223.htm",
            EXCERPT,
        ),
    ),
    "OR": LateFeeRule(
        3,
        "rent is unpaid at the end of the fourth day of its rental period",
        None,
        None,
        S("ORS 90.260", "https://oregon.public.law/statutes/ors_90.260", EXCERPT),
    ),
    "TN": LateFeeRule(
        5,
        "rent is unpaid five days after its due date (the next business day when the fifth falls"
        " on a Sunday or holiday)",
        "10% of the rent past due",
        of_late("10"),
        S(
            "Tenn. Code 66-28-201(d)",
            "https://ezel.ai/surveys/late-rent-fee-limits/tennessee",
            SECONDARY,
        ),
    ),
    "TX": LateFeeRule(
        2,
        "rent is unpaid two full days after its due date",
        "12% of the monthly rent at a property of four units or fewer, else 10% (presumed"
        " reasonable; more only within the landlord's actual damages)",
        by_units(4, "12", "10"),
        S(
            "Tex. Prop. Code 92.019",
            "https://codes.findlaw.com/tx/property-code/prop-sect-92-019/",
            EXCERPT,
        ),
    ),
    "VA": LateFeeRule(
        0,
        None,
        "the lesser of 10% of the periodic rent or 10% of the balance due",
        of_late("10"),
        S("Va. Code 55.1-1204(E)", "https://law.lis.virginia.gov/vacode/55.1-1204/", EXCERPT),
    ),
    "WA": LateFeeRule(
        5,
        "rent is unpaid five days after its due date",
        None,
        None,
        S(
            "RCW 59.18.170(2)",
            "https://app.leg.wa.gov/rcw/default.aspx?cite=59.18.170",
            EXCERPT,
        ),
    ),
}

RENT_NOTICES = {
    "CA": RentNoticeRule(
        30,
        "a rent increase needs 30 days' written notice (five more when mailed)",
        S(
            "Cal. Civ. Code 827(b)",
            "https://ezel.ai/surveys/rent-increase-notice-requirements/california",
            SECONDARY,
        ),
        bigger=Bigger(Decimal(10), 90),
    ),
    "CO": RentNoticeRule(
        60,
        "a rent increase needs 60 days' written notice where there is no written lease",
        S(
            "C.R.S. 38-12-701(2)(a)",
            "https://colorado.public.law/statutes/crs_38-12-701",
            EXCERPT,
        ),
        once_a_year=S(
            "C.R.S. 38-12-702", "https://colorado.public.law/statutes/crs_38-12-702", EXCERPT
        ),
    ),
    "CT": RentNoticeRule(
        45,
        "a rent increase needs 45 days' written notice under an agreement made, renewed or"
        " extended since 10/01/2024",
        S(
            "Conn. Gen. Stat. 47a-4e",
            "https://ezel.ai/surveys/rent-increase-notice-requirements/connecticut",
            SECONDARY,
        ),
    ),
    "DC": RentNoticeRule(
        61,
        "a rent increase starts from a rent due date more than 60 days after written notice",
        S(
            "D.C. Code 42-3509.04(b)",
            "https://code.dccouncil.gov/us/dc/council/code/sections/42-3509.04.html",
            EXCERPT,
        ),
    ),
    "DE": RentNoticeRule(
        60,
        "a lease renewed at a new rent needs 60 days' written notice before its term ends",
        S(
            "25 Del. C. 5107(a)",
            "https://ipropertymanagement.com/laws/delaware-rent-increases",
            EXCERPT,
        ),
    ),
    "HI": RentNoticeRule(
        45,
        "a rent increase on a month-to-month tenancy needs 45 days' written notice",
        S(
            "Haw. Rev. Stat. 521-21(d)",
            "https://data.capitol.hawaii.gov/sessions/sessionlaws/Years/SLH1978/SLH1978_Act124.pdf",
            EXCERPT,
        ),
    ),
    "IA": RentNoticeRule(
        30,
        "a rent increase needs 30 days' written notice and takes effect no sooner than the"
        " lease's end",
        S(
            "Iowa Code ch. 562A",
            "https://ipropertymanagement.com/laws/iowa-rent-increases",
            EXCERPT,
        ),
    ),
    "IN": RentNoticeRule(
        30,
        "a rent increase needs 30 days' written notice unless the written agreement says otherwise",
        S(
            "Ind. Code 32-31-5-4",
            "https://law.justia.com/codes/indiana/2010/title32/ar31/ch5.html",
            EXCERPT,
        ),
    ),
    "MD": RentNoticeRule(
        60,
        "a rent increase needs 60 days' written notice",
        S(
            "Md. Code, Real Prop. 8-209(b)",
            "https://mgaleg.maryland.gov/2026RS/Statute_Web/grp/8-209.pdf",
            EXCERPT,
        ),
        longer_term=(1, 90),
    ),
    "ME": RentNoticeRule(
        45,
        "a rent increase needs 45 days' written notice",
        S(
            "14 M.R.S. 6015",
            "https://legislature.maine.gov/statutes/14/title14sec6015-2.html",
            EXCERPT,
        ),
        bigger=Bigger(Decimal(10), 75, at_least=True),
    ),
    "NM": RentNoticeRule(
        30,
        "a rent increase on a month-to-month tenancy needs 30 days' written notice before the"
        " rent day",
        S(
            "N.M. Stat. 47-8-15(F)",
            "https://resources.hemlane.com/new-mexico-rent-control-laws",
            EXCERPT,
        ),
    ),
    "NV": RentNoticeRule(
        60,
        "a rent increase on a tenancy of a month or more needs 60 days' written notice",
        S(
            "NRS 118A.300",
            "https://law.justia.com/codes/nevada/chapter-118a/statute-118a-300",
            EXCERPT,
        ),
    ),
    "NY": RentNoticeRule(
        0,
        "a rent increase of 5% or more needs 30, 60 or 90 days' notice by how long the tenant has"
        " lived there or the lease's term",
        S("N.Y. Real Prop. Law 226-c", "https://nysenate.gov/legislation/laws/RPP/226-C", EXCERPT),
        by_tenancy=(Decimal(5), ((2, 90), (1, 60), (0, 30))),
    ),
    "OR": RentNoticeRule(
        90,
        "a rent increase needs 90 days' written notice",
        S("ORS 90.323(1)", "https://www.oregonlaws.org/ors/90.323", EXCERPT),
        not_first_year=S("ORS 90.323(1)", "https://www.oregonlaws.org/ors/90.323", EXCERPT),
    ),
    "RI": RentNoticeRule(
        60,
        "a rent increase needs 60 days' written notice (120 for a tenant over 62)",
        S(
            "R.I. Gen. Laws ch. 34-18 (2024 H 7304)",
            "https://webserver.rilegislature.gov/BillText24/HouseText24/H7304A.htm",
            EXCERPT,
        ),
    ),
    "VT": RentNoticeRule(
        60,
        "a rent increase needs 60 days' actual notice and starts on the first day of a rental"
        " period",
        S(
            "9 V.S.A. 4455(b)",
            "https://legislature.vermont.gov/statutes/section/09/137/04455",
            EXCERPT,
        ),
    ),
    "WA": RentNoticeRule(
        90,
        "a rent increase needs 90 days' written notice (30 where the rent is set by the tenant's"
        " income)",
        S(
            "RCW 59.18.140(3)",
            "https://lawfilesext.leg.wa.gov/biennium/2025-26/Pdf/Bills/House%20Bills/1217.pdf",
            EXCERPT,
        ),
    ),
}

ENTRIES = {
    "AK": EntryRule(
        24,
        "at least 24 hours' notice before entering, at reasonable times",
        S(
            "AS 34.03.140(c)",
            "https://ipropertymanagement.com/laws/alaska-landlord-entry-rights",
            EXCERPT,
        ),
    ),
    "AL": EntryRule(
        48,
        "at least two days' notice before entering (a note on the main entry door counts)",
        S(
            "Ala. Code 35-9A-303",
            "https://ipropertymanagement.com/laws/alabama-landlord-entry-rights",
            EXCERPT,
        ),
    ),
    "AZ": EntryRule(
        48,
        "at least two days' notice before entering, at reasonable times",
        S("A.R.S. 33-1343(D)", "https://azleg.gov/ars/33/01343.htm", EXCERPT),
    ),
    "CA": EntryRule(
        24,
        "reasonable written notice before entering, 24 hours presumed reasonable (six days if"
        " mailed), in normal business hours",
        S(
            "Cal. Civ. Code 1954(d)(1)",
            "https://answers.uslegal.com/landlord-tenant/13465/",
            EXCERPT,
        ),
    ),
    "DC": EntryRule(
        48,
        "written notice at least 48 hours before entering, unless the tenant agrees in writing"
        " to less",
        S(
            "D.C. Code 42-3505.51",
            "https://code.dccouncil.gov/us/dc/council/code/sections/42-3505.51",
            EXCERPT,
        ),
    ),
    "DE": EntryRule(
        48,
        "at least 48 hours' notice before entering, between 8 a.m. and 9 p.m., except for"
        " repairs the tenant asked for",
        S(
            "25 Del. C. 5509(b)",
            "https://ipropertymanagement.com/laws/delaware-landlord-entry-rights",
            EXCERPT,
        ),
        unless_requested=True,
    ),
    "FL": EntryRule(
        24,
        "at least 24 hours' notice before entering for repairs, between 7:30 a.m. and 8 p.m.",
        S("Fla. Stat. 83.53(2)", "https://www.flsenate.gov/Laws/Statutes/2025/0083.53", EXCERPT),
    ),
    "HI": EntryRule(
        48,
        "at least two days' notice before entering, at reasonable hours",
        S(
            "Haw. Rev. Stat. 521-53(b)",
            "https://data.capitol.hawaii.gov/hrscurrent/Vol12_Ch0501-0588/HRS0521/HRS_0521-0053.htm",
            EXCERPT,
        ),
    ),
    "IA": EntryRule(
        24,
        "at least 24 hours' notice before entering, at reasonable times",
        S("Iowa Code 562A.19(3)", "https://www.legis.iowa.gov/docs/code/2022/562A.19.pdf", EXCERPT),
    ),
    "KY": EntryRule(
        48,
        "at least two days' notice before entering, at reasonable times, where the local"
        " government adopted the uniform act",
        S(
            "KRS 383.615(3)",
            "https://codes.findlaw.com/ky/title-xxxii-ownership-and-conveyance-of-property/",
            EXCERPT,
        ),
    ),
    "MD": EntryRule(
        24,
        "written notice at least 24 hours before entering, giving the day, the time and why,"
        " between 7 a.m. and 7 p.m., Monday to Saturday",
        S(
            "Md. Code, Real Prop. 8-221",
            "https://mgaleg.maryland.gov/2027RS/Statute_Web/grp/8-221.pdf",
            EXCERPT,
        ),
    ),
    "ME": EntryRule(
        24,
        "reasonable notice before entering, 24 hours presumed reasonable",
        S(
            "14 M.R.S. 6025(2)",
            "https://legislature.maine.gov/statutes/14/title14sec6025.html",
            EXCERPT,
        ),
    ),
    "MN": EntryRule(
        24,
        "at least 24 hours' notice before entering, naming a time or window, between 8 a.m. and"
        " 8 p.m.",
        S(
            "Minn. Stat. 504B.211",
            "https://www.revisor.mn.gov/statutes/2024/cite/504B.211/pdf",
            EXCERPT,
        ),
    ),
    "MT": EntryRule(
        24,
        "at least 24 hours' notice before entering, at reasonable times",
        S("MCA 70-24-312", "https://archive.legmt.gov/bills/MCA/title_0700/chapter_0240/", EXCERPT),
    ),
    "NE": EntryRule(
        24,
        "at least 24 hours' written notice before entering, delivered to the unit, saying why"
        " and when",
        S(
            "Neb. Rev. Stat. 76-1423",
            "https://ipropertymanagement.com/laws/nebraska-landlord-entry-rights",
            EXCERPT,
        ),
    ),
    "NJ": EntryRule(
        24,
        "one day's notice before entering, in a building of three or more units (a regulation)",
        S(
            "N.J.A.C. 5:10-5.1(c)",
            "https://www.nj.gov/dca/codes/publications/pdf_lti/right_of_entry.pdf",
            EXCERPT,
        ),
    ),
    "NM": EntryRule(
        24,
        "24 hours' written notice before entering, saying why, the day and the time",
        S(
            "N.M. Stat. 47-8-24(A)",
            "https://www.nolo.com/legal-encyclopedia/free-books/renters-rights-book/chapter8-1.html",
            EXCERPT,
        ),
    ),
    "NV": EntryRule(
        24,
        "at least 24 hours' notice before entering, in normal business hours unless the tenant"
        " agrees otherwise",
        S("NRS 118A.330(3)", "https://nevada.public.law/statutes/nrs_118a.330", EXCERPT),
    ),
    "OH": EntryRule(
        24,
        "reasonable notice before entering, 24 hours presumed reasonable",
        S(
            "Ohio Rev. Code 5321.04(A)(8)",
            "https://www.casemine.com/act/us/6416f301378c563dad8b20ec",
            EXCERPT,
        ),
    ),
    "OK": EntryRule(
        24,
        "at least one day's notice before entering, at reasonable times",
        S("41 O.S. 128", "https://law.justia.com/codes/oklahoma/title-41/section-41-128/", EXCERPT),
    ),
    "OR": EntryRule(
        24,
        "at least 24 hours' actual notice before entering, at reasonable times",
        S("ORS 90.322(1)", "https://oregon.public.law/statutes/ors_90.322", EXCERPT),
    ),
    "RI": EntryRule(
        48,
        "at least two days' notice before entering, at reasonable times",
        S(
            "R.I. Gen. Laws 34-18-26(c)",
            "https://webserver.rilegislature.gov/Statutes/TITLE34/34-18/34-18-26.htm",
            EXCERPT,
        ),
    ),
    "SC": EntryRule(
        24,
        "at least 24 hours' notice before entering, at reasonable times",
        S(
            "S.C. Code 27-40-530(C)",
            "https://ezel.ai/surveys/landlord-entry-notice/south-carolina",
            SECONDARY,
        ),
    ),
    "SD": EntryRule(
        24,
        "reasonable notice before entering, 24 hours' written notice presumed reasonable",
        S("SDCL 43-32-32", "https://sdlegislature.gov/Statutes/43-32-32", EXCERPT),
    ),
    "UT": EntryRule(
        24,
        "at least 24 hours' notice before entering, unless the rental agreement says otherwise",
        S(
            "Utah Code 57-22-4(2)",
            "https://law.justia.com/codes/utah/2011/title57/chapter22/section4.html",
            EXCERPT,
        ),
    ),
    "VA": EntryRule(
        72,
        "at least 72 hours' notice before routine maintenance the tenant didn't ask for",
        S("Va. Code 55.1-1229(A)", "https://law.lis.virginia.gov/vacode/55.1-1229/", EXCERPT),
        unless_requested=True,
    ),
    "VT": EntryRule(
        48,
        "at least 48 hours' notice before entering, between 9 a.m. and 9 p.m.",
        S(
            "9 V.S.A. 4460(b)",
            "https://legislature.vermont.gov/statutes/section/09/137/04460",
            EXCERPT,
        ),
    ),
    "WA": EntryRule(
        48,
        "at least two days' written notice before entering, at reasonable times",
        S(
            "RCW 59.18.150(6)",
            "https://www.seattle.gov/sdci/codes/common-code-questions/notice-to-enter-a-rental-unit",
            EXCERPT,
        ),
    ),
    "WI": EntryRule(
        12,
        "at least 12 hours' notice before entering, unless the tenant agrees to less (a"
        " regulation)",
        S(
            "Wis. Admin. Code ATCP 134.09(2)(a)",
            "https://regulations.justia.com/states/wisconsin/atcp/atcp-90-139/chapter-atcp-134/",
            EXCERPT,
        ),
    ),
}

RULES: dict[str, StateRules] = {
    code: StateRules(
        name,
        late_fee=LATE_FEES.get(code),
        rent_notice=RENT_NOTICES.get(code),
        entry=ENTRIES.get(code),
    )
    for code, name in NAMES.items()
    if code in LATE_FEES or code in RENT_NOTICES or code in ENTRIES
}


# --- the checks ----------------------------------------------------------------------------------

Rule = Literal[
    "late fee wait",
    "late fee cap",
    "rent increase notice",
    "rent increase in the first year",
    "rent increase once a year",
    "entry notice",
]


@dataclass(frozen=True)
class StateWarning:
    state: str
    rule: Rule
    said: str
    cite: str
    how: How


def dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def us_date(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def days_words(days: int) -> str:
    return f"{days} day" if days == 1 else f"{days} days"


def rules_of(state: str | None) -> StateRules | None:
    return RULES.get(state) if state else None


def late_fee_warnings(
    state: str | None,
    grace_days: int,
    flat_fee: Decimal,
    percent: Decimal,
    maximum: Decimal | None,
    rents: Sequence[tuple[str, Decimal]],
    units: int,
) -> list[StateWarning]:
    """Late fee terms (grace days, a flat fee, a percent of the rent left unpaid, at most a
    maximum) against the state's wait and cap. The cap is checked at each rent of `rents` ((what,
    monthly rent): the leases in effect at the property), with `units` units at the property: with
    all of it late, a dollar of it, and where the fee or the cap changes how it grows, since
    between those both grow in straight lines."""
    found = rules_of(state)
    if found is None or found.late_fee is None:
        return []
    assert state is not None
    rule = found.late_fee
    cite, how = rule.source.cite, rule.source.how
    warnings = []
    if grace_days < rule.grace_days:
        warnings.append(
            StateWarning(
                state,
                "late fee wait",
                f"{found.name}: no late fee until {rule.wait_words} ({cite}). These terms charge"
                f" one after {days_words(grace_days)} of grace; {rule.grace_days} or more meets"
                " it.",
                cite,
                how,
            )
        )
    if rule.cap is None:
        return warnings

    def fee(late: Decimal) -> Decimal:
        most = (flat_fee + percent * late / 100).quantize(CENT, ROUND_HALF_UP)
        return most if maximum is None else min(most, maximum)

    over = []
    for what, rent in rents:
        lates = {rent, min(rent, ONE_DOLLAR), *rule.kinks}
        if maximum is not None and percent > 0:
            lates.add(((maximum - flat_fee) * 100 / percent).quantize(CENT))
        for late in sorted((late for late in lates if 0 < late <= rent), reverse=True):
            if fee(late) > rule.cap(rent, late, units):
                over.append((what, rent, late, fee(late), rule.cap(rent, late, units)))
                break
    if over:
        what, rent, late, most, allowed = over[0]
        others = len(over) - 1
        more = f"; {others} more {'lease' if others == 1 else 'leases'} too" if others else ""
        when = (
            f"On {what}'s rent of {dollars(rent)} these terms charge up to {dollars(most)}"
            if late == rent
            else f"On {what}'s rent of {dollars(rent)}, with {dollars(late)} of it late, these"
            f" terms charge {dollars(most)}"
        )
        warnings.append(
            StateWarning(
                state,
                "late fee cap",
                f"{found.name}: a late fee is at most {rule.cap_words} ({cite}). {when}, over"
                f" {dollars(allowed)}{more}.",
                cite,
                how,
            )
        )
    return warnings


@dataclass(frozen=True)
class RentHistory:
    """A lease's rent over its tenancy at the unit: the first day of the first lease its renewals
    came from, each day the rent became a new amount (oldest first; the first is `since`), and the
    lease's own first and last days."""

    since: date
    steps: tuple[tuple[date, Decimal], ...]
    lease_starts: date
    lease_ends: date | None

    def rent_on(self, day: date) -> Decimal:
        rent = self.steps[0][1]
        for on, amount in self.steps:
            if on > day:
                break
            rent = amount
        return rent


def years_between(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


def a_year_before(day: date) -> date:
    return months_after(day, -12)


def months_after(day: date, months: int) -> date:
    """The same day `months` months on (or back), the month's last day when it has no such day."""
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    month += 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise AssertionError(day)


def rent_notice_words(rule: RentNoticeRule) -> str:
    """The rule in words, with what more it asks."""
    parts = [rule.words]
    if rule.bigger is not None:
        parts.append(bigger_words(rule.bigger))
    if rule.longer_term is not None:
        parts.append(longer_term_words(*rule.longer_term))
    if rule.not_first_year is not None:
        parts.append(f"none in a tenancy's first year ({rule.not_first_year.cite})")
    if rule.once_a_year is not None:
        parts.append(f"at most one increase in 12 months ({rule.once_a_year.cite})")
    return "; ".join(parts)


def bigger_words(bigger: Bigger, lowest: Decimal | None = None) -> str:
    than = "at least" if bigger.at_least else "more than"
    of = "" if lowest is None else f" (from {dollars(lowest)})"
    return (
        f"an increase of {than} {bigger.percent.normalize():f}% within 12 months{of} needs"
        f" {bigger.days} days' written notice"
    )


def longer_term_words(months: int, days: int) -> str:
    return (
        f"a lease for a term longer than {months} month{'' if months == 1 else 's'} needs"
        f" {days} days' written notice of a rent increase"
    )


def rent_change_warnings(
    state: str | None,
    effective_on: date,
    notice_given_on: date,
    monthly_rent: Decimal,
    history: RentHistory,
) -> list[StateWarning]:
    """A rent of `monthly_rent` from `effective_on`, with written notice given on
    `notice_given_on`, against the state's notice, first-year and once-a-year rules. Only an
    increase is checked: a lower rent needs none of them."""
    found = rules_of(state)
    if found is None or found.rent_notice is None:
        return []
    assert state is not None
    rule = found.rent_notice
    before = history.rent_on(effective_on - timedelta(days=1))
    if monthly_rent <= before:
        return []
    warnings = []
    year_ago = a_year_before(effective_on)
    needed, why = rule.days, rule.words
    if rule.bigger is not None:
        bigger = rule.bigger
        lowest = min(
            [history.rent_on(max(year_ago, history.since))]
            + [amount for on, amount in history.steps if year_ago <= on < effective_on]
        )
        rise, least = (monthly_rent - lowest) * 100, bigger.percent * lowest
        if (rise >= least if bigger.at_least else rise > least) and bigger.days > needed:
            needed, why = bigger.days, bigger_words(bigger, lowest)
    if rule.longer_term is not None and history.lease_ends is not None:
        months, days = rule.longer_term
        if history.lease_ends >= months_after(history.lease_starts, months) and days > needed:
            needed, why = days, longer_term_words(months, days)
    if rule.by_tenancy is not None:
        least, tiers = rule.by_tenancy
        if (monthly_rent - before) * 100 >= least * before:
            term = (
                years_between(history.lease_starts, history.lease_ends + timedelta(days=1))
                if history.lease_ends is not None
                else 0
            )
            lived = max(years_between(history.since, effective_on), term)
            for years, days in tiers:
                if lived >= years:
                    if days > needed:
                        needed = days
                        why = (
                            f"an increase of {least.normalize():f}% or more to a tenant there"
                            f" {years} {'year' if years == 1 else 'years'} or more, or on a lease"
                            f" that long, needs {days} days' written notice"
                            if years
                            else f"an increase of {least.normalize():f}% or more needs {days}"
                            " days' written notice"
                        )
                    break
    given = (effective_on - notice_given_on).days
    if given < needed:
        warnings.append(
            StateWarning(
                state,
                "rent increase notice",
                f"{found.name}: {why} ({rule.source.cite}). Notice given"
                f" {us_date(notice_given_on)} for {us_date(effective_on)} is {days_words(given)}.",
                rule.source.cite,
                rule.source.how,
            )
        )
    first_year = rule.not_first_year
    if first_year is not None and effective_on < months_after(history.since, 12):
        warnings.append(
            StateWarning(
                state,
                "rent increase in the first year",
                f"{found.name}: no rent increase in a tenancy's first year ({first_year.cite})."
                f" This tenancy began {us_date(history.since)}.",
                first_year.cite,
                first_year.how,
            )
        )
    once = rule.once_a_year
    if once is not None:
        rises = [
            on
            for (on, amount), (_, earlier) in zip(history.steps[1:], history.steps, strict=False)
            if year_ago < on < effective_on and amount > earlier
        ]
        if rises:
            warnings.append(
                StateWarning(
                    state,
                    "rent increase once a year",
                    f"{found.name}: at most one rent increase in 12 months ({once.cite}). The"
                    f" rent rose {us_date(rises[-1])}.",
                    once.cite,
                    once.how,
                )
            )
    return warnings


def entry_warnings(
    state: str | None, visit_on: date, told_on: date, requested: bool = False
) -> list[StateWarning]:
    """A visit into a home on `visit_on`, set on `told_on`, against the state's notice before
    entry, counted in whole days (a visit's hour is not known); `requested`: a repair the tenant
    asked for."""
    found = rules_of(state)
    if found is None or found.entry is None:
        return []
    assert state is not None
    rule = found.entry
    if (visit_on - told_on).days * 24 >= rule.hours or (requested and rule.unless_requested):
        return []
    cite = rule.source.cite
    return [
        StateWarning(
            state,
            "entry notice",
            f"{found.name} asks {rule.words} ({cite}). This visit on {us_date(visit_on)} was set"
            f" on {us_date(told_on)}.",
            cite,
            rule.source.how,
        )
    ]
