"""The screening partner interface and its fake partner (src/pms/leasing/screening.py)."""

from datetime import date
from decimal import Decimal

import pytest

from pms.api import Settings
from pms.leasing import screening


def application(income, rent="1500.00"):
    return screening.Application(
        applicants=(screening.Applicant("Applicant 1", "a@example.com"),),
        move_in_on=date(2026, 11, 1),
        monthly_income=Decimal(income),
        monthly_rent=Decimal(rent),
    )


@pytest.mark.parametrize(
    ("income", "recommendation"),
    [
        ("4500.00", "approve"),
        ("99999.00", "approve"),
        ("4499.99", "conditional"),
        ("3000.00", "conditional"),
        ("2999.99", "decline"),
        ("0.00", "decline"),
    ],
)
def test_the_fake_partner_answers_from_income_against_rent(income, recommendation):
    result = screening.FakeScreening().screen(application(income))

    assert result.recommendation == recommendation
    assert bool(result.reasons) == (recommendation != "approve")
    assert result.partner == "Fake Screening Partner"
    assert "synthetic" in result.agency


def test_the_fake_partner_applies_the_companys_criteria():
    """Migration 0089 (q085): approved from approve_multiple times the rent, on conditions from
    conditional_multiple times, declined below; each reason names the multiple it missed."""
    criteria = screening.Criteria(Decimal("1.5"), Decimal("1"))
    fake = screening.FakeScreening()

    answers = [
        fake.screen(application(income, "1000.00"), criteria)
        for income in ("1500.00", "1499.99", "1000.00", "999.99")
    ]

    assert [(a.recommendation, a.reasons) for a in answers] == [
        ("approve", ()),
        ("conditional", ("Household income is less than 1.5 times the rent",)),
        ("conditional", ("Household income is less than 1.5 times the rent",)),
        ("decline", ("Household income is less than the rent",)),
    ]
    assert screening.Criteria(Decimal(3), Decimal(2)) == screening.DEFAULT


@pytest.mark.parametrize(
    ("multiple", "words"),
    [
        ("1", "the rent"),
        ("1.00", "the rent"),
        ("2", "twice the rent"),
        ("3.00", "three times the rent"),
        ("10", "ten times the rent"),
        ("2.5", "2.5 times the rent"),
        ("2.50", "2.5 times the rent"),
        ("0.75", "0.75 times the rent"),
    ],
)
def test_a_reason_says_the_multiple_in_words(multiple, words):
    assert screening.times(Decimal(multiple)) == words


def test_each_fake_screening_has_its_own_reference():
    fake = screening.FakeScreening()

    assert fake.screen(application("4500")).reference != fake.screen(application("4500")).reference


def test_a_partner_is_chosen_by_name():
    assert screening.partner_named("") is None
    assert screening.partner_named("  ") is None
    assert isinstance(screening.partner_named("fake"), screening.FakeScreening)
    with pytest.raises(screening.NoPartner, match="no screening partner called 'acme'"):
        screening.partner_named("acme")


def test_the_server_reads_its_partner_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://example.invalid/pms")
    monkeypatch.setenv("PMS_SCREENING_PARTNER", "fake")
    assert Settings.from_env().screening_partner == "fake"
    monkeypatch.delenv("PMS_SCREENING_PARTNER")
    assert Settings.from_env().screening_partner == ""
