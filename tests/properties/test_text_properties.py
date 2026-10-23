"""Property-based tests for texts (src/pms/texts.py, migration 20261022000161).

Hypothesis gives a tenant any run of words on texts (staff's yes or no at either of two numbers,
the tenant's own in the portal, STOP and START replies from either number) with texts written
between them, and at the end delivers what is due:
  - a person's latest word is always what the model says, and the same word again writes nothing;
  - STOP stops only a yes at its own number, START takes back only a STOP at its own number;
  - a text is written exactly while the latest word says yes, to that word's number;
  - a delivery sends a text only while the word it was written under is still the latest, and
    gives up on every other.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime

import psycopg
from helpers import add_staff, fresh_phone, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import texts

NOW = datetime(2026, 3, 2, 17, 0, tzinfo=UTC)

steps = st.lists(
    st.one_of(
        st.tuples(st.just("staff"), st.integers(0, 1), st.booleans()),
        st.tuples(st.just("portal"), st.integers(0, 1), st.booleans()),
        st.tuples(st.just("reply"), st.integers(0, 1), st.sampled_from(["STOP", "start", "hi"])),
        st.tuples(st.just("text")),
    ),
    min_size=1,
    max_size=16,
)


class Kept:
    name = "fake"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to, body):
        self.sent.append((to, body))
        return f"fake-{len(self.sent)}"


@given(run=steps)
def check_texts(conn, run):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    staff = add_staff(conn, pmc.pmc_id)
    numbers = [fresh_phone(), fresh_phone()]
    latest: tuple[str, bool, str] | None = None  # the model: (number, yes, how)
    written: list[tuple[str, str]] = []  # (text id, number) the model expects
    for n, step in enumerate(run):
        if step[0] in ("staff", "portal"):
            phone, yes = numbers[step[1]], step[2]
            how = "in writing" if step[0] == "staff" else "portal"
            changed = latest is None or latest[:2] != (phone, yes)
            assert texts.record_consent(conn, pmc.pmc_id, "tenant", tenant, phone, yes, how) == (
                changed
            )
            if changed:
                latest = (phone, yes, how)
        elif step[0] == "reply":
            phone, word = numbers[step[1]], step[2]
            stops = word == "STOP" and latest is not None and latest[:2] == (phone, True)
            starts = (
                word == "start" and latest is not None and latest == (phone, False, "stop reply")
            )
            assert texts.reply(conn, phone, word) == int(stops or starts)
            if stops:
                latest = (phone, False, "stop reply")
                event("stopped")
            if starts:
                latest = (phone, True, "start reply")
                event("started again")
        else:
            made = texts.write(
                conn, pmc.pmc_id, "message", "tenant", tenant, f"Text {n}.", NOW, sent_by=staff
            )
            assert (made is not None) == (latest is not None and latest[1])
            if made is not None:
                assert latest is not None
                written.append((str(made), latest[0]))
        word = texts.consent_of(conn, pmc.pmc_id, "tenant", tenant)
        assert (None if word is None else (word.phone, word.consented, word.how)) == latest

    for text_id, phone in written:
        assert (
            conn.execute("SELECT to_phone FROM trust_texts WHERE id = %s", (text_id,)).fetchone()[0]
            == phone
        )
    kept = Kept()
    # The database is shared: hand over everything due, other tests' texts too, until none is.
    while texts.deliver_pending(conn, kept, NOW) != texts.Delivered(0, 0):
        pass
    current = texts.consent_of(conn, pmc.pmc_id, "tenant", tenant)
    for text_id, _ in written:
        consent_id, error = conn.execute(
            "SELECT t.consent_id, d.error FROM trust_texts AS t"
            " JOIN trust_text_deliveries AS d ON d.text_id = t.id WHERE t.id = %s",
            (text_id,),
        ).fetchone()
        went = current is not None and current.consented and current.id == consent_id
        assert error == (None if went else texts.STOPPED)
        event("sent" if went else "given up")


def test_a_text_goes_only_under_the_latest_yes_at_its_number(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_texts(conn)
