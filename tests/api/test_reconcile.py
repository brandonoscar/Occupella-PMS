"""Staff's month-end (src/pms/api/reconcile.py): importing statements, matching their lines to
the books, and approving a period. Every statement here is synthetic."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import approve, make_pmc, post
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
JAN_1, JAN_31 = date(2026, 1, 1), date(2026, 1, 31)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Reconciler 1")


def call(client, path, **body):
    return client.post(f"/api/reconcile/{path}", json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in answer.json()["detail"]


def csv(*rows):
    return "Date,Description,Amount,Reference\n" + "".join(
        f"{day},{what},{amount},{ref}\n" for day, what, amount, ref in rows
    )


def statement(client, pmc, content, kind="csv", **more):
    body = {"bank_account_id": pmc.operating_bank_id, "format": kind, "content": content} | more
    return call(client, "statements", **body)


def rent(conn, pmc, amount, day, memo="Rent"):
    (moved,) = post(
        conn,
        pmc.pmc_id,
        f"rent-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal(amount))],
        datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        {"memo": memo},
    )
    return moved


def sheet(client, pmc, through=None):
    query = f"?through={through}" if through else ""
    return ok(client.get(f"/api/reconcile/{pmc.operating_bank_id}{query}"), 200)


def bank(client, pmc):
    (found,) = [b for b in ok(client.get("/api/reconcile"), 200) if b["kind"] == "operating"]
    return found


# --- importing -------------------------------------------------------------------------------


def test_a_first_csv_statement_brings_its_opening_balance_in_as_a_line(client, conn, staff, pmc):
    imported = ok(
        statement(
            client,
            pmc,
            csv(
                ("2026-01-05", "Deposit", "1500.00", "D1"),
                ("01/23/2026", "Check 7", "(40.00)", "7"),
            ),
            first_day=JAN_1,
            last_day=JAN_31,
            opening_balance="250.00",
            closing_balance="1710.00",
        )
    )

    assert imported | {"id": None} == {
        "id": None,
        "first_day": "2026-01-01",
        "last_day": "2026-01-31",
        "lines": 3,
        "closing_balance": "1710.00",
    }
    (listed,) = bank(client, pmc)["statements"]
    assert (listed["opening_balance"], listed["closing_balance"], listed["source"]) == (
        "0.00",
        "1710.00",
        "csv",
    )
    lines = sheet(client, pmc)["open_lines"]
    assert [(ln["posted_on"], ln["amount"], ln["description"]) for ln in lines] == [
        ("2026-01-01", "250.00", "Balance brought forward"),
        ("2026-01-05", "1500.00", "Deposit"),
        ("2026-01-23", "-40.00", "Check 7"),
    ]


def test_the_next_statement_follows_the_last_by_default(client, conn, staff, pmc):
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-05", "Deposit", "100.00", "")),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )

    following = ok(statement(client, pmc, csv(("2026-02-03", "Fee", "-5.00", ""))))

    assert (following["first_day"], following["last_day"], following["closing_balance"]) == (
        "2026-02-01",
        "2026-02-03",
        "95.00",
    )
    assert [s["opening_balance"] for s in bank(client, pmc)["statements"]] == ["100.00", "0.00"]


def test_an_ofx_statement_gives_its_days_and_closing_balance(client, conn, staff, pmc):
    ofx = (
        "<OFX><DTSTART>20260101<DTEND>20260131"
        "<STMTTRN><DTPOSTED>20260105<TRNAMT>100.00<FITID>F1<NAME>Deposit</STMTTRN>"
        "<LEDGERBAL><BALAMT>350.00</LEDGERBAL></OFX>"
    )

    imported = ok(statement(client, pmc, ofx, kind="ofx"))

    assert (imported["first_day"], imported["last_day"], imported["lines"]) == (
        "2026-01-01",
        "2026-01-31",
        2,  # 250.00 brought forward, and the deposit
    )
    assert bank(client, pmc)["statements"][0]["source"] == "ofx"


@pytest.mark.parametrize(
    "more, status, why",
    [
        (
            {"closing_balance": "99.00"},
            422,
            "the lines take 0.00 to 100.00; the statement says 99.00",
        ),
        ({"first_day": "2026-01-06", "last_day": JAN_31}, 422, "a line on 2026-01-05 is outside"),
        ({"last_day": "2025-12-31"}, 422, "last day is on or after its first"),
        ({"content": "Date,Amount\n2026-01-01,abc\n"}, 422, "line 2: 'abc' is not an amount"),
        (
            {"content": "Date,Amount\n", "first_day": None},
            422,
            "say which days the statement covers",
        ),
        ({"last_day": str(TODAY + timedelta(days=1)), "first_day": str(TODAY)}, 422, "outside"),
    ],
)
def test_a_statement_that_does_not_add_up_or_fit_is_refused(client, staff, pmc, more, status, why):
    body = {"content": csv(("2026-01-05", "Deposit", "100.00", "")), "first_day": JAN_1} | more
    refused(statement(client, pmc, **body), status, why)
    assert bank(client, pmc)["statements"] == []


def test_a_statement_out_of_turn_twice_or_ahead_of_today_is_refused(client, staff, pmc):
    quiet = csv()  # a month with no lines
    ok(statement(client, pmc, quiet, first_day=JAN_1, last_day=JAN_31, opening_balance="100.00"))

    refused(
        statement(client, pmc, quiet, last_day="2026-02-28"),
        409,
        "imported for this trust bank account already",
    )
    refused(
        statement(client, pmc, quiet, first_day="2026-02-02", last_day="2026-02-28"),
        409,
        "starts on 2026-02-01",
    )
    refused(
        statement(client, pmc, quiet, last_day=str(TODAY + timedelta(days=1))),
        409,
        "days that have come",
    )
    refused(
        statement(client, pmc, quiet, last_day="2026-02-28", opening_balance="1.00"),
        409,
        "opens at 100.00",
    )


# --- the worksheet and matching ----------------------------------------------------------------


def test_the_worksheet_shows_the_report_and_what_no_match_holds(client, conn, staff, pmc):
    paid = rent(conn, pmc, "1500.00", date(2026, 1, 4), memo="Rent, Unit 1")
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-05", "Deposit", "1500.00", "D1")),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )

    shown = sheet(client, pmc)

    assert shown["through"] == "2026-01-31"
    assert {r["item"]: r["amount"] for r in shown["rows"] if r["line"] in (4, 5, 8, 9)} == {
        "bank balance (statements)": "1500.00",
        "deposits in transit": "1500.00",
        "trust journal (book cash)": "1500.00",
        "difference: adjusted bank balance - trust journal": "1500.00",
    }
    assert shown["open_transfers"] == [
        {"id": paid, "on": "2026-01-04", "amount": "1500.00", "memo": "Rent, Unit 1"}
    ]
    assert [ln["reference"] for ln in shown["open_lines"]] == ["D1"]
    assert shown["matches"] == []
    refused(
        client.get(f"/api/reconcile/{pmc.operating_bank_id}?through=2026-02-01"),
        422,
        "no statement of trust bank account",
    )


def test_a_worksheet_before_any_statement_lists_only_the_books(client, conn, staff, pmc):
    rent(conn, pmc, "10.00", date(2026, 1, 4))
    shown = sheet(client, pmc)
    assert (shown["through"], shown["rows"], len(shown["open_transfers"])) == (None, [], 1)


def test_lines_are_matched_to_transfers_and_undone(client, conn, staff, pmc):
    one = rent(conn, pmc, "1500.00", date(2026, 1, 4))
    two = rent(conn, pmc, "700.00", date(2026, 1, 4))
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-05", "Deposit", "2200.00", "D1")),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )
    (line,) = sheet(client, pmc)["open_lines"]
    path = f"{pmc.operating_bank_id}/matches"

    refused(
        call(client, path, line_ids=[line["id"]], transfer_ids=[one]),
        409,
        "the lines come to 2200.00 and the transfers and items to 1500.00",
    )
    made = ok(call(client, path, line_ids=[line["id"]], transfer_ids=[one, two]))
    shown = sheet(client, pmc)
    refused(
        call(client, path, line_ids=[line["id"]], transfer_ids=[one, two]), 409, "matched already"
    )

    assert (shown["open_lines"], shown["open_transfers"]) == ([], [])
    (standing,) = shown["matches"]
    assert standing["id"] == made["id"]
    assert sorted(t["id"] for t in standing["transfers"]) == sorted([one, two])
    assert {r["item"]: r["amount"] for r in shown["rows"]}["adjusted bank balance"] == "2200.00"

    ok(client.post(f"/api/reconcile/matches/{made['id']}/undo", json={}), 204)
    ok(client.post(f"/api/reconcile/matches/{made['id']}/undo", json={}), 204)  # safe to retry
    assert len(sheet(client, pmc)["open_transfers"]) == 2
    refused(
        client.post(f"/api/reconcile/matches/{uuid.uuid4()}/undo", json={}), 404, "no such match"
    )


def test_a_match_names_only_this_accounts_lines(client, conn, staff, pmc):
    paid = rent(conn, pmc, "100.00", date(2026, 1, 4))
    refused(
        call(
            client,
            f"{pmc.operating_bank_id}/matches",
            line_ids=[str(uuid.uuid4())],
            transfer_ids=[paid],
        ),
        422,
        "not on a statement of trust bank account",
    )


def test_a_match_or_undo_in_an_approved_period_is_refused(client, conn, staff, pmc):
    paid = rent(conn, pmc, "100.00", date(2026, 1, 4))
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-05", "Deposit", "100.00", "")),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )
    (line,) = sheet(client, pmc)["open_lines"]
    made = ok(
        call(client, f"{pmc.operating_bank_id}/matches", line_ids=[line["id"]], transfer_ids=[paid])
    )
    ok(call(client, f"{pmc.operating_bank_id}/approve", through=JAN_31))

    refused(
        client.post(f"/api/reconcile/matches/{made['id']}/undo", json={}), 409, "closed through"
    )


def test_auto_match_pairs_a_line_only_with_its_one_sure_transfer(client, conn, staff, pmc):
    sure = rent(conn, pmc, "1500.00", date(2026, 1, 4))
    rent(conn, pmc, "700.00", date(2026, 1, 10))
    rent(conn, pmc, "700.00", date(2026, 1, 11))  # two partners: left to staff
    rent(conn, pmc, "300.00", date(2026, 1, 2))  # too far from its line
    ok(
        statement(
            client,
            pmc,
            csv(
                ("2026-01-05", "Deposit", "1500.00", "D1"),
                ("2026-01-12", "Deposit", "700.00", "D2"),
                ("2026-01-20", "Deposit", "300.00", "D3"),
            ),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )

    counted = ok(client.post(f"/api/reconcile/{pmc.operating_bank_id}/auto-match", json={}), 200)

    (standing,) = sheet(client, pmc)["matches"]
    assert counted == {"matched": 1}
    assert [t["id"] for t in standing["transfers"]] == [sure]


def test_auto_match_leaves_an_approved_period_alone(client, conn, staff, pmc):
    rent(conn, pmc, "100.00", date(2026, 1, 4))
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-05", "Deposit", "100.00", "")),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )
    approve(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 2, 1, tzinfo=UTC),
        "100.00",
    )

    assert ok(client.post(f"/api/reconcile/{pmc.operating_bank_id}/auto-match", json={}), 200) == {
        "matched": 0
    }


# --- approving ---------------------------------------------------------------------------------


def test_a_period_is_approved_once_the_books_lack_no_line(client, conn, staff, pmc):
    paid = rent(conn, pmc, "1500.00", date(2026, 1, 4))
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-05", "Deposit", "1500.00", ""), ("2026-01-28", "Fee", "-5.00", "")),
            first_day=JAN_1,
            last_day=JAN_31,
        )
    )
    lines = sheet(client, pmc)["open_lines"]
    ok(
        call(
            client,
            f"{pmc.operating_bank_id}/matches",
            line_ids=[lines[0]["id"]],
            transfer_ids=[paid],
        )
    )
    path = f"{pmc.operating_bank_id}/approve"

    refused(call(client, path, through=JAN_31), 409, "come to -5.00 that the books lack")
    refused(call(client, path, through="2025-12-31"), 422, "the next period starts on 2026-01-01")
    approved = ok(call(client, path, through="2026-01-20"))
    refused(call(client, path, through="2026-01-20"), 422, "the next period starts on 2026-01-21")
    refused(call(client, path, through=TODAY), 422, "once its last day has ended")
    refused(call(client, path, through=TODAY - timedelta(days=1)), 422, "no statement")

    shown = bank(client, pmc)
    (listed,) = shown["reconciliations"]
    assert listed["id"] == approved["id"]
    assert (listed["statement_balance"], listed["prepared_by"], listed["approved_by"]) == (
        "1500.00",
        "Reconciler 1",
        "Reconciler 1",
    )
    assert (shown["closed_through"], shown["next_start"]) == ("2026-01-20", "2026-01-21")


def test_approving_needs_a_statement(client, staff, pmc):
    refused(
        call(client, f"{pmc.operating_bank_id}/approve", through=JAN_31),
        422,
        "import a statement first",
    )


# --- who may, and what is asked ----------------------------------------------------------------


def test_another_pmcs_trust_bank_account_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    bank_id = other.operating_bank_id
    cases = [
        client.get(f"/api/reconcile/{bank_id}"),
        call(client, f"{bank_id}/matches", line_ids=[], transfer_ids=[]),
        client.post(f"/api/reconcile/{bank_id}/auto-match", json={}),
        call(client, f"{bank_id}/approve", through=JAN_31),
        call(client, "statements", bank_account_id=bank_id, format="csv", content=csv()),
    ]
    for answer in cases:
        refused(answer, 404, "no such trust bank account")


ENDPOINTS = [
    ("get", "/api/reconcile"),
    ("get", f"/api/reconcile/{uuid.uuid4()}"),
    ("post", "/api/reconcile/statements"),
    ("post", f"/api/reconcile/{uuid.uuid4()}/matches"),
    ("post", f"/api/reconcile/matches/{uuid.uuid4()}/undo"),
    ("post", f"/api/reconcile/{uuid.uuid4()}/auto-match"),
    ("post", f"/api/reconcile/{uuid.uuid4()}/approve"),
    ("post", f"/api/reconcile/{uuid.uuid4()}/opening-match"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_only_staff_reconcile(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("statements", {"bank_account_id": str(uuid.uuid4()), "format": "pdf", "content": "x"}),
        ("statements", {"bank_account_id": str(uuid.uuid4()), "format": "csv", "content": ""}),
        (
            "statements",
            {
                "bank_account_id": str(uuid.uuid4()),
                "format": "csv",
                "content": "x",
                "opening_balance": "1.001",
            },
        ),
        (f"{uuid.uuid4()}/matches", {"line_ids": ["x"]}),
        (f"{uuid.uuid4()}/matches", {"transfer_ids": ["t"] * 101}),
        (f"{uuid.uuid4()}/matches", {"cutover_item_ids": ["x"]}),
        (f"{uuid.uuid4()}/opening-match", {"line_id": "x"}),
        (f"{uuid.uuid4()}/approve", {"through": "January"}),
        ("x/auto-match", {}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    refused(client.post(f"/api/reconcile/{path}", json=body), 422)


# --- a migrated account ----------------------------------------------------------------------


def migrated(conn, pmc, staff, signed=True):
    """The operating account cut over on Jan 1 with 1000.00, a check of 200.00 not cleared."""
    from helpers import carry_over, open_with_balances, sign_off

    open_with_balances(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        JAN_1,
        {pmc.owners[0].account: "1000.00"},
        "1000.00",
    )
    item = carry_over(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        staff.person_id,
        date(2025, 12, 30),
        "-200.00",
        "Check to Vendor 1",
        "1041",
    )
    if signed:
        sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff.person_id)
    return item


def test_a_migrated_accounts_opening_and_its_items_are_matched(client, conn, staff, pmc):
    item = migrated(conn, pmc, staff)
    ok(
        statement(
            client,
            pmc,
            csv(("2026-01-09", "Check 1041", "-200.00", "1041")),
            first_day=JAN_1,
            last_day=JAN_31,
            opening_balance="1200.00",
        )
    )
    before = sheet(client, pmc)
    (forward, cleared) = before["open_lines"]
    path = f"{pmc.operating_bank_id}/opening-match"

    refused(
        call(client, path, line_id=cleared["id"]), 409, "the balance brought forward is -200.00"
    )
    made = ok(call(client, path, line_id=forward["id"]))
    refused(call(client, path, line_id=forward["id"]), 409, "matched already")
    after = sheet(client, pmc)
    cleared_by = ok(
        call(
            client,
            f"{pmc.operating_bank_id}/matches",
            line_ids=[cleared["id"]],
            cutover_item_ids=[str(item)],
        )
    )
    done = sheet(client, pmc)

    assert before["opening"] == {
        "book_cash": "1000.00",
        "carried": "-200.00",
        "brought_forward": "1200.00",
        "signed_off": True,
        "matched": False,
    }
    assert (forward["description"], forward["amount"]) == ("Balance brought forward", "1200.00")
    assert before["open_items"] == []
    assert after["opening"]["matched"] is True
    assert after["open_items"] == [
        {
            "id": str(item),
            "issued_on": "2025-12-30",
            "amount": "-200.00",
            "description": "Check to Vendor 1",
            "reference": "1041",
        }
    ]
    assert done["open_items"] == []
    assert {m["id"]: len(m["items"]) for m in done["matches"]} == {
        made["id"]: 1,
        cleared_by["id"]: 1,
    }
    assert {r["item"]: r["amount"] for r in done["rows"]}[
        "difference: adjusted bank balance - trust journal"
    ] == "0.00"
    ok(call(client, f"{pmc.operating_bank_id}/approve", through=JAN_31))


def test_an_opening_match_needs_the_sign_off_and_the_accounts_own_line(client, conn, staff, pmc):
    migrated(conn, pmc, staff, signed=False)
    ok(statement(client, pmc, csv(), first_day=JAN_1, last_day=JAN_31, opening_balance="1200.00"))
    (forward,) = sheet(client, pmc)["open_lines"]
    path = f"{pmc.operating_bank_id}/opening-match"

    refused(call(client, path, line_id=forward["id"]), 409, "no signed-off cutover")
    refused(call(client, path, line_id=str(uuid.uuid4())), 409, "no signed-off cutover")
    from helpers import sign_off

    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff.person_id)
    refused(call(client, path, line_id=str(uuid.uuid4())), 422, "not on a statement")
    refused(
        call(client, f"{make_pmc(conn).operating_bank_id}/opening-match", line_id=forward["id"]),
        404,
        "no such trust bank account",
    )
    assert sheet(client, pmc)["opening"]["signed_off"] is True


def test_an_account_that_did_not_cut_over_has_no_opening(client, staff, pmc):
    assert sheet(client, pmc)["opening"] is None
