"""The demo PMC (src/pms/demo.py): built through the API's own routes, it opens on last month's
three-way reconciliation waiting for the manager's approval, with a check outstanding, and its
owner with three properties has a statement that rolls them up. Every other page has something
on it, and the Dashboard lists what waits."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from pms import demo
from pms.api import offers, payables, reconcile, rent, today

DAY = timedelta(days=1)


@pytest.fixture
def built(app_conn):
    return demo.build(app_conn)


def utc(day):
    return datetime.combine(day, time(), UTC)


def report(conn, query, *params):
    return conn.execute(query, params).fetchall()


def test_last_month_waits_for_the_manager_with_the_roof_check_outstanding(conn, built):
    _, last_month, this_month = built.months
    rows = report(
        conn,
        "SELECT item, detail, amount FROM trust_report_bank_reconciliation(%s, %s, %s)",
        built.pmc_id,
        built.operating,
        this_month - DAY,
    )
    figures = {item: amount for item, _, amount in rows if amount is not None}

    assert figures["outstanding withdrawals"] == Decimal("-650.00")
    assert figures["difference: adjusted bank balance - trust journal"] == 0
    assert [detail for item, detail, _ in rows if item == "outstanding withdrawal"] == [
        f"{this_month - DAY} Bill RF-2207 paid"
    ]
    assert conn.execute(
        "SELECT prepared_by, through FROM trust_reconciliation_preparations WHERE id = %s",
        (built.preparation,),
    ).fetchone() == (built.bookkeeper, this_month - DAY)
    # Approved through the month before last, for both trust bank accounts; not last month.
    assert {
        closed
        for (closed,) in conn.execute(
            "SELECT closed_through FROM trust_bank_accounts WHERE pmc_id = %s", (built.pmc_id,)
        )
    } == {utc(last_month)}


def test_the_manager_approves_last_month_and_it_ties_three_ways(conn, app_conn, built):
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as session:
        approved = reconcile.approve(
            session,
            app_conn,
            built.operating,
            reconcile.Approval(preparation_id=built.preparation),
        ).id

    rows = report(
        conn,
        "SELECT item, detail, amount FROM trust_report_three_way_reconciliation(%s, %s)",
        built.pmc_id,
        approved,
    )
    by_item = {item: (detail, amount) for item, detail, amount in rows if item != "ledger"}

    assert by_item["prepared by"][0] == "Demo Bookkeeper"
    assert by_item["approved by"][0].startswith("Demo Manager at ")
    assert by_item["difference: trust journal - beneficiary ledgers"][1] == 0
    # The bank's side ties once the roof check, not yet cleared, is taken off.
    assert report(
        conn,
        "SELECT amount FROM trust_report_bank_reconciliation(%s, %s, %s)"
        " WHERE item = 'difference: adjusted bank balance - trust journal'",
        built.pmc_id,
        built.operating,
        built.months[2] - DAY,
    ) == [(0,)]


def test_the_owner_with_three_properties_has_them_on_one_statement(conn, built):
    _, last_month, this_month = built.months
    rows = report(
        conn,
        "SELECT property, item, amount, balance FROM trust_report_owner_statement(%s, %s, %s, %s)",
        built.pmc_id,
        built.owners["Demo Owner A"],
        utc(last_month),
        utc(this_month),
    )

    assert sorted({p for p, *_ in rows if p}) == ["Birch Flats", "Cedar House", "Elm Court"]
    # Each property keeps its 250.00 reserve after the month's draw.
    closing = {p: balance for p, item, _, balance in rows if item == "closing balance"}
    assert set(closing.values()) == {Decimal("250.00")}
    assert [b for _, item, _, b in rows if item == "all properties: closing balance"] == [750]


def test_the_owners_bills_show_on_their_income_statement_as_repairs(conn, built):
    month_ago, _, this_month = built.months
    rows = report(
        conn,
        "SELECT property, item, schedule_e, amount"
        " FROM trust_report_income_statement(%s, %s, %s, %s)",
        built.pmc_id,
        built.owners["Demo Owner A"],
        month_ago,
        this_month,
    )

    # The kitchen drain at Elm Court and the roof at Cedar House, both in Repairs.
    assert [(p, amount) for p, item, _, amount in rows if item.endswith("expense: Repairs")] == [
        ("Cedar House", Decimal("650.00")),
        ("Elm Court", Decimal("380.00")),
        (None, Decimal("1030.00")),
    ]
    assert ("Elm Court", "Schedule E line 14: Repairs", 14, Decimal("380.00")) in rows


@pytest.fixture
def books(app_conn):
    return demo.books(app_conn)


def test_two_months_of_the_work_staff_do(conn, books):
    built = books
    month_ago, last_month, this_month = built.months
    count = lambda query: conn.execute(query, (built.pmc_id,)).fetchone()[0]  # noqa: E731

    # Rent for six leases each month, pet rent twice, one late fee, one bounce.
    assert count("SELECT count(*) FROM trust_charges WHERE pmc_id = %s AND kind = 'rent'") == 12
    assert (
        count(
            "SELECT count(*) FROM trust_charges WHERE pmc_id = %s"
            " AND recurring_charge_id IS NOT NULL"
        )
        == 2
    )
    assert count(
        "SELECT array_agg(c.due_on) FROM trust_charges AS c WHERE c.pmc_id = %s"
        " AND c.kind = 'fee' AND c.recurring_charge_id IS NULL"
    ) == [month_ago + 6 * DAY]  # the late fee, due the day it was assessed
    assert count("SELECT count(*) FROM trust_payment_reversals WHERE pmc_id = %s") == 1
    # Every lease is paid up.
    assert (
        count(
            "SELECT sum(CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)"
            " - (SELECT coalesce(sum(p.amount), 0) FROM trust_charge_payments AS p"
            "    WHERE p.pmc_id = c.pmc_id)"
            " + (SELECT coalesce(sum(r.amount), 0) FROM trust_payment_reversals AS r"
            "    WHERE r.pmc_id = c.pmc_id)"
            " FROM trust_charges AS c WHERE c.pmc_id = %s GROUP BY c.pmc_id"
        )
        == 0
    )
    # Two bills paid, a management fee for each property each month, last month's counting
    # the pet rent: 8% of 1395.00 + 35.00 + 1610.00.
    assert (
        count("SELECT count(*) FROM trust_bill_payments WHERE pmc_id = %s AND step = 'paid'") == 2
    )
    assert count("SELECT count(*) FROM trust_management_fees WHERE pmc_id = %s") == 8
    assert conn.execute(
        "SELECT fee FROM trust_management_fees WHERE ledger_account_id = %s AND period_start = %s",
        (built.accounts["Birch Flats"], last_month),
    ).fetchone() == (Decimal("243.20"),)


def test_today_lists_what_waits_on_staff(conn, app_conn, built):
    _, last_month, this_month = built.months
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as session:
        waiting = today.today(session, app_conn)
    last_months_fees = conn.execute(
        "SELECT sum(fee) FROM trust_management_fees WHERE pmc_id = %s AND period_start = %s",
        (built.pmc_id, last_month),
    ).fetchone()[0]

    # Last month waits for the manager, for both trust bank accounts.
    assert [u.waiting_for_approval for u in waiting.unreconciled] == [this_month - DAY] * 2
    assert [(e.id, e.memo) for e in waiting.journal_waiting] == [
        (built.journal_entry, "Reserve moved to Birch Flats, as Demo Owner A asked")
    ]
    # Only last month's fees are still in trust: the month before last's were taken out.
    assert [(f.bank_account, Decimal(f.held), f.since) for f in waiting.company_fees] == [
        ("Demo operating trust account", last_months_fees, this_month - DAY)
    ]
    # The electrician's policy ran out five days before this month; the roofer's runs out soon.
    assert [(v.vendor, v.expires_on) for v in waiting.vendor_insurance] == [
        ("Demo Electric Co", this_month - 5 * DAY),
        ("Demo Roofing Co", waiting.on + 20 * DAY),
    ]
    assert [(u.property, u.unit, u.standing) for u in waiting.renters_insurance] == [
        ("Aspen Row", "Unit 1", "none on file")
    ]
    # Each trust bank account has its broker, and the bookkeeper's bond runs a while yet.
    assert waiting.signers == []


def test_the_company_took_the_month_before_lasts_fees_out_of_trust(conn, built):
    month_ago, last_month, _ = built.months
    fees = conn.execute(
        "SELECT sum(fee) FROM trust_management_fees WHERE pmc_id = %s AND period_start = %s",
        (built.pmc_id, month_ago),
    ).fetchone()[0]

    assert conn.execute(
        "SELECT w.amount, t.event_at FROM trust_pmc_fee_withdrawals AS w"
        " JOIN pgledger_transfers AS t ON t.id = w.transfer_id WHERE w.pmc_id = %s",
        (built.pmc_id,),
    ).fetchall() == [(fees, utc(last_month + 4 * DAY))]


def test_every_other_page_has_something_on_it(conn, app_conn, built):
    on = datetime.now(UTC).date()
    pmc = built.pmc_id
    assert report(
        conn,
        "SELECT bank_account, signer, role, standing"
        " FROM trust_report_trust_account_signers(%s, %s) ORDER BY line",
        pmc,
        on,
    ) == [
        ("Demo operating trust account", "Demo Manager", "broker", "licensed"),
        ("Demo operating trust account", "Demo Bookkeeper", "signer", "bonded"),
        ("Demo security deposit account", "Demo Manager", "broker", "licensed"),
    ]
    assert report(
        conn,
        "SELECT r.name, s.display_name FROM trust_staff_custom_roles AS g"
        " JOIN trust_custom_roles AS r ON r.id = g.role_id"
        " JOIN trust_staff AS s ON s.id = g.staff_id WHERE g.pmc_id = %s",
        pmc,
    ) == [("Leasing", "Demo Leasing Agent")]
    assert report(
        conn,
        "SELECT vendor, standing FROM trust_report_vendor_insurance(%s, %s)"
        " WHERE item = 'policy' ORDER BY line",
        pmc,
        on,
    ) == [
        ("Demo Electric Co", "expired"),
        ("Demo Plumbing Co", "current"),
        ("Demo Roofing Co", "expires in 20 days"),
    ]
    assert report(
        conn,
        "SELECT room, move_in, detail FROM trust_report_inspection(%s, %s) WHERE item = 'room'"
        " ORDER BY line",
        pmc,
        built.leases[("Elm Court", "Unit 1")],
    ) == [
        ("Kitchen", "good", None),
        ("Living room", "good", None),
        ("Bathroom", "fair", "move-in: Grout stained by the tub"),
    ]
    budget = report(
        conn,
        "SELECT property, item, budget FROM trust_report_budget_vs_actual(%s, %s, %s, %s)"
        " WHERE property = 'Elm Court' AND budget <> 0 ORDER BY line",
        pmc,
        built.owners["Demo Owner A"],
        on.replace(month=1, day=1),
        on.replace(month=12, day=31),
    )
    # Twelve months of 2975.00 rent and 100.00 of repairs.
    assert [(item, amount) for _, item, amount in budget] == [
        ("income: rent", Decimal("35700.00")),
        ("total income", Decimal("35700.00")),
        ("expense: Repairs", Decimal("1200.00")),
        ("total expenses", Decimal("1200.00")),
        ("net income", Decimal("34500.00")),
    ]
    # Cedar House's rent goes up next month, noticed in writing a full month ahead.
    with demo.Builder(app_conn, pmc).as_staff(built.manager) as session:
        [cedar] = [
            lease
            for lease in rent.leases(session, app_conn)
            if lease.id == built.leases[("Cedar House", "House")]
        ]
    assert [(c.effective_on, c.monthly_rent) for c in cedar.rent_changes] == [
        (demo.first_of_month(on, -1), "2300.00")
    ]
    assert cedar.monthly_rent == "2250.00"
    assert report(
        conn,
        "SELECT property, standing, below_minimum FROM trust_report_renters_insurance(%s, %s)"
        " WHERE required ORDER BY line",
        pmc,
        on,
    ) == [("Aspen Row", "none on file", False), ("Cedar House", "current", False)]


def test_the_founders_seed_puts_something_on_every_list(conn, app_conn, built):
    on = datetime.now(UTC).date()
    pmc = built.pmc_id
    this_month = built.months[2]
    rows = lambda query, *more: report(conn, query, pmc, *more)  # noqa: E731

    # Six properties, Harbor Tower with 48 units, in two groups.
    assert rows(
        "SELECT p.display_name, count(u.id) FROM trust_properties AS p"
        " LEFT JOIN trust_units AS u ON u.property_id = p.id WHERE p.pmc_id = %s"
        ' GROUP BY p.display_name ORDER BY p.display_name COLLATE "C"'
    ) == [
        ("Aspen Row", 2),
        ("Birch Flats", 2),
        ("Cedar House", 1),
        ("Elm Court", 2),
        ("Harbor Tower", 48),
        ("Oak Terrace", 2),
    ]
    assert rows(
        "SELECT g.display_name, count(*) FROM trust_property_groups AS g"
        " CROSS JOIN LATERAL trust_group_properties(g.id) AS p WHERE g.pmc_id = %s"
        " GROUP BY g.display_name ORDER BY g.display_name"
    ) == [("North portfolio", 3), ("South portfolio", 3)]
    # This month's rent charged to every lease in effect: three Harbor Tower tenants owe it, one
    # paid part, one none and one's payment bounced, with its NSF fee.
    assert rows(
        "SELECT count(*) FROM trust_charges WHERE pmc_id = %s AND kind = 'rent' AND due_on = %s",
        this_month,
    ) == [(50,)]
    owing = sorted(
        unit for (prop, unit), lease in built.leases.items() if rent.owed(conn, lease) > 0
    )
    assert owing == [demo.HARBOR_PARTIAL, demo.HARBOR_UNPAID, demo.HARBOR_BOUNCED]
    assert rows("SELECT count(*) FROM trust_nsf_fees WHERE pmc_id = %s") == [(1,)]
    # A lease ending in 45 days, one to come, and one ended with its deposit still held.
    oak = built.leases[("Oak Terrace", "Unit 1")]
    assert rows("SELECT ends_on FROM trust_leases WHERE pmc_id = %s AND id = %s", oak) == [
        (on + 45 * DAY,)
    ]
    assert rows(
        "SELECT starts_on FROM trust_leases WHERE pmc_id = %s AND id = %s",
        built.leases[("Oak Terrace", "Unit 2")],
    ) == [(demo.first_of_month(on, -1),)]
    ended = built.leases[("Harbor Tower", demo.HARBOR_ENDED)]
    assert rows(
        "SELECT count(*) FROM trust_deposit_returns WHERE pmc_id = %s AND lease_id = %s", ended
    ) == [(0,)]
    # Applicants at each step, a lease sent to sign and a renewal half signed.
    assert rows(
        "SELECT (SELECT count(*) FROM trust_screenings AS s WHERE s.application_id = a.id),"
        " d.decision FROM trust_applications AS a"
        " LEFT JOIN trust_application_decisions AS d ON d.application_id = a.id"
        " WHERE a.pmc_id = %s ORDER BY a.submitted_at"
    ) == [(0, None), (1, None), (1, "approved"), (1, "declined"), (0, None)]
    assert rows(
        "SELECT p.display_name, w.reason FROM trust_application_withdrawals AS w"
        " JOIN trust_applicants AS p ON p.application_id = w.application_id"
        " WHERE w.pmc_id = %s"
    ) == [("Kendall Applicant", "Took a home closer to work")]
    # Two of Harbor Tower's vacant units are on the public page.
    assert rows(
        "SELECT u.display_name FROM trust_listings AS s"
        " JOIN trust_application_links AS l ON l.id = s.link_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        ' WHERE s.pmc_id = %s ORDER BY u.display_name COLLATE "C"'
    ) == [("Unit 409",), ("Unit 410",)]
    assert sorted(
        rows(
            "SELECT o.renews_lease_id IS NOT NULL, count(g.signed_at), count(*)"
            " FROM trust_lease_offers AS o"
            " JOIN trust_lease_offer_signers AS s ON s.offer_id = o.id"
            " LEFT JOIN trust_signatures AS g"
            " ON g.offer_id = s.offer_id AND g.position = s.position"
            " WHERE o.pmc_id = %s GROUP BY o.id"
        )
    ) == [(False, 0, 2), (True, 0, 2), (True, 1, 2)]
    # Draft leases: the applicant's sent with its deposit and parking, a renewal half signed and
    # Cedar House's renewal a draft.
    with demo.Builder(app_conn, pmc).as_staff(built.manager):
        drafts, _ = offers.offers_of(app_conn, pmc, show="open")
    assert sorted((o.property, o.status, o.security_deposit) for o in drafts) == [
        ("Cedar House", "draft", None),
        ("Harbor Tower", "sent", f"{demo.harbor_rent(demo.APPLICANTS[2][0]):.2f}"),
        ("Oak Terrace", "partially signed", None),
    ]
    # Eight bills: two due this week, one overdue, one waiting for its owner, three paid and
    # this month's landscaping, entered by the nightly run.
    with demo.Builder(app_conn, pmc).as_staff(built.manager):
        bills, _ = payables.find_bills(app_conn, pmc)
    assert sorted((b.reference, b.status, b.overdue) for b in bills) == [
        ("ELC-3005", "paid", False),
        (f"LAWN-{this_month:%Y-%m}", "to set aside", on.day > 28),
        ("LND-3001", "to set aside", False),
        ("PLB-1001", "paid", False),
        ("PLB-3002", "to set aside", False),
        ("PLB-3003", "to set aside", True),
        ("RF-2207", "paid", False),
        ("RF-3004", "needs approval", False),
    ]
    # Six work orders, one at each step, the roofer's among them, each with its priority.
    assert rows(
        "SELECT w.summary, (SELECT s.step FROM trust_work_order_steps AS s"
        "  WHERE s.work_order_id = w.id ORDER BY s.created_at DESC, s.id DESC LIMIT 1),"
        " (SELECT d.priority FROM trust_work_order_details AS d WHERE d.work_order_id = w.id)"
        " FROM trust_work_orders AS w WHERE w.pmc_id = %s ORDER BY w.summary"
    ) == [
        ("Front walk light out", None, "normal"),
        ("Inspect roof after the storm", "assigned", "high"),
        ("Kitchen faucet drips", "completed", "low"),
        ("Repaint the mailbox", "cancelled", None),
        ("Smoke detector chirping", None, "urgent"),
        ("Water heater rumbles", "scheduled", "normal"),
    ]
    # Four vendors, a W-9 on file, and files on a lease, an owner, a bill and a work order too.
    assert rows("SELECT count(*) FROM trust_vendors WHERE pmc_id = %s") == [(4,)]
    # Each with their trade, whether they get a 1099 and how they are paid.
    assert rows(
        "SELECT v.display_name, p.trade, p.gets_1099, p.payment_method FROM trust_vendors AS v"
        " JOIN trust_vendor_profiles AS p ON p.vendor_id = v.id WHERE v.pmc_id = %s"
        ' ORDER BY v.display_name COLLATE "C"'
    ) == [
        ("Demo Electric Co", "Electrical", False, "check"),
        ("Demo Landscaping Co", "Landscaping", True, "EFT"),
        ("Demo Plumbing Co", "Plumbing", True, "EFT"),
        ("Demo Roofing Co", "Roofing", True, "check"),
    ]
    assert rows("SELECT kind, title FROM trust_documents WHERE pmc_id = %s ORDER BY kind") == [
        ("move_out_photo", "Kitchen at move-out"),
        ("owner_document", "Management agreement"),
        ("receipt", "Roof repair receipt"),
        ("w9", "W-9, Demo Plumbing Co"),
        ("work_order_photo", "Faucet before the repair"),
    ]
    # The company's address and the fee terms a property added from now on starts with; the
    # demo's own properties keep their own.
    assert rows(
        "SELECT address_line_1, city, late_grace_days, late_flat_fee, nsf_fee"
        " FROM trust_company_profiles WHERE pmc_id = %s"
    ) == [("100 Example Street", "Sampletown", 5, Decimal("50.00"), Decimal("35.00"))]


def test_the_books_alone_leave_every_other_page_for_the_feature_tests(conn, app_conn):
    # Each browser test of a feature starts from the books (frontend/e2e/seed.py): no broker
    # named, no policy, inspection, budget, rent change, insurance terms, role, entry, company
    # profile or email settings yet.
    books = demo.books(app_conn)

    for table in (
        "trust_account_signers",
        "trust_custom_roles",
        "trust_vendor_insurance",
        "trust_inspections",
        "trust_budgets",
        "trust_rent_changes",
        "trust_renters_insurance_terms",
        "trust_journal_entries",
        "trust_company_profiles",
        "trust_email_settings",
    ):
        assert conn.execute(
            f"SELECT count(*) FROM {table} WHERE pmc_id = %s", (books.pmc_id,)
        ).fetchone() == (0,), table
    # The company's fees are taken out of trust on time in the books too.
    assert conn.execute(
        "SELECT count(*) FROM trust_pmc_fee_withdrawals WHERE pmc_id = %s", (books.pmc_id,)
    ).fetchone() == (1,)


def test_every_write_but_the_first_staff_member_and_their_role_names_a_staff_member(conn, built):
    assert conn.execute(
        "SELECT table_name, count(*) FROM trust_audit_log"
        " WHERE pmc_id = %s AND staff_id IS NULL GROUP BY table_name ORDER BY table_name",
        (built.pmc_id,),
    ).fetchall() == [
        # Applicants apply themselves, through a link: they have no login to name.
        ("trust_applicants", 6),
        ("trust_applications", 5),
        ("trust_pmcs", 1),
        # A signer signs through their link (ADR 0008): they have no login either.
        ("trust_signature_evidence", 1),
        ("trust_signatures", 1),
        ("trust_signing_events", 2),
        ("trust_staff", 1),
        ("trust_staff_roles", 1),
    ]


def test_another_demo_goes_in_beside_the_first(conn, app_conn, built):
    second = demo.build(app_conn)

    assert second.pmc_id != built.pmc_id
    for made in (built, second):
        assert conn.execute(
            "SELECT count(*) FROM trust_reconciliation_preparations WHERE pmc_id = %s",
            (made.pmc_id,),
        ).fetchone() == (4,)


def test_the_command_prints_a_sign_in_link_for_the_manager_an_owner_and_a_tenant(
    conn, database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example/")

    assert demo.main([]) == 0

    out = capsys.readouterr().out.splitlines()
    pmc_id = out[0].split()[2].rstrip(":")
    links = [line for line in out if line.startswith("Sign in as ")]
    assert [line.split(":")[0] for line in links] == [
        "Sign in as the manager, who approves last month's reconciliation",
        "Sign in as Demo Owner A, with three properties",
        "Sign in as Taylor Demo, a tenant of Cedar House",
    ]
    assert all("https://pms.example/invite#" in line for line in links)
    assert conn.execute(
        "SELECT kind, count(*) FROM trust_invites AS i JOIN trust_logins AS l"
        " ON l.id = i.login_id WHERE l.pmc_id = %s GROUP BY kind ORDER BY kind",
        (pmc_id,),
    ).fetchall() == [("owner", 1), ("staff", 1), ("tenant", 1)]


def test_if_empty_adds_no_demo_to_a_database_that_holds_a_pmc(
    conn, database_url, built, monkeypatch, capsys
):
    # compose.demo.yaml runs the command each time it comes up: a second demo never goes in.
    monkeypatch.setenv("DATABASE_URL", database_url)
    before = conn.execute("SELECT count(*) FROM trust_pmcs").fetchone()

    assert demo.main(["--if-empty"]) == 0

    assert conn.execute("SELECT count(*) FROM trust_pmcs").fetchone() == before
    assert capsys.readouterr().out == (
        "This database holds a PMC already, so no demo was added (--if-empty).\n"
    )
