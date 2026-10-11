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

from pms.api import Settings, create_app

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
    assert client.get("/api/leasing/offers").json() == [made["offer"]]
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
    assert client.get("/api/leasing/offers").json() == []
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


def test_a_tenant_sees_a_renewal_not_sent_yet_and_one_on_a_server_without_a_partner(
    other_client, conn, app_conn, database_url, place
):
    pmc_id = place.pmc.pmc_id
    join_as(other_client, app_conn, pmc_id, "tenant", place.tenant)
    staff_id = add_staff(conn, pmc_id, "Offering staff")
    unsent = offer_lease(
        conn, pmc_id, place.leased_unit, staff_id, JAN_1, renews_lease_id=place.lease
    )
    sent = offer_lease(
        conn, pmc_id, place.leased_unit, staff_id, JUN_30, renews_lease_id=place.lease
    )
    send_offer(conn, pmc_id, sent, staff_id)

    with_fake = other_client.get("/api/tenant/renewals").json()
    bare = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(bare)) as plain:
        plain.cookies = other_client.cookies
        without = plain.get("/api/tenant/renewals").json()

    for seen in (with_fake, without):
        assert {o["id"]: o["sent"] for o in seen} == {str(unsent): False, str(sent): True}
        assert [s["signed_at"] for o in seen for s in o["signers"]] == [None, None]


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
    ]

    assert [a.status_code for a in answers] == [404] * 6
    assert client.get("/api/leasing/offers", params={"show": "all"}).json() == []


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
    ]

    assert [a.status_code for a in answers] == [expected] * 8


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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/leasing/offers?show=some"),
        ("post", "/api/leasing/offers/not-an-offer/complete"),
        ("post", "/api/leasing/leases/not-a-lease/renewal"),
    ],
)
def test_bad_offer_paths_are_refused(client, staff, method, path):
    answer = client.get(path) if method == "get" else client.post(path, json=terms())

    assert answer.status_code == 422
