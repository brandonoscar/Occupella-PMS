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
