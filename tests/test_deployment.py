"""A real server refuses the test partners (src/pms/deployment.py): a server whose PMS_PUBLIC_URL
is https names no fake partner, unless PMS_FAKE_PARTNERS=allow says it is a public demo of a
synthetic company. Development, CI, the browser tests and the demo all run over http."""

import pytest

from pms import deployment, email, nightly
from pms.api import Settings, create_app

ALL_FAKE = {name: "fake" for name in deployment.PARTNERS}
NONE = {name: "" for name in deployment.PARTNERS}


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8123", "http://localhost:8100", "http://testserver", ""]
)
def test_a_server_over_http_runs_the_fake_partners(url):
    deployment.refuse_fakes(url, ALL_FAKE)


@pytest.mark.parametrize("name", deployment.PARTNERS)
def test_a_server_over_https_refuses_each_fake_partner(name):
    with pytest.raises(deployment.FakePartnerRefused) as refused:
        deployment.refuse_fakes("https://pms.example.com", {**NONE, name: " fake "})
    assert str(refused.value).startswith(f"{name} names the fake partner")
    assert "PMS_FAKE_PARTNERS=allow" in str(refused.value)


def test_the_refusal_names_every_fake_partner_at_once():
    with pytest.raises(deployment.FakePartnerRefused) as refused:
        deployment.refuse_fakes("HTTPS://pms.example.com", ALL_FAKE)
    assert str(refused.value).startswith(
        "PMS_EMAIL_SENDER, PMS_GEOCODER, PMS_PAYMENTS, PMS_SCREENING_PARTNER name the fake"
    )


def test_real_partners_or_none_start_over_https():
    deployment.refuse_fakes("https://pms.example.com", NONE)
    deployment.refuse_fakes(
        "https://pms.example.com",
        {
            "PMS_PAYMENTS": "stripe",
            "PMS_SCREENING_PARTNER": "",
            "PMS_GEOCODER": "census",
            "PMS_EMAIL_SENDER": "smtp",
        },
    )


@pytest.mark.parametrize("allow", ["allow", " Allow "])
def test_allow_lets_a_public_demo_run_them(allow):
    deployment.refuse_fakes("https://demo.example.com", ALL_FAKE, allow)


@pytest.mark.parametrize("allow", ["", "yes", "true", "1"])
def test_anything_but_allow_still_refuses(allow):
    with pytest.raises(deployment.FakePartnerRefused):
        deployment.refuse_fakes("https://demo.example.com", ALL_FAKE, allow)


def test_the_app_refuses_to_start_with_a_fake_partner_over_https():
    with pytest.raises(deployment.FakePartnerRefused, match="PMS_PAYMENTS names the fake"):
        create_app(
            Settings(
                database_url="postgresql://unused",
                public_url="https://pms.example.com",
                payments="fake",
            )
        )


def test_the_app_starts_a_public_demo_when_allowed():
    app = create_app(
        Settings(
            database_url="postgresql://unused",
            public_url="https://demo.example.com",
            payments="fake",
            screening_partner="fake",
            geocoder="fake",
            email_sender="fake",
            fake_partners="allow",
        )
    )
    assert app.state.payments.server.name == "fake"


def test_settings_read_the_allowance_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PMS_FAKE_PARTNERS", "allow")
    assert Settings.from_env().fake_partners == "allow"
    monkeypatch.delenv("PMS_FAKE_PARTNERS")
    assert Settings.from_env().fake_partners == ""


def https_with_fake_payments(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example.com")
    monkeypatch.setenv("PMS_PAYMENTS", "fake")
    monkeypatch.delenv("PMS_FAKE_PARTNERS", raising=False)


def test_the_nightly_command_refuses_before_it_connects(monkeypatch):
    https_with_fake_payments(monkeypatch)
    with pytest.raises(deployment.FakePartnerRefused):
        nightly.main([])


def test_the_email_command_refuses_before_it_sends(monkeypatch):
    https_with_fake_payments(monkeypatch)
    monkeypatch.setenv("PMS_EMAIL_SENDER", "fake")
    with pytest.raises(deployment.FakePartnerRefused, match="PMS_EMAIL_SENDER, PMS_PAYMENTS"):
        email.main(["deliver"])
