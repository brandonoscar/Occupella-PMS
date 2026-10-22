"""Vendors (src/pms/api/vendors.py): each vendor's W-9 and insurance as of a day, as
trust_report_vendor_insurance has it, and recording a policy; the list a page at a time, a vendor
added with their profile (trade, 1099, how they are paid; migration 0073) and contact, and the
profile changed."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_document, enter_bill, make_pmc
from people import join_as_owner, join_as_staff


def today():
    """The day the API takes as today (UTC), read when a test needs it: a day fixed when the
    module loads is yesterday's for a test run that crosses midnight."""
    return datetime.now(UTC).date()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def policy(**change):
    return {
        "coverage": "general liability",
        "carrier": "Carrier A",
        "policy_number": "GL-1",
        "starts_on": str(today() - timedelta(days=300)),
        "expires_on": str(today() + timedelta(days=65)),
        "limit_amount": "1000000",
    } | change


def test_staff_record_a_policy_and_see_how_it_stands(client, conn, pmc, staff):
    vendor = pmc.vendor_id
    ok(client.post(f"/api/vendors/{vendor}/insurance", json=policy()), 201)
    ok(
        client.post(
            f"/api/vendors/{vendor}/insurance",
            json=policy(
                coverage="commercial auto",
                policy_number="AU-1",
                carrier=" Carrier C ",
                expires_on=str(today() + timedelta(days=10)),
                limit_amount=None,
            ),
        ),
        201,
    )
    add_document(conn, pmc.pmc_id, "w9", vendor)

    found = ok(client.get(f"/api/vendors/{vendor}"))

    assert (found["name"], found["w9_on_file"]) == ("Vendor 1", True)
    assert [
        (p["coverage"], p["carrier"], p["limit_amount"], p["standing"]) for p in found["insurance"]
    ] == [
        ("commercial auto", "Carrier C", None, "expires in 10 days"),
        ("general liability", "Carrier A", "1000000.00", "current"),
    ]
    # The list says how it stands by the coverage that runs out first; as of a later day, that
    # one has run out.
    assert found["insurance_standing"] == "expires in 10 days"
    assert ok(client.get("/api/vendors"))["items"][0]["insurance"] == "expires in 10 days"
    later = ok(client.get("/api/vendors", params={"on": str(today() + timedelta(days=11))}))
    assert later["items"][0]["insurance"] == "expired"
    # The audit log names who recorded it.
    assert conn.execute(
        "SELECT count(*) FROM trust_vendor_insurance WHERE recorded_by = %s", (staff.person_id,)
    ).fetchone() == (2,)


def test_a_vendor_with_no_policy_says_so(client, conn, pmc, staff):
    (found,) = ok(client.get("/api/vendors"))["items"]
    assert (found["w9_on_file"], found["insurance"]) == (False, "no insurance on file")
    page = ok(client.get(f"/api/vendors/{pmc.vendor_id}"))
    assert (page["insurance"], page["insurance_standing"]) == ([], "no insurance on file")
    # A W-9 kept and then removed is not on file.
    w9 = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id)
    assert ok(client.get("/api/vendors"))["items"][0]["w9_on_file"] is True
    conn.execute(
        "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
        (w9, pmc.pmc_id),
    )
    assert ok(client.get("/api/vendors"))["items"][0]["w9_on_file"] is False


def test_expiring_insurance_waits_on_today(client, pmc, staff):
    """Today lists every coverage that isn't current: expiring and expired, not current."""
    for coverage, expires in (
        ("general liability", today()),
        ("commercial auto", today() - timedelta(days=1)),
        ("workers compensation", today() + timedelta(days=31)),
    ):
        ok(
            client.post(
                f"/api/vendors/{pmc.vendor_id}/insurance",
                json=policy(coverage=coverage, expires_on=str(expires)),
            ),
            201,
        )
    day = ok(client.get("/api/today"))
    assert [(x["vendor"], x["coverage"], x["standing"]) for x in day["vendor_insurance"]] == [
        ("Vendor 1", "commercial auto", "expired"),
        ("Vendor 1", "general liability", "expires today"),
    ]


def test_only_the_pmcs_staff_record_or_see_insurance(
    client, other_client, conn, app_conn, pmc, staff
):
    path = f"/api/vendors/{pmc.vendor_id}/insurance"
    refused(other_client.get("/api/vendors"), 401)
    refused(other_client.post(path, json=policy()), 401)
    join_as_owner(other_client, app_conn, pmc)
    refused(other_client.post(path, json=policy()), 403, "only the PMC's staff")

    other = make_pmc(conn)
    other_client.post("/api/sign-out", json={})
    join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(other_client.post(path, json=policy()), 404, "no such vendor")
    refused(
        other_client.post(f"/api/vendors/{pmc.vendor_id}/profile", json={"trade": "x"}),
        404,
        "no such vendor",
    )
    (theirs,) = ok(other_client.get("/api/vendors"))["items"]
    assert (theirs["name"], theirs["insurance"]) == ("Vendor 1", "no insurance on file")
    assert theirs["id"] == str(other.vendor_id)


@pytest.mark.parametrize(
    "change",
    [
        {"coverage": "flood"},
        {"carrier": " "},
        {"policy_number": "x" * 101},
        {"expires_on": str(today() - timedelta(days=301))},
        {"limit_amount": "0"},
        {"limit_amount": "1.001"},
        {"starts_on": "soon"},
    ],
)
def test_a_policy_names_a_coverage_carrier_number_and_days_that_run_forward(
    client, conn, pmc, staff, change
):
    refused(client.post(f"/api/vendors/{pmc.vendor_id}/insurance", json=policy(**change)), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_vendor_insurance WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_staff_add_a_vendor_with_their_profile_and_contact(client, conn, pmc, staff):
    made = ok(
        client.post(
            "/api/vendors",
            json={
                "name": " Vendor 3 ",
                "trade": " Electrical ",
                "gets_1099": True,
                "payment_method": "EFT",
                "contact": {"email": "vendor3@example.com", "phone": "555-555-0103"},
            },
        ),
        201,
    )

    page = ok(client.get(f"/api/vendors/{made['id']}"))
    assert (page["name"], page["profile"]) == (
        "Vendor 3",
        {"trade": "Electrical", "gets_1099": True, "payment_method": "EFT"},
    )
    assert (page["contact"]["email"], page["contact"]["phone"]) == (
        "vendor3@example.com",
        "555-555-0103",
    )
    listed = ok(client.get("/api/vendors"))["items"]
    assert [(v["name"], v["trade"], v["email"]) for v in listed] == [
        ("Vendor 1", None, None),
        ("Vendor 3", "Electrical", "vendor3@example.com"),
    ]
    # The profile names who recorded it; a vendor added with nothing said has none, and no
    # contact row either.
    assert conn.execute(
        "SELECT recorded_by FROM trust_vendor_profiles WHERE vendor_id = %s", (made["id"],)
    ).fetchall() == [(staff.person_id,)]
    bare = ok(client.post("/api/vendors", json={"name": "Vendor 4"}), 201)["id"]
    assert ok(client.get(f"/api/vendors/{bare}"))["profile"] is None
    for table in ("trust_vendor_profiles", "trust_contacts"):
        assert conn.execute(
            f"SELECT count(*) FROM {table} WHERE vendor_id = %s", (bare,)
        ).fetchone() == (0,)


def test_a_vendors_profile_changes_row_by_row(client, conn, pmc, staff):
    path = f"/api/vendors/{pmc.vendor_id}/profile"
    assert ok(client.post(path, json={})) == {"changed": False}
    assert ok(client.post(path, json={"trade": "Plumbing", "gets_1099": True})) == {"changed": True}
    assert ok(client.post(path, json={"trade": " Plumbing ", "gets_1099": True})) == {
        "changed": False
    }
    assert ok(client.post(path, json={"trade": None, "payment_method": "check"})) == {
        "changed": True
    }

    assert ok(client.get(f"/api/vendors/{pmc.vendor_id}"))["profile"] == {
        "trade": None,
        "gets_1099": False,
        "payment_method": "check",
    }
    assert conn.execute(
        "SELECT trade, gets_1099, payment_method FROM trust_vendor_profiles"
        " WHERE vendor_id = %s ORDER BY created_at",
        (pmc.vendor_id,),
    ).fetchall() == [("Plumbing", True, None), (None, False, "check")]


def test_the_list_comes_a_page_at_a_time_by_name(client, conn, pmc, staff):
    for name in ("Vendor 3", "Vendor 2"):
        ok(client.post("/api/vendors", json={"name": name}), 201)

    page = ok(client.get("/api/vendors", params={"per_page": 2, "page": 2}))

    assert (page["total"], page["page"], page["per_page"]) == (3, 2, 2)
    assert [v["name"] for v in page["items"]] == ["Vendor 3"]


@pytest.mark.parametrize("caller", ["nobody", "owner"])
def test_only_staff_add_a_vendor_or_change_one(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    refused(client.post("/api/vendors", json={"name": "Vendor 9"}), status)
    refused(client.post(f"/api/vendors/{pmc.vendor_id}/profile", json={}), status)
    refused(client.get(f"/api/vendors/{pmc.vendor_id}"), status)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/vendors", {"name": " "}),
        ("/api/vendors", {"name": "x" * 201}),
        ("/api/vendors", {"name": "Vendor 9", "payment_method": "cash"}),
        ("/api/vendors", {"name": "Vendor 9", "contact": {"email": "not an email"}}),
        ("PROFILE", {"trade": "x" * 101}),
        ("PROFILE", {"trade": " "}),
        ("PROFILE", {"gets_1099": "maybe"}),
        ("PROFILE", {"payment_method": "wire"}),
    ],
)
def test_bad_vendor_input_is_refused(client, conn, pmc, staff, path, body):
    path = f"/api/vendors/{pmc.vendor_id}/profile" if path == "PROFILE" else path
    refused(client.post(path, json=body), 422)
    refused(client.get("/api/vendors", params={"per_page": 0}), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_vendors WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)


# --- archiving (migration 0110; the founder's review q131) -------------------------------------


def archive(client, vendor, archived=True):
    return client.post(f"/api/vendors/{vendor}/archive", json={"archived": archived})


def test_staff_archive_a_vendor_who_leaves_the_list_pickers_and_search_and_bring_them_back(
    client, conn, pmc, staff
):
    vendor = pmc.vendor_id
    other = ok(client.post("/api/vendors", json={"name": "Vendor 2"}), 201)["id"]

    ok(archive(client, vendor), 204)

    assert [v["name"] for v in ok(client.get("/api/vendors"))["items"]] == ["Vendor 2"]
    archived = ok(client.get("/api/vendors", params={"archived": True}))["items"]
    assert [v["id"] for v in archived] == [str(vendor)]
    assert ok(client.get(f"/api/vendors/{vendor}"))["archived"] is True
    assert ok(client.get(f"/api/vendors/{other}"))["archived"] is False
    # Neither a work order nor a new bill offers them; search leaves them out.
    assert [v["name"] for v in ok(client.get("/api/maintenance/vendors"))] == ["Vendor 2"]
    assert [v["name"] for v in ok(client.get("/api/setup"))["vendors"]] == ["Vendor 2"]
    assert [f["label"] for f in ok(client.get("/api/search", params={"q": "Vendor"}))] == [
        "Vendor 2"
    ]
    refused(archive(client, vendor), 409, "it is archived already")

    ok(archive(client, vendor, archived=False), 204)
    assert [v["name"] for v in ok(client.get("/api/vendors"))["items"]] == ["Vendor 1", "Vendor 2"]
    assert conn.execute(
        "SELECT count(*) FROM trust_archivals WHERE vendor_id = %s AND recorded_by = %s",
        (vendor, staff.person_id),
    ).fetchone() == (2,)


def test_a_vendor_with_a_bill_not_paid_stays_and_says_why(client, conn, pmc, staff):
    enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "INV-1", today(), today(), "50"
    )

    refused(
        archive(client, pmc.vendor_id),
        409,
        "Vendor 1 has a bill not paid; pay or decline it before archiving",
    )
    assert ok(client.get(f"/api/vendors/{pmc.vendor_id}"))["archived"] is False


def test_archiving_a_vendor_takes_staff_of_the_company_whose_roles_allow_setup(
    client, other_client, conn, app_conn, pmc
):
    refused(archive(client, pmc.vendor_id), 401)
    join_as_owner(other_client, app_conn, pmc)
    refused(archive(other_client, pmc.vendor_id), 403)

    join_as_staff(client, conn, app_conn, pmc)
    refused(archive(client, make_pmc(conn).vendor_id), 404, "no such vendor")
    refused(client.post(f"/api/vendors/{pmc.vendor_id}/archive", json={}), 422)
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Bills", "areas": ["bills"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused(archive(other_client, pmc.vendor_id), 403, "your roles don't let you change")
