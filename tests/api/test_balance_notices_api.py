"""Balance notices (migration 20261023000201, src/pms/api/balance_notices.py): the leases that owe,
each with its tenants, where their email goes and how the legal notice for nonpayment goes in its
property's state; a send that makes a notice from the company's template for each lease picked,
listing what is open by charge, emails each tenant with an email on file, shows on their Email
tab, and prints as one PDF. A reminder, never the legal notice. Every name and address here is
synthetic."""

import io
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from helpers import add_tenant, add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant
from pypdf import PdfReader

from pms.api import balance_notices

TODAY = datetime.now(UTC).date()
FIRST = TODAY.replace(day=1)
REMINDER_TEXT = (
    "Dear {{tenant_names}},\n\nThe account for {{property}} {{unit}} shows {{balance_due}} open."
    " You can pay at the office or by check.\n\n{{company_name}}"
)
NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def template(client, name="Balance reminder", body=REMINDER_TEXT):
    return ok(post(client, "/api/notices/templates", name=name, body=body), 201)["id"]


def lease_owing(
    conn, pmc, unit_name="1A", people=("Reese Sample", "Avery Sample"), rent="1395.00", owes=True
):
    prop = pmc.owners[0].property_id
    tenants = [add_tenant(conn, pmc.pmc_id, prop, n) for n in people]
    unit = add_unit(conn, pmc.pmc_id, prop, unit_name)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, rent, tenants)
    if owes:
        charge(conn, pmc.pmc_id, lease, FIRST, Decimal(rent))
        charge(conn, pmc.pmc_id, lease, FIRST, Decimal("40.00"), "fee", "Parking")
    return lease, tenants


def contact(client, tenant, address):
    ok(client.post(f"/api/tenants/{tenant}/contact", json={"email": address}), 204)


def send(client, template_id, leases, dated_on=TODAY):
    return post(
        client, "/api/balance-notices", template_id=template_id, dated_on=dated_on,
        lease_ids=[str(lease) for lease in leases],
    )  # fmt: skip


def locate(client, pmc, state):
    ok(
        client.post(
            f"/api/properties/{pmc.owners[0].property_id}/address",
            json={"line_1": "1 Synthetic Way", "city": "Springfield", "state": state,
                  "zip": "00000"},
        ),
        204,
    )  # fmt: skip


def test_staff_see_the_leases_that_owe_with_their_tenants_and_email(client, conn, staff, pmc):
    lease, (reese, avery) = lease_owing(conn, pmc)
    lease_owing(conn, pmc, "1B", ("Quinn Sample",), owes=False)  # owes nothing
    contact(client, reese, "reese.sample@example.com")
    (found,) = ok(client.get("/api/balance-notices/owing"))
    assert (found["lease_id"], found["unit"], found["owed"]) == (str(lease), "1A", "1435.00")
    assert [(t["name"], t["email"]) for t in found["tenants"]] == [
        ("Avery Sample", None),
        ("Reese Sample", "reese.sample@example.com"),
    ]
    assert found["delivery"]["state"] is None
    assert "isn't on file" in found["delivery"]["words"]
    # Narrowed to another property, none.
    other = conn.execute(
        "SELECT id FROM trust_properties WHERE pmc_id = %s AND id <> %s LIMIT 1",
        (pmc.pmc_id, pmc.owners[0].property_id),
    ).fetchone()[0]
    assert ok(client.get("/api/balance-notices/owing", params={"property": str(other)})) == []


def test_a_send_makes_a_notice_per_lease_lists_what_is_open_and_emails_each_tenant(
    client, conn, staff, pmc
):
    lease, (reese, avery) = lease_owing(conn, pmc)
    contact(client, reese, "reese.sample@example.com")
    run = ok(send(client, template(client), [lease]), 201)
    (sent,) = run["sent"]
    assert (sent["owed"], sent["without_email"]) == ("1435.00", ["Avery Sample"])
    assert [t["email"] for t in sent["emailed"]] == ["reese.sample@example.com"]

    body = conn.execute(
        "SELECT body FROM trust_notices WHERE id = %s", (sent["notice_id"],)
    ).fetchone()[0]
    assert body.startswith("Dear Avery Sample and Reese Sample,")
    assert "What is open on the account:" in body
    assert f"  {FIRST:%m/%d/%Y}  Rent  $1,395.00" in body
    assert f"  {FIRST:%m/%d/%Y}  Parking  $40.00" in body
    assert "  Total  $1,435.00" in body
    assert body.endswith(balance_notices.REMINDER)

    # On Reese's Email tab, as a balance notice from the clerk.
    (line,) = ok(client.get("/api/emails", params={"about": "tenant", "about_id": str(reese)}))
    assert (line["kind"], line["to"], line["by"]) == (
        "balance_notice",
        "reese.sample@example.com",
        "Clerk 1",
    )
    assert line["subject"] == "Balance reminder: Property 1, 1A"
    assert balance_notices.REMINDER in line["body"]
    assert ok(client.get("/api/emails", params={"about": "tenant", "about_id": str(avery)})) == []
    owed = conn.execute(
        "SELECT owed FROM trust_balance_notices WHERE notice_id = %s", (sent["notice_id"],)
    ).fetchone()[0]
    assert owed == Decimal("1435.00")


def test_the_open_charges_blank_places_the_list_where_the_template_says(client, conn, staff, pmc):
    lease, _ = lease_owing(conn, pmc)
    placed = template(client, "Placed", "Hello {{tenant_names}}.\n{{ open_charges }}\nThanks.")
    (sent,) = ok(send(client, placed, [lease]), 201)["sent"]
    body = conn.execute(
        "SELECT body FROM trust_notices WHERE id = %s", (sent["notice_id"],)
    ).fetchone()[0]
    assert body.count("What is open on the account:") == 1
    assert body.index("What is open") < body.index("Thanks.") < body.index(balance_notices.REMINDER)


def test_every_notice_of_a_send_prints_as_one_pdf(client, conn, staff, pmc):
    first, _ = lease_owing(conn, pmc)
    second, _ = lease_owing(conn, pmc, "2A", ("Jordan Sample",), rent="1100.00")
    run = ok(send(client, template(client), [second, first]), 201)
    answer = client.get(f"/api/balance-notices/runs/{run['id']}/pdf")
    assert answer.status_code == 200 and answer.headers["content-type"] == "application/pdf"
    pages = PdfReader(io.BytesIO(answer.content)).pages
    texts = [" ".join(p.extract_text().split()) for p in pages]
    assert len(pages) == 2
    assert "Avery Sample and Reese Sample" in texts[0] and "Jordan Sample" in texts[1]
    assert all("It is not a notice to pay rent or quit" in t for t in texts)
    assert client.get(f"/api/balance-notices/runs/{NOWHERE}/pdf").status_code == 404


@pytest.mark.parametrize(
    ("state", "may", "says"),
    [
        ("OH", False, "In OH the legal notice for nonpayment goes by mail, posting or hand"),
        ("FL", True, "Fla. Stat. 83.505"),
        ("TX", True, "Tex. Prop. Code 24.005"),
        ("MI", True, "Mich. Comp. Laws 600.5718(1)(d)"),
    ],
)
def test_each_state_says_how_its_legal_notice_goes(client, conn, staff, pmc, state, may, says):
    lease_owing(conn, pmc)
    locate(client, pmc, state)
    (found,) = ok(client.get("/api/balance-notices/owing"))
    assert (found["delivery"]["state"], found["delivery"]["email_may_count"]) == (state, may)
    assert says in found["delivery"]["words"]


def test_texas_email_counts_only_from_2026():
    assert balance_notices.delivery("TX", date(2025, 12, 31)).email_may_count is False
    assert balance_notices.delivery("TX", date(2026, 1, 1)).email_may_count is True


def test_a_template_with_legal_words_is_refused(client, conn, staff, pmc):
    lease, _ = lease_owing(conn, pmc)
    legal = template(client, "Pay or quit", "Pay {{balance_due}} in 3 days or quit {{unit}}.")
    refused = send(client, legal, [lease])
    assert refused.status_code == 422
    assert "reads like the legal notice for nonpayment (quit)" in refused.json()["detail"]
    for word in ("eviction", "Possession", "terminate", "court"):
        assert balance_notices.legal_words(f"a {word} here") == [word.lower()]
    assert balance_notices.legal_words("Pay at the courtyard office.") == []
    assert conn.execute(
        "SELECT count(*) FROM trust_balance_notice_runs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_lease_that_owes_nothing_or_is_another_companys_is_refused(client, conn, staff, pmc):
    paid_up, _ = lease_owing(conn, pmc, "1B", ("Quinn Sample",), owes=False)
    reminder = template(client)
    refused = send(client, reminder, [paid_up])
    assert refused.status_code == 409 and "owes nothing" in refused.json()["detail"]
    other = make_pmc(conn)
    stranger, _ = lease_owing(conn, other)
    assert send(client, reminder, [stranger]).status_code == 404
    assert send(client, NOWHERE, [paid_up]).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"template_id": NOWHERE, "dated_on": "2026-10-23", "lease_ids": []},
        {"template_id": NOWHERE, "dated_on": "2026-10-23", "lease_ids": [NOWHERE, NOWHERE]},
        {"template_id": NOWHERE, "dated_on": "not a day", "lease_ids": [NOWHERE]},
        {"template_id": "x", "dated_on": "2026-10-23", "lease_ids": [NOWHERE]},
        {"dated_on": "2026-10-23", "lease_ids": [NOWHERE]},
    ],
)
def test_bad_input_is_refused(client, staff, body):
    assert client.post("/api/balance-notices", json=body).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_send_balance_notices(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/balance-notices/owing").status_code == status
    body = {"template_id": NOWHERE, "dated_on": "2026-10-23", "lease_ids": [NOWHERE]}
    assert client.post("/api/balance-notices", json=body).status_code == status
    assert client.get(f"/api/balance-notices/runs/{NOWHERE}/pdf").status_code == status


def test_a_custom_role_without_email_may_not_send(client, other_client, conn, app_conn, staff, pmc):
    lease, _ = lease_owing(conn, pmc)
    reminder = template(client)
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused = send(other_client, reminder, [lease])
    assert refused.status_code == 403 and "email to tenants" in refused.json()["detail"]
    assert other_client.get("/api/balance-notices/owing").status_code == 200  # reads stay open
