"""A phone at sign-up (src/pms/phones.py, migration 20261104000700, the founder's review of
11/03, item 14): a tenant or an owner setting their portal password gives a phone, stored as E.164,
and says whether the company may text them, unchecked unless they check it; the answer is kept
with the exact words shown and when. Staff are asked neither. Every number here is fictional."""

import psycopg
import pytest
from helpers import add_staff
from people import PASSWORD, accept, invite_token, tenant_of

from pms import phones


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def consents(conn, column, person):
    return conn.execute(
        f"SELECT phone, texts_ok, wording FROM trust_text_consents WHERE {column} = %s"
        " ORDER BY created_at, id",
        (person,),
    ).fetchall()


def test_an_owner_gives_a_phone_says_texts_are_ok_and_the_words_shown_are_kept(
    client, conn, app_conn, pmc
):
    owner = pmc.owners[0].owner_id
    token = invite_token(app_conn, pmc.pmc_id, "owner", owner, "o@example.com")
    shown = ok(client.post("/api/invites/view", json={"token": token}))["text_consent"]

    ok(client.post("/api/invites/accept", json=accept(token, phone="555.555.0123", texts_ok=True)))

    assert shown.startswith("OK to text me. Synthetic PMC")
    assert "Reply STOP to stop" in shown
    assert consents(conn, "owner_id", owner) == [("+15555550123", True, shown)]
    # The phone they gave is their contact phone, given by them.
    assert conn.execute(
        "SELECT phone, by_person FROM trust_contacts WHERE owner_id = %s", (owner,)
    ).fetchall() == [("+15555550123", True)]


def test_a_tenant_who_leaves_the_box_unchecked_says_no(client, conn, app_conn, pmc):
    tenant = tenant_of(conn, pmc)
    token = invite_token(app_conn, pmc.pmc_id, "tenant", tenant, "t@example.com")

    ok(client.post("/api/invites/accept", json=accept(token, phone="1 (555) 555-0142")))

    [(phone, texts_ok, _)] = consents(conn, "tenant_id", tenant)
    assert (phone, texts_ok) == ("+15555550142", False)


def test_a_new_password_records_a_new_answer_and_the_latest_holds(client, conn, app_conn, pmc):
    owner = pmc.owners[0].owner_id
    first = invite_token(app_conn, pmc.pmc_id, "owner", owner, "o@example.com")
    ok(client.post("/api/invites/accept", json=accept(first, texts_ok=True)))
    again = invite_token(app_conn, pmc.pmc_id, "owner", owner, "o@example.com")

    ok(
        client.post(
            "/api/invites/accept",
            json=accept(again, "a brand new passphrase", phone="555-555-0177"),
        )
    )

    assert [(p, ok_) for p, ok_, _ in consents(conn, "owner_id", owner)] == [
        ("+15555550123", True),
        ("+15555550177", False),
    ]


def test_no_phone_is_refused_and_the_link_still_works(client, conn, app_conn, pmc):
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com")

    answers = [
        client.post("/api/invites/accept", json={"token": token, "password": PASSWORD}),
        client.post("/api/invites/accept", json=accept(token, phone="   ")),
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (422, "Enter your phone number.")
    ] * 2
    assert client.post("/api/invites/view", json={"token": token}).status_code == 200
    assert consents(conn, "owner_id", pmc.owners[0].owner_id) == []


@pytest.mark.parametrize(
    "phone",
    [
        "123",
        "call me",
        "(055) 555-0123",
        "5550550123",  # its exchange starts 0
        "+0 555 555 0123",
        "555 555 01234",
        "x" * 41,
    ],
)
def test_a_phone_that_isnt_one_is_refused(client, conn, app_conn, pmc, phone):
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com")

    answer = client.post("/api/invites/accept", json=accept(token, phone=phone))

    assert answer.status_code == 422
    assert client.get("/api/me").status_code == 401  # not signed in
    assert consents(conn, "owner_id", pmc.owners[0].owner_id) == []


def test_staff_are_asked_neither(client, conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "New Hire")
    token = invite_token(app_conn, pmc.pmc_id, "staff", staff, "new.hire@example.com")

    view = ok(client.post("/api/invites/view", json={"token": token}))
    enrolled = ok(client.post("/api/invites/accept", json={"token": token, "password": PASSWORD}))

    assert view["text_consent"] is None
    assert "totp_uri" in enrolled
    assert conn.execute(
        "SELECT count(*) FROM trust_text_consents WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_the_database_keeps_an_answer_to_the_logins_own_person(conn, app_conn, pmc):
    owner = pmc.owners[0].owner_id
    invite_token(app_conn, pmc.pmc_id, "owner", owner, "o@example.com")
    login = conn.execute("SELECT id FROM trust_logins WHERE owner_id = %s", (owner,)).fetchone()[0]

    with pytest.raises(psycopg.errors.CheckViolation, match="not this person"):
        app_conn.execute(
            "INSERT INTO trust_text_consents (pmc_id, login_id, owner_id, phone, texts_ok, wording)"
            " VALUES (%s, %s, %s, '+15555550123', true, 'OK')",
            (pmc.pmc_id, login, pmc.owners[1].owner_id),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        app_conn.execute(
            "INSERT INTO trust_text_consents (pmc_id, login_id, owner_id, phone, texts_ok, wording)"
            " VALUES (%s, %s, %s, '555-555-0123', true, 'OK')",
            (pmc.pmc_id, login, owner),
        )


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("(555) 555-0123", "+15555550123"),
        ("555.555.0123", "+15555550123"),
        ("1 555 555 0123", "+15555550123"),
        ("+1 (555) 555-0123", "+15555550123"),
        ("+44 20 7946 0000", "+442079460000"),
    ],
)
def test_a_phone_is_kept_as_e164(typed, kept):
    assert phones.e164(typed) == kept


@pytest.mark.parametrize(
    "typed", ["", "12345", "+1 055 555 0123", "+12345678901234567", "5551550123"]
)
def test_what_isnt_a_phone_says_how_to_write_one(typed):
    with pytest.raises(phones.BadPhone, match="with its area code"):
        phones.e164(typed)
