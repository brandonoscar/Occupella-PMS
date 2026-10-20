"""The PMS's own signing page through the API (src/pms/api/signing.py, ADR 0008): a signer with
nothing but their link reads the disclosures and agrees to sign electronically or asks for paper,
opens the lease, signs it, and keeps the lease and its signing certificate. Staff see where each
signer stands and download the certificate. The database's rules are tested in
tests/test_esign.py."""

import base64
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from helpers import (
    LEASE_PDF,
    PDF,
    add_document,
    add_staff,
    add_tenant,
    add_unit,
    offer_lease,
    open_application_link,
    open_lease,
    png,
    record_decision,
    send_offer,
    sign_offer,
    submit_application,
)
from people import join_as, join_as_owner, join_as_staff
from pypdf import PdfReader

from pms.api import signing
from pms.leasing import esign

SHA = hashlib.sha256(LEASE_PDF).hexdigest()


class Place:
    """An approved application for Unit 1 from two applicants."""

    def __init__(self, conn, pmc):
        pmc_id = pmc.pmc_id
        self.pmc = pmc
        self.unit = add_unit(conn, pmc_id, pmc.owners[0].property_id, "Unit 1")
        staff = add_staff(conn, pmc_id, "Leasing staff")
        link = open_application_link(conn, pmc_id, self.unit, staff)
        self.application = submit_application(
            conn,
            pmc_id,
            link,
            applicants=[("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")],
        )
        record_decision(conn, pmc_id, self.application, staff, "approved")


@pytest.fixture
def place(conn, pmc):
    return Place(conn, pmc)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def made(client, staff, place):
    """The offer, sent: its two signers' tokens."""
    answer = client.post(
        f"/api/leasing/applications/{place.application}/offer",
        json={
            "starts_on": "2027-01-01",
            "monthly_rent": "1500.00",
            "document_title": "Residential lease",
            "document_base64": base64.b64encode(LEASE_PDF).decode(),
        },
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    return SimpleNamespace(
        offer=body["offer"]["id"], tokens=[link["url"].split("#", 1)[1] for link in body["links"]]
    )


def post(browser, step, token, **more):
    return browser.post(f"/api/signing/{step}", json={"token": token, **more})


def agree(browser, token):
    seen = post(browser, "view", token).json()
    return post(browser, "consent", token, disclosure_sha256=seen["disclosure_sha256"])


def sign(browser, token, name="Applicant A", **more):
    return post(browser, "sign", token, typed_name=name, document_sha256=SHA, **more)


def pdf_text(content):
    return " ".join(
        " ".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages).split()
    )


def test_a_signer_reads_agrees_opens_and_signs_then_keeps_the_lease_and_certificate(
    client, other_client, conn, made
):
    token = made.tokens[0]

    seen = post(other_client, "view", token).json()
    agreed = agree(other_client, token)
    lease = post(other_client, "document", token)
    signed = sign(
        other_client, token, " applicant a ", drawing_base64=base64.b64encode(png()).decode()
    )
    again = post(other_client, "document", token)
    kept = post(other_client, "certificate", token)
    staff_view = client.get(f"/api/leasing/offers/{made.offer}").json()
    staff_copy = client.get(f"/api/leasing/offers/{made.offer}/certificate")

    assert (seen["status"], seen["consented"], seen["opened"], seen["signed_at"]) == (
        "open",
        False,
        False,
        None,
    )
    assert (seen["signer"], seen["role"], seen["home"]) == (
        "Applicant A",
        "tenant",
        "Property 1, Unit 1",
    )
    assert (seen["document_title"], seen["monthly_rent"], seen["ends_on"]) == (
        "Residential lease",
        "1500.00",
        None,
    )
    assert seen["document_sha256"] == SHA
    assert seen["disclosure_sha256"] == hashlib.sha256(seen["disclosure"].encode()).hexdigest()
    assert seen["disclosure"].startswith(f"{seen['company']} asks you to receive and sign")
    assert [(s["name"], s["signed"]) for s in seen["signers"]] == [
        ("Applicant A", False),
        ("Applicant B", False),
    ]
    assert agreed.json()["consented"] is True
    assert lease.content == LEASE_PDF and lease.headers["content-type"] == "application/pdf"
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "signed" and signed.json()["signed_at"]
    assert again.content == LEASE_PDF
    assert kept.headers["content-type"] == "application/pdf"
    text = pdf_text(kept.content)
    for words in ("Signing certificate", SHA, "Signer 1: Applicant A", "Name typed applicant a"):
        assert words in text, words
    assert [s["progress"] for s in staff_view["signers"]] == ["signed", "waiting"]
    assert staff_copy.status_code == 200 and pdf_text(staff_copy.content).startswith(
        "Signing certificate"
    )
    evidence = conn.execute(
        "SELECT typed_name, drawing = %s, user_agent FROM trust_signature_evidence"
        " WHERE offer_id = %s",
        (png(), made.offer),
    ).fetchone()
    assert evidence == ("applicant a", True, "testclient")
    # The signer named nobody: they have no login.
    assert conn.execute(
        "SELECT count(*), count(a.staff_id) FROM trust_audit_log AS a"
        " JOIN trust_signing_links AS l ON l.id = (a.row_data ->> 'link_id')::uuid"
        " WHERE a.table_name = 'trust_signing_events' AND l.offer_id = %s",
        (made.offer,),
    ).fetchone() == (2, 0)


def test_new_links_go_only_to_signers_who_havent_signed(client, other_client, made):
    agree(other_client, made.tokens[0])
    post(other_client, "document", made.tokens[0])
    sign(other_client, made.tokens[0])

    answer = client.post(f"/api/leasing/offers/{made.offer}/links", json={})

    assert answer.status_code == 200, answer.text
    assert [(link["position"], link["name"]) for link in answer.json()] == [(2, "Applicant B")]


def test_staff_see_how_far_each_signer_got(client, other_client, made):
    first, second = made.tokens

    def progress():
        found = client.get(f"/api/leasing/offers/{made.offer}").json()
        return [s["progress"] for s in found["signers"]]

    before = progress()
    agree(other_client, first)
    agreed = progress()
    post(other_client, "document", first)
    post(other_client, "paper", second)
    later = progress()

    assert before == ["waiting", "waiting"]
    assert agreed == ["agreed", "waiting"]
    assert later == ["opened", "paper"]


def test_a_signer_who_asks_for_paper_signs_nothing_through_that_link(client, other_client, made):
    token = made.tokens[0]
    agree(other_client, token)

    asked = post(other_client, "paper", token)
    answers = [
        agree(other_client, token),
        post(other_client, "document", token),
        sign(other_client, token),
        post(other_client, "paper", token),
    ]
    (new_link, _) = client.post(f"/api/leasing/offers/{made.offer}/links", json={}).json()
    fresh = new_link["url"].split("#", 1)[1]

    assert asked.json()["status"] == "paper"
    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (409, "you asked to sign this lease on paper")
    ] * 4
    # A new link from staff opens the way again, if the signer changes their mind.
    assert post(other_client, "view", fresh).json()["status"] == "open"
    assert agree(other_client, fresh).status_code == 200


def test_each_step_comes_in_its_order(other_client, made):
    token = made.tokens[0]

    early_lease = post(other_client, "document", token)
    early_sign = sign(other_client, token)
    agree(other_client, token)
    unopened = sign(other_client, token)
    early_certificate = post(other_client, "certificate", token)

    assert (early_lease.status_code, early_lease.json()["detail"]) == (
        409,
        "agree to sign electronically first, or ask for paper",
    )
    for refused in (early_sign, unopened):
        assert (refused.status_code, refused.json()["detail"]) == (
            409,
            "agree to sign electronically and open the lease first",
        )
    assert (early_certificate.status_code, early_certificate.json()["detail"]) == (
        409,
        "the certificate is yours once you have signed",
    )


def test_a_signer_agrees_to_the_disclosures_they_read(client, other_client, conn, made):
    token = made.tokens[0]
    seen = post(other_client, "view", token).json()
    client.post(
        "/api/company",
        json={
            "name": "Demo Co",
            "address": {
                "address_line_1": "1 Example Way",
                "city": "Springfield",
                "state": "ZZ",
                "postal_code": "00000",
            },
            "reply_to": "office@example.com",
        },
    )

    stale = post(other_client, "consent", token, disclosure_sha256=seen["disclosure_sha256"])
    now = post(other_client, "view", token).json()
    agreed = agree(other_client, token)

    assert (stale.status_code, stale.json()["detail"]) == (
        409,
        "the disclosures changed since you read them: read them again",
    )
    assert "write to Demo Co at office@example.com or at 1 Example Way" in now["disclosure"]
    assert agreed.status_code == 200
    assert conn.execute(
        "SELECT e.disclosure FROM trust_signing_events AS e"
        " JOIN trust_signing_links AS l ON l.id = e.link_id"
        " WHERE l.offer_id = %s AND e.kind = 'consented'",
        (made.offer,),
    ).fetchall() == [(now["disclosure"],)]


@pytest.mark.parametrize(
    ("more", "status", "detail"),
    [
        ({"typed_name": "Someone Else"}, 422, "type your name as it is on the lease"),
        ({"drawing_base64": "not base64!"}, 422, "the signature drawn isn't base64"),
        (
            {"drawing_base64": base64.b64encode(b"GIF89a a picture").decode()},
            422,
            "the signature drawn is a PNG of at most 100 KB",
        ),
        (
            {"drawing_base64": base64.b64encode(png(3000, 10)).decode()},
            422,
            "the signature drawn is a PNG of at most 100 KB",
        ),
        ({"document_sha256": "0" * 64}, 409, "this is not the lease sent to sign"),
    ],
)
def test_a_bad_signature_is_refused_and_nothing_is_kept(
    other_client, conn, made, more, status, detail
):
    token = made.tokens[0]
    agree(other_client, token)
    post(other_client, "document", token)

    answer = post(
        other_client,
        "sign",
        token,
        **{"typed_name": "Applicant A", "document_sha256": SHA, **more},
    )

    assert (answer.status_code, answer.json()["detail"]) == (status, detail)
    assert conn.execute(
        "SELECT count(*) FROM trust_signature_evidence WHERE offer_id = %s", (made.offer,)
    ).fetchone() == (0,)


def test_a_signer_signs_once(other_client, made):
    token = made.tokens[0]
    agree(other_client, token)
    post(other_client, "document", token)
    sign(other_client, token)

    answers = [
        sign(other_client, token),
        agree(other_client, token),
        post(other_client, "paper", token),
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (409, "you have signed this lease already")
    ] * 3


def test_a_link_that_ran_out_or_whose_offer_was_voided_signs_nothing(
    client, other_client, conn, pmc, made
):
    token = "an-old-signing-token-of-enough-length"
    conn.execute(
        "INSERT INTO trust_signing_links (pmc_id, offer_id, position, token_sha256, created_at)"
        " VALUES (%s, %s, 1, %s, %s)",
        (pmc.pmc_id, made.offer, esign.token_digest(token), datetime.now(UTC) - timedelta(days=31)),
    )
    old = post(other_client, "view", token).json()
    ran_out = agree(other_client, token)
    agree(other_client, made.tokens[1])
    post(other_client, "document", made.tokens[1])
    client.post(f"/api/leasing/offers/{made.offer}/void", json={})
    voided = post(other_client, "view", made.tokens[1]).json()
    late = sign(other_client, made.tokens[1], "Applicant B")

    assert old["status"] == "expired"
    assert (ran_out.status_code, ran_out.json()["detail"]) == (
        409,
        "this signing link has run out; ask for a new one",
    )
    assert voided["status"] == "void"
    assert (late.status_code, late.json()["detail"]) == (
        409,
        "this lease offer was withdrawn; it takes no signature",
    )


def test_a_step_refused_by_the_database_is_said_in_words(other_client, conn, made, monkeypatch):
    # The offer is voided between the page's read and its write.
    token = made.tokens[0]
    agree(other_client, token)
    post(other_client, "document", token)
    read = signing.open_link

    def as_first_read(conn_, token_):
        found = read(conn_, token_)
        conn_.execute(
            "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id) VALUES (%s, %s)",
            (found.pmc_id, found.offer_id),
        )
        return found

    monkeypatch.setattr(signing, "open_link", as_first_read)
    answers = [sign(other_client, token), post(other_client, "paper", made.tokens[1])]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (409, "this signing link no longer signs; ask for a new one")
    ] * 2


@pytest.mark.parametrize("step", ["view", "consent", "paper", "document", "sign", "certificate"])
def test_a_link_that_is_not_ours_is_not_found(other_client, made, step):
    answer = post(
        other_client,
        step,
        "a-made-up-token-of-enough-length",
        disclosure_sha256="0" * 64,
        typed_name="Applicant A",
        document_sha256=SHA,
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        404,
        "this signing link is not one of ours",
    )


@pytest.mark.parametrize(
    ("step", "body"),
    [
        ("view", {}),
        ("view", {"token": "short"}),
        ("consent", {"token": "x" * 40, "disclosure_sha256": "not a digest"}),
        ("sign", {"token": "x" * 40, "typed_name": "", "document_sha256": SHA}),
        ("sign", {"token": "x" * 40, "typed_name": "A", "document_sha256": SHA.upper()}),
        (
            "sign",
            {
                "token": "x" * 40,
                "typed_name": "A",
                "document_sha256": SHA,
                "drawing_base64": "x" * 140_001,
            },
        ),
        ("certificate", {"token": "x" * 101}),
    ],
)
def test_bad_input_is_refused(other_client, step, body):
    assert other_client.post(f"/api/signing/{step}", json=body).status_code == 422


def test_the_evidence_keeps_the_address_and_browser_a_step_came_from():
    seen = SimpleNamespace(
        client=SimpleNamespace(host="192.0.2.10"), headers={"user-agent": " A browser "}
    )
    proxied = SimpleNamespace(client=SimpleNamespace(host="not an address"), headers={})
    unknown = SimpleNamespace(client=None, headers={"user-agent": "x" * 600})

    assert signing.origin(seen) == ("192.0.2.10", "A browser")
    assert signing.origin(proxied) == (None, None)
    assert signing.origin(unknown) == (None, "x" * 500)


# --- the tenant keeps the lease as signed (q120) ------------------------------------------------


def leased(client, other_client, conn, made):
    """Both applicants sign and staff make it the lease: the lease and Applicant A's tenant id."""
    for token, name in zip(made.tokens, ("Applicant A", "Applicant B"), strict=True):
        agree(other_client, token)
        post(other_client, "document", token)
        assert sign(other_client, token, name).status_code == 200
    lease = client.post(f"/api/leasing/offers/{made.offer}/complete", json={}).json()["lease_id"]
    (tenant,) = conn.execute(
        "SELECT t.id FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        " WHERE lt.lease_id = %s AND t.display_name = 'Applicant A'",
        (lease,),
    ).fetchone()
    return lease, tenant


def test_a_tenant_downloads_their_lease_as_signed_its_certificate_and_its_documents(
    client, other_client, conn, app_conn, pmc, made
):
    lease, tenant = leased(client, other_client, conn, made)
    addendum = add_document(conn, pmc.pmc_id, "lease_document", lease, title="Pet addendum")
    removed = add_document(conn, pmc.pmc_id, "lease_document", lease, title="Old draft")
    client.post(f"/api/documents/{removed}/remove", json={})
    add_document(conn, pmc.pmc_id, "move_out_photo", lease, PDF, title="Kitchen")
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", tenant)

    (mine,) = other_client.get("/api/tenant/leases").json()
    got = {f["kind"]: other_client.get(f["url"]) for f in mine["files"]}

    assert [(f["kind"], f["title"]) for f in mine["files"]] == [
        ("signed lease", "Residential lease"),
        ("signing certificate", "Signing certificate"),
        ("lease document", "Pet addendum"),
    ]
    assert mine["files"][2]["url"] == f"/api/tenant/leases/{lease}/documents/{addendum}"
    assert got["signed lease"].content == LEASE_PDF
    assert pdf_text(got["signing certificate"].content).startswith("Signing certificate")
    assert got["lease document"].content == PDF
    assert all(answer.headers["content-type"] == "application/pdf" for answer in got.values())


def test_a_tenant_reaches_only_their_own_leases_files(
    client, other_client, conn, app_conn, pmc, made
):
    lease, _ = leased(client, other_client, conn, made)
    photo = add_document(conn, pmc.pmc_id, "move_out_photo", lease, PDF, title="Kitchen")
    addendum = add_document(conn, pmc.pmc_id, "lease_document", lease, title="Pet addendum")
    # Another tenant of the PMC, on a lease of their own made without an offer.
    prop = pmc.owners[0].property_id
    neighbour = add_tenant(conn, pmc.pmc_id, prop, "Neighbour")
    theirs = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, prop, "Unit 9"),
        "2026-01-01",
        None,
        "900",
        [neighbour],
    )
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", neighbour)
    base = f"/api/tenant/leases/{lease}"

    others = [
        other_client.get(f"{base}/signed-lease"),
        other_client.get(f"{base}/signing-certificate"),
        other_client.get(f"{base}/documents/{addendum}"),
    ]
    own = [
        other_client.get(f"/api/tenant/leases/{theirs}/signed-lease"),
        other_client.get(f"/api/tenant/leases/{theirs}/signing-certificate"),
        other_client.get(f"/api/tenant/leases/{theirs}/documents/{addendum}"),
        other_client.get(f"/api/tenant/leases/{theirs}/documents/{photo}"),
    ]

    assert [a.status_code for a in others] == [404] * 3
    assert [(a.status_code, a.json()["detail"]) for a in own] == [
        (404, "this lease wasn't signed through the PMS"),
        (404, "this lease wasn't signed through the PMS"),
        (404, "no such document"),
        (404, "no such document"),
    ]
    assert other_client.get("/api/tenant/leases").json()[0]["files"] == []


def test_a_lease_signed_through_another_partner_has_no_certificate_here(
    client, other_client, conn, app_conn, pmc, place
):
    staff = add_staff(conn, pmc.pmc_id, "Offering staff")
    offer = offer_lease(
        conn,
        pmc.pmc_id,
        place.unit,
        staff,
        "2027-01-01",
        application_id=place.application,
        signers=(("Applicant A", "a@example.com"),),
    )
    send_offer(conn, pmc.pmc_id, offer, staff)  # through another partner, before 0097
    sign_offer(conn, pmc.pmc_id, offer, 1)
    lease = conn.execute(
        "SELECT trust_complete_lease_offer(%s, %s, %s)", (pmc.pmc_id, offer, staff)
    ).fetchone()[0]
    (tenant,) = conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s", (lease,)
    ).fetchone()
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", tenant)

    (mine,) = other_client.get("/api/tenant/leases").json()
    certificate = other_client.get(f"/api/tenant/leases/{lease}/signing-certificate")

    assert [f["kind"] for f in mine["files"]] == ["signed lease"]
    assert other_client.get(mine["files"][0]["url"]).content == LEASE_PDF
    assert (certificate.status_code, certificate.json()["detail"]) == (
        404,
        "this lease was signed elsewhere: it has no certificate here",
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff"])
def test_only_a_tenant_downloads_a_leases_files(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    some = uuid.uuid4()

    answers = [
        client.get(f"/api/tenant/leases/{some}/signed-lease"),
        client.get(f"/api/tenant/leases/{some}/signing-certificate"),
        client.get(f"/api/tenant/leases/{some}/documents/{uuid.uuid4()}"),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 3


@pytest.mark.parametrize(
    "path",
    [
        "/api/tenant/leases/not-a-lease/signed-lease",
        "/api/tenant/leases/not-a-lease/signing-certificate",
        f"/api/tenant/leases/{uuid.UUID(int=1)}/documents/not-a-document",
    ],
)
def test_bad_lease_file_paths_are_refused(other_client, conn, app_conn, pmc, path):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    join_as(other_client, app_conn, pmc.pmc_id, "tenant", tenant)

    assert other_client.get(path).status_code == 422
