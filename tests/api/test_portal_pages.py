"""The built portal (frontend/dist), served by the API when PMS_STATIC_DIR is set."""

import re

import pytest
from fastapi.testclient import TestClient

from pms.api import Settings, create_app


@pytest.fixture
def portal(tmp_path, database_url):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><head></head><div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log('portal')")
    settings = Settings(database_url=database_url, cookie_secure=False, static_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/sign-in", "/invite", "/anything/else"])
def test_every_page_is_the_portal_with_a_strict_policy(portal, path):
    answer = portal.get(path)

    assert answer.status_code == 200
    assert answer.text.startswith("<!doctype html>")
    policy = answer.headers["content-security-policy"]
    assert "default-src 'self'" in policy and "script-src 'self'" in policy
    assert "unsafe-inline" not in policy and "frame-ancestors 'none'" in policy
    assert answer.headers["x-frame-options"] == "DENY"


def test_each_page_carries_a_fresh_nonce_for_react_arias_styles_and_allows_no_other(portal):
    pages = [portal.get("/"), portal.get("/")]
    nonces = [re.findall(r'<meta property="csp-nonce" nonce="([^"]+)">', p.text) for p in pages]

    assert [len(found) for found in nonces] == [1, 1] and nonces[0] != nonces[1]
    for page, (nonce,) in zip(pages, nonces, strict=True):
        assert page.text.startswith('<!doctype html><head><meta property="csp-nonce"')
        assert len(nonce) >= 22
        policy = page.headers["content-security-policy"]
        assert f"style-src 'self' 'nonce-{nonce}';" in policy
        # Scripts take no nonce: they stay the portal's own files.
        assert "script-src 'self';" in policy and policy.count("nonce-") == 1


def test_the_portals_files_are_served(portal):
    answer = portal.get("/assets/app.js")

    assert answer.status_code == 200
    assert "portal" in answer.text


def test_the_api_and_health_are_not_pages(portal):
    assert portal.get("/api/nothing").status_code == 404
    assert portal.get("/api/nothing").json() == {"detail": "no such endpoint"}
    assert portal.get("/api").status_code == 404
    assert portal.get("/health").json() == {"status": "ok"}
    assert portal.get("/api/me").status_code == 401


def test_without_a_portal_there_are_no_pages(client):
    assert client.get("/").status_code == 404
