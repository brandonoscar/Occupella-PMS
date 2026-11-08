"""The PMS mounted under a path, as Occupella's hosted app serves it (https://app.occupella.com
/books, AgenticHelixis src/helixis/books.py): an outer app mounts create_app at /books and runs
its lifespan, and PMS_PUBLIC_URL carries the path.

Starlette keeps the whole path in a mounted app's request (/books/api/...) and the mount's in
root_path, so these pin that every rule reading a path reads it within the PMS (deps.app_path):
the custom roles' areas, the API's no-store, the portal's pages and assets, and every link the
server writes carrying the path. The portal itself is built for the path (frontend/src/base.ts,
whose tests check every URL the browser gets).
"""

import dataclasses
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from people import join_as_staff
from starlette.requests import Request

from pms.api import create_app
from pms.api.deps import app_path

PUBLIC_URL = "http://testserver/books"
INDEX = (
    '<!doctype html><html><head><script type="module" src="/books/assets/app.js"></script>'
    "</head><body></body></html>"
)


def mounted(settings, portal) -> TestClient:
    """A browser on an outer app that mounts the PMS at /books, its requests sent under it."""
    inner = create_app(dataclasses.replace(settings, public_url=PUBLIC_URL, static_dir=portal))

    @asynccontextmanager
    async def lifespan(_app):
        async with inner.router.lifespan_context(inner):
            yield

    outer = FastAPI(lifespan=lifespan)
    outer.mount("/books", inner)
    return TestClient(outer, base_url="http://testserver/books")


@pytest.fixture
def portal(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    return tmp_path


@pytest.fixture
def under(settings, portal):
    with mounted(settings, portal) as browser:
        yield browser


@pytest.fixture
def other_under(settings, portal):
    """A second browser on the mounted app, with its own cookies."""
    with mounted(settings, portal) as browser:
        yield browser


def test_the_api_answers_under_the_path_and_is_never_cached(under):
    answer = under.get("/api/me")
    assert answer.request.url.path == "/books/api/me"
    assert answer.status_code == 401
    assert answer.json()["detail"] == "sign in first"
    assert answer.headers["Cache-Control"] == "no-store"


def test_the_portal_and_its_assets_are_served_under_the_path(under):
    for page in ("/", "/properties", "/leases/00000000-0000-4000-8000-000000000001"):
        answer = under.get(page)
        assert answer.status_code == 200, page
        assert 'src="/books/assets/app.js"' in answer.text
        assert '<meta property="csp-nonce"' in answer.text
        assert "nonce-" in answer.headers["Content-Security-Policy"]
    script = under.get("/assets/app.js")
    assert script.status_code == 200
    assert script.text == "export {};"
    # An API path that isn't one is the API's 404, never the portal's page.
    missing = under.get("/api/nothing-here")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")


def test_a_change_from_another_site_is_refused_under_the_path(under):
    answer = under.post("/api/sign-out", json={}, headers={"Origin": "http://elsewhere.test"})
    assert answer.status_code == 403
    same = under.post("/api/sign-out", json={}, headers={"Origin": "http://testserver"})
    assert same.status_code == 204


def test_a_custom_role_holds_under_the_path(under, other_under, conn, app_conn, pmc):
    """The areas read the path within the PMS: under a mount a clerk whose role allows only
    bills is still refused owners' money (deps.app_path; without it no area matched /books/...
    and every staff write passed)."""
    join_as_staff(under, conn, app_conn, pmc, name="Admin 1")
    clerk = join_as_staff(other_under, conn, app_conn, pmc, name="Clerk 1", roles=[])
    made = under.post("/api/custom-roles", json={"name": "Bills only", "areas": ["bills"]})
    assert made.status_code == 201, made.text
    given = under.post(
        f"/api/custom-roles/{made.json()['id']}/staff",
        json={"staff_id": str(clerk.person_id), "granted": True},
    )
    assert given.status_code == 204, given.text
    refused = other_under.post(
        "/api/payables/contributions",
        json={
            "request_id": str(uuid.uuid4()),
            "account": pmc.owners[0].account,
            "amount": "10.00",
            "on": str(date.today()),
        },
    )
    assert refused.status_code == 403, refused.text
    assert "your roles don't let you change owners' money" in refused.json()["detail"]


def test_the_links_the_server_writes_carry_the_path(under, conn, app_conn, pmc):
    join_as_staff(under, conn, app_conn, pmc, name="Admin 1")
    invite = under.post(
        "/api/invites",
        json={"kind": "owner", "person_id": str(pmc.owners[0].owner_id), "email": "o1@example.com"},
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["url"].startswith("http://testserver/books/invite#")
    feed = under.post("/api/calendar/mine", json={})
    assert feed.status_code == 201, feed.text
    assert feed.json()["url"].startswith("http://testserver/books/api/calendar/")


@pytest.mark.parametrize(
    ("path", "root", "within"),
    [
        ("/books/api/rent/x", "/books", "/api/rent/x"),
        ("/books", "/books", "/"),
        ("/books/", "/books", "/"),
        ("/api/rent/x", "", "/api/rent/x"),
        # A root path that only starts with the mount's letters is not under it.
        ("/bookshelf/api/rent/x", "/books", "/bookshelf/api/rent/x"),
    ],
)
def test_the_path_within_the_pms(path, root, within):
    request = Request({"type": "http", "path": path, "root_path": root, "headers": []})
    assert app_path(request) == within
