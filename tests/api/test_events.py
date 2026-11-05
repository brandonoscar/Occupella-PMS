"""Events through the API (ADR 0011, migration 20261105000500, src/pms/api/events.py): software
with a key reads its company's events in order, after the last one it read; an admin adds the
webhooks they go to and sees each secret once, and takes one back. Every name and address here is
synthetic."""

import base64

import pytest
from fastapi.testclient import TestClient
from helpers import events_seen, make_pmc
from people import join_as_staff, join_as_tenant

from pms.api import create_app

ADMINS = "only an admin sees, makes and takes back API keys"
NOT_VALID = "this key was taken back or doesn't exist"
HOOK = "https://hooks.example.com/occupella"


@pytest.fixture
def data_key(monkeypatch):
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(b"e" * 32).decode())


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


@pytest.fixture
def software(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def key(client, admin):
    made = ok(client.post("/api/ai/keys", json={"name": "Assistant"}), 201)
    return {"Authorization": f"Bearer {made['secret']}"}


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, says=None):
    assert answer.status_code == status, answer.text
    if says is not None:
        assert says in str(answer.json()["detail"]), answer.text


def open_order(client, pmc, summary):
    return ok(
        client.post(
            "/api/work-orders",
            json={"property_id": str(pmc.owners[0].property_id), "summary": summary},
        ),
        201,
    )["id"]


def read(software, key, **params):
    return ok(software.get("/api/events", params=params, headers=key))


def test_software_reads_its_companys_events_in_order_after_the_last_it_read(
    client, software, conn, pmc, admin, key
):
    first = read(software, key)
    start = first["next"]
    one = open_order(client, pmc, "Leak")
    two = open_order(client, pmc, "Gate")
    events_seen(conn, pmc.pmc_id)

    got = read(software, key, after=start) if start else read(software, key)
    assert [(e["kind"], e["subject"]["work_order_id"]) for e in got["events"]] == [
        ("work_order.opened", one),
        ("work_order.opened", two),
    ]
    assert got["next"] == got["events"][-1]["id"]
    # Ids only: the work order's words are read through the API, as the key allows.
    assert "Leak" not in str(got)
    # Again after the last: nothing new, and the same place to ask after.
    again = read(software, key, after=got["next"])
    assert again == {"events": [], "next": got["next"]}
    # A page at a time.
    paged = read(software, key, after=start, limit=1) if start else read(software, key, limit=1)
    assert [e["subject"]["work_order_id"] for e in paged["events"]] == [one]
    rest = read(software, key, after=paged["next"])
    assert [e["subject"]["work_order_id"] for e in rest["events"]] == [two]


def test_a_draft_decided_is_an_event(client, software, conn, pmc, admin, key):
    start = read(software, key)["next"]
    made = ok(
        software.post(
            "/api/ai/drafts",
            json={
                "proposal": {
                    "kind": "note",
                    "about": "owner",
                    "about_id": str(pmc.owners[0].owner_id),
                    "body": "Hi",
                }
            },
            headers=key,
        ),
        201,
    )
    ok(client.post(f"/api/drafts/{made['id']}/decline", json={"reason": "Not now"}))
    events_seen(conn, pmc.pmc_id)
    got = read(software, key, after=start) if start else read(software, key)
    assert [(e["kind"], e["subject"]) for e in got["events"]] == [
        ("draft.declined", {"draft_id": made["id"]})
    ]


def test_another_companys_events_are_never_read(
    client, other_client, software, conn, app_conn, pmc, admin, key
):
    elsewhere = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, elsewhere, name="Other Staff 1")
    open_order(other_client, elsewhere, "Theirs")
    events_seen(conn, elsewhere.pmc_id)  # theirs readable by now, were it ever
    got = read(software, key)
    assert all(
        e["subject"].get("property_id") != str(elsewhere.owners[0].property_id)
        for e in got["events"]
    )
    theirs = conn.execute(
        "SELECT id FROM trust_events WHERE pmc_id = %s ORDER BY id DESC LIMIT 1",
        (elsewhere.pmc_id,),
    ).fetchone()[0]
    refused(
        software.get("/api/events", params={"after": theirs}, headers=key),
        422,
        "after names no event of this company",
    )


@pytest.mark.parametrize("params", [{"after": 0}, {"after": "x"}, {"limit": 0}, {"limit": 501}])
def test_a_bad_place_or_page_is_refused(software, key, params):
    refused(software.get("/api/events", params=params, headers=key), 422)


def test_only_a_key_reads_events(client, other_client, software, conn, app_conn, pmc, admin):
    refused(software.get("/api/events"), 401, NOT_VALID)
    refused(client.get("/api/events"), 401, NOT_VALID)  # a staff session is no key
    join_as_tenant(other_client, conn, app_conn, pmc)
    refused(other_client.get("/api/events"), 401, NOT_VALID)


# --- an admin's webhooks -----------------------------------------------------------------------


def test_an_admin_adds_a_webhook_sees_its_secret_once_and_takes_it_back(
    client, conn, pmc, admin, data_key
):
    made = ok(client.post("/api/ai/webhooks", json={"url": f"  {HOOK}  "}), 201)
    assert made["url"] == HOOK and made["secret"].startswith("whsec_")
    (listed,) = ok(client.get("/api/ai/webhooks"))
    assert listed == {
        "id": made["id"],
        "url": HOOK,
        "last4": made["secret"][-4:],
        "made_at": listed["made_at"],
        "made_by": "Admin 1",
        "revoked_at": None,
        "last_attempt_at": None,
        "last_error": None,
    }
    # Kept sealed: the secret is in no row as itself.
    sealed = conn.execute(
        "SELECT secret_sealed FROM trust_event_subscriptions WHERE id = %s", (made["id"],)
    ).fetchone()[0]
    assert made["secret"].encode() not in bytes(sealed)

    ok(client.post(f"/api/ai/webhooks/{made['id']}/revoke", json={}), 204)
    refused(
        client.post(f"/api/ai/webhooks/{made['id']}/revoke", json={}),
        409,
        "this webhook was taken back already",
    )
    assert ok(client.get("/api/ai/webhooks"))[0]["revoked_at"] is not None
    refused(
        client.post(f"/api/ai/webhooks/{made['id']}/revoke", json={}),
        409,
        "taken back already",
    )


@pytest.mark.parametrize(
    ("url", "says"),
    [
        ("http://hooks.example.com/x", "starts with https://"),
        ("https://127.0.0.1/x", "never the server's own network"),
        ("https://169.254.169.254/latest/meta-data", "never the server's own network"),
        ("https://user:pw@example.com/x", "no user name or password"),
        ("", None),
    ],
)
def test_a_webhook_goes_only_to_a_public_https_address(client, admin, data_key, url, says):
    refused(client.post("/api/ai/webhooks", json={"url": url}), 422, says)


def test_a_server_without_a_data_key_keeps_no_webhook(client, admin, monkeypatch):
    monkeypatch.delenv("PMS_DATA_KEY", raising=False)
    refused(
        client.post("/api/ai/webhooks", json={"url": HOOK}),
        409,
        "it has no PMS_DATA_KEY, so it can't keep a webhook's secret",
    )


def test_only_an_admin_keeps_webhooks(client, other_client, conn, app_conn, pmc, admin, data_key):
    made = ok(client.post("/api/ai/webhooks", json={"url": HOOK}), 201)
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=["approve"])
    refused(other_client.get("/api/ai/webhooks"), 403, ADMINS)
    refused(other_client.post("/api/ai/webhooks", json={"url": HOOK}), 403, ADMINS)
    refused(other_client.post(f"/api/ai/webhooks/{made['id']}/revoke", json={}), 403, ADMINS)


def test_another_companys_webhook_is_not_found(
    client, other_client, conn, app_conn, pmc, admin, data_key
):
    made = ok(client.post("/api/ai/webhooks", json={"url": HOOK}), 201)
    elsewhere = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, elsewhere, name="Other Admin 1")
    refused(other_client.post(f"/api/ai/webhooks/{made['id']}/revoke", json={}), 404)
    assert ok(other_client.get("/api/ai/webhooks")) == []
