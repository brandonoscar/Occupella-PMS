"""The MCP server (ADR 0011, src/pms/api/mcp.py): an MCP client with an API key initializes, lists
the tools and calls them at /mcp; read tools find the company's records with their ids and read
its reports, ledgers and events; propose tools write drafts that wait for staff. Everything is
the key's company's, read as the AI's role. Every name here is synthetic."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import add_tenant, add_unit, events_seen, make_pmc, open_lease
from people import join_as_staff

from pms import cpi, rent_cap
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
        "rent_increase_limit",
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
    events_seen(conn, pmc.pmc_id)
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


def test_a_refusal_says_only_the_pmss_own_words(software, key):
    # A report given an argument it can't read: never the database's own message.
    refused = tool(software, key, "run_report", name="rent_roll", args=["not a day"])
    assert refused["isError"] is True
    assert refused["content"][0]["text"] == (
        "an argument of the rent_roll report isn't what it takes: list_reports says its types"
    )
    wrong = tool(software, key, "run_report", name="rent_roll", args=[])
    assert wrong["content"][0]["text"] == (
        "the rent_roll report takes other arguments: list_reports names them"
    )


def test_a_tool_that_breaks_tells_the_client_nothing_of_why(settings, key, monkeypatch):
    from pms.api import mcp

    def breaks(conn, key, args):
        raise KeyError("an internal detail")

    monkeypatch.setitem(mcp.BY_NAME, "find", mcp.Tool("find", "", {}, breaks))
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        answer = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "find", "arguments": {}},
            },
            headers=key,
        )
    assert answer.status_code == 500
    assert "internal detail" not in answer.text


def test_a_tool_asked_wrongly_says_how_to_ask(software, key, monkeypatch):
    refused = tool(software, key, "run_report", name="rent_roll", args="2026-11-05")
    assert refused["isError"] is True
    assert refused["content"][0]["text"] == (
        "args is a list of text, one for each of the report's arguments"
    )
    # An event id that is none of this company's.
    refused = tool(software, key, "recent_events", after=2_000_000_000)
    assert refused["content"][0]["text"] == "after names no event of this company"
    # Params that are no object.
    assert rpc(software, key, "ping", params=[1])["error"] == {
        "code": -32602,
        "message": "params are an object",
    }
    # A report the key's role may not run says so, never the database's words.
    import psycopg

    from pms import exports

    def refuse(conn, name, args):
        raise psycopg.errors.InsufficientPrivilege("permission denied for table trust_secret")

    monkeypatch.setattr(exports, "run_report", refuse)
    refused = tool(software, key, "run_report", name="rent_roll", args=["2026-11-05"])
    assert refused["content"][0]["text"] == "this key may not run the rent_roll report"


# --- the most a rent may rise (src/pms/rent_cap.py, renewals.limit_for) -------------------------


def at_address(conn, pmc, state, zip_code):
    conn.execute(
        "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
        " VALUES (%s, %s, '100 Synthetic Way', 'Demo City', %s, %s)",
        (pmc.pmc_id, pmc.owners[0].property_id, state, zip_code),
    )


def test_rent_increase_limit_says_what_staff_are_warned_of(client, software, key, conn, pmc, home):
    at_address(conn, pmc, "CA", "90000")
    starts = date(2027, 3, 1)
    # A change of rent in the 12 months before: the cap counts from the lowest, 1,100.00, and
    # the AI's role reads it without the app's own function (renewals.AI_LOWEST).
    conn.execute(
        "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
        " notice_given_on, served_by) VALUES (%s, %s, '2026-09-01', 1150, '2026-07-01', 'mail')",
        (pmc.pmc_id, home["lease"]),
    )
    first, last = rent_cap.california_months(rent_cap.LOS_ANGELES, starts)
    cpi.keep(
        conn,
        pmc.pmc_id,
        "fake",
        {rent_cap.LOS_ANGELES.series: {first: Decimal("300"), last: Decimal("309")}},
    )

    found = tool(
        software,
        key,
        "rent_increase_limit",
        lease_id=str(home["lease"]),
        monthly_rent="1200.00",
        starts_on=str(starts),
    )

    assert found["isError"] is False
    limit = found["structuredContent"]
    assert (limit["state"], limit["zip"], limit["rent_now"], limit["lowest_rent"]) == (
        "CA",
        "90000",
        "1150.00",
        "1100.00",
    )
    assert (limit["new_rent"], limit["starts_on"], limit["unit"]) == (
        "1200.00",
        "2027-03-01",
        "Unit 7",
    )
    assert (limit["cap"]["max_percent"], limit["cap"]["max_rent"], limit["cap"]["over"]) == (
        "8.0",
        "1188.00",
        True,
    )
    assert limit["words"].startswith(limit["cap"]["words"])
    # The same words the staff's warning gives for that rent change (the key's admin, signed in).
    staff = client.get(
        f"/api/leasing/renewals/{home['lease']}/rent-change-notice",
        params={
            "effective_on": "2027-03-01",
            "monthly_rent": "1200.00",
            "notice_given_on": "2026-11-01",
        },
    ).json()
    assert staff["cap"] == limit["cap"]


def test_rent_increase_limit_without_a_new_rent_says_the_most_from_next_month(
    software, key, conn, pmc, home
):
    at_address(conn, pmc, "OR", "97000")

    limit = tool(software, key, "rent_increase_limit", lease_id=str(home["lease"]))[
        "structuredContent"
    ]

    next_month = (TODAY.replace(day=1) + timedelta(days=32)).replace(day=1)
    assert (limit["starts_on"], limit["new_rent"], limit["notice"]) == (str(next_month), None, None)
    assert limit["cap"]["checked"] is False
    assert "The PMS couldn't work out this year's figure" in limit["words"]
    assert "This new rent" not in limit["words"]


def test_rent_increase_limit_says_when_it_cant_tell_or_the_state_has_no_cap(
    software, key, conn, pmc, home
):
    asked = {"lease_id": str(home["lease"]), "starts_on": "2027-03-01"}

    nowhere = tool(software, key, "rent_increase_limit", **asked)["structuredContent"]
    assert (nowhere["state"], nowhere["cap"]) == (None, None)
    assert "this property has no address on file" in nowhere["words"]
    at_address(conn, pmc, "TX", "75000")
    texas = tool(software, key, "rent_increase_limit", **asked, monthly_rent="1200.00")
    assert texas["structuredContent"]["cap"] is None
    assert (
        "The PMS records no cap on a year's rent increase in TX"
        in (texas["structuredContent"]["words"])
    )
    lower = tool(software, key, "rent_increase_limit", **asked, monthly_rent="1000.00")
    assert lower["structuredContent"]["words"].startswith(
        "$1,000.00 is no increase over the rent now, $1,100.00."
    )


def test_rent_increase_limit_says_when_the_new_rent_starts_too_soon_for_its_notice(
    software, key, conn, pmc, home
):
    at_address(conn, pmc, "CA", "90000")
    starts = TODAY + timedelta(days=10)

    limit = tool(
        software,
        key,
        "rent_increase_limit",
        lease_id=str(home["lease"]),
        monthly_rent="1200.00",
        starts_on=str(starts),
    )["structuredContent"]

    # 9.1% over 1,100.00: California asks 30 days' written notice (Civil Code 827), counted from
    # the key's today, as staff's warning counts it from the day notice goes out.
    notice = limit["notice"]
    assert (notice["state"], notice["days"], notice["earliest"]) == (
        "CA",
        30,
        str(TODAY + timedelta(days=30)),
    )
    assert notice["warning"].startswith("California asks 30 days' written notice")
    assert "This new rent starts in 10 days." in notice["warning"]
    assert limit["words"] == f"{limit['cap']['words']} {notice['warning']}"


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"lease_id": "nope"}, "lease_id is an id from find"),
        ({"monthly_rent": "abc"}, "monthly_rent is an amount a month, as 1250.00"),
        ({"monthly_rent": "0"}, "monthly_rent is an amount a month, as 1250.00"),
        ({"monthly_rent": "10.001"}, "monthly_rent is an amount a month, as 1250.00"),
        ({"starts_on": "03/01/2027"}, "starts_on is a day as YYYY-MM-DD"),
    ],
)
def test_rent_increase_limit_asked_wrongly_says_how_to_ask(software, key, home, arguments, says):
    asked = {"lease_id": str(home["lease"]), **arguments}

    refused = tool(software, key, "rent_increase_limit", **asked)

    assert refused["isError"] is True
    assert refused["content"][0]["text"] == says


def test_rent_increase_limit_reads_only_the_keys_company(software, key, conn):
    other = make_pmc(conn)
    prop = other.owners[0].property_id
    unit = add_unit(conn, other.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, other.pmc_id, prop, "Tenant A")
    theirs = open_lease(conn, other.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])

    refused = tool(software, key, "rent_increase_limit", lease_id=str(theirs))

    assert (refused["isError"], refused["content"][0]["text"]) == (
        True,
        "no such lease in this company",
    )
