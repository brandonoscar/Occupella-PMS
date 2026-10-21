"""Notice templates (migration 0119, src/pms/api/notice_templates.py; the founder's review q095):
the company's own notices with blanks the PMS fills from a lease, kept as versions, made into a
notice for a lease, kept as filled in, and downloaded as a PDF. The words are the company's."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_tenant, add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
LATE = (
    "Dear {{tenant_names}},\n\nOn {{notice_date}} the lease of {{property}} {{unit}} owes"
    " {{balance_due}}; its rent is {{ monthly_rent }} a month.\n\n{{company_name}}"
)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, says=None):
    assert answer.status_code == status, answer.text
    if says is not None:
        assert answer.json()["detail"] == says


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


@pytest.fixture
def lease(conn, pmc):
    prop = pmc.owners[0].property_id
    people = [add_tenant(conn, pmc.pmc_id, prop, n) for n in ("Reese Sample", "Avery Sample")]
    unit = add_unit(conn, pmc.pmc_id, prop, "1A")
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1395.00", people)
    charge(conn, pmc.pmc_id, lease, TODAY.replace(day=1), Decimal("1395.00"))
    return lease


def add(client, name="Late rent", body=LATE):
    return ok(post(client, "/api/notices/templates", name=name, body=body), 201)


def rows(conn, pmc, table):
    return conn.execute(
        f"SELECT count(*) FROM {table} WHERE pmc_id = %s",  # noqa: S608
        (pmc.pmc_id,),
    ).fetchone()[0]


def test_staff_keep_templates_with_the_blanks_the_pms_fills(client, conn, staff, pmc):
    late = add(client)
    entry = add(client, "Entry", "We will enter {{unit}} on {{notice_date}}.")
    again = ok(
        post(client, f"/api/notices/templates/{late['id']}", name="Late rent", body=LATE), 201
    )

    listed = ok(client.get("/api/notices/templates"))
    assert [(t["name"], t["saved_by"]) for t in listed["templates"]] == [
        ("Entry", "Staff 1"),
        ("Late rent", "Staff 1"),
    ]
    assert again == late  # saving what it says already writes nothing
    assert rows(conn, pmc, "trust_notice_template_versions") == 2
    assert {f["name"] for f in listed["fields"]} >= {"tenant_names", "balance_due", "notice_date"}

    changed = ok(
        post(
            client, f"/api/notices/templates/{entry['id']}", name="Entry notice", body="Hi {{unit}}"
        ),
        201,
    )
    assert (changed["id"], changed["name"]) == (entry["id"], "Entry notice")
    assert changed["version_id"] != entry["version_id"]


def test_a_blank_the_pms_does_not_fill_is_refused_in_words(client, conn, staff, pmc):
    refused(
        post(client, "/api/notices/templates", name="Late", body="Pay {{amount}} by {{due}}."),
        422,
        "{{amount}}, {{due}} are not blanks the PMS fills; the list beside the text shows the"
        " ones it does",
    )
    late = add(client)
    refused(
        post(client, f"/api/notices/templates/{late['id']}", name="Late", body="{{nope}}"),
        422,
        "{{nope}} is not a blank the PMS fills; the list beside the text shows the ones it does",
    )
    assert rows(conn, pmc, "trust_notice_template_versions") == 1


def test_a_name_is_the_companys_once_whatever_its_case_until_removed(client, conn, staff, pmc):
    late = add(client)
    refused(
        post(client, "/api/notices/templates", name="LATE RENT", body="x"),
        409,
        "a notice template is named LATE RENT already",
    )
    entry = add(client, "Entry", "x")
    refused(
        post(client, f"/api/notices/templates/{entry['id']}", name="late rent", body="x"),
        409,
        "a notice template is named late rent already",
    )
    ok(post(client, f"/api/notices/templates/{late['id']}/remove"), 204)

    add(client, "Late rent", "A new one")  # the name is free once the other is removed
    refused(post(client, f"/api/notices/templates/{late['id']}/remove"), 404)
    refused(
        post(client, f"/api/notices/templates/{late['id']}", name="Late rent", body="x"),
        404,
        "no such notice template",
    )
    assert [t["name"] for t in ok(client.get("/api/notices/templates"))["templates"]] == [
        "Entry",
        "Late rent",
    ]


def test_a_notice_is_the_template_filled_from_the_lease_kept_and_downloaded(
    client, conn, staff, pmc, lease
):
    late = add(client)
    asked = {"lease_id": lease, "template_id": late["id"], "dated_on": TODAY}
    on = f"{TODAY:%m/%d/%Y}"
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]

    seen = ok(post(client, "/api/notices/preview", **asked))
    assert seen == {
        "title": "Late rent",
        "letterhead": company,
        "body": f"Dear Avery Sample and Reese Sample,\n\nOn {on} the lease of Property 1 1A owes"
        f" $1,395.00; its rent is $1,395.00 a month.\n\n{company}",
    }
    assert rows(conn, pmc, "trust_notices") == 0  # a preview writes nothing

    made = ok(post(client, "/api/notices", **asked), 201)
    assert (made["title"], made["dated_on"], made["made_by"]) == (
        "Late rent",
        str(TODAY),
        "Staff 1",
    )
    # The template changes later; the notice keeps the words it was made with.
    ok(
        post(client, f"/api/notices/templates/{late['id']}", name="Late rent", body="New words"),
        201,
    )
    assert conn.execute(
        "SELECT body FROM trust_notices WHERE id = %s", (made["id"],)
    ).fetchone() == (seen["body"],)

    listed = ok(client.get("/api/notices", params={"lease_id": str(lease)}))
    assert listed == [made]
    pdf = client.get(f"/api/notices/{made['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert f'filename="notice-{TODAY}.pdf"' in pdf.headers["content-disposition"]


def test_a_notice_reads_the_companys_address_and_who_made_it(client, conn, staff, pmc, lease):
    ok(
        post(
            client,
            "/api/company",
            name="Synthetic Homes LLC",
            address={
                "address_line_1": "100 Example Way",
                "address_line_2": None,
                "city": "Springfield",
                "state": "IL",
                "postal_code": "62701",
            },
        )
    )
    tpl = add(
        client, "Signed", "{{company_address}} / {{staff_name}} / {{lease_start}} to {{lease_end}}"
    )
    seen = ok(
        post(client, "/api/notices/preview", lease_id=lease, template_id=tpl["id"], dated_on=TODAY)
    )
    assert seen["letterhead"] == "Synthetic Homes LLC\n100 Example Way, Springfield, IL 62701"
    assert seen["body"] == (
        "100 Example Way, Springfield, IL 62701 / Staff 1 / 01/01/2026 to month to month"
    )


def test_another_companys_lease_template_or_notice_reads_as_not_found(
    client, conn, staff, pmc, lease
):
    theirs = make_pmc(conn)
    prop = theirs.owners[0].property_id
    their_lease = open_lease(
        conn,
        theirs.pmc_id,
        add_unit(conn, theirs.pmc_id, prop, "9"),
        date(2026, 1, 1),
        None,
        "900.00",
        [add_tenant(conn, theirs.pmc_id, prop, "Sky Sample")],
    )
    late = add(client)
    refused(
        post(client, "/api/notices", lease_id=their_lease, template_id=late["id"], dated_on=TODAY),
        404,
        "no such lease",
    )
    refused(
        post(client, "/api/notices", lease_id=lease, template_id=uuid.uuid4(), dated_on=TODAY),
        404,
        "no such notice template",
    )
    refused(client.get(f"/api/notices/{uuid.uuid4()}/pdf"), 404, "no such notice")
    assert ok(client.get("/api/notices", params={"lease_id": str(their_lease)})) == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": "x"},
        {"name": " ", "body": "x"},
        {"name": "x" * 101, "body": "x"},
        {"name": "x", "body": "   "},
        {"name": "x", "body": "x" * 20001},
    ],
)
def test_a_template_takes_a_name_and_some_text(client, conn, staff, pmc, body):
    refused(client.post("/api/notices/templates", json=body), 422)
    assert rows(conn, pmc, "trust_notice_templates") == 0


@pytest.mark.parametrize(
    "body",
    [{}, {"lease_id": "x", "template_id": "y", "dated_on": "2026-01-01"}, {"dated_on": "soon"}],
)
def test_a_notice_takes_a_lease_a_template_and_a_day(client, staff, body):
    refused(client.post("/api/notices", json=body), 422)
    refused(client.post("/api/notices/preview", json=body), 422)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_templates_and_make_notices(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    some = uuid.uuid4()
    asked = {"lease_id": lease, "template_id": some, "dated_on": TODAY}
    for answer in (
        client.get("/api/notices/templates"),
        post(client, "/api/notices/templates", name="x", body="x"),
        post(client, f"/api/notices/templates/{some}", name="x", body="x"),
        post(client, f"/api/notices/templates/{some}/remove"),
        post(client, "/api/notices/preview", **asked),
        post(client, "/api/notices", **asked),
        client.get("/api/notices", params={"lease_id": str(lease)}),
        client.get(f"/api/notices/{some}/pdf"),
    ):
        refused(answer, status)
    assert rows(conn, pmc, "trust_notice_templates") == 0


def test_a_tenant_who_left_is_not_named(client, conn, staff, pmc, lease):
    tenant = conn.execute(
        "SELECT t.id FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        " WHERE lt.lease_id = %s AND t.display_name = 'Avery Sample'",
        (lease,),
    ).fetchone()[0]
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, NULL)",
        (pmc.pmc_id, lease, tenant, TODAY - timedelta(days=1)),
    )
    tpl = add(client, "Names", "{{tenant_names}}")
    seen = ok(
        post(client, "/api/notices/preview", lease_id=lease, template_id=tpl["id"], dated_on=TODAY)
    )
    assert seen["body"] == "Reese Sample"
