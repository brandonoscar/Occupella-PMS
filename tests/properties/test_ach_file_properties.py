"""Property-based tests for bank files (src/pms/ach.py, migration 20261022000156).

Hypothesis makes any payouts (owners' and vendors', any amounts, names in any characters) and
any runs of files made and voided:
  - read back record by record, a file is 94-character records in blocks of ten, its entries are
    the payouts given, once each, traced 1, 2, 3... in its batches' order, and every batch's and
    the file's count, hash and total are what its entries say;
  - in the database, a payout is in at most one file not voided, each file comes to its count and
    total, and what waits for a file is every EFT payout in none.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import contribute, draw_owner, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import ach, sealed
from pms.api import bank_details

ORIGINATOR = ach.Originator("001000009", "Demo Trust Bank", "1000000001", "Demo Property Co",
                            "1000000001")  # fmt: skip
ROUTINGS = ["002000008", "003000007", "009000001", "001000009"]

entries = st.builds(
    ach.Entry,
    sec=st.sampled_from(["PPD", "CCD"]),
    routing=st.sampled_from(ROUTINGS),
    account_type=st.sampled_from(["checking", "savings"]),
    account_number=st.text(alphabet="0123456789", min_size=4, max_size=17),
    amount=st.integers(min_value=1, max_value=99_999_999_99).map(lambda c: Decimal(c) / 100),
    identification=st.text(max_size=20),
    name=st.text(min_size=1, max_size=22),
)


@given(payouts=st.lists(entries, min_size=1, max_size=30), modifier=st.sampled_from("A0Z9"))
def test_a_file_reads_back_as_its_payouts(payouts, modifier):
    text = ach.build(ORIGINATOR, datetime(2026, 10, 23, 9, 30, tzinfo=UTC), modifier,
                     date(2026, 10, 26), payouts)  # fmt: skip
    lines = text.split("\r\n")
    assert lines.pop() == ""
    assert all(len(line) == 94 and line.isascii() for line in lines)
    assert len(lines) % 10 == 0
    # The 9s pad the last block only, after the file's control.
    end = next((i for i, line in enumerate(lines) if line == "9" * 94), len(lines))
    assert all(line == "9" * 94 for line in lines[end:]) and len(lines) - end < 10
    records = lines[:end]
    assert records[0][0] == "1" and records[0][33] == modifier
    file_control = records[-1]
    assert file_control[0] == "9"

    expected = [e for sec in ("PPD", "CCD") for e in payouts if e.sec == sec]
    seen = []
    batch = None
    traces = []
    for line in records[1:-1]:
        kind = line[0]
        if kind == "5":
            batch = {"sec": line[50:53], "number": line[87:94], "entries": []}
        elif kind == "6":
            assert batch is not None
            batch["entries"].append(line)
            seen.append((batch["sec"], line[3:12], line[12:29].rstrip(), int(line[29:39])))
            traces.append(int(line[87:94]))
            assert line[79:87] == "00100000"
        else:
            assert kind == "8" and batch is not None
            got = batch["entries"]
            assert int(line[4:10]) == len(got)
            assert int(line[10:20]) == sum(int(e[3:11]) for e in got) % 10**10
            assert int(line[20:32]) == 0
            assert int(line[32:44]) == sum(int(e[29:39]) for e in got)
            assert line[87:94] == batch["number"]
            batch = None
    assert seen == [(e.sec, e.routing, e.account_number, int(e.amount * 100)) for e in expected]
    assert traces == list(range(1, len(payouts) + 1))
    assert int(file_control[1:7]) == len({e.sec for e in payouts})
    assert int(file_control[7:13]) == len(lines) // 10
    assert int(file_control[13:21]) == len(payouts)
    assert int(file_control[21:31]) == sum(int(e.routing[:8]) for e in payouts) % 10**10
    assert int(file_control[43:55]) == sum(int(e.amount * 100) for e in payouts)


def weekday():
    day = datetime.now(UTC).date()
    while day.isoweekday() > 5:
        day += timedelta(days=1)
    return day


steps = st.lists(
    st.one_of(
        st.tuples(st.just("pay"), st.sampled_from(["EFT", "check"]), st.integers(1, 5000)),
        st.tuples(st.just("file"), st.lists(st.integers(0, 30), min_size=1, max_size=4)),
        st.tuples(st.just("void"), st.integers(0, 30)),
    ),
    min_size=1,
    max_size=14,
)


@given(run=steps)
def check_files(conn, run):
    pmc = make_pmc(conn, owners=1)
    owner = pmc.owners[0]
    settings = conn.execute(
        "INSERT INTO trust_ach_settings (pmc_id, bank_account_id, bank_routing, bank_name, origin,"
        " company_name, company_id) VALUES (%s, %s, '001000009', 'Demo Trust Bank', '1000000001',"
        " 'Demo Property Co', '1000000001') RETURNING id",
        (pmc.pmc_id, pmc.operating_bank_id),
    ).fetchone()[0]
    account = conn.execute(
        "INSERT INTO trust_payee_bank_accounts (pmc_id, owner_id, holder_name, routing_number,"
        " account_type, account_number_sealed, account_last4)"
        " VALUES (%s, %s, 'Demo Owner A', '002000008', 'checking', %s, '6789') RETURNING id",
        (
            pmc.pmc_id,
            owner.owner_id,
            sealed.seal("000123456789", bank_details.context(pmc.pmc_id, "owner", owner.owner_id)),
        ),
    ).fetchone()[0]
    payouts: list[tuple[str, str, Decimal]] = []  # transfer, method, amount
    live: dict[str, str] = {}  # payout -> the file not voided it is in (the model)
    files: list[str] = []
    voided: set[str] = set()
    for step in run:
        if step[0] == "pay":
            amount = Decimal(step[2]) / 100
            contribute(conn, pmc.pmc_id, owner.account, f"in/{uuid.uuid4()}", amount)
            key = f"draw/{uuid.uuid4()}"
            draw_owner(conn, pmc.pmc_id, owner.account, key, amount, datetime.now(UTC))
            (transfer,) = conn.execute(
                "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
                (pmc.pmc_id, key),
            ).fetchone()
            conn.execute(
                "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method)"
                " VALUES (%s, %s, %s)",
                (pmc.pmc_id, transfer, step[1]),
            )
            payouts.append((transfer, step[1], amount))
        elif step[0] == "file" and payouts:
            picked = list(dict.fromkeys(payouts[i % len(payouts)] for i in step[1]))
            ok = all(how == "EFT" and transfer not in live for transfer, how, _ in picked)
            try:
                with conn.transaction():
                    (file_id,) = conn.execute(
                        "INSERT INTO trust_ach_files (pmc_id, bank_account_id, settings_id,"
                        " effective_on, entries, total) VALUES (%s, %s, %s, %s, %s, %s)"
                        " RETURNING id",
                        (pmc.pmc_id, pmc.operating_bank_id, settings, weekday(), len(picked),
                         sum(a for _, _, a in picked)),
                    ).fetchone()  # fmt: skip
                    for sequence, (transfer, _, amount) in enumerate(picked, start=1):
                        conn.execute(
                            "INSERT INTO trust_ach_file_items (pmc_id, file_id, sequence,"
                            " transfer_id, payee_bank_account_id, amount)"
                            " VALUES (%s, %s, %s, %s, %s, %s)",
                            (pmc.pmc_id, file_id, sequence, transfer, account, amount),
                        )
            except psycopg.errors.CheckViolation:
                assert not ok
                event("a file refused")
            else:
                assert ok
                event("a file made")
                files.append(file_id)
                live.update({transfer: file_id for transfer, _, _ in picked})
        elif step[0] == "void" and files:
            file_id = files[step[1] % len(files)]
            try:
                conn.execute(
                    "INSERT INTO trust_ach_file_voids (pmc_id, file_id, reason)"
                    " VALUES (%s, %s, 'The bank refused it')",
                    (pmc.pmc_id, file_id),
                )
            except psycopg.errors.UniqueViolation:
                assert file_id in voided
            else:
                assert file_id not in voided
                event("a file voided")
                voided.add(file_id)
                live = {t: f for t, f in live.items() if f != file_id}

    in_files = conn.execute(
        "SELECT i.transfer_id, i.file_id FROM trust_ach_file_items AS i"
        " WHERE i.pmc_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_ach_file_voids AS v WHERE v.file_id = i.file_id)",
        (pmc.pmc_id,),
    ).fetchall()
    assert dict(in_files) == live
    assert len(in_files) == len(live)
    for file_id, entries, total, count, summed in conn.execute(
        "SELECT f.id, f.entries, f.total, count(i.id), sum(i.amount) FROM trust_ach_files AS f"
        " JOIN trust_ach_file_items AS i ON i.file_id = f.id WHERE f.pmc_id = %s GROUP BY f.id",
        (pmc.pmc_id,),
    ).fetchall():
        assert (entries, total) == (count, summed), file_id
    waiting = {
        t
        for (t,) in conn.execute(
            "SELECT m.transfer_id FROM trust_payout_methods AS m"
            " CROSS JOIN LATERAL trust_payout(m.pmc_id, m.transfer_id) AS p"
            " WHERE m.pmc_id = %s AND p.method = 'EFT'",
            (pmc.pmc_id,),
        ).fetchall()
    } - set(live)
    assert waiting == {t for t, how, _ in payouts if how == "EFT"} - set(live)


def test_a_payout_is_in_one_file_not_voided_and_files_come_to_their_totals(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_files(conn)
