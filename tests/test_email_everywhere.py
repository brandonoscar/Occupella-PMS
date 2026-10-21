"""Email beyond a bill's approval (migration 20261017000076, src/pms/email.py,
docs/adr/0007-email.md): invites, password resets and staff messages in the outbox, each naming
whom it went to; an invite's or a reset's sign-in link kept only in memory until it is sent.
Every name and address here is synthetic."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from helpers import add_staff, make_pmc

from pms import accounts, email

URL = "https://pms.example"
WEEK = datetime(2026, 1, 8, tzinfo=UTC)


class Kept:
    """A sender that keeps what it is handed, to read in the test."""

    name = "fake"

    def __init__(self):
        self.sent: list[email.Message] = []

    def send(self, message):
        self.sent.append(message)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def rows(conn, pmc):
    return conn.execute(
        "SELECT kind, tenant_id, owner_id, vendor_id, staff_id, sent_by, needs_link, body"
        " FROM trust_emails WHERE pmc_id = %s ORDER BY created_at, id",
        (pmc.pmc_id,),
    ).fetchall()


def attempts(conn, email_id):
    return conn.execute(
        "SELECT sender, error FROM trust_email_deliveries WHERE email_id = %s"
        " ORDER BY attempted_at",
        (email_id,),
    ).fetchall()


def deliver_only(app_conn, sender, made):
    """Delivers what waits (every PMC's, as a server does) and returns what this test's emails
    were handed as."""
    email.deliver_pending(app_conn, sender, limit=1000)
    return [m for m in sender.sent if m.to in made]


# --- invites and resets ---------------------------------------------------------------------


def test_an_invite_email_carries_its_link_only_when_sent(conn, app_conn, pmc):
    owner = pmc.owners[0].owner_id
    invite = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", owner, "a.owner@example.com", None
    )
    link = f"{URL}/invite#{invite.token}"
    made = email.write_invite(
        app_conn, pmc.pmc_id, "owner", owner, "a.owner@example.com", link, invite.expires_at
    )

    ((kind, _, owner_id, _, _, by, needs_link, body),) = rows(conn, pmc)
    assert (kind, owner_id, by, needs_link) == ("invite", owner, None, True)
    assert invite.token not in body and email.LINK in body  # the outbox never holds the link

    sender = Kept()
    (sent,) = deliver_only(app_conn, sender, {"a.owner@example.com"})
    assert link in sent.body and email.LINK not in sent.body
    assert sent.from_name.startswith("Synthetic PMC")
    assert sent.reply_to == ""  # no reply-to on file: replies go to the From address
    assert attempts(conn, made) == [("fake", None)]
    assert deliver_only(app_conn, Kept(), {"a.owner@example.com"}) == []  # sent once


def test_replies_go_to_the_companys_own_email_when_it_has_one(conn, app_conn, pmc):
    conn.execute(
        "INSERT INTO trust_company_profiles (pmc_id, reply_to) VALUES (%s, 'office@example.com')",
        (pmc.pmc_id,),
    )
    owner = pmc.owners[0].owner_id
    invite = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", owner, "b.owner@example.com", None
    )
    email.write_invite(
        app_conn, pmc.pmc_id, "owner", owner, "b.owner@example.com", "x", invite.expires_at
    )

    (sent,) = deliver_only(app_conn, Kept(), {"b.owner@example.com"})
    assert sent.reply_to == "office@example.com"


def test_a_staff_invite_says_an_authenticator_comes_next(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "Staff Synthetic")
    invite = accounts.create_invite(
        app_conn, pmc.pmc_id, "staff", staff, "s.staff@example.com", None
    )
    email.write_invite(
        app_conn, pmc.pmc_id, "staff", staff, "s.staff@example.com", "x", invite.expires_at
    )
    ((kind, _, _, _, staff_id, _, _, body),) = rows(conn, pmc)
    assert (kind, staff_id) == ("invite", staff)
    assert "authenticator app" in body and "Occupella PMS" in body


def test_a_link_another_process_holds_is_left_to_it_then_given_up_on_once(
    conn, app_conn, pmc, monkeypatch
):
    tenant = tenant_of(conn, pmc)
    made = email.write_password_reset(
        app_conn, pmc.pmc_id, "tenant", tenant, "t.tenant@example.com", "secret-link", WEEK
    )
    email._drop_link(made)  # as if another process wrote it and holds its link

    assert deliver_only(app_conn, Kept(), {"t.tenant@example.com"}) == []
    assert attempts(conn, made) == []  # young: left to the process that holds the link

    monkeypatch.setattr(email, "LINK_MINUTES", 0)  # now it is older than a link is held
    assert deliver_only(app_conn, Kept(), {"t.tenant@example.com"}) == []
    assert attempts(conn, made) == [("fake", email.LINK_LOST)]
    deliver_only(app_conn, Kept(), {"t.tenant@example.com"})
    assert attempts(conn, made) == [("fake", email.LINK_LOST)]  # given up on once, not again


def test_a_link_is_held_no_longer_than_its_minutes(monkeypatch):
    first = uuid4()
    email.hold_link(first, "one")
    assert email._held_link(first) == "one"
    monkeypatch.setattr(email, "LINK_MINUTES", 0)
    assert email._held_link(first) is None


def test_a_failed_send_keeps_the_link_for_the_next_try(conn, app_conn, pmc):
    tenant = tenant_of(conn, pmc)
    made = email.write_password_reset(
        app_conn, pmc.pmc_id, "tenant", tenant, "t2.tenant@example.com", "the-link", WEEK
    )

    class Down:
        name = "smtp"

        def send(self, message):
            raise OSError("network is down")

    email.deliver_pending(app_conn, Down(), limit=1000)
    sender = Kept()
    (sent,) = deliver_only(app_conn, sender, {"t2.tenant@example.com"})
    assert "the-link" in sent.body
    assert [error is None for _, error in attempts(conn, made)] == [False, True]


# --- messages -------------------------------------------------------------------------------


def test_a_message_is_signed_by_who_wrote_it_and_the_company(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "Writer Synthetic")
    vendor = pmc.vendor_id
    email.write_message(
        app_conn, pmc.pmc_id, "vendor", vendor, "v@example.com", "Gate code", "It is 0000.", staff
    )
    ((kind, _, _, vendor_id, _, by, needs_link, body),) = rows(conn, pmc)
    assert (kind, vendor_id, by, needs_link) == ("message", vendor, staff, False)
    assert body.startswith("It is 0000.\n\nWriter Synthetic\nSynthetic PMC")


def test_a_message_names_someone_of_the_company(conn, app_conn, pmc):
    other = make_pmc(conn)
    staff = add_staff(conn, pmc.pmc_id)
    with pytest.raises(LookupError):
        email.write_message(
            app_conn,
            pmc.pmc_id,
            "tenant",
            tenant_of(conn, other),
            "x@example.com",
            "Hi",
            "Hi",
            staff,
        )
    with pytest.raises(LookupError):
        email.write_message(
            app_conn,
            pmc.pmc_id,
            "tenant",
            tenant_of(conn, pmc),
            "x@example.com",
            "Hi",
            "Hi",
            add_staff(conn, other.pmc_id),
        )


# --- what the database holds ----------------------------------------------------------------


def insert(conn, pmc, **row):
    values = {
        "kind": "message",
        "to_address": "x@example.com",
        "subject": "s",
        "body": "b",
        "needs_link": False,
        **row,
    }
    columns = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    conn.execute(
        f"INSERT INTO trust_emails (pmc_id, {columns}) VALUES (%s, {marks})",
        (pmc.pmc_id, *values.values()),
    )


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"tenant_id": "tenant"}, id="a message names who wrote it"),
        pytest.param(
            {"tenant_id": "tenant", "owner_id": "owner", "sent_by": "staff"}, id="one person"
        ),
        pytest.param({"staff_id": "staff", "sent_by": "staff"}, id="no message to staff"),
        pytest.param(
            {"kind": "invite", "vendor_id": "vendor", "needs_link": True},
            id="no invite to a vendor",
        ),
        pytest.param(
            {"kind": "password_reset", "staff_id": "staff", "needs_link": True},
            id="no reset for staff",
        ),
        pytest.param({"kind": "invite", "tenant_id": "tenant"}, id="an invite has a link"),
        pytest.param(
            {"tenant_id": "tenant", "sent_by": "staff", "needs_link": True}, id="a message has none"
        ),
        pytest.param(
            {"kind": "notice", "tenant_id": "tenant", "sent_by": "staff"}, id="known kinds only"
        ),
        pytest.param(
            {"kind": "invite", "tenant_id": "tenant", "needs_link": True, "sent_by": "staff"},
            id="only a message names a writer",
        ),
    ],
)
def test_each_email_goes_to_whom_its_kind_allows(conn, pmc, row):
    ids = {
        "tenant": tenant_of(conn, pmc),
        "owner": pmc.owners[0].owner_id,
        "vendor": pmc.vendor_id,
        "staff": add_staff(conn, pmc.pmc_id),
    }
    resolved = {
        key: ids.get(value, value) if isinstance(value, str) else value
        for key, value in row.items()
    }
    with pytest.raises(psycopg.errors.CheckViolation):
        insert(conn, pmc, **resolved)


def test_each_kind_goes_to_whom_it_allows(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    tenant, owner = tenant_of(conn, pmc), pmc.owners[0].owner_id
    insert(conn, pmc, tenant_id=tenant, sent_by=staff)
    insert(conn, pmc, owner_id=owner, sent_by=staff)
    insert(conn, pmc, vendor_id=pmc.vendor_id, sent_by=staff)
    insert(conn, pmc, kind="invite", staff_id=staff, needs_link=True)
    insert(conn, pmc, kind="invite", owner_id=owner, needs_link=True)
    insert(conn, pmc, kind="password_reset", tenant_id=tenant, needs_link=True)
    assert len(rows(conn, pmc)) == 6


def test_an_email_names_people_of_its_own_company(conn, pmc):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert(conn, pmc, tenant_id=tenant_of(conn, other), sent_by=add_staff(conn, pmc.pmc_id))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert(conn, pmc, tenant_id=tenant_of(conn, pmc), sent_by=add_staff(conn, other.pmc_id))


def test_a_custom_role_may_allow_email(conn, pmc):
    role = conn.execute(
        "INSERT INTO trust_custom_roles (pmc_id, name) VALUES (%s, 'Mailer') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
        " VALUES (%s, %s, 'email', true)",
        (pmc.pmc_id, role),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
            " VALUES (%s, %s, 'texting', true)",
            (pmc.pmc_id, role),
        )


def test_the_link_is_never_kept_even_after_an_hour(conn, app_conn, pmc):
    tenant = tenant_of(conn, pmc)
    invite = accounts.create_invite(
        app_conn, pmc.pmc_id, "tenant", tenant, "late@example.com", None
    )
    email.write_invite(
        app_conn,
        pmc.pmc_id,
        "tenant",
        tenant,
        "late@example.com",
        f"{URL}/invite#{invite.token}",
        invite.expires_at + timedelta(hours=1),
    )
    stored = conn.execute(
        "SELECT string_agg(body || subject, '') FROM trust_emails WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()[0]
    assert invite.token not in stored
