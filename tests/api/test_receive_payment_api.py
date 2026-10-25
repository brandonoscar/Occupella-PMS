"""Receive payment through the API (ADR 0010 phase 1c, the founder's part D): what staff write
about a tenant's payment, its file (a check's photo, a PDF), and the open charges it pays first,
the rest of it paying as before (rent first, oldest first). The note stays with staff: a tenant
never sees it and the AI never reads it."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import PDF, PNG, add_unit, balance, charge, make_pmc, open_lease, post
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api import create_app

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - 90 * DAY


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's lease of a unit at the first owner's property since START, $1,500.00 a month."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant_of(conn, pmc)])


def pay(client, lease, amount, request_id=None, **more):
    body = {
        "request_id": str(request_id or uuid.uuid4()),
        "amount": amount,
        "received_on": str(TODAY),
        **more,
    }
    return client.post(
        f"/api/rent/leases/{lease}/payments", json=json.loads(json.dumps(body, default=str))
    )


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def payments(client, lease):
    return ok(client.get(f"/api/leases/{lease}"), 200)["payments"]


def matched(conn, transfers):
    return dict(
        conn.execute(
            "SELECT charge_id, sum(amount) FROM trust_charge_payments"
            " WHERE transfer_id = ANY(%s) GROUP BY charge_id",
            (transfers,),
        ).fetchall()
    )


def attach(client, payment, content=PNG, content_type="image/png", title="Check 1042"):
    return client.post(
        f"/api/rent/payments/{payment}/attachments",
        json={
            "title": title,
            "content_type": content_type,
            "content_base64": base64.b64encode(content).decode(),
        },
    )


# --- a note --------------------------------------------------------------------------------


def test_a_note_is_kept_with_the_payment_once_and_names_who_wrote_it(
    client, conn, staff, pmc, lease
):
    charge(conn, pmc.pmc_id, lease, START, "1500.00")
    request = uuid.uuid4()

    first = ok(pay(client, lease, "1500.00", request, note="  Left at the office, check 1042  "))
    again = ok(pay(client, lease, "1500.00", request, note="Another note"))

    assert again == first
    (listed,) = payments(client, lease)
    # Sent again, the note stays as first written.
    assert (listed["note"], listed["attachments"]) == ("Left at the office, check 1042", [])
    assert conn.execute(
        "SELECT note, recorded_by FROM trust_payment_notes WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("Left at the office, check 1042", staff.person_id)]


def test_a_payment_without_a_note_has_none(client, conn, staff, pmc, lease):
    ok(pay(client, lease, "100.00"))
    (listed,) = payments(client, lease)
    assert listed["note"] is None


def test_a_tenant_never_sees_what_staff_wrote(client, conn, app_conn, staff, pmc, lease):
    ok(pay(client, lease, "100.00", note="Paid late again"))
    with TestClient(create_app(client.app.state.settings)) as portal:
        join_as_tenant(portal, conn, app_conn, pmc)
        leases = ok(portal.get("/api/tenant/leases"), 200)
    (mine,) = [each for each in leases if each["lease_id"] == str(lease)]
    (paid,) = mine["payments"]
    assert (paid["amount"], paid["note"], paid["attachments"]) == ("100.00", None, [])


# --- the charges it pays -------------------------------------------------------------------


def test_a_payment_pays_the_charges_staff_chose_first_and_the_rest_as_before(
    client, conn, staff, pmc, lease
):
    october = charge(conn, pmc.pmc_id, lease, START, "1500.00")
    fee = charge(conn, pmc.pmc_id, lease, START + 5 * DAY, "50.00", "fee")
    november = charge(conn, pmc.pmc_id, lease, START + 31 * DAY, "1500.00")

    paid = ok(
        pay(
            client,
            lease,
            "1600.00",
            charges=[
                {"charge_id": str(november), "amount": "1500.00"},
                {"charge_id": str(fee), "amount": "20.00"},
            ],
        )
    )

    # November and $20.00 of the fee first; the $80.00 left pays October, the oldest rent.
    assert matched(conn, paid["transfers"]) == {
        november: Decimal("1500.00"),
        fee: Decimal("20.00"),
        october: Decimal("80.00"),
    }
    assert (paid["to_owner"], paid["to_prepaid"]) == ("1600.00", "0.00")
    assert balance(conn, pmc.owners[0].account) == Decimal("1600.00")
    # The choice is kept with the payment's transfers, in the order staff gave it.
    assert conn.execute(
        "SELECT metadata -> 'charges' FROM pgledger_transfers WHERE id = %s",
        (paid["transfers"][0],),
    ).fetchone()[0] == [[str(november), "1500.00"], [str(fee), "20.00"]]


def test_a_payment_beyond_what_is_owed_still_holds_the_rest_as_prepaid_rent(
    client, conn, staff, pmc, lease
):
    fee = charge(conn, pmc.pmc_id, lease, START, "50.00", "fee")
    rent = charge(conn, pmc.pmc_id, lease, START + DAY, "1500.00")

    paid = ok(pay(client, lease, "1700.00", charges=[{"charge_id": str(fee), "amount": "50.00"}]))

    assert matched(conn, paid["transfers"]) == {fee: Decimal("50.00"), rent: Decimal("1500.00")}
    assert (paid["to_owner"], paid["to_prepaid"]) == ("1550.00", "150.00")


def test_sending_a_chosen_payment_again_records_it_once(client, conn, staff, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, START, "1500.00")
    request = uuid.uuid4()
    chosen = [{"charge_id": str(rent), "amount": "700.00"}]

    first = ok(pay(client, lease, "700.00", request, charges=chosen))
    again = ok(pay(client, lease, "700.00", request, charges=chosen))

    assert again == first
    assert matched(conn, first["transfers"]) == {rent: Decimal("700.00")}


@pytest.mark.parametrize(
    ("chosen", "amount", "detail"),
    [
        ([("other", "10.00")], "100.00", "a charge chosen isn't open on this lease"),
        ([("paid", "10.00")], "100.00", "a charge chosen isn't open on this lease"),
        ([("fee", "10.00"), ("fee", "10.00")], "100.00", "choose each charge once"),
        ([("fee", "60.00")], "100.00", "only 50.00 is open of a charge chosen; 60.00 is more"),
        (
            [("fee", "50.00"), ("rent", "100.00")],
            "120.00",
            "the charges chosen come to more than the payment",
        ),
    ],
)
def test_a_choice_that_does_not_fit_is_refused_and_records_nothing(
    client, conn, staff, pmc, lease, chosen, amount, detail
):
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 9")
    other_lease = open_lease(
        conn, pmc.pmc_id, other_unit, START, None, "900.00", [tenant_of(conn, pmc)]
    )
    charges = {
        "paid": charge(conn, pmc.pmc_id, lease, START - DAY, "25.00"),
        "rent": charge(conn, pmc.pmc_id, lease, START, "1500.00"),
        "fee": charge(conn, pmc.pmc_id, lease, START + DAY, "50.00", "fee"),
        "other": charge(conn, pmc.pmc_id, other_lease, START, "900.00"),
    }
    ok(pay(client, lease, "25.00"))  # pays the oldest rent, all $25.00 of it
    before = balance(conn, pmc.owners[0].account)

    answer = pay(
        client,
        lease,
        amount,
        charges=[{"charge_id": str(charges[key]), "amount": part} for key, part in chosen],
    )

    refused(answer, 422, detail)
    assert balance(conn, pmc.owners[0].account) == before


def test_a_lease_its_tenants_moved_to_pays_each_lease_in_turn_and_takes_no_choice(
    client, conn, staff, pmc, lease
):
    rent = charge(conn, pmc.pmc_id, lease, START, "1500.00")
    earlier = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 8"),
        START - 400 * DAY,
        START - DAY,
        "1400.00",
        [tenant_of(conn, pmc)],
    )
    conn.execute(
        "INSERT INTO trust_lease_transfers (from_lease_id, pmc_id, to_lease_id, moved_on,"
        " owner_account) VALUES (%s, %s, %s, %s, %s)",
        (earlier, pmc.pmc_id, lease, START, pmc.owners[0].account),
    )

    refused(
        pay(client, lease, "100.00", charges=[{"charge_id": str(rent), "amount": "100.00"}]),
        422,
        "this lease's tenants moved from another lease: a payment pays what each lease owes,"
        " oldest lease first",
    )


# --- a file --------------------------------------------------------------------------------


def test_a_file_is_kept_with_the_payment_and_listed_on_the_lease(client, conn, staff, pmc, lease):
    charge(conn, pmc.pmc_id, lease, START, "1500.00")
    paid = ok(pay(client, lease, "1500.00", reference="Check 1042"))

    photo = ok(attach(client, paid["payment_id"]))["id"]
    scan = ok(attach(client, paid["payment_id"], PDF, "application/pdf", "Deposit slip"))["id"]

    (listed,) = payments(client, lease)
    assert listed["transfer_id"] == paid["payment_id"]
    assert listed["attachments"] == [photo, scan]
    library = ok(client.get("/api/documents"), 200)["documents"]
    kept = {d["id"]: (d["kind"], d["about_id"], d["title"]) for d in library}
    assert kept[photo] == ("payment_attachment", str(lease), "Check 1042")
    assert kept[scan] == ("payment_attachment", str(lease), "Deposit slip")
    assert client.get(f"/api/documents/{photo}/file").content == PNG


def test_a_file_is_kept_only_with_a_tenants_payment_of_the_pmc(client, conn, staff, pmc, lease):
    theirs = make_pmc(conn)
    their_unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id)
    their_lease = open_lease(
        conn, theirs.pmc_id, their_unit, START, None, "900.00", [tenant_of(conn, theirs)]
    )
    (their_payment,) = post(
        conn,
        theirs.pmc_id,
        "their-payment",
        [(theirs.operating_cash, theirs.owners[0].account, "100.00")],
        metadata={"kind": "tenant payment", "lease_id": str(their_lease)},
    )
    (contribution,) = post(
        conn, pmc.pmc_id, "not-a-payment", [(pmc.operating_cash, pmc.owners[0].account, "100.00")]
    )

    for transfer in (their_payment, contribution, "pglt_nothing"):
        refused(attach(client, transfer), 404, "no such payment")
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_documents WHERE pmc_id = ANY(%s)",
            ([pmc.pmc_id, theirs.pmc_id],),
        ).fetchone()[0]
        == 0
    )


def test_a_files_bytes_must_be_what_its_type_says(client, conn, staff, pmc, lease):
    paid = ok(pay(client, lease, "100.00"))
    refused(attach(client, paid["payment_id"], PDF, "image/png"), 422)
    # A payment's file comes with its payment, never by itself from Files.
    refused(
        client.post(
            "/api/documents",
            json={
                "kind": "payment_attachment",
                "about_id": str(lease),
                "title": "Check",
                "content_type": "image/png",
                "content_base64": base64.b64encode(PNG).decode(),
            },
        ),
        422,
        "a payment's file comes with the payment, on Receive payment",
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_a_payments_file(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = attach(client, "pglt_anything")

    assert answer.status_code == (401 if caller == "nobody" else 403)


# --- bad input -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "more",
    [
        {"note": " "},
        {"note": "x" * 2001},
        {"charges": []},
        {"charges": [{"charge_id": "x", "amount": "1.00"}]},
        {"charges": [{"charge_id": str(uuid.uuid4()), "amount": "0"}]},
        {"charges": [{"charge_id": str(uuid.uuid4()), "amount": "1.001"}]},
    ],
)
def test_bad_input_is_refused(client, staff, lease, more):
    refused(pay(client, lease, "10.00", **more), 422)


@pytest.mark.parametrize(
    "body",
    [
        {"title": " ", "content_type": "image/png", "content_base64": "aGVsbG8="},
        {"title": "Check", "content_type": "image/gif", "content_base64": "aGVsbG8="},
        {"title": "Check", "content_type": "image/png"},
    ],
)
def test_a_file_needs_a_title_a_type_and_its_bytes(client, conn, staff, pmc, lease, body):
    paid = ok(pay(client, lease, "100.00"))
    refused(client.post(f"/api/rent/payments/{paid['payment_id']}/attachments", json=body), 422)
