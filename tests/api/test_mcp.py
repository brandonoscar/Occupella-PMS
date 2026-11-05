"""The MCP server (ADR 0011, src/pms/api/mcp.py): an MCP client with an API key initializes, lists
the tools and calls them at /mcp; read tools find the company's records with their ids and read
its reports, ledgers and events; propose tools write drafts that wait for staff. Everything is
the key's company's, read as the AI's role. Every name here is synthetic."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_staff

from pms.api import create_app

NOT_VALID = "this key was taken back or doesn't exist"
TODAY = datetime.now(UTC).date()


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


@pytest.fixture
def software(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def key(client, admin):
    made = client.post("/api/ai/keys", json={"name": "Claude"})
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['secret']}"}


@pytest.fixture
def home(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 7")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Robin Sample")
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1100.00", [tenant])
    return {"unit": unit, "tenant": tenant, "lease": lease}


def rpc(software, key, method, params=None, message_id=1, status=200):
    body = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        body["params"] = params
    answer = software.post("/mcp", json=body, headers=key)
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def tool(software, key, tool_name, /, **arguments):
    got = rpc(software, key, "tools/call", {"name": tool_name, "arguments": arguments})
    assert "result" in got, got
    return got["result"]


def test_a_client_initializes_and_lists_the_tools(software, key, conn, pmc):
    started = rpc(
        software,
        key,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Test"}},
    )["result"]
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert started["protocolVersion"] == "2025-06-18"
    assert started["capabilities"] == {"tools": {"listChanged": False}}
    assert started["serverInfo"]["name"] == "occupella-pms"
    assert started["serverInfo"]["company"] == company
    assert "Nothing here moves money" in started["instructions"]
    # A version it doesn't speak: it offers its own.
    older = rpc(software, key, "initialize", {"protocolVersion": "2024-01-01"})["result"]
    assert older["protocolVersion"] == "2025-06-18"
    # A notification is taken, with no answer.
    taken = software.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=key
    )
    assert (taken.status_code, taken.content) == (202, b"")
    assert rpc(software, key, "ping")["result"] == {}

    tools = rpc(software, key, "tools/list")["result"]["tools"]
    names = {t["name"]: t for t in tools}
    assert set(names) == {
        "find",
        "list_reports",
        "run_report",
        "rent_roll",
        "delinquency",
        "work_orders",
        "lease_ledger",
        "recent_events",
        "my_drafts",
        "propose_email",
        "propose_notice",
        "propose_work_order",
        "propose_vendor",
        "propose_visit",
        "propose_note",
    }
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert all(t["annotations"]["destructiveHint"] is False for t in tools)
    assert {n for n, t in names.items() if not t["annotations"]["readOnlyHint"]} == {
        n for n in names if n.startswith("propose_")
    }


def test_find_gives_each_record_its_id_of_this_company_only(software, key, conn, pmc, home):
    elsewhere = make_pmc(conn)
    add_tenant(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Robin Elsewhere")
    found = tool(software, key, "find", kind="tenant", name="robin")
    assert found["isError"] is False
    assert found["structuredContent"]["found"] == [
        {"id": str(home["tenant"]), "name": "Robin Sample"}
    ]
    lease = tool(software, key, "find", kind="lease", name="Robin")["structuredContent"]["found"]
    assert lease == [
        {
            "id": str(home["lease"]),
            "name": lease[0]["name"],
            "unit_id": str(home["unit"]),
        }
    ]
    assert lease[0]["name"].endswith(", Unit 7 (Robin Sample)")
    # A pattern's own characters are only themselves.
    assert tool(software, key, "find", kind="tenant", name="%")["structuredContent"]["found"] == []
    refused = tool(software, key, "find", kind="bank account")
    assert refused["isError"] is True and "kind is one of" in refused["content"][0]["text"]


def test_reports_ledgers_and_events_read_the_keys_company(software, key, conn, pmc, home):
    roll = tool(software, key, "rent_roll", as_of=str(TODAY))["structuredContent"]
    assert "property" in roll["columns"] and roll["rows"]
    assert tool(software, key, "delinquency")["isError"] is False
    assert tool(software, key, "work_orders")["isError"] is False
    ledger = tool(software, key, "lease_ledger", lease_id=str(home["lease"]))
    assert ledger["isError"] is False
    reports = tool(software, key, "list_reports")["structuredContent"]["reports"]
    assert {"name": "rent_roll", "args": [{"name": "as_of", "type": "date"}]} in reports
    ran = tool(software, key, "run_report", name="rent_roll", args=[str(TODAY)])
    assert ran["structuredContent"] == roll
    seen = tool(software, key, "recent_events")["structuredContent"]
    assert ("lease.opened", str(home["lease"])) in {
        (e["kind"], e["subject"].get("lease_id")) for e in seen["events"]
    }

    # Another company's lease, a report that isn't one, a bad day: each refused in words.
    elsewhere = make_pmc(conn)
    unit = add_unit(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Unit 1")
    theirs = open_lease(
        conn,
        elsewhere.pmc_id,
        unit,
        date(2026, 1, 1),
        None,
        "900.00",
        [add_tenant(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Other Tenant 1")],
    )
    for name, arguments, says in [
        ("lease_ledger", {"lease_id": str(theirs)}, "no such lease in this company"),
        ("lease_ledger", {"lease_id": "nope"}, "lease_id is an id from find"),
        ("run_report", {"name": "drop_everything"}, "no report named"),
        ("rent_roll", {"as_of": "11/05/2026"}, "as_of is a day as YYYY-MM-DD"),
        ("recent_events", {"after": 0}, "after is an event's id"),
    ]:
        refused = tool(software, key, name, **arguments)
        assert refused["isError"] is True, (name, refused)
        assert says in refused["content"][0]["text"], (name, refused)


def test_a_proposal_is_a_draft_that_waits_for_staff(client, software, key, conn, pmc, home):
    made = tool(
        software,
        key,
        "propose_note",
        about="tenant",
        about_id=str(home["tenant"]),
        body="Asked about parking.",
        why="From their last message.",
    )
    assert made["isError"] is False
    draft = made["structuredContent"]["draft_id"]
    assert made["structuredContent"]["status"] == "waiting"
    shown = client.get(f"/api/drafts/{draft}").json()
    assert (shown["what"], shown["why"], shown["key"]) == (
        "Note about tenant Robin Sample",
        "From their last message.",
        "Claude",
    )
    mine = tool(software, key, "my_drafts")["structuredContent"]["items"]
    assert [(d["id"], d["status"]) for d in mine] == [(draft, "waiting")]
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_notes WHERE tenant_id = %s", (home["tenant"],)
        ).fetchone()[0]
        == 0
    )  # nothing is done until someone approves it


def test_a_proposal_carries_what_its_kind_takes_and_nothing_else(software, key, conn, pmc, home):
    for name, arguments, says in [
        (
            "propose_email",
            {
                "to": [{"about": "tenant", "id": str(home["tenant"])}],
                "subject": "Hi",
                "body": "Hi",
                "address": "robin@example.com",
            },
            "Extra inputs are not permitted",
        ),
        ("propose_note", {"about": "tenant", "about_id": str(home["tenant"])}, "body"),
        (
            "propose_work_order",
            {"property_id": str(make_pmc(conn).owners[0].property_id), "summary": "Leak"},
            "no such property in this company",
        ),
    ]:
        refused = tool(software, key, name, **arguments)
        assert refused["isError"] is True, (name, refused)
        assert says in refused["content"][0]["text"], (name, refused)
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_drafts WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


def test_a_message_it_doesnt_know_is_answered_as_json_rpc(software, key):
    assert rpc(software, key, "resources/list")["error"]["code"] == -32601
    unknown = rpc(software, key, "tools/call", {"name": "pay_bill", "arguments": {}})
    assert unknown["error"] == {"code": -32602, "message": "no tool named pay_bill"}
    bad = rpc(software, key, "tools/call", {"name": "find", "arguments": [1]})
    assert bad["error"]["code"] == -32602
    for body in ([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], {"id": 1, "method": "ping"}):
        refused = software.post("/mcp", json=body, headers=key)
        assert refused.status_code == 400
        assert refused.json()["error"]["code"] == -32600
    assert software.get("/mcp", headers=key).status_code == 405


def test_only_a_standing_key_opens_the_server(client, software, key):
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert software.post("/mcp", json=ping).status_code == 401
    assert client.post("/mcp", json=ping).status_code == 401  # a staff session is no key
    (listed,) = client.get("/api/ai/keys").json()
    assert client.post(f"/api/ai/keys/{listed['id']}/revoke", json={}).status_code == 204
    refused = software.post("/mcp", json=ping, headers=key)
    assert (refused.status_code, refused.json()["detail"]) == (401, NOT_VALID)
