"""Closing a year through the API (src/pms/api/year_close.py, migration 0106; the founder's review
q116): staff see last year's readiness, close it with the approve role, and from then on a charge
dated in it is refused in words."""

from datetime import UTC, date, datetime

import pytest
from helpers import (
    add_tenant,
    add_unit,
    approve_bank,
    import_statement,
    make_pmc,
    open_lease,
    staff_named,
    transfer_batch,
)
from people import join_as_staff, join_as_tenant

LAST = datetime.now(UTC).year - 1


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def money_in(conn, pmc, year=LAST):
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "100.00")],
        datetime(year, 12, 5, 12, tzinfo=UTC),
    )


def december(conn, pmc, year=LAST):
    importer = staff_named(conn, pmc.pmc_id, "Importer 1")
    import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, importer, date(year, 12, 1), date(year, 12, 31), []
    )
    approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, date(year, 12, 31))


def test_staff_see_last_year_get_ready_close_it_and_then_nothing_is_charged_in_it(
    client, conn, pmc, staff
):
    money_in(conn, pmc)
    prop = pmc.owners[0].property_id
    lease = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, prop),
        date(LAST, 11, 1),
        None,
        "900.00",
        [add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")],
    )

    before = ok(client.get("/api/year-close"))
    not_yet = client.post("/api/year-close", json={"year": LAST})
    december(conn, pmc)
    ready = ok(client.get("/api/year-close"))
    closed = ok(client.post("/api/year-close", json={"year": LAST}), 201)
    again = ok(client.post("/api/year-close", json={"year": LAST}), 201)
    after = ok(client.get("/api/year-close"))
    late = client.post(
        f"/api/rent/leases/{lease}/charges",
        json={"kind": "fee", "amount": "25.00", "due_on": f"{LAST}-12-20", "memo": "Key"},
    )

    operating, deposits = sorted(
        before["next"]["accounts"], key=lambda a: "operating" not in a["name"]
    )
    assert before["closed"] == [] and before["next"]["year"] == LAST
    assert (operating["in_the_year"], operating["ready"], operating["approved_through"]) == (
        True,
        False,
        None,
    )
    assert (deposits["in_the_year"], deposits["ready"]) == (False, True)
    assert before["next"]["ready"] is False
    assert not_yet.status_code == 409
    assert not_yet.json()["detail"].startswith(f"approve month-end through 12/31/{LAST} first:")
    assert ready["next"]["ready"] is True
    (approved,) = [a for a in ready["next"]["accounts"] if a["id"] == operating["id"]]
    assert approved["approved_through"] == f"{LAST}-12-31"
    assert (
        closed
        == again
        == {
            "year": LAST,
            "closed_on": datetime.now(UTC).date().isoformat(),
            "closed_by": "Staff 1",
        }
    )
    assert after == {"closed": [closed], "closed_until": f"{LAST}-12-31", "next": None}
    assert late.status_code == 409
    assert late.json()["detail"] == (
        f"{LAST} is closed: a charge dated 12/20/{LAST} can't be added;"
        f" date it 01/01/{LAST + 1} or later"
    )


def test_closing_takes_the_approve_role(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc, name="Clerk 1", roles=())
    money_in(conn, pmc)
    december(conn, pmc)

    refused = client.post("/api/year-close", json={"year": LAST})

    assert refused.status_code == 403
    assert "may not close a year (the approve role)" in refused.json()["detail"]


def test_only_staff_see_or_close_a_year(client, other_client, conn, app_conn, pmc):
    join_as_tenant(other_client, conn, app_conn, pmc)

    assert client.get("/api/year-close").status_code == 401
    assert client.post("/api/year-close", json={"year": LAST}).status_code == 401
    assert other_client.get("/api/year-close").status_code == 403
    assert other_client.post("/api/year-close", json={"year": LAST}).status_code == 403


def test_bad_years_are_refused(client, conn, pmc, staff):
    this_year = datetime.now(UTC).year

    assert client.post("/api/year-close", json={"year": "last"}).status_code == 422
    assert client.post("/api/year-close", json={}).status_code == 422
    assert client.post("/api/year-close", json={"year": 1999}).status_code == 422
    not_over = client.post("/api/year-close", json={"year": this_year})
    assert not_over.status_code == 422
    assert not_over.json()["detail"] == f"{this_year} has not ended yet"
    nothing = client.post("/api/year-close", json={"year": LAST})
    assert nothing.status_code == 409
    assert nothing.json()["detail"] == (
        f"no trust bank account was reconciled through 12/31/{LAST}: there is no {LAST} to close"
    )


def test_a_company_sees_only_its_own_closes(client, conn, pmc, staff):
    theirs = make_pmc(conn)
    money_in(conn, theirs)
    december(conn, theirs)
    closer = staff_named(conn, theirs.pmc_id, "Closer 1", ["approve"])
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(closer),))
    conn.execute("SELECT trust_close_year(%s, %s)", (theirs.pmc_id, LAST))
    conn.execute("SELECT set_config('trust.staff_id', '', false)")

    mine = ok(client.get("/api/year-close"))

    assert mine["closed"] == [] and mine["closed_until"] is None
    assert mine["next"]["year"] == LAST
