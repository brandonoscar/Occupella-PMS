"""A real server refuses the test partners (src/pms/deployment.py): a server whose PMS_PUBLIC_URL
is https names no fake partner, unless PMS_FAKE_PARTNERS=allow says it is a public demo of a
synthetic company. Development, CI, the browser tests and the demo all run over http."""

import pathlib

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
                public_url="https://rentals.example.com",
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
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://rentals.example.com")
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


# --- PMS_PUBLIC_URL (docs/edge-cases.md H-059, H-060) ----------------------------------------
# The address people open the PMS at. Every change a browser sends is refused unless it comes
# from this origin, and every emailed link starts with it, so a server at the wrong one lets
# nobody sign in and emails links that go nowhere. It refuses to start instead.


ROOT = pathlib.Path(__file__).resolve().parents[1]


def env_example_public_url():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith("PMS_PUBLIC_URL="):
            return line.removeprefix("PMS_PUBLIC_URL=")
    raise AssertionError(".env.example sets no PMS_PUBLIC_URL")


def test_env_example_still_offers_the_placeholder_the_server_refuses():
    assert env_example_public_url() == deployment.PLACEHOLDER


@pytest.mark.parametrize(
    "url", [deployment.PLACEHOLDER, deployment.PLACEHOLDER + "/", " HTTPS://PMS.Example.com "]
)
def test_the_app_refuses_to_start_at_the_example_address(url):
    with pytest.raises(deployment.PublicUrlRefused) as refused:
        create_app(Settings(database_url="postgresql://unused", public_url=url))
    assert str(refused.value).startswith(
        "PMS_PUBLIC_URL is still .env.example's https://pms.example.com"
    )


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("pms.rentals.example.com", "starts with https:// or http://"),
        ("ftp://rentals.example.com", "starts with https:// or http://"),
        ("https://", "names no host"),
        ("https:///invite", "names no host"),
        ("https://rentals.example.com/pms", "is served at the root"),
        ("https://rentals.example.com/?from=email", "is served at the root"),
        ("https://rentals.example.com/#top", "is served at the root"),
    ],
)
def test_the_app_refuses_to_start_at_an_address_no_browser_opens_it_at(url, says):
    with pytest.raises(deployment.PublicUrlRefused) as refused:
        create_app(Settings(database_url="postgresql://unused", public_url=url))
    assert says in str(refused.value)
    assert str(refused.value).startswith(f"PMS_PUBLIC_URL {url!r}")


@pytest.mark.parametrize(
    "url",
    [
        "http://testserver",
        "http://127.0.0.1:8123",
        "http://localhost:8100",
        "https://rentals.example.com",
        "https://rentals.example.com/",
        "https://pms.example.net:8443",
    ],
)
def test_an_origin_starts(url):
    deployment.refuse_public_url(url)


@pytest.mark.parametrize("command", ["nightly", "email"])
def test_the_commands_refuse_the_example_address_before_they_connect(monkeypatch, command):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PMS_PUBLIC_URL", deployment.PLACEHOLDER)
    monkeypatch.setenv("PMS_EMAIL_SENDER", "smtp")
    for name in ("PMS_PAYMENTS", "PMS_SCREENING_PARTNER", "PMS_GEOCODER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(deployment.PublicUrlRefused):
        if command == "nightly":
            nightly.main([])
        else:
            email.main(["deliver"])


def test_the_commands_run_with_no_address_set(monkeypatch):
    # Unset, each command keeps its own default (http://localhost:8000), as before.
    monkeypatch.delenv("PMS_PUBLIC_URL", raising=False)
    deployment.refuse_fakes_in({})
