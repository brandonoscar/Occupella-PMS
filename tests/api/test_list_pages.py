"""Lists a page at a time (the enterprise test, 2026-10-25): the rent roll and the Tenants list
pick their page first, by what they are found and sorted by, then work out what each row shows
(what it owes, its deposits, contact details) for that page alone; a deep page used to work out
every row before it. Read one row a page, each is the whole list in its order, and the rent
roll's "owing" is exactly the leases that owe, a bounced payment's included."""

from pms import demo, nightly
from pms.api import directory, rent, rentals

SHOWS = ("current", "future", "past", "owing", "all")


def test_the_rent_roll_a_lease_a_page_is_the_whole_roll_in_order(app_conn):
    built = demo.books(app_conn)
    # This month's rent, so leases owe; the demo's bounce (paid again since) counts in each sum.
    nightly.run_pmc(app_conn, built.pmc_id, rent.today(), "http://testserver")
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        reversed_ = app_conn.execute(
            "SELECT count(*) FROM trust_payment_reversals AS r"
            " JOIN trust_charges AS c ON c.id = r.charge_id WHERE c.pmc_id = %s",
            (built.pmc_id,),
        ).fetchone()
        assert reversed_ is not None and reversed_[0] > 0
        for show in SHOWS:
            whole = rentals.rent_roll(s, app_conn, None, None, None, show, 1, 200)
            one_by_one = [
                rentals.rent_roll(s, app_conn, None, None, None, show, n, 1)
                for n in range(1, whole.total + 1)
            ]
            assert [page.items[0] for page in one_by_one] == whole.items, show
            assert {page.total for page in one_by_one} <= {whole.total}, show
            past_the_end = rentals.rent_roll(
                s, app_conn, None, None, None, show, whole.total + 1, 1
            )
            assert past_the_end.items == []

        every = rentals.rent_roll(s, app_conn, None, None, None, "all", 1, 200).items
        owing = rentals.rent_roll(s, app_conn, None, None, None, "owing", 1, 200).items
        assert owing, "the demo's books owe something"
        assert [r.id for r in owing] == [r.id for r in every if rent.owed(app_conn, r.id) > 0]
        assert all(r.owed == rent.cents(rent.owed(app_conn, r.id)) for r in every)


def test_the_tenants_list_a_tenant_a_page_is_the_whole_list_in_order(app_conn):
    built = demo.books(app_conn)
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        whole = directory.tenants(s, app_conn, None, False, None, None, 1, 200)
        assert whole.total == len(whole.items) > 1
        one_by_one = [
            directory.tenants(s, app_conn, None, False, None, None, n, 1)
            for n in range(1, whole.total + 1)
        ]
        assert [page.items[0] for page in one_by_one] == whole.items
        # Found by the unit of the lease each shows, as before.
        unit = next(t.lease.unit.name for t in whole.items if t.lease is not None)
        found = directory.tenants(s, app_conn, unit, False, None, None, 1, 200).items
        assert found and all(
            unit.lower()
            in (t.name + t.property.name + (t.lease.unit.name if t.lease else "")).lower()
            for t in found
        )
