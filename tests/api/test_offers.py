"""Lease offers through the API (src/pms/api/offers.py): staff offer a lease or a renewal with
the PMC's own PDF, signers sign on the fake e-sign partner's page, and staff complete the offer
into a lease. Tenants see and sign their renewals."""

import base64
import hashlib
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import (
    LEASE_PDF,
    add_staff,
    add_tenant,
    add_unit,
    make_pmc,
    offer_lease,
    open_application_link,
    open_lease,
    record_decision,
    send_offer,
    submit_application,
)
from people import join_as, join_as_owner, join_as_staff, join_as_tenant

from pms import email as outgoing
from pms.api import Settings, create_app, offers

JAN_1, JUN_30 = date(2027, 1, 1), date(2027, 6, 30)


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        esign_partner="fake",
    )


class Place:
    """Unit 1 with an application from two applicants, approved; Unit 2 leased to Tenant 1."""

    def __init__(self, conn, pmc):
        pmc_id, prop = pmc.pmc_id, pmc.owners[0].property_id
        self.pmc = pmc
        self.unit = add_unit(conn, pmc_id, prop, "Unit 1")
        self.leased_unit = add_unit(conn, pmc_id, prop, "Unit 2")
        staff = add_staff(conn, pmc_id, "Leasing staff")
        link = open_application_link(conn, pmc_id, self.unit, staff)
        self.application = submit_application(
            conn,
            pmc_id,
            link,
            applicants=[("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")],
        )
        record_decision(conn, pmc_id, self.application, staff, "approved")
        self.tenant = add_tenant(conn, pmc_id, prop, "Tenant 1")
        self.lease = open_lease(
            conn, pmc_id, self.leased_unit, date(2026, 1, 1), None, "1200", [self.tenant]
        )


@pytest.fixture
def place(conn, pmc):
    return Place(conn, pmc)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def terms(**changes):
    return {
        "starts_on": JAN_1.isoformat(),
        "ends_on": JUN_30.isoformat(),
        "monthly_rent": "1500.00",
        "document_title": "Residential lease",
        "document_base64": base64.b64encode(LEASE_PDF).decode(),
        **changes,
    }


def offer(client, place, **changes):
    answer = client.post(
        f"/api/leasing/applications/{place.application}/offer", json=terms(**changes)
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


def sign(browser, link, name):
    token = link["url"].split("#", 1)[1]
    return browser.post("/api/fake-esign/sign", json={"token": token, "name": name})


def test_staff_offer_a_lease_to_the_applicants_and_each_gets_a_link(client, staff, place):
    made = offer(client, place)

    assert made["offer"]["kind"] == "new lease"
    assert made["offer"]["sent"] is True
    assert [
        (s["position"], s["name"], s["email"], s["signed_at"]) for s in made["offer"]["signers"]
    ] == [
        (1, "Applicant A", "a@example.com", None),
        (2, "Applicant B", "b@example.com", None),
    ]
    assert [link["url"].startswith("http://testserver/esign#") for link in made["links"]] == [
        True
    ] * 2
    assert client.get("/api/leasing/offers").json()["items"] == [made["offer"]]
    again = client.get(f"/api/leasing/offers/{made['offer']['id']}/links").json()
    assert again == made["links"]


def test_signers_sign_and_staff_complete_the_offer_into_a_lease(
    client, other_client, conn, staff, place
):
    made = offer(client, place)
    complete = f"/api/leasing/offers/{made['offer']['id']}/complete"

    early = client.post(complete, json={})
    viewed = other_client.post(
        "/api/fake-esign/view", json={"token": made["links"][0]["url"].split("#", 1)[1]}
    ).json()
    signed = [sign(other_client, link, link["name"].upper()) for link in made["links"]]
    checked = client.post(f"/api/leasing/offers/{made['offer']['id']}/signatures", json={}).json()
    done = client.post(complete, json={})

    assert early.status_code == 409
    assert "not signed by everyone" in early.json()["detail"]
    assert viewed["signer"] == "Applicant A" and viewed["signed"] is False
    assert viewed["document_sha256"] == hashlib.sha256(LEASE_PDF).hexdigest()
    assert [s.status_code for s in signed] == [204, 204]
    assert all(s["signed_at"] for s in checked["signers"])
    assert done.status_code == 200, done.text
    lease = done.json()["lease_id"]
    assert conn.execute(
        "SELECT l.unit_id, l.starts_on, l.ends_on, l.monthly_rent,"
        " array(SELECT t.display_name FROM trust_lease_tenants AS lt"
        "       JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        "       WHERE lt.lease_id = l.id ORDER BY t.display_name)"
        " FROM trust_leases AS l WHERE l.id = %s",
        (lease,),
    ).fetchone() == (place.unit, JAN_1, JUN_30, 1500, ["Applicant A", "Applicant B"])
    assert client.get("/api/leasing/offers").json()["items"] == []
    assert client.post(complete, json={}).json()["lease_id"] == lease


def test_a_tenant_signs_their_renewal_and_staff_complete_it(
    client, other_client, conn, app_conn, staff, place
):
    tenant = join_as(other_client, app_conn, place.pmc.pmc_id, "tenant", place.tenant)
    (lease,) = [x for x in client.get("/api/leasing/leases").json() if x["unit"] == "Unit 2"]
    renewal = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2027-07-01",
            ends_on=None,
            monthly_rent="1250.00",
            signers=[{"name": "Tenant 1", "email": tenant.email}],
        ),
    )
    offered = renewal.json()["offer"]

    seen = other_client.get("/api/tenant/renewals").json()
    link = other_client.post(f"/api/tenant/renewals/{offered['id']}/signing-link", json={})
    document = other_client.get(f"/api/tenant/renewals/{offered['id']}/document")
    signed = sign(other_client, link.json(), "Tenant 1")
    done = client.post(f"/api/leasing/offers/{offered['id']}/complete", json={})

    assert lease["tenants"] == [{"name": "Tenant 1", "email": tenant.email}]
    assert renewal.status_code == 201 and offered["kind"] == "renewal"
    assert [o["id"] for o in seen] == [offered["id"]]
    assert link.status_code == 200 and link.json()["url"].startswith("http://testserver/esign#")
    assert document.content == LEASE_PDF
    assert signed.status_code == 204
    assert done.status_code == 200, done.text
    assert conn.execute(
        "SELECT starts_on, ends_on, monthly_rent FROM trust_leases WHERE unit_id = %s"
        " ORDER BY starts_on",
        (place.leased_unit,),
    ).fetchall() == [(date(2026, 1, 1), JUN_30, 1200), (date(2027, 7, 1), None, 1250)]
    assert other_client.get("/api/tenant/renewals").json() == []


@pytest.mark.parametrize(
    ("due_day", "july_charged", "ends_on", "kept"),
    [
        (5, False, None, [(date(2027, 7, 1), 5)]),
        # July was charged on the lease it renews: the renewal is charged its July days at once,
        # and keeps the due day from August.
        (5, True, None, [(date(2027, 8, 1), 5)]),
        # ...unless it ends in July: no month of it is left to be due on the 5th.
        (5, True, "2027-07-31", []),
        (None, False, None, []),
    ],
)
def test_a_renewal_keeps_the_rent_due_day_of_the_lease_it_renews(
    client, other_client, conn, staff, place, due_day, july_charged, ends_on, kept
):
    if due_day is not None:
        conn.execute(
            "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
            " VALUES (%s, %s, '2026-03-01', %s)",
            (place.pmc.pmc_id, place.lease, due_day),
        )
    if july_charged:
        conn.execute("SELECT trust_run_rent(%s, '2027-07-01', '2027-07-05')", (place.pmc.pmc_id,))
    made = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2027-07-15",
            ends_on=ends_on,
            signers=[{"name": "Tenant 1", "email": "tenant1@example.com"}],
        ),
    ).json()
    for link in made["links"]:
        assert sign(other_client, link, "Tenant 1").status_code == 204
    done = client.post(f"/api/leasing/offers/{made['offer']['id']}/complete", json={})

    assert done.status_code == 200, done.text
    # From the renewal's first month, its rent is due on the day the lease it renews had.
    assert (
        conn.execute(
            "SELECT starts_on, due_day FROM trust_rent_due_days WHERE lease_id = %s",
            (done.json()["lease_id"],),
        ).fetchall()
        == kept
    )


def test_a_tenant_sees_their_signature_at_once_and_only_staff_record_it(
    client, other_client, conn, app_conn, staff, place
):
    tenant = join_as(other_client, app_conn, place.pmc.pmc_id, "tenant", place.tenant)
    people = [
        {"name": "Tenant 1", "email": tenant.email},
        {"name": "Co-signer", "email": "co-signer@example.com"},
    ]
    renewal = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(starts_on="2027-07-01", ends_on=None, signers=people),
    ).json()["offer"]
    recorded = "SELECT position FROM trust_signatures WHERE offer_id = %s ORDER BY position"

    link = other_client.post(f"/api/tenant/renewals/{renewal['id']}/signing-link", json={})
    before = other_client.get("/api/tenant/renewals").json()
    sign(other_client, link.json(), "Tenant 1")
    after = other_client.get("/api/tenant/renewals").json()
    unrecorded = conn.execute(recorded, (renewal["id"],)).fetchall()
    client.post(f"/api/leasing/offers/{renewal['id']}/signatures", json={})
    then = other_client.get("/api/tenant/renewals").json()

    signed = lambda seen: [s["signed_at"] is not None for s in seen[0]["signers"]]  # noqa: E731
    assert signed(before) == [False, False]
    assert signed(after) == [True, False]
    assert unrecorded == []
    assert conn.execute(recorded, (renewal["id"],)).fetchall() == [(1,)]
    assert then[0]["signers"][0]["signed_at"] == after[0]["signers"][0]["signed_at"]


def test_a_tenant_sees_only_renewals_sent_to_sign_on_a_server_with_or_without_a_partner(
    other_client, conn, app_conn, database_url, place
):
    pmc_id = place.pmc.pmc_id
    join_as(other_client, app_conn, pmc_id, "tenant", place.tenant)
    staff_id = add_staff(conn, pmc_id, "Offering staff")
    offer_lease(conn, pmc_id, place.leased_unit, staff_id, JAN_1, renews_lease_id=place.lease)
    sent = offer_lease(
        conn, pmc_id, place.leased_unit, staff_id, JUN_30, renews_lease_id=place.lease
    )
    send_offer(conn, pmc_id, sent, staff_id)
    voided = offer_lease(
        conn, pmc_id, place.leased_unit, staff_id, date(2027, 2, 1), renews_lease_id=place.lease
    )
    send_offer(conn, pmc_id, voided, staff_id)
    conn.execute(
        "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id) VALUES (%s, %s)", (pmc_id, voided)
    )

    with_fake = other_client.get("/api/tenant/renewals").json()
    bare = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(bare)) as plain:
        plain.cookies = other_client.cookies
        without = plain.get("/api/tenant/renewals").json()
    draft_document = other_client.get(f"/api/tenant/renewals/{voided}/document")

    for seen in (with_fake, without):
        assert [(o["id"], o["status"]) for o in seen] == [(str(sent), "sent")]
        assert [s["signed_at"] for o in seen for s in o["signers"]] == [None]
    assert draft_document.status_code == 404


def test_the_fake_partner_takes_only_the_signers_name_on_a_real_link(
    client, other_client, staff, place
):
    made = offer(client, place)

    wrong_name = sign(other_client, made["links"][0], "Someone Else")
    made_up = other_client.post(
        "/api/fake-esign/sign", json={"token": "x" * 40, "name": "Applicant A"}
    )
    unseen = other_client.post("/api/fake-esign/view", json={"token": "x" * 40})

    assert (wrong_name.status_code, wrong_name.json()["detail"]) == (
        422,
        "type your name as it is on the lease",
    )
    assert made_up.status_code == 422
    assert unseen.status_code == 404


def test_without_a_partner_there_is_no_signing(conn, app_conn, database_url, pmc, place):
    bare = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(bare)) as client:
        join_as_staff(client, conn, app_conn, pmc)
        answer = client.post(f"/api/leasing/applications/{place.application}/offer", json=terms())
        page = client.post("/api/fake-esign/view", json={"token": "x" * 40})

    assert (answer.status_code, answer.json()["detail"]) == (
        503,
        "no e-sign partner is set up on this server",
    )
    assert page.status_code == 404


@pytest.mark.parametrize(
    ("changes", "status", "detail"),
    [
        ({"document_base64": "not base64!"}, 422, "the document isn't base64"),
        (
            {"document_base64": base64.b64encode(b"plain text").decode()},
            422,
            "the document is a PDF",
        ),
        (
            {"document_base64": base64.b64encode(LEASE_PDF + b"x" * 5_000_000).decode()},
            422,
            "the document is at most 5 MB",
        ),
        ({"ends_on": "2026-12-31"}, 422, "a lease ends on or after the day it starts"),
        ({"monthly_rent": "0"}, 422, None),
        ({"document_title": ""}, 422, None),
        (
            {
                "signers": [
                    {"name": "A", "email": "x@example.com"},
                    {"name": "B", "email": "X@example.com"},
                ]
            },
            422,
            "each signer has their own email",
        ),
        ({"signers": [{"name": "  ", "email": "x@example.com"}]}, 422, "every signer has a name"),
        ({"signers": [{"name": "A", "email": "not an email"}]}, 422, None),
    ],
)
def test_a_bad_offer_is_refused_and_nothing_is_kept(
    client, conn, staff, place, changes, status, detail
):
    answer = client.post(
        f"/api/leasing/applications/{place.application}/offer", json=terms(**changes)
    )

    assert answer.status_code == status, answer.text
    if detail:
        assert answer.json()["detail"] == detail
    assert conn.execute(
        "SELECT count(*) FROM trust_lease_offers WHERE application_id = %s", (place.application,)
    ).fetchone() == (0,)


def test_an_offer_is_only_for_an_approved_application_and_a_renewal_names_its_signers(
    client, conn, staff, place, pmc
):
    link = open_application_link(conn, pmc.pmc_id, place.unit, add_staff(conn, pmc.pmc_id, "S"))
    undecided = submit_application(conn, pmc.pmc_id, link)

    not_approved = client.post(f"/api/leasing/applications/{undecided}/offer", json=terms())
    unsigned_renewal = client.post(f"/api/leasing/leases/{place.lease}/renewal", json=terms())
    early_renewal = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2025-06-01",
            ends_on=None,
            signers=[{"name": "Tenant 1", "email": "t1@example.com"}],
        ),
    )

    assert not_approved.status_code == 409 and "is not approved" in not_approved.json()["detail"]
    assert unsigned_renewal.status_code == 422
    assert early_renewal.status_code == 409 and "starts after" in early_renewal.json()["detail"]


def test_a_tenant_reaches_only_their_own_renewals(
    client, other_client, conn, app_conn, staff, place, pmc
):
    renewal = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2027-07-01",
            ends_on=None,
            signers=[{"name": "Tenant 1", "email": "t1@example.com"}],
        ),
    ).json()["offer"]
    neighbour = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Neighbour")
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", neighbour)

    answers = [
        other_client.post(f"/api/tenant/renewals/{renewal['id']}/signing-link", json={}),
        other_client.get(f"/api/tenant/renewals/{renewal['id']}/document"),
    ]

    assert other_client.get("/api/tenant/renewals").json() == []
    assert [a.status_code for a in answers] == [404, 404]


def test_a_tenant_on_the_lease_who_isnt_a_signer_gets_no_link(
    client, other_client, conn, app_conn, staff, place, pmc
):
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", place.tenant)
    renewal = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2027-07-01",
            ends_on=None,
            signers=[{"name": "Someone", "email": "s@example.com"}],
        ),
    ).json()["offer"]

    answer = other_client.post(f"/api/tenant/renewals/{renewal['id']}/signing-link", json={})

    assert (answer.status_code, answer.json()["detail"]) == (
        403,
        "you aren't one of this renewal's signers",
    )


def test_another_pmcs_offers_are_not_found(client, other_client, conn, app_conn, staff, place):
    elsewhere = make_pmc(conn)
    theirs = Place(conn, elsewhere)
    join_as_staff(other_client, conn, app_conn, elsewhere, "Staff 2")
    their_offer = offer(other_client, theirs)["offer"]["id"]

    answers = [
        client.post(f"/api/leasing/applications/{theirs.application}/offer", json=terms()),
        client.post(f"/api/leasing/leases/{theirs.lease}/renewal", json=terms()),
        client.get(f"/api/leasing/offers/{their_offer}/links"),
        client.post(f"/api/leasing/offers/{their_offer}/signatures", json={}),
        client.post(f"/api/leasing/offers/{their_offer}/complete", json={}),
        client.get(f"/api/leasing/offers/{their_offer}/document"),
        client.get(f"/api/leasing/offers/{their_offer}"),
        client.post(f"/api/leasing/offers/{their_offer}/send", json={}),
        client.post(f"/api/leasing/offers/{their_offer}/void", json={}),
    ]

    assert [a.status_code for a in answers] == [404] * 9
    assert client.get("/api/leasing/offers", params={"show": "all"}).json()["items"] == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_the_offer_endpoints(client, conn, app_conn, pmc, place, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403
    some = uuid.uuid4()

    answers = [
        client.post(f"/api/leasing/applications/{place.application}/offer", json=terms()),
        client.get("/api/leasing/leases"),
        client.post(f"/api/leasing/leases/{place.lease}/renewal", json=terms()),
        client.get("/api/leasing/offers"),
        client.get(f"/api/leasing/offers/{some}/links"),
        client.post(f"/api/leasing/offers/{some}/signatures", json={}),
        client.post(f"/api/leasing/offers/{some}/complete", json={}),
        client.get(f"/api/leasing/offers/{some}/document"),
        client.get(f"/api/leasing/offers/{some}"),
        client.post(f"/api/leasing/offers/{some}/send", json={}),
        client.post(f"/api/leasing/offers/{some}/void", json={}),
    ]

    assert [a.status_code for a in answers] == [expected] * 11


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff"])
def test_only_a_tenant_reaches_the_renewal_endpoints(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403
    some = uuid.uuid4()

    answers = [
        client.get("/api/tenant/renewals"),
        client.post(f"/api/tenant/renewals/{some}/signing-link", json={}),
        client.get(f"/api/tenant/renewals/{some}/document"),
    ]

    assert [a.status_code for a in answers] == [expected] * 3


def test_staff_download_the_lease_they_offered(client, staff, place):
    made = offer(client, place)

    answer = client.get(f"/api/leasing/offers/{made['offer']['id']}/document")

    assert answer.content == LEASE_PDF
    assert answer.headers["content-type"] == "application/pdf"


def test_an_application_with_no_applicants_needs_signers_named(client, conn, staff, place):
    pmc_id = place.pmc.pmc_id
    staff_id = add_staff(conn, pmc_id, "Deciding staff")
    link = open_application_link(conn, pmc_id, place.unit, staff_id)
    nobody = submit_application(conn, pmc_id, link, applicants=())
    record_decision(conn, pmc_id, nobody, staff_id, "approved")

    answer = client.post(f"/api/leasing/applications/{nobody}/offer", json=terms())

    assert (answer.status_code, answer.json()["detail"]) == (422, "name who signs")


def test_an_offer_never_sent_has_no_links(client, other_client, conn, app_conn, staff, place):
    pmc_id = place.pmc.pmc_id
    tenant = join_as(other_client, app_conn, pmc_id, "tenant", place.tenant)
    staff_id = add_staff(conn, pmc_id, "Offering staff")
    unsent = offer_lease(
        conn,
        pmc_id,
        place.leased_unit,
        staff_id,
        JUN_30,
        renews_lease_id=place.lease,
        signers=(("Tenant 1", tenant.email),),
    )

    links = client.get(f"/api/leasing/offers/{unsent}/links")
    signing = other_client.post(f"/api/tenant/renewals/{unsent}/signing-link", json={})

    assert (links.status_code, links.json()["detail"]) == (
        409,
        "this offer was never sent for signing",
    )
    # A draft is not the tenant's to see.
    assert (signing.status_code, signing.json()["detail"]) == (
        404,
        "no such renewal offer of yours",
    )


def test_a_renewal_that_is_a_lease_already_gives_no_signing_link(
    client, other_client, app_conn, staff, place
):
    tenant = join_as(other_client, app_conn, place.pmc.pmc_id, "tenant", place.tenant)
    renewal = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2027-07-01",
            ends_on=None,
            signers=[{"name": "Tenant 1", "email": tenant.email}],
        ),
    ).json()
    sign(other_client, renewal["links"][0], "Tenant 1")
    client.post(f"/api/leasing/offers/{renewal['offer']['id']}/complete", json={})

    answer = other_client.post(
        f"/api/tenant/renewals/{renewal['offer']['id']}/signing-link", json={}
    )

    assert (answer.status_code, answer.json()["detail"]) == (409, "this renewal is a lease already")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/leasing/offers?show=some"),
        ("get", "/api/leasing/offers?application=not-one"),
        ("get", "/api/leasing/offers/not-an-offer"),
        ("post", "/api/leasing/offers/not-an-offer/complete"),
        ("post", "/api/leasing/offers/not-an-offer/send"),
        ("post", "/api/leasing/offers/not-an-offer/void"),
        ("post", "/api/leasing/leases/not-a-lease/renewal"),
    ],
)
def test_bad_offer_paths_are_refused(client, staff, method, path):
    answer = client.get(path) if method == "get" else client.post(path, json=terms())

    assert answer.status_code == 422


# --- Draft leases (ADR 0006): drafts, a deposit and monthly charges, voids ------------------


def signed_all(client, other_client, made):
    for link in made["links"]:
        assert sign(other_client, link, link["name"]).status_code == 204


def test_staff_keep_an_offer_as_a_draft_and_send_it_later(client, staff, place):
    made = offer(client, place, send=False)
    path = f"/api/leasing/offers/{made['offer']['id']}"

    early = client.post(f"{path}/complete", json={})
    page = client.get(path).json()
    sent = client.post(f"{path}/send", json={})
    again = client.post(f"{path}/send", json={})

    assert (made["offer"]["status"], made["offer"]["sent"], made["links"]) == ("draft", False, [])
    assert page == made["offer"]
    assert (early.status_code, early.json()["detail"]) == (
        409,
        "this offer is a draft: send it to its signers first",
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["offer"]["status"] == "sent"
    assert [link["name"] for link in sent.json()["links"]] == ["Applicant A", "Applicant B"]
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "this offer was sent already; its links are under Resend",
    )


def test_a_draft_needs_no_partner_until_it_is_sent(app_conn, conn, database_url, pmc, place):
    bare = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(bare)) as plain:
        join_as_staff(plain, conn, app_conn, pmc)
        drafted = plain.post(
            f"/api/leasing/applications/{place.application}/offer", json=terms(send=False)
        )
        sending = plain.post(f"/api/leasing/offers/{drafted.json()['offer']['id']}/send", json={})

    assert drafted.status_code == 201, drafted.text
    assert (sending.status_code, sending.json()["detail"]) == (
        503,
        "no e-sign partner is set up on this server",
    )


def test_an_offer_asks_a_deposit_and_its_monthly_charges_become_the_leases(
    client, other_client, conn, staff, place
):
    charges = [
        {"kind": "fee", "memo": "  Pet rent ", "amount": "35.00"},
        {"kind": "credit", "memo": "Autopay", "amount": "10.00"},
    ]
    made = offer(client, place, security_deposit="1500.00", recurring_charges=charges)
    signed_all(client, other_client, made)

    done = client.post(f"/api/leasing/offers/{made['offer']['id']}/complete", json={}).json()

    assert made["offer"]["security_deposit"] == "1500.00"
    assert made["offer"]["recurring_charges"] == [
        {"kind": "fee", "memo": "Pet rent", "amount": "35.00"},
        {"kind": "credit", "memo": "Autopay", "amount": "10.00"},
    ]
    assert done["status"] == "leased"
    assert conn.execute(
        "SELECT kind, amount, memo, starts_on FROM trust_recurring_charges"
        " WHERE lease_id = %s ORDER BY created_at, kind DESC",
        (done["lease_id"],),
    ).fetchall() == [("fee", 35, "Pet rent", JAN_1), ("credit", 10, "Autopay", JAN_1)]


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        (
            {"recurring_charges": [{"kind": "fee", "memo": "   ", "amount": "5.00"}]},
            "every monthly charge says what it is for",
        ),
        ({"recurring_charges": [{"kind": "fee", "memo": "Parking", "amount": "5"}] * 11}, None),
        ({"recurring_charges": [{"kind": "rent", "memo": "Parking", "amount": "5"}]}, None),
        ({"recurring_charges": [{"kind": "fee", "memo": "Parking", "amount": "0"}]}, None),
        ({"security_deposit": "0"}, None),
        ({"security_deposit": "10.001"}, None),
    ],
)
def test_bad_deposits_and_monthly_charges_are_refused(client, conn, staff, place, changes, detail):
    answer = client.post(
        f"/api/leasing/applications/{place.application}/offer", json=terms(**changes)
    )

    assert answer.status_code == 422, answer.text
    if detail:
        assert answer.json()["detail"] == detail
    assert conn.execute(
        "SELECT count(*) FROM trust_lease_offers WHERE application_id = %s", (place.application,)
    ).fetchone() == (0,)


def test_staff_void_an_offer_and_it_takes_nothing_more(client, other_client, conn, staff, place):
    made = offer(client, place)
    path = f"/api/leasing/offers/{made['offer']['id']}"

    voided = client.post(f"{path}/void", json={"reason": "  Terms changed "})
    signed_all(client, other_client, made)  # on the partner's page, after the void
    answers = [
        client.post(f"{path}/send", json={}),
        client.get(f"{path}/links"),
        client.post(f"{path}/complete", json={}),
        client.post(f"{path}/void", json={}),
    ]
    checked = client.post(f"{path}/signatures", json={}).json()

    assert voided.status_code == 200, voided.text
    body = voided.json()
    assert (body["status"], body["void"]["reason"], body["void"]["recorded_by"]) == (
        "void",
        "Terms changed",
        "Staff 1",
    )
    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (409, "this lease offer was voided; it takes nothing more"),
        (409, "this lease offer was voided; it takes nothing more"),
        (409, "this lease offer was voided; it takes nothing more"),
        (409, "this offer is voided already"),
    ]
    assert [s["signed_at"] for s in checked["signers"]] == [None, None]  # never recorded
    assert conn.execute(
        "SELECT recorded_by FROM trust_lease_offer_voids WHERE offer_id = %s",
        (made["offer"]["id"],),
    ).fetchone() == (staff.person_id,)


def test_a_lease_is_not_voided(client, other_client, staff, place):
    made = offer(client, place)
    signed_all(client, other_client, made)
    client.post(f"/api/leasing/offers/{made['offer']['id']}/complete", json={})

    answer = client.post(f"/api/leasing/offers/{made['offer']['id']}/void", json={})

    assert (answer.status_code, answer.json()["detail"]) == (409, "this offer is a lease already")


def test_a_lease_made_while_a_void_was_on_its_way_refuses_it_in_words(
    client, other_client, staff, place, monkeypatch
):
    # Staff read the offer as signed; it became the lease before the void landed.
    made = offer(client, place)
    signed_all(client, other_client, made)
    client.post(f"/api/leasing/offers/{made['offer']['id']}/complete", json={})
    read = offers.offer_of
    stale = iter([True])

    def as_first_read(conn, pmc_id, offer_id):
        found = read(conn, pmc_id, offer_id)
        return found.model_copy(update={"status": "signed"}) if next(stale, False) else found

    monkeypatch.setattr(offers, "offer_of", as_first_read)
    answer = client.post(f"/api/leasing/offers/{made['offer']['id']}/void", json={})

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "this offer is a lease already; it is not voided",
    )


def test_a_void_made_while_a_draft_was_being_sent_refuses_the_send_in_words(
    client, conn, staff, place, monkeypatch
):
    made = offer(client, place, send=False)
    conn.execute(
        "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id) VALUES (%s, %s)",
        (place.pmc.pmc_id, made["offer"]["id"]),
    )
    read = offers.offer_of
    stale = iter([True])

    def as_first_read(conn, pmc_id, offer_id):
        found = read(conn, pmc_id, offer_id)
        return found.model_copy(update={"status": "draft"}) if next(stale, False) else found

    monkeypatch.setattr(offers, "offer_of", as_first_read)
    answer = client.post(f"/api/leasing/offers/{made['offer']['id']}/send", json={})

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "this lease offer was voided; it takes nothing more",
    )


def test_the_list_shows_offers_by_where_they_stand(client, other_client, conn, staff, place):
    draft = offer(client, place, send=False)["offer"]["id"]
    voided = offer(client, place)["offer"]["id"]
    client.post(f"/api/leasing/offers/{voided}/void", json={})
    leased = offer(client, place)
    signed_all(client, other_client, leased)
    client.post(f"/api/leasing/offers/{leased['offer']['id']}/complete", json={})
    renewing = client.post(
        f"/api/leasing/leases/{place.lease}/renewal",
        json=terms(
            starts_on="2027-07-01",
            ends_on=None,
            signers=[{"name": "Tenant 1", "email": "t@example.com"}],
        ),
    )
    assert renewing.status_code == 201, renewing.text
    renewal = renewing.json()["offer"]["id"]

    def shown(**params):
        page = client.get("/api/leasing/offers", params=params).json()
        return [(o["id"], o["status"]) for o in page["items"]], page["total"]

    assert shown() == ([(renewal, "sent"), (draft, "draft")], 2)
    assert shown(show="leased") == ([(leased["offer"]["id"], "leased")], 1)
    assert shown(show="void") == ([(voided, "void")], 1)
    assert shown(show="all")[1] == 4
    assert shown(show="all", application=str(place.application))[1] == 3
    assert shown(show="all", per_page=1, page=2) == ([(leased["offer"]["id"], "leased")], 4)
    assert shown(property=str(place.pmc.owners[1].property_id)) == ([], 0)


def test_a_partly_signed_offer_says_so_and_today_lists_it(client, other_client, staff, place):
    made = offer(client, place)
    sign(other_client, made["links"][0], "Applicant A")
    client.post(f"/api/leasing/offers/{made['offer']['id']}/signatures", json={})
    offer(client, place, send=False)  # a draft is not waiting on signers

    found = client.get(f"/api/leasing/offers/{made['offer']['id']}").json()
    today = client.get("/api/today").json()

    assert found["status"] == "partially signed"
    assert [o["id"] for o in today["unsigned_offers"]] == [made["offer"]["id"]]


# --- signing links by email (migration 0090; the founder's review, q081) ---------------------


class Kept:
    """The server's sender, keeping what it sends."""

    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def test_sending_an_offer_emails_each_signer_their_own_link(client, conn, staff, place):
    kept = Kept()
    client.app.state.email = kept

    made = offer(client, place)
    again = client.post(f"/api/leasing/offers/{made['offer']['id']}/send", json={})

    assert made["emailed"] == ["a@example.com", "b@example.com"]
    by_address = {m.to: m for m in kept.sent}
    for link in made["links"]:
        message = by_address[link["email"]]
        assert message.subject.endswith(": the lease for Property 1, Unit 1 to sign")
        assert f"Hello {link['name']}," in message.body
        assert link["url"] in message.body
    # The links themselves were never written to the outbox.
    rows = conn.execute(
        "SELECT signer_position, needs_link, body FROM trust_emails"
        " WHERE offer_id = %s AND kind = 'signing_request' ORDER BY signer_position",
        (made["offer"]["id"],),
    ).fetchall()
    assert [(position, needs) for position, needs, _ in rows] == [(1, True), (2, True)]
    assert all("/esign#" not in body and "{{link}}" in body for _, _, body in rows)
    assert again.status_code == 409


def test_an_offer_sent_while_the_server_sends_no_email_emails_nobody(client, conn, staff, place):
    made = offer(client, place)

    assert made["emailed"] == []
    assert conn.execute(
        "SELECT count(*) FROM trust_emails WHERE offer_id = %s", (made["offer"]["id"],)
    ).fetchone() == (0,)


def test_a_signer_is_emailed_their_link_once(conn, place, staff, client):
    made = offer(client, place)
    offer_id = made["offer"]["id"]
    args = (conn, place.pmc.pmc_id, offer_id, 1, "Applicant A", "a@example.com", "a lease", "u")

    first = outgoing.write_signing_request(*args)
    second = outgoing.write_signing_request(*args)

    assert first is not None
    assert second is None
