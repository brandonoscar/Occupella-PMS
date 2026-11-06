"""What one address may try (src/pms/api/throttle.py): an address that failed too many sign-ins is
refused for a while whatever email it tries, while other addresses go on; an address asks for only
so many password resets. Each app keeps its own counts, so these tests lower them on theirs."""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from people import PASSWORD, join_as_owner

from pms.api import create_app, throttle


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- the limiter ------------------------------------------------------------------------------


def test_an_address_waits_once_it_hits_the_limit_until_its_oldest_leaves_the_window():
    clock = Clock()
    limiter = throttle.Limiter(3, 60.0, clock)
    for _ in range(3):
        assert limiter.wait("198.51.100.7") is None
        clock.now += 10
        limiter.hit("198.51.100.7")
    # Hits at 1010, 1020 and 1030 (each counted after its 10 seconds): the first leaves at 1070.
    assert limiter.wait("198.51.100.7") == pytest.approx(40.0)
    assert limiter.wait("198.51.100.8") is None
    clock.now = 1070.0
    assert limiter.wait("198.51.100.7") is None


def test_an_address_whose_hits_all_left_the_window_is_forgotten():
    clock = Clock()
    limiter = throttle.Limiter(1, 60.0, clock)
    limiter.hit("198.51.100.9")
    assert limiter.wait("198.51.100.9") is not None
    clock.now += 61
    assert limiter.wait("198.51.100.9") is None
    assert "198.51.100.9" not in limiter._seen  # nothing kept of it


def test_it_forgets_the_address_heard_from_longest_ago(monkeypatch):
    monkeypatch.setattr(throttle, "REMEMBERED", 2)
    limiter = throttle.Limiter(1, 60.0, Clock())
    for address in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        limiter.hit(address)
    assert limiter.wait("203.0.113.1") is None
    assert limiter.wait("203.0.113.2") is not None
    assert limiter.wait("203.0.113.3") is not None


def test_a_waiting_address_is_told_in_minutes_with_retry_after():
    limiter = throttle.Limiter(1, 600.0, Clock())
    limiter.hit("192.0.2.9")
    with pytest.raises(throttle.HTTPException) as refused:
        throttle.refuse_if_waiting(limiter, "192.0.2.9", "failed sign-ins")
    assert refused.value.status_code == 429
    assert (
        refused.value.detail
        == "too many failed sign-ins from this address: try again in 10 minutes"
    )
    assert refused.value.headers == {"Retry-After": "600"}


def test_an_ipv6_visitor_counts_as_their_whole_64(app, pmc):
    """docs/edge-cases.md U-032: an IPv6 visitor holds a /64 and may use any address in it, so
    each address counting alone let one rotate past the limit. A /64 counts as one address; IPv4
    and an IPv4 address written as IPv6 count as the IPv4 address."""
    wrong = {"pmc_id": str(pmc.pmc_id), "email": "someone@example.com", "password": "wrong"}
    hosts = [f"2001:db8:5:7::{n + 1}" for n in range(3)] + [
        "2001:db8:5:7:ffff::9",
        "2001:db8:5:8::1",
    ]
    with ExitStack() as open_:
        clients = [open_.enter_context(TestClient(app, client=(h, 50000))) for h in hosts]
        for here in clients[:3]:
            assert here.post("/api/sign-in", json=wrong).status_code == 401
        same_64, next_64 = clients[3:]
        assert same_64.post("/api/sign-in", json=wrong).status_code == 429
        assert next_64.post("/api/sign-in", json=wrong).status_code == 401


def test_the_address_counted_is_an_ipv6_64_or_the_ipv4_address():
    def counted(host):
        return throttle.counted_as(host)

    assert counted("2001:db8:5:7:1:2:3:4") == "2001:db8:5:7::/64"
    assert counted("2001:DB8:5:7::1") == "2001:db8:5:7::/64"
    assert counted("::ffff:198.51.100.7") == "198.51.100.7"
    assert counted("198.51.100.7") == "198.51.100.7"
    assert counted("testclient") == "testclient"  # not an address: as it came


# --- signing in -------------------------------------------------------------------------------


@pytest.fixture
def app(settings):
    made = create_app(settings)
    made.state.sign_in_failures = throttle.Limiter(3, 900.0)
    made.state.resets_asked = throttle.Limiter(2, 3600.0)
    return made


def test_an_address_that_failed_too_often_is_refused_whatever_it_tries(
    app, other_client, app_conn, pmc
):
    owner = join_as_owner(other_client, app_conn, pmc)
    right = {"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": PASSWORD}
    with (
        TestClient(app, client=("198.51.100.20", 50000)) as here,
        TestClient(app, client=("198.51.100.21", 50000)) as elsewhere,
    ):
        # A right password doesn't count; three wrong ones on three emails do.
        assert here.post("/api/sign-in", json=right).status_code == 200
        for n in range(3):
            wrong = {**right, "email": f"someone.{n}@example.com"}
            assert here.post("/api/sign-in", json=wrong).status_code == 401

        refused = here.post("/api/sign-in", json=right)
        assert refused.status_code == 429, refused.text
        assert refused.json()["detail"].startswith("too many failed sign-ins from this address")
        assert int(refused.headers["retry-after"]) > 0
        # Another address signs in as before.
        assert elsewhere.post("/api/sign-in", json=right).status_code == 200


def test_bad_input_is_refused_before_it_counts(app, pmc):
    with TestClient(app, client=("198.51.100.30", 50000)) as here:
        for _ in range(5):
            assert here.post("/api/sign-in", json={"pmc_id": "nope"}).status_code == 422
        assert app.state.sign_in_failures.wait("198.51.100.30") is None


def test_an_address_asks_for_only_so_many_password_resets(app, pmc):
    body = {"pmc_id": str(pmc.pmc_id), "email": "someone@example.com"}
    with TestClient(app, client=("198.51.100.40", 50000)) as here:
        assert here.post("/api/password-reset", json=body).status_code == 202
        assert here.post("/api/password-reset", json=body).status_code == 202
        refused = here.post("/api/password-reset", json=body)
        assert refused.status_code == 429
        assert refused.json()["detail"].startswith("too many password resets from this address")


def test_behind_the_proxy_a_visitor_cant_dodge_the_limit_by_naming_another_address(settings, pmc):
    """docs/edge-cases.md U-033, H-068: compose.selfhost.yaml trusts whoever connects ("*"),
    the HTTPS proxy on the server itself, and nginx as docs/install.md sets it appends the
    visitor's address to any X-Forwarded-For the visitor sent. The address is the one the proxy
    wrote, the last; the ones before it are the visitor's own words."""
    from dataclasses import replace

    app = create_app(replace(settings, trusted_proxies="*"))
    app.state.sign_in_failures = throttle.Limiter(3, 900.0)
    wrong = {"pmc_id": str(pmc.pmc_id), "email": "someone@example.com", "password": "wrong"}
    with TestClient(app, client=("127.0.0.1", 50000)) as proxy:
        for n in range(3):
            forged = {"X-Forwarded-For": f"192.0.2.{n}, 198.51.100.50"}
            assert proxy.post("/api/sign-in", json=wrong, headers=forged).status_code == 401
        forged = {"X-Forwarded-For": "192.0.2.99, 198.51.100.50"}
        assert proxy.post("/api/sign-in", json=wrong, headers=forged).status_code == 429
        # Another visitor, through the same proxy, goes on.
        other = {"X-Forwarded-For": "198.51.100.51"}
        assert proxy.post("/api/sign-in", json=wrong, headers=other).status_code == 401


def test_a_proxy_the_app_doesnt_trust_is_named_in_the_log_once(settings, pmc, caplog):
    """docs/edge-cases.md H-067, C-110, U-034: behind a proxy PMS_TRUSTED_PROXIES doesn't name,
    every visitor has the proxy's address, so one person's failed sign-ins lock out everyone
    behind it and signatures keep the proxy's address. The server's log says so, once a proxy,
    so whoever runs it sees what to set."""
    import logging

    caplog.set_level(logging.WARNING, logger="pms.api")
    app = create_app(settings)
    with (
        TestClient(app, client=("10.20.30.40", 50000)) as proxied,
        TestClient(app, client=("198.51.100.60", 50000)) as direct,
    ):
        for _ in range(3):
            proxied.get("/api/me", headers={"X-Forwarded-For": "198.51.100.61"})
        direct.get("/api/me")

    said = [
        r.getMessage()
        for r in caplog.records
        if r.name == "pms.api" and "PMS_TRUSTED_PROXIES" in r.getMessage()
    ]
    assert said == [
        "requests come through a proxy at '10.20.30.40' that PMS_TRUSTED_PROXIES doesn't name:"
        " every visitor behind it counts as that one address, for the sign-in limit and in"
        " signatures' evidence; name it in PMS_TRUSTED_PROXIES"
    ]


def test_a_trusted_proxy_is_said_nothing_of(settings, caplog):
    import logging
    from dataclasses import replace

    app = create_app(replace(settings, trusted_proxies="127.0.0.1"))
    with caplog.at_level(logging.WARNING, logger="pms.api"), TestClient(app) as here:
        here.get("/api/me", headers={"X-Forwarded-For": "198.51.100.50"})
    assert not [r for r in caplog.records if "PMS_TRUSTED_PROXIES" in r.getMessage()]
