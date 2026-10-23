"""Checks through the API (src/pms/api/checks.py, migration 20261022000157): where an account's
checks print, the payouts by check waiting, a run numbered from the first sheet, its PDF, and a
void freeing a payout for a new check under a new number. Names and amounts are made up."""

import io
import uuid
from datetime import UTC, datetime

import pytest
from helpers import (
    ROLES,
    approve_bill,
    contribute,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
)
from people import join_as_owner, join_as_staff, join_as_tenant
from pypdf import PdfReader


def method(conn, pmc, transfer, how="check"):
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)",
        (pmc.pmc_id, transfer, how),
    )
    return transfer


def a_draw(conn, pmc, amount="40.00", how="check"):
    owner = pmc.owners[0].account
    contribute(conn, pmc.pmc_id, owner, f"in/{uuid.uuid4()}", "500.00")
    key = f"draw/{uuid.uuid4()}"
    draw_owner(conn, pmc.pmc_id, owner, key, amount, datetime.now(UTC))
    transfer = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
        (pmc.pmc_id, key),
    ).fetchone()[0]
    return method(conn, pmc, transfer, how)


def a_bill_paid(conn, pmc, reference="ROOF-17", amount="1000.00"):
    today = datetime.now(UTC).date()
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, f"in/{uuid.uuid4()}", amount)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, reference, today, today, amount
    )
    approve_bill(conn, pmc.pmc_id, bill, today, "Owner, by phone")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime.now(UTC))
    return method(conn, pmc, pay_bill(conn, pmc.pmc_id, bill, datetime.now(UTC)))


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def ready(conn, staff, pmc):
    """A draw and a bill paid, both recorded as sent by check."""
    return a_draw(conn, pmc, "125.50"), a_bill_paid(conn, pmc)


def path(pmc):
    return f"/api/checks/{pmc.operating_bank_id}"


def names(conn, pmc):
    return conn.execute(
        "SELECT o.display_name, p.display_name, v.display_name FROM trust_owners AS o,"
        " trust_properties AS p, trust_vendors AS v WHERE o.id = %s AND p.id = %s AND v.id = %s",
        (pmc.owners[0].owner_id, pmc.owners[0].property_id, pmc.vendor_id),
    ).fetchone()


def test_staff_print_payouts_by_check_and_download_them(client, conn, pmc, ready):
    draw, bill = ready
    owner, prop, vendor = names(conn, pmc)
    a_draw(conn, pmc, "9.00", how="EFT")  # by EFT: never on a check

    shown = client.get(path(pmc)).json()
    assert shown["settings"] == {
        "position": "top",
        "shift_right": 0,
        "shift_down": 0,
        "recorded_by": None,
        "recorded_at": None,
    }
    assert [(p["transfer_id"], p["name"], p["what"], p["amount"]) for p in shown["waiting"]] == [
        (draw, owner, f"Owner draw, {prop}", "125.50"),
        (bill, vendor, "Bill ROOF-17", "1000.00"),
    ]
    assert (shown["runs"], shown["next_number"]) == ([], None)

    kept = client.post(f"{path(pmc)}/settings", json={"position": "middle", "shift_right": 4})
    made = client.post(path(pmc), json={"first_number": 1001, "transfer_ids": [bill, draw]})

    assert kept.status_code == 201
    assert kept.json() | {"recorded_at": None} == {
        "position": "middle",
        "shift_right": 4,
        "shift_down": 0,
        "recorded_by": "Staff 1",
        "recorded_at": None,
    }
    assert made.status_code == 201, made.text
    run = made.json()
    assert run["made_by"] == "Staff 1"
    # Numbered in the order paid: the draw first.
    assert [(c["number"], c["name"], c["amount"], c["voided"]) for c in run["checks"]] == [
        (1001, owner, "125.50", None),
        (1002, vendor, "1000.00", None),
    ]
    after = client.get(path(pmc)).json()
    assert (after["waiting"], after["next_number"]) == ([], 1003)

    pdf = client.get(f"/api/checks/runs/{run['id']}.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"] == 'attachment; filename="checks-1001-1002.pdf"'
    pages = [p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages]
    assert len(pages) == 2
    assert "One hundred twenty-five and 50/100" in pages[0] and "Check 1001" in pages[0]
    assert "One thousand and 00/100" in pages[1] and "Bill ROOF-17: " in pages[1]


def test_a_void_frees_the_payout_for_a_new_number(client, conn, pmc, ready):
    draw, bill = ready
    run = client.post(path(pmc), json={"first_number": 1001, "transfer_ids": [draw]}).json()
    (check,) = run["checks"]

    again = client.post(path(pmc), json={"first_number": 1002, "transfer_ids": [draw]})
    voided = client.post(f"/api/checks/{check['id']}/void", json={"reason": "Jammed"})
    twice = client.post(f"/api/checks/{check['id']}/void", json={"reason": "Again"})
    reused = client.post(path(pmc), json={"first_number": 1001, "transfer_ids": [draw]})
    printed = client.post(path(pmc), json={"first_number": 1002, "transfer_ids": [draw, bill]})
    void_pdf = client.get(f"/api/checks/runs/{run['id']}.pdf")

    assert again.status_code == 409
    assert again.json()["detail"] == f"{draw} is not a payout by check waiting to be printed"
    assert (voided.status_code, twice.status_code) == (204, 409)
    assert reused.status_code == 409
    assert reused.json()["detail"] == (
        "a check numbered 1001 to 1001 is printed already from this account:"
        " give the number on the first sheet loaded"
    )
    assert printed.status_code == 201
    assert [c["number"] for c in printed.json()["checks"]] == [1002, 1003]
    assert (void_pdf.status_code, void_pdf.json()["detail"]) == (
        409,
        "every check of this run is void",
    )
    runs = client.get(path(pmc)).json()["runs"]
    shown = [
        [(c["number"], c["voided"] and c["voided"]["reason"]) for c in r["checks"]] for r in runs
    ]
    assert shown == [[(1002, None), (1003, None)], [(1001, "Jammed")]]


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"first_number": 1001, "transfer_ids": ["draw", "draw"]}, 422, "pick each payout once"),
        ({"first_number": 1001, "transfer_ids": []}, 422, None),
        ({"first_number": 0, "transfer_ids": ["draw"]}, 422, None),
        ({"first_number": 99_999_999, "transfer_ids": ["draw", "bill"]}, 422,
         "check numbers go to 99999999"),
        ({"first_number": 1001, "transfer_ids": ["pglt_none"]}, 409,
         "pglt_none is not a payout by check waiting to be printed"),
        ({"first_number": "next", "transfer_ids": ["draw"]}, 422, None),
    ],
)  # fmt: skip
def test_bad_runs_are_refused_and_print_nothing(client, conn, pmc, ready, body, status, detail):
    draw, bill = ready
    ids = [{"draw": draw, "bill": bill}.get(t, t) for t in body["transfer_ids"]]

    answer = client.post(path(pmc), json=body | {"transfer_ids": ids})

    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail
    assert client.get(path(pmc)).json()["runs"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"position": "side"},
        {"position": "top", "shift_right": 37},
        {"position": "top", "shift_down": -40},
    ],
)
def test_bad_settings_are_refused(client, conn, staff, pmc, body):
    assert client.post(f"{path(pmc)}/settings", json=body).status_code == 422
    assert client.get(path(pmc)).json()["settings"]["recorded_by"] is None


def test_another_companys_account_run_or_check_is_not_found(client, conn, pmc, ready):
    other = make_pmc(conn)
    for account in (other.operating_bank_id, uuid.uuid4()):
        assert client.get(f"/api/checks/{account}").status_code == 404
        kept = client.post(f"/api/checks/{account}/settings", json={"position": "top"})
        made = client.post(
            f"/api/checks/{account}", json={"first_number": 1, "transfer_ids": ["x"]}
        )
        assert (kept.status_code, made.status_code) == (404, 404)
    assert client.get(f"/api/checks/runs/{uuid.uuid4()}.pdf").status_code == 404
    assert client.post(f"/api/checks/{uuid.uuid4()}/void", json={"reason": "x"}).status_code == 404


def test_staff_who_may_not_disburse_see_the_page_but_print_and_void_nothing(
    client, other_client, conn, app_conn, pmc, ready
):
    draw, bill = ready
    run = client.post(path(pmc), json={"first_number": 1001, "transfer_ids": [draw]}).json()
    join_as_staff(
        other_client, conn, app_conn, pmc, name="Clerk", roles=[r for r in ROLES if r != "disburse"]
    )

    assert other_client.get(path(pmc)).status_code == 200
    for answer in (
        other_client.post(f"{path(pmc)}/settings", json={"position": "bottom"}),
        other_client.post(path(pmc), json={"first_number": 2001, "transfer_ids": [bill]}),
        other_client.get(f"/api/checks/runs/{run['id']}.pdf"),
        other_client.post(f"/api/checks/{run['checks'][0]['id']}/void", json={"reason": "x"}),
    ):
        assert answer.status_code == 403
        assert "disburse" in answer.json()["detail"]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_checks(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    some = uuid.uuid4()
    for answer in (
        client.get(path(pmc)),
        client.post(f"{path(pmc)}/settings", json={"position": "top"}),
        client.post(path(pmc), json={"first_number": 1, "transfer_ids": ["x"]}),
        client.get(f"/api/checks/runs/{some}.pdf"),
        client.post(f"/api/checks/{some}/void", json={"reason": "x"}),
    ):
        assert answer.status_code == status
