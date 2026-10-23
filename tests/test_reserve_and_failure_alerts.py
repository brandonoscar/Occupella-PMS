"""Two more emails the PMS writes by itself (migration 20261022000150, src/pms/notices.py,
src/pms/email.py, src/pms/nightly.py; the founder, 2026-10-22: q054 and q149): an owner hears once
each time one of their property accounts falls below its reserve, for a company that turned it
on; a company's admins hear when its nightly run failed or an email was given up on. Each only
while the server sends email. Every name and address is synthetic."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_staff,
    change_role,
    contribute,
    enter_bill,
    make_pmc,
    set_aside_bill,
)

from pms import email, nightly, notices

URL = "https://pms.example"
DAY = timedelta(days=1)
OCT_10 = date(2026, 10, 10)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def turn_on(conn, pmc_id, on=True):
    conn.execute(
        "INSERT INTO trust_email_settings (pmc_id, late_notices, work_order_notices,"
        " adverse_action_notices, reserve_alerts) VALUES (%s, false, false, false, %s)",
        (pmc_id, on),
    )


def contact(conn, pmc_id, owner, address):
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, owner_id, email) VALUES (%s, %s, %s)",
        (pmc_id, owner, address),
    )


def spend(conn, pmc, account, reference, amount, on):
    """A bill set aside from the owner's account: its balance goes down by the amount."""
    bill = enter_bill(conn, pmc.pmc_id, pmc.vendor_id, account, reference, on, on, amount)
    set_aside_bill(conn, pmc.pmc_id, bill, datetime.combine(on, datetime.min.time(), UTC))


def events(conn, account):
    return conn.execute(
        "SELECT number, kind, found_on, balance::text, reserve::text FROM trust_reserve_events"
        " WHERE ledger_account_id = %s ORDER BY number",
        (account,),
    ).fetchall()


def reserve_emails(conn, pmc_id):
    return conn.execute(
        "SELECT e.to_address, e.subject, e.body, e.owner_id, r.number FROM trust_emails AS e"
        " JOIN trust_reserve_events AS r ON r.id = e.reserve_event_id"
        " WHERE e.pmc_id = %s AND e.kind = 'reserve_low' ORDER BY r.number",
        (pmc_id,),
    ).fetchall()


def funded(conn, pmc, reserve="500.00", held="600.00"):
    """The first owner's account holds `held` against a reserve of `reserve`, the owner has an
    email, and the company turned the alerts on."""
    first = pmc.owners[0]
    add_agreement(
        conn, pmc.pmc_id, first.account, date(2026, 1, 1), reserve=reserve, approval_limit="1000"
    )
    contribute(conn, pmc.pmc_id, first.account, "c-1", held, datetime(2026, 9, 1, tzinfo=UTC))
    contact(conn, pmc.pmc_id, first.owner_id, "owner.one@example.com")
    turn_on(conn, pmc.pmc_id)
    return first


# --- an owner's account below its reserve -------------------------------------------------------


def test_an_owner_hears_once_each_time_an_account_falls_below_its_reserve(conn, app_conn, pmc):
    first = funded(conn, pmc)
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL) == 0  # 600 of 500

    spend(conn, pmc, first.account, "B-1", "200.00", OCT_10)  # 400 of 500
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 + DAY, URL) == 1
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 + 2 * DAY, URL) == 0  # once
    spend(conn, pmc, first.account, "B-2", "50.00", OCT_10)  # lower still: the same drop
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 + 3 * DAY, URL) == 0

    contribute(conn, pmc.pmc_id, first.account, "c-2", "150.00", datetime(2026, 10, 14, tzinfo=UTC))
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 + 4 * DAY, URL) == 0  # back
    spend(conn, pmc, first.account, "B-3", "0.01", OCT_10)  # a cent below: a drop of its own
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 + 5 * DAY, URL) == 1

    assert events(conn, first.account) == [
        (1, "below", OCT_10 + DAY, "400.00", "500.00"),
        (2, "back", OCT_10 + 4 * DAY, "500.00", "500.00"),
        (3, "below", OCT_10 + 5 * DAY, "499.99", "500.00"),
    ]
    (to, subject, body, owner, number), second = reserve_emails(conn, pmc.pmc_id)
    assert (to, subject, owner, number) == (
        "owner.one@example.com",
        "Property 1: your account is below its reserve",
        first.owner_id,
        1,
    )
    assert body.startswith(
        "Hello Owner 1,\n\nYour account for Property 1 holds $400.00 as of 10/11/2026, below the"
        " reserve of $500.00 your management agreement keeps in it for bills.\n$100.00 would"
        " bring it back up. Send it to Synthetic PMC"
    )
    assert "owner portal" not in body  # no portal login
    assert "$0.01 would bring it back up" in second[2] and second[4] == 3


def test_a_higher_reserve_from_a_day_is_a_drop_too(conn, app_conn, pmc):
    first = funded(conn, pmc)
    add_agreement(conn, pmc.pmc_id, first.account, OCT_10 + DAY, reserve="750.00")

    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL) == 0
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 + DAY, URL) == 1
    assert events(conn, first.account) == [(1, "below", OCT_10 + DAY, "600.00", "750.00")]


def test_no_alert_without_a_reserve_or_an_email_and_none_for_the_archived(conn, app_conn, pmc):
    first, second = pmc.owners
    turn_on(conn, pmc.pmc_id)
    # No agreement, then one with no reserve: an empty account is not below anything.
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL) == 0
    add_agreement(conn, pmc.pmc_id, first.account, date(2026, 1, 1))
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL) == 0
    # The second owner has no email: their drop is found, and nobody is written to.
    add_agreement(conn, pmc.pmc_id, second.account, date(2026, 1, 1), reserve="100.00")
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL) == 0
    assert [e[1] for e in events(conn, second.account)] == ["below"]
    assert events(conn, first.account) == []
    # An archived owner's account is looked at no more.
    other = make_pmc(conn)
    add_agreement(conn, other.pmc_id, other.owners[0].account, date(2026, 1, 1), reserve="5.00")
    contact(conn, other.pmc_id, other.owners[0].owner_id, "owner.three@example.com")
    turn_on(conn, other.pmc_id)
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, owner_id, archived) VALUES (%s, %s, true)",
        (other.pmc_id, other.owners[0].owner_id),
    )
    assert notices.write_reserve_alerts(app_conn, other.pmc_id, OCT_10, URL) == 0
    assert events(conn, other.owners[0].account) == []


def test_a_look_on_a_day_before_the_last_one_changes_nothing(conn, app_conn, pmc):
    first = funded(conn, pmc, held="100.00")
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL) == 1
    contribute(conn, pmc.pmc_id, first.account, "c-2", "900.00", datetime(2026, 10, 1, tzinfo=UTC))

    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10 - DAY, URL) == 0
    assert [e[1] for e in events(conn, first.account)] == ["below"]
    notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL)
    assert [e[1] for e in events(conn, first.account)] == ["below", "back"]


def test_the_night_looks_only_when_turned_on_and_the_server_sends_email(conn, app_conn, pmc):
    first = funded(conn, pmc, held="100.00")
    turn_on(conn, pmc.pmc_id, on=False)

    assert nightly.run_pmc(app_conn, pmc.pmc_id, OCT_10, URL, sending=True).emails == 0
    turn_on(conn, pmc.pmc_id)
    assert nightly.run_pmc(app_conn, pmc.pmc_id, OCT_10, URL).emails == 0  # sends no email
    assert events(conn, first.account) == []
    assert nightly.run_pmc(app_conn, pmc.pmc_id, OCT_10, URL, sending=True).emails == 1
    assert notices.write_scheduled(app_conn, pmc.pmc_id, OCT_10, URL) == notices.Written()


def test_reserve_events_follow_one_another_and_are_kept_for_good(conn, pmc):
    first, theirs = pmc.owners[0], make_pmc(conn).owners[0].account
    insert = (
        "INSERT INTO trust_reserve_events"
        " (pmc_id, ledger_account_id, number, kind, found_on, balance, reserve)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    for row, refused in (
        ((first.account, 2, "back", OCT_10, 5, 0), psycopg.errors.CheckViolation),  # no drop
        ((first.account, 1, "back", OCT_10, 5, 0), psycopg.errors.CheckViolation),  # first: drop
        ((first.account, 1, "below", OCT_10, 5, 5), psycopg.errors.CheckViolation),  # not below
        ((first.account, 1, "below", OCT_10, 5, "5.001"), psycopg.errors.CheckViolation),
        ((pmc.operating_cash, 1, "below", OCT_10, 0, 5), psycopg.errors.CheckViolation),
        ((theirs, 1, "below", OCT_10, 0, 5), psycopg.errors.ForeignKeyViolation),
    ):  # fmt: skip
        with conn.transaction(), pytest.raises(refused):
            conn.execute(insert, (pmc.pmc_id, *row))

    admin = add_staff(conn, pmc.pmc_id, "Admin 1")
    with acting_as(conn, admin):
        conn.execute(insert, (pmc.pmc_id, first.account, 1, "below", OCT_10, 5, 10))
    with conn.transaction(), pytest.raises(psycopg.errors.CheckViolation, match="follows no"):
        conn.execute(insert, (pmc.pmc_id, first.account, 2, "back", OCT_10 - DAY, 10, 10))
    conn.execute(insert, (pmc.pmc_id, first.account, 2, "back", OCT_10, 10, 10))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (pmc.pmc_id, first.account, 2, "back", OCT_10, 10, 10))
    for query in (
        "UPDATE trust_reserve_events SET balance = 0",
        "DELETE FROM trust_reserve_events",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_reserve_events'"
        " AND pmc_id = %s ORDER BY id",
        (pmc.pmc_id,),
    ).fetchall() == [(admin,), (None,)]


# --- what failed, to the company's admins --------------------------------------------------------


def login(conn, pmc_id, staff, address, accepted=True):
    made = conn.execute(
        "INSERT INTO trust_logins (pmc_id, kind, staff_id, email) VALUES (%s, 'staff', %s, %s)"
        " RETURNING id",
        (pmc_id, staff, address),
    ).fetchone()[0]
    if accepted:
        conn.execute(
            "INSERT INTO trust_login_secrets (login_id, pmc_id, password_hash)"
            " VALUES (%s, %s, '$argon2id$synthetic')",
            (made, pmc_id),
        )
    return made


def admins(conn, pmc_id):
    """Two admins with logins, an admin invited who never signed in, one whose login was
    disabled, and a staff member who is no admin."""
    named = {}
    for name, address, is_admin, accepted, disabled in (
        ("Admin 1", "admin.one@example.com", True, True, False),
        ("Admin 2", "admin.two@example.com", True, True, False),
        ("Admin 3", "admin.three@example.com", True, False, False),
        ("Admin 4", "admin.four@example.com", True, True, True),
        ("Clerk 1", "clerk.one@example.com", False, True, False),
    ):
        staff = add_staff(conn, pmc_id, name)
        if is_admin:
            change_role(conn, pmc_id, staff, "admin", by=named.get("Admin 1"))
        made = login(conn, pmc_id, staff, address, accepted)
        if disabled:
            conn.execute("UPDATE trust_logins SET disabled_at = now() WHERE id = %s", (made,))
        named[name] = staff
    return named


def alerts(conn, pmc_id):
    return conn.execute(
        "SELECT e.to_address, e.subject, e.body, a.kind, a.day, a.what, a.staff_id"
        " FROM trust_emails AS e JOIN trust_admin_alerts AS a ON a.id = e.alert_id"
        " WHERE e.pmc_id = %s AND e.kind = 'admin_alert' ORDER BY e.to_address COLLATE \"C\"",
        (pmc_id,),
    ).fetchall()


class Broken(Exception):
    pass


def failing_run(monkeypatch, failing_pmc):
    """The run of one PMC fails as a bug would, the others as they always do."""
    real = nightly.run_pmc

    def run(conn, pmc_id, day, url, sending=False, texting=False):
        if pmc_id == failing_pmc:
            raise Broken("the rent run could not finish\nsecond line, left out")
        return real(conn, pmc_id, day, url, sending, texting)

    monkeypatch.setattr(nightly, "run_pmc", run)


def test_each_admin_hears_once_a_day_when_the_nightly_run_fails(
    conn, app_conn, pmc, monkeypatch, caplog
):
    staff = admins(conn, pmc.pmc_id)
    other = make_pmc(conn)
    failing_run(monkeypatch, pmc.pmc_id)

    with caplog.at_level(logging.ERROR, logger="pms.nightly"):
        done = nightly.run_all(app_conn, OCT_10, URL, [pmc.pmc_id, other.pmc_id], sending=True)
        nightly.run_all(app_conn, OCT_10, URL, [pmc.pmc_id], sending=True)  # again that day

    assert [r.pmc_id for r in done] == [other.pmc_id]  # the others still run
    assert f"the nightly run of PMC {pmc.pmc_id} failed" in caplog.text
    told = alerts(conn, pmc.pmc_id)
    assert [(to, kind, day, staff_id) for to, _, _, kind, day, _, staff_id in told] == [
        ("admin.one@example.com", "nightly_failed", OCT_10, staff["Admin 1"]),
        ("admin.two@example.com", "nightly_failed", OCT_10, staff["Admin 2"]),
    ]
    to, subject, body, _, _, what, _ = told[0]
    assert what == "Broken: the rent run could not finish"
    assert subject.endswith(": the nightly run of 10/10/2026 failed")
    assert body.startswith("Hello Admin 1,\n\nThe nightly run of 10/10/2026 failed for Synthetic")
    assert "What failed: Broken: the rent run could not finish\n" in body
    assert f"{URL}/rent-roll" in body and "docs/support.md" in body
    # The next day's failure is told again.
    nightly.run_all(app_conn, OCT_10 + DAY, URL, [pmc.pmc_id], sending=True)
    assert len(alerts(conn, pmc.pmc_id)) == 4


def test_no_alert_while_the_server_sends_no_email(conn, app_conn, pmc, monkeypatch):
    admins(conn, pmc.pmc_id)
    failing_run(monkeypatch, pmc.pmc_id)

    nightly.run_all(app_conn, OCT_10, URL, [pmc.pmc_id])

    assert alerts(conn, pmc.pmc_id) == []


def test_a_database_error_is_told_too_and_a_company_with_no_admin_gets_none(
    conn, app_conn, pmc, monkeypatch
):
    admins(conn, pmc.pmc_id)
    lonely = make_pmc(conn)
    real = nightly.run_pmc
    # Each company's run refused by the database (a run of no company can't be recorded).
    monkeypatch.setattr(
        nightly,
        "run_pmc",
        lambda c, _pmc, day, url, sending=False, texting=False: real(c, uuid.uuid4(), day, url),
    )

    nightly.run_all(app_conn, OCT_10, URL, [pmc.pmc_id, lonely.pmc_id], sending=True)

    told = alerts(conn, pmc.pmc_id)
    assert len(told) == 2
    assert told[0][5].split(":")[0] in {"ForeignKeyViolation", "RaiseException", "CheckViolation"}
    assert alerts(conn, lonely.pmc_id) == []


class Failing:
    name = "smtp"

    def __init__(self, error="Connection unexpectedly closed"):
        self.error = error

    def send(self, message):
        if message.to.startswith("admin."):
            return None  # the admins' own mail goes through
        raise OSError(self.error)


def waiting_email(conn, pmc_id, to="owner.one@example.com"):
    """An email from staff waiting in the outbox."""
    staff = add_staff(conn, pmc_id, "Writer 1")
    return email.write_message(conn, pmc_id, "owner", _owner(conn, pmc_id), to, "Hi", "Hi", staff)


def _owner(conn, pmc_id):
    return conn.execute(
        "SELECT id FROM trust_owners WHERE pmc_id = %s ORDER BY display_name LIMIT 1", (pmc_id,)
    ).fetchone()[0]


def attempts(conn, email_id):
    return conn.execute(
        "SELECT count(*) FROM trust_email_deliveries WHERE email_id = %s", (email_id,)
    ).fetchone()[0]


def test_admins_hear_once_a_day_when_an_email_is_given_up_on(conn, app_conn, pmc, caplog):
    admins(conn, pmc.pmc_id)
    first = waiting_email(conn, pmc.pmc_id)
    second = waiting_email(conn, pmc.pmc_id, "o@example.com")
    failing = Failing()

    with caplog.at_level(logging.ERROR, logger="pms.email"):
        for _ in range(email.MAX_ATTEMPTS - 1):
            email.deliver_pending(app_conn, failing, limit=100_000)
        assert alerts(conn, pmc.pmc_id) == []  # not yet given up on
        email.deliver_pending(app_conn, failing, limit=100_000)

    assert attempts(conn, first) == attempts(conn, second) == email.MAX_ATTEMPTS
    assert f"email {first} (message) of PMC {pmc.pmc_id} was given up on" in caplog.text
    told = alerts(conn, pmc.pmc_id)
    assert [(to, kind) for to, _, _, kind, *_ in told] == [
        ("admin.one@example.com", "deliveries_failing"),
        ("admin.two@example.com", "deliveries_failing"),
    ]  # two emails given up on, told once
    _, subject, body, _, day, what, _ = told[0]
    assert subject.endswith(": emails are not going out")
    assert day == datetime.now(UTC).date()
    assert what == (
        'The last attempt, to owner.one@example.com ("Hi"), said: OSError: Connection'
        " unexpectedly closed"
    )
    assert f"failed {email.MAX_ATTEMPTS} times and was given up on. {what}\n" in body
    # The alerts themselves go (the sender takes the admins' mail).
    email.deliver_pending(app_conn, failing, limit=100_000)
    assert conn.execute(
        "SELECT count(*) FROM trust_email_deliveries AS d JOIN trust_emails AS e"
        " ON e.id = d.email_id WHERE e.pmc_id = %s AND e.kind = 'admin_alert'"
        " AND d.error IS NULL",
        (pmc.pmc_id,),
    ).fetchone() == (2,)


def test_an_alert_that_cant_be_written_leaves_the_log_and_the_rest_as_they_were(
    conn, app_conn, pmc, monkeypatch, caplog
):
    admins(conn, pmc.pmc_id)
    waiting = waiting_email(conn, pmc.pmc_id)
    failing_run(monkeypatch, pmc.pmc_id)

    def refused(*args, **kwargs):
        raise psycopg.errors.InsufficientPrivilege("synthetic: may not write")

    monkeypatch.setattr(email, "write_admin_alert", refused)
    with caplog.at_level(logging.ERROR):
        nightly.run_all(app_conn, OCT_10, URL, [pmc.pmc_id], sending=True)
        for _ in range(email.MAX_ATTEMPTS):
            email.deliver_pending(app_conn, Failing(), limit=100_000)

    assert f"the admins of PMC {pmc.pmc_id} could not be told its nightly run failed" in caplog.text
    assert f"the admins of PMC {pmc.pmc_id} could not be told an email was given up on" in (
        caplog.text
    )
    # Every attempt is recorded all the same, and nothing was told.
    assert attempts(conn, waiting) == email.MAX_ATTEMPTS
    assert alerts(conn, pmc.pmc_id) == []


def test_an_alert_given_up_on_is_told_to_nobody(conn, app_conn, pmc):
    staff = admins(conn, pmc.pmc_id)

    class Down:
        name = "smtp"

        def send(self, message):
            raise OSError("mail server down")

    with conn.transaction():
        email.write_admin_alert(conn, pmc.pmc_id, "nightly_failed", OCT_10, "Broken: no", URL)
    for _ in range(email.MAX_ATTEMPTS + 1):
        email.deliver_pending(app_conn, Down(), limit=100_000)

    # Each admin with a login was told of the night; its emails failing are told to nobody.
    assert [(kind, staff_id) for *_, kind, _, _, staff_id in alerts(conn, pmc.pmc_id)] == [
        ("nightly_failed", staff["Admin 1"]),
        ("nightly_failed", staff["Admin 2"]),
    ]


def test_alerts_are_kept_for_good_audited_and_each_once(conn, pmc):
    staff = admins(conn, pmc.pmc_id)
    insert = (
        "INSERT INTO trust_admin_alerts (pmc_id, staff_id, kind, day, what)"
        " VALUES (%s, %s, %s, %s, %s)"
    )
    for row, refused in (
        (("nightly_failed", OCT_10, " "), psycopg.errors.CheckViolation),
        (("nightly_failed", OCT_10, "x" * 1001), psycopg.errors.CheckViolation),
        (("bad_weather", OCT_10, "rain"), psycopg.errors.CheckViolation),
    ):
        with conn.transaction(), pytest.raises(refused):
            conn.execute(insert, (pmc.pmc_id, staff["Admin 1"], *row))
    elsewhere = make_pmc(conn).pmc_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(insert, (elsewhere, staff["Admin 1"], "nightly_failed", OCT_10, "x"))
    conn.execute(insert, (pmc.pmc_id, staff["Admin 1"], "nightly_failed", OCT_10, "x"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (pmc.pmc_id, staff["Admin 1"], "nightly_failed", OCT_10, "y"))
    for query in ("UPDATE trust_admin_alerts SET what = 'z'", "DELETE FROM trust_admin_alerts"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_admin_alerts'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (1,)


# --- the outbox's rules for the two kinds ----------------------------------------------------


def test_each_new_kind_names_what_it_is_about_and_nothing_else(conn, app_conn, pmc):
    first = funded(conn, pmc, held="100.00")
    notices.write_reserve_alerts(app_conn, pmc.pmc_id, OCT_10, URL)
    ((drop,),) = conn.execute(
        "SELECT id FROM trust_reserve_events WHERE ledger_account_id = %s", (first.account,)
    ).fetchall()
    staff = admins(conn, pmc.pmc_id)
    owner, admin = first.owner_id, staff["Admin 1"]
    alert = conn.execute(
        "INSERT INTO trust_admin_alerts (pmc_id, staff_id, kind, day, what)"
        " VALUES (%s, %s, 'nightly_failed', %s, 'x') RETURNING id",
        (pmc.pmc_id, staff["Admin 1"], OCT_10),
    ).fetchone()[0]
    columns = "pmc_id, kind, to_address, subject, body"
    for kind, about in (
        ("reserve_low", {"owner_id": owner}),  # no drop
        ("reserve_low", {"reserve_event_id": drop}),  # no owner
        ("reserve_low", {"owner_id": owner, "reserve_event_id": drop, "staff_id": admin}),
        ("admin_alert", {}),  # no alert
        ("admin_alert", {"alert_id": alert, "owner_id": owner}),
        ("admin_alert", {"alert_id": alert, "staff_id": admin}),
        ("owner_statement", {"owner_id": owner, "period": date(2026, 9, 1), "alert_id": alert}),
        ("message", {"owner_id": owner, "reserve_event_id": drop}),
    ):  # fmt: skip
        names = ", ".join(about)
        with conn.transaction(), pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                f"INSERT INTO trust_emails ({columns}{', ' if about else ''}{names})"
                f" VALUES (%s, %s, 'a@example.com', 'S', 'B'{', %s' * len(about)})",
                (pmc.pmc_id, kind, *about.values()),
            )
    # Each once.
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            f"INSERT INTO trust_emails ({columns}, owner_id, reserve_event_id)"
            " VALUES (%s, 'reserve_low', 'a@example.com', 'S', 'B', %s, %s)",
            (pmc.pmc_id, first.owner_id, drop),
        )
    conn.execute(
        f"INSERT INTO trust_emails ({columns}, alert_id)"
        " VALUES (%s, 'admin_alert', 'a@example.com', 'S', 'B', %s)",
        (pmc.pmc_id, alert),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            f"INSERT INTO trust_emails ({columns}, alert_id)"
            " VALUES (%s, 'admin_alert', 'a@example.com', 'S', 'B', %s)",
            (pmc.pmc_id, alert),
        )
    # Of its own company.
    unsent = conn.execute(
        "INSERT INTO trust_admin_alerts (pmc_id, staff_id, kind, day, what)"
        " VALUES (%s, %s, 'nightly_failed', %s, 'x') RETURNING id",
        (pmc.pmc_id, staff["Admin 2"], OCT_10),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            f"INSERT INTO trust_emails ({columns}, alert_id)"
            " VALUES (%s, 'admin_alert', 'a@example.com', 'S', 'B', %s)",
            (make_pmc(conn).pmc_id, unsent),
        )


def test_the_kinds_allowed_before_are_allowed_still(conn):
    (check,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'trust_emails_kind_check' AND conrelid = 'trust_emails'::regclass"
    ).fetchone()
    for kind in (
        "bill_approval", "invite", "password_reset", "message", "rent_reminder", "late_notice",
        "owner_statement", "work_order", "adverse_action", "emergency_request", "signing_request",
        "reserve_low", "admin_alert",
    ):  # fmt: skip
        assert f"'{kind}'::text" in check
