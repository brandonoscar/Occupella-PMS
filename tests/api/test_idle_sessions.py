"""A session left alone ends (accounts.IDLE_MINUTES, migration 20261105000200): used within the
hour it goes on, up to its twelve hours; left alone for an hour it is signed out, and signing in
again starts a new one. Its last use moves forward at most every few minutes, in a table of its
own that the audit log leaves out."""

from people import PASSWORD, join_as_owner

from pms import accounts


def activity(conn, login_id):
    return conn.execute(
        "SELECT a.last_seen_at FROM trust_session_activity AS a"
        " JOIN trust_sessions AS s ON s.id = a.session_id"
        " WHERE s.login_id = %s AND s.ended_at IS NULL",
        (login_id,),
    ).fetchall()


def left_alone(conn, login_id, minutes):
    """As if the login's open session was last used `minutes` ago."""
    conn.execute(
        "UPDATE trust_session_activity AS a"
        " SET last_seen_at = now() - make_interval(mins => %s)"
        " FROM trust_sessions AS s"
        " WHERE s.id = a.session_id AND s.login_id = %s AND s.ended_at IS NULL",
        (minutes, login_id),
    )


def test_a_session_used_within_the_hour_goes_on(client, conn, app_conn, pmc):
    owner = join_as_owner(client, app_conn, pmc)
    assert client.get("/api/me").status_code == 200
    assert len(activity(conn, owner.login_id)) == 1

    left_alone(conn, owner.login_id, accounts.IDLE_MINUTES - 1)
    assert client.get("/api/me").status_code == 200
    # Using it moved its last use to now.
    (seen,) = activity(conn, owner.login_id)
    assert conn.execute("SELECT now() - %s < interval '1 minute'", seen).fetchone() == (True,)


def test_a_session_left_alone_for_the_hour_is_signed_out(client, conn, app_conn, pmc):
    owner = join_as_owner(client, app_conn, pmc)
    left_alone(conn, owner.login_id, accounts.IDLE_MINUTES + 1)

    assert client.get("/api/me").status_code == 401

    again = client.post(
        "/api/sign-in",
        json={"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": PASSWORD},
    )
    assert again.status_code == 200
    assert client.get("/api/me").status_code == 200


def test_its_last_use_moves_forward_only_every_few_minutes(client, conn, app_conn, pmc):
    owner = join_as_owner(client, app_conn, pmc)
    left_alone(conn, owner.login_id, accounts.TOUCH_MINUTES - 1)
    (before,) = activity(conn, owner.login_id)

    assert client.get("/api/me").status_code == 200
    assert activity(conn, owner.login_id) == [before]

    left_alone(conn, owner.login_id, accounts.TOUCH_MINUTES + 1)
    (stale,) = activity(conn, owner.login_id)
    assert client.get("/api/me").status_code == 200
    assert activity(conn, owner.login_id) != [stale]


def test_a_session_that_never_recorded_a_use_counts_from_its_start(client, conn, app_conn, pmc):
    """A session opened before migration 20261105000200 has no row yet: its start counts."""
    owner = join_as_owner(client, app_conn, pmc)
    conn.execute(
        "DELETE FROM trust_session_activity AS a USING trust_sessions AS s"
        " WHERE s.id = a.session_id AND s.login_id = %s",
        (owner.login_id,),
    )
    assert client.get("/api/me").status_code == 200  # started just now
    conn.execute(
        "DELETE FROM trust_session_activity AS a USING trust_sessions AS s"
        " WHERE s.id = a.session_id AND s.login_id = %s",
        (owner.login_id,),
    )
    conn.execute("ALTER TABLE trust_sessions DISABLE TRIGGER USER")
    try:
        conn.execute(
            "UPDATE trust_sessions SET created_at = now() - interval '2 hours'"
            " WHERE login_id = %s AND ended_at IS NULL",
            (owner.login_id,),
        )
    finally:
        conn.execute("ALTER TABLE trust_sessions ENABLE TRIGGER USER")
    assert client.get("/api/me").status_code == 401


def test_session_activity_is_left_out_of_the_audit_log(client, conn, app_conn, pmc):
    join_as_owner(client, app_conn, pmc)
    assert client.get("/api/me").status_code == 200
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_session_activity'"
    ).fetchone() == (0,)
