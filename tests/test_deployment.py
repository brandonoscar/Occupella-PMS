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


# --- Stripe's test keys on a live server (docs/edge-cases.md P-048) -------------------------
# A test payment would go into the trust books as real money: a live server (https, a host other
# than the machine itself) takes only live keys, from the environment or entered by a company.

TEST_KEYS = ["sk_" + "test_" + "Synthetic000000000000A1b2", "rk_" + "test_" + "Synthetic0000A1b2"]
LIVE_KEYS = ["sk_" + "live_" + "Synthetic000000000000Z9y8", "rk_" + "live_" + "Synthetic0000Z9y8"]
WEBHOOK = "whsec_" + "Synthetic0000000000000001"


@pytest.mark.parametrize(
    "url, live",
    [
        ("https://rentals.example.com", True),
        ("HTTPS://Rentals.Example.com/books", True),
        ("http://rentals.example.com", False),
        ("https://localhost:8443", False),
        ("https://127.0.0.1", False),
        ("https://[::1]:8443", False),
        ("https://pms.localhost", False),
        ("", False),
    ],
)
def test_a_live_server_is_at_https_on_a_host_other_than_the_machine_itself(url, live):
    assert deployment.is_live(url) is live


def test_a_public_demo_allowed_its_test_partners_takes_no_real_money():
    assert not deployment.is_live("https://demo.example.com", " Allow ")
    assert deployment.is_live("https://demo.example.com", "yes")


@pytest.mark.parametrize("key", [*TEST_KEYS, "", "pk_" + "live_" + "Synthetic0000Z9y8"])
def test_a_live_server_refuses_stripes_test_keys_without_repeating_them(key):
    with pytest.raises(deployment.KeysNotLive) as refused:
        deployment.refuse_test_keys("https://rentals.example.com", "stripe", key)
    assert "docs/edge-cases.md P-048" in str(refused.value)
    assert not key or key not in str(refused.value)


@pytest.mark.parametrize("key", LIVE_KEYS)
def test_a_live_server_takes_live_keys(key):
    deployment.refuse_test_keys("https://rentals.example.com", " stripe ", f" {key} ")


@pytest.mark.parametrize(
    "url, partner, allow",
    [
        ("http://rentals.example.com", "stripe", ""),
        ("https://localhost:8443", "stripe", ""),
        ("https://demo.example.com", "stripe", "allow"),
        ("https://rentals.example.com", "", ""),
        ("https://rentals.example.com", "fake", "allow"),
    ],
)
def test_test_keys_run_where_no_real_money_is_taken(url, partner, allow):
    deployment.refuse_test_keys(url, partner, TEST_KEYS[0], allow)


def test_the_app_refuses_to_start_with_stripes_test_keys_over_https(monkeypatch):
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", TEST_KEYS[0])
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", WEBHOOK)
    with pytest.raises(deployment.KeysNotLive):
        create_app(
            Settings(
                database_url="postgresql://unused",
                public_url="https://rentals.example.com",
                payments="stripe",
            )
        )


def test_a_live_servers_payments_take_live_keys_and_no_companys_test_keys(monkeypatch):
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", LIVE_KEYS[0])
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", WEBHOOK)
    live = create_app(
        Settings(
            database_url="postgresql://unused",
            public_url="https://rentals.example.com",
            payments="stripe",
        )
    )
    assert live.state.payments.live is True
    assert live.state.payments.server.live is True
    local = create_app(Settings(database_url="postgresql://unused", public_url="http://localhost"))
    assert local.state.payments.live is False


def test_the_command_line_tools_refuse_test_keys_too(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://rentals.example.com")
    monkeypatch.setenv("PMS_PAYMENTS", "stripe")
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", TEST_KEYS[0])
    monkeypatch.delenv("PMS_FAKE_PARTNERS", raising=False)
    with pytest.raises(deployment.KeysNotLive):
        nightly.main([])
    with pytest.raises(deployment.KeysNotLive):
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
        ("https://rentals.example.com/?from=email", "carries a query or a fragment"),
        ("https://rentals.example.com/#top", "carries a query or a fragment"),
        ("https://rentals.example.com/books?", "carries a query or a fragment"),
        ("https://rentals.example.com/books#", "carries a query or a fragment"),
        ("https://rentals.example.com/books/../admin", "isn't plain words between slashes"),
        ("https://rentals.example.com//books", "isn't plain words between slashes"),
        ("https://rentals.example.com/my books", "isn't plain words between slashes"),
        # docs/edge-cases.md H-167: every emailed link would carry them.
        ("https://admin:synthetic@rentals.test", "names a user or password"),
        ("https://admin@rentals.test", "names a user or password"),
    ],
)
def test_the_app_refuses_to_start_at_an_address_no_browser_opens_it_at(url, says):
    with pytest.raises(deployment.PublicUrlRefused) as refused:
        create_app(Settings(database_url="postgresql://unused", public_url=url))
    assert says in str(refused.value)
    if "@" in url:  # never repeated: it carries a user and maybe a password
        assert "admin" not in str(refused.value) and "synthetic" not in str(refused.value)
    else:
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
        # Where an app mounts the PMS: Occupella's hosted app at /books (frontend/src/base.ts).
        "https://app.example.com/books",
        "https://app.example.com/books/",
        "https://app.example.com/pms/portal",
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


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        # docs/edge-cases.md H-161: a browser's Origin leaves the default port out.
        ("https://rentals.example.com:443", "https://rentals.example.com"),
        ("http://localhost:80", "http://localhost"),
        # H-162: and writes the scheme and host in lower case.
        ("HTTPS://Rentals.Example.COM/", "https://rentals.example.com"),
        ("https://rentals.example.com:8443", "https://rentals.example.com:8443"),
        ("http://[::1]:8000", "http://[::1]:8000"),
        ("http://127.0.0.1:8123", "http://127.0.0.1:8123"),
    ],
)
def test_the_origin_is_written_as_a_browser_sends_it(url, origin):
    assert Settings(database_url="postgresql://unused", public_url=url).origin == origin
