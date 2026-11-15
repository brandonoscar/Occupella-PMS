"""The company covers an owner's shortfall when a payment bounces (migration 20261115000100,
docs/edge-cases.md M-060, GitHub issue #232; the founder, 2026-11-15).

A tenant's $2,250.00 payment comes back after part of the owner's money went out: the bounce would
take the owner's property account below zero, which the ledger refuses. The company deposits its
own money into trust for exactly what the account is short (trust_cover_returned_payment), the
bounce posts after it in the same transaction, and the owner owes it back
(trust_company_advance_owed) until they repay it (trust_repay_company_advance)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi import HTTPException
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    admin_of,
    apply_payment,
    balance,
    bank_sums,
    charge,
    draw_owner,
    give_roles,
    make_pmc,
    open_lease,
)

from pms.api import rent

DAY = timedelta(days=1)
TODAY = datetime.now(UTC).date()


def on_this_day() -> None:
    """Today's date (UTC), worked out again before each test (tests/conftest.py)."""
    global TODAY
    TODAY = datetime.now(UTC).date()


class Books:
    """A lease at Owner 1's property, charged $2,250.00 of rent and paid it; then $110.00 of the
    owner's money went out, so the payment coming back would leave the account $110.00 short."""

    def __init__(self, conn, draw="110.00"):
        self.conn = conn
        self.pmc = make_pmc(conn)
        pmc_id = self.pmc.pmc_id
        prop = self.pmc.owners[0].property_id
        tenant = add_tenant(conn, pmc_id, prop, "Tenant A")
        unit = add_unit(conn, pmc_id, prop, "Unit A")
        self.lease = open_lease(conn, pmc_id, unit, TODAY - 40 * DAY, None, "2250.00", [tenant])
        self.charge = charge(conn, pmc_id, self.lease, TODAY - 10 * DAY, "2250.00")
        self.owner = self.pmc.owners[0].account
        with conn.transaction():
            paid = rent.record_payment(
                conn, pmc_id, self.lease, uuid.uuid4(), Decimal("2250.00"), TODAY - 9 * DAY
            )
        self.payment = paid.transfers[0]
        self.staff = add_staff(conn, pmc_id, "Manager A")
        give_roles(conn, pmc_id, self.staff, ["disburse"])
        if draw:
            with acting_as(conn, self.staff):
                draw_owner(conn, pmc_id, self.owner, "draw-1", draw, datetime.now(UTC))

    def bounce(self, conn=None, cover=None, staff=None):
        conn = conn or self.conn
        with acting_as(conn, staff or self.staff), conn.transaction():
            return rent.bounce(conn, self.pmc.pmc_id, self.payment, TODAY, cover)

    def advances(self):
        return self.conn.execute(
            "SELECT request_key, payment_key, payment_transfer_id, ledger_account_id, amount,"
            " transfer_id, recorded_by FROM trust_company_advances WHERE pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchall()

    def owed(self):
        return self.conn.execute("SELECT trust_company_advance_owed(%s)", (self.owner,)).fetchone()[
            0
        ]

    def repay(self, conn, key, amount, staff=None):
        with acting_as(conn, staff or self.staff):
            return conn.execute(
                "SELECT trust_repay_company_advance(%s, %s, %s, %s, now())",
                (self.pmc.pmc_id, self.owner, Decimal(amount), key),
            ).fetchone()[0]


@pytest.fixture
def books(conn):
    on_this_day()
    return Books(conn)


def test_what_a_bounce_would_leave_short_is_read_per_owner_account(conn, books):
    rows = conn.execute(
        "SELECT * FROM trust_return_shortfall(%s, %s)", (books.pmc.pmc_id, books.payment)
    ).fetchall()

    assert rows == [(books.owner, Decimal("2250.00"), Decimal("2140.00"), Decimal("110.00"))]
    assert rent.shortfall(conn, books.pmc.pmc_id, books.payment) == Decimal("110.00")


def test_a_bounce_the_owner_cannot_take_is_refused_saying_how_much_and_moves_nothing(books):
    with pytest.raises(HTTPException) as refused:
        books.bounce()

    assert refused.value.status_code == 409
    assert refused.value.detail == (
        "not enough money: the owner's account would be $110.00 short. The company can cover it"
        " from its own money, and the owner owes it back"
    )
    assert balance(books.conn, books.owner) == Decimal("2140.00")
    assert books.advances() == []


def test_the_company_covers_exactly_the_shortfall_and_the_bounce_posts(conn, app_conn, books):
    returned = books.bounce(conn=app_conn, cover=uuid.uuid4())

    assert returned.covered == Decimal("110.00")
    assert balance(conn, books.owner) == Decimal("0.00")
    [(key, payment_key, first, account, amount, transfer, by)] = books.advances()
    assert (first, account, amount, by) == (
        books.payment,
        books.owner,
        Decimal("110.00"),
        books.staff,
    )
    assert key.startswith("return cover/") and payment_key.startswith("payment/")
    moved = conn.execute(
        "SELECT from_account_id, to_account_id, amount, metadata ->> 'kind' FROM pgledger_transfers"
        " WHERE id = %s",
        (transfer,),
    ).fetchone()
    assert moved == (books.pmc.operating_cash, books.owner, Decimal("110.00"), "company advance")
    assert books.owed() == Decimal("110.00")
    assert set(bank_sums(conn, books.pmc.pmc_id).values()) == {Decimal(0)}
    # The rent is owed again, as after any bounce.
    open_now = dict(rent.open_charges(conn, books.pmc.pmc_id, books.lease))
    assert open_now[books.charge] == Decimal("2250.00")


def test_nothing_short_covers_nothing(conn):
    on_this_day()
    books = Books(conn, draw=None)

    returned = books.bounce(cover=uuid.uuid4())

    assert returned.covered == Decimal(0)
    assert books.advances() == []
    assert balance(conn, books.owner) == Decimal("0.00")


def test_a_cover_without_its_bounce_is_refused_at_commit(app_conn, books):
    only_as = "covers a returned payment only as"
    with (
        pytest.raises(psycopg.errors.CheckViolation, match=only_as),
        acting_as(app_conn, books.staff),
        app_conn.transaction(),
    ):
        app_conn.execute(
            "SELECT trust_cover_returned_payment(%s, %s, 'k-1', now())",
            (books.pmc.pmc_id, books.payment),
        )

    assert books.advances() == []
    assert balance(app_conn, books.owner) == Decimal("2140.00")


def test_a_cover_larger_than_its_bounce_needed_is_refused_at_commit(conn, books):
    """Money that came into the account after the cover, before the bounce, would leave the
    company's money sitting in trust: refused, as no cover is ever more than the shortfall."""
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="not what its bounce needed"),
        acting_as(conn, books.staff),
        conn.transaction(),
    ):
        conn.execute(
            "SELECT trust_cover_returned_payment(%s, %s, 'k-1', now())",
            (books.pmc.pmc_id, books.payment),
        )
        conn.execute(
            "SELECT trust_record_owner_contribution(%s, %s, 'in-1', 5.00, now(), NULL)",
            (books.pmc.pmc_id, books.owner),
        )
        rent.bounce(conn, books.pmc.pmc_id, books.payment, TODAY)

    assert books.advances() == []


def test_the_same_cover_again_records_nothing_new_and_a_used_key_covers_nothing_else(conn, books):
    cover = uuid.uuid4()
    first = books.bounce(cover=cover)
    again = books.bounce(cover=cover)

    assert again == first
    assert len(books.advances()) == 1
    # The function itself: the same key returns what it covered; the payment takes no other cover.
    with acting_as(conn, books.staff):
        same = conn.execute(
            "SELECT trust_cover_returned_payment(%s, %s, %s, now())",
            (books.pmc.pmc_id, books.payment, rent.cover_key(cover)),
        ).fetchone()[0]
        assert same == Decimal("110.00")
        with pytest.raises(psycopg.errors.UniqueViolation, match="already covered the return"):
            conn.execute(
                "SELECT trust_cover_returned_payment(%s, %s, 'another', now())",
                (books.pmc.pmc_id, books.payment),
            )
    # The same request id for another payment of the company covers nothing.
    second = second_payment(conn, books)
    with (
        acting_as(conn, books.staff),
        pytest.raises(psycopg.errors.UniqueViolation, match="covered another returned"),
    ):
        conn.execute(
            "SELECT trust_cover_returned_payment(%s, %s, %s, now())",
            (books.pmc.pmc_id, second, rent.cover_key(cover)),
        )
    with (
        pytest.raises(HTTPException) as refused,
        acting_as(conn, books.staff),
        conn.transaction(),
    ):
        rent.bounce(conn, books.pmc.pmc_id, second, TODAY, cover)
    assert refused.value.detail == "this request id covered another payment's return"
    assert len(books.advances()) == 1


def second_payment(conn, books):
    charge(conn, books.pmc.pmc_id, books.lease, TODAY - 2 * DAY, "2250.00")
    with conn.transaction():
        paid = rent.record_payment(
            conn, books.pmc.pmc_id, books.lease, uuid.uuid4(), Decimal("2250.00"), TODAY
        )
    return paid.transfers[0]


def test_a_cover_names_a_staff_member(app_conn, books):
    with (
        pytest.raises(psycopg.errors.InvalidParameterValue, match="name the staff member"),
        acting_as(app_conn, None),
    ):
        app_conn.execute(
            "SELECT trust_cover_returned_payment(%s, %s, 'k-1', now())",
            (books.pmc.pmc_id, books.payment),
        )


def test_staff_whose_roles_leave_out_rent_work_may_not_cover(conn, app_conn, books):
    pmc_id = books.pmc.pmc_id
    with acting_as(conn, admin_of(conn, pmc_id)):
        role = conn.execute(
            "INSERT INTO trust_custom_roles (pmc_id, name) VALUES (%s, 'Bills only') RETURNING id",
            (pmc_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
            " VALUES (%s, %s, 'bills', true)",
            (pmc_id, role),
        )
        conn.execute(
            "INSERT INTO trust_staff_custom_roles (pmc_id, staff_id, role_id, granted)"
            " VALUES (%s, %s, %s, true)",
            (pmc_id, books.staff, role),
        )

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="may not record rent work"):
        books.bounce(conn=app_conn, cover=uuid.uuid4())
    assert books.advances() == []


def test_a_cover_is_never_a_payment_of_a_charge(conn, books):
    books.bounce(cover=uuid.uuid4())
    [advance] = books.advances()

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="cover of a returned payment"):
        apply_payment(conn, books.pmc.pmc_id, books.charge, advance[5], Decimal("110.00"))


def test_the_owner_repays_no_more_than_is_owed_into_the_companys_fee_account(conn, app_conn, books):
    books.bounce(cover=uuid.uuid4())
    conn.execute(
        "SELECT trust_record_owner_contribution(%s, %s, 'in-1', 500.00, now(), NULL)",
        (books.pmc.pmc_id, books.owner),
    )

    first = books.repay(app_conn, "r-1", "60.00")
    assert books.repay(app_conn, "r-1", "60.00") == first  # a retry takes nothing more
    with pytest.raises(psycopg.errors.UniqueViolation, match="already repaid"):
        books.repay(app_conn, "r-1", "61.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="50.00 is owed back"):
        books.repay(app_conn, "r-2", "50.01")
    books.repay(app_conn, "r-3", "50.00")

    assert books.owed() == Decimal("0.00")
    assert balance(conn, books.owner) == Decimal("390.00")
    assert balance(conn, books.pmc.pmc_income) == Decimal("110.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="0.00 is owed back"):
        books.repay(app_conn, "r-4", "0.01")
    assert set(bank_sums(conn, books.pmc.pmc_id).values()) == {Decimal(0)}


def test_repaying_takes_disburse_and_names_the_staff_member(conn, app_conn, books):
    books.bounce(cover=uuid.uuid4())
    conn.execute(
        "SELECT trust_record_owner_contribution(%s, %s, 'in-1', 500.00, now(), NULL)",
        (books.pmc.pmc_id, books.owner),
    )
    clerk = add_staff(conn, books.pmc.pmc_id, "Clerk A")

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="repay the company"):
        books.repay(app_conn, "r-1", "10.00", staff=clerk)
    with (
        pytest.raises(psycopg.errors.InvalidParameterValue, match="name the staff member"),
        acting_as(app_conn, None),
    ):
        app_conn.execute(
            "SELECT trust_repay_company_advance(%s, %s, 10.00, 'r-2', now())",
            (books.pmc.pmc_id, books.owner),
        )
    assert books.owed() == Decimal("110.00")


def test_a_repayment_never_takes_the_owner_below_zero(app_conn, books):
    books.bounce(cover=uuid.uuid4())  # the account holds nothing now

    with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
        books.repay(app_conn, "r-1", "10.00")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_company_advances SET amount = 1",
        "DELETE FROM trust_company_advances",
        "UPDATE trust_company_advance_repayments SET amount = 1",
        "DELETE FROM trust_company_advance_repayments",
    ],
)
def test_covers_and_repayments_are_append_only_even_for_the_owner(conn, books, statement):
    books.bounce(cover=uuid.uuid4())
    with pytest.raises(psycopg.Error):
        conn.execute(statement)


def test_the_ai_reads_covers_and_repayments_and_calls_none_of_it(ai_conn, books):
    books.bounce(cover=uuid.uuid4())

    assert ai_conn.execute("SELECT count(*) FROM trust_company_advances").fetchone()[0] >= 1
    ai_conn.execute("SELECT count(*) FROM trust_company_advance_repayments")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute(
            "SELECT trust_cover_returned_payment(%s, %s, 'ai', now())",
            (books.pmc.pmc_id, books.payment),
        )
