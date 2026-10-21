"""A listing's photos and days listed through the API (migration 0087; the founder's review, q076
and q084; src/pms/api/listings.py, src/pms/api/documents.py): staff keep photos of a unit, which
the public vacancy page shows while it is listed, and see how long each listing has been up.
Every name here is synthetic."""

import base64
import uuid

import pytest
from helpers import JPEG, PDF, PNG, add_document, add_unit, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def listed_unit(client, conn, pmc, name="Unit 1"):
    """A unit with an open application link on the public page: (unit, link, listing)."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    link = ok(
        client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": "1500.00"}),
        201,
    )["id"]
    listing = ok(client.post(f"/api/leasing/links/{link}/listing", json={}), 201)["id"]
    return unit, link, listing


def photo(client, unit, content=PNG, content_type="image/png", title="Kitchen"):
    return client.post(
        "/api/documents",
        json={
            "kind": "listing_photo",
            "about_id": str(unit),
            "title": title,
            "content_type": content_type,
            "content_base64": base64.b64encode(content).decode(),
        },
    )


def test_a_listed_units_photos_show_on_the_public_page_oldest_first(
    client, other_client, conn, staff, pmc
):
    unit, _, _ = listed_unit(client, conn, pmc)
    kitchen = ok(photo(client, unit), 201)["id"]
    porch = ok(photo(client, unit, JPEG, "image/jpeg", "Porch"), 201)["id"]

    page = ok(other_client.get(f"/api/listings/{pmc.pmc_id}"))  # nobody signed in
    served = other_client.get(f"/api/listings/{pmc.pmc_id}/photos/{porch}")
    kept = ok(client.get(f"/api/leasing/units/{unit}/photos"))
    (row,) = ok(client.get("/api/leasing/vacancies"))["items"]
    files = ok(client.get("/api/documents/files", params={"kind": "listing_photo"}))

    assert page["listings"][0]["photos"] == [kitchen, porch]
    assert served.status_code == 200
    assert (served.content, served.headers["content-type"]) == (JPEG, "image/jpeg")
    assert served.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert [(p["id"], p["title"]) for p in kept] == [(kitchen, "Kitchen"), (porch, "Porch")]
    assert (row["photos"], row["days_listed"]) == (2, 1)
    assert row["listed_at"] is not None
    assert {d["id"] for d in files["items"]} == {kitchen, porch}


def test_a_removed_photo_or_an_unlisted_unit_shows_nothing(client, other_client, conn, staff, pmc):
    unit, _, listing = listed_unit(client, conn, pmc)
    kept, removed = ok(photo(client, unit), 201)["id"], ok(photo(client, unit), 201)["id"]
    # A unit's other documents are never served as a listing's.
    plan = add_document(conn, pmc.pmc_id, "unit_document", unit, PNG, "image/png", "Plan")
    # A unit with an open link that isn't listed shows no photo either.
    quiet = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    ok(client.post("/api/leasing/links", json={"unit_id": str(quiet), "monthly_rent": "900"}), 201)
    unlisted = ok(photo(client, quiet), 201)["id"]
    ok(client.post(f"/api/documents/{removed}/remove", json={}), 204)

    def served(document):
        return other_client.get(f"/api/listings/{pmc.pmc_id}/photos/{document}")

    before = [served(d).status_code for d in (kept, removed, plan, unlisted, uuid.uuid4())]
    elsewhere = other_client.get(f"/api/listings/{make_pmc(conn).pmc_id}/photos/{kept}")
    page = ok(other_client.get(f"/api/listings/{pmc.pmc_id}"))
    ok(client.post(f"/api/leasing/listings/{listing}/remove", json={}), 204)
    after = served(kept)

    assert before == [200, 404, 404, 404, 404]
    assert served(removed).json()["detail"] == "no such photo"
    assert elsewhere.status_code == 404
    assert page["listings"][0]["photos"] == [kept]
    assert after.status_code == 404
    assert [p["id"] for p in ok(client.get(f"/api/leasing/units/{unit}/photos"))] == [kept]
    counts = {r["unit"]: r["photos"] for r in ok(client.get("/api/leasing/vacancies"))["items"]}
    assert counts == {"Unit 1": 1, "Unit 2": 1}


def test_a_listing_photo_is_an_image_and_a_unit_shows_at_most_twelve(client, conn, staff, pmc):
    unit, _, _ = listed_unit(client, conn, pmc)
    pdf = photo(client, unit, PDF, "application/pdf")
    twelve = [ok(photo(client, unit, title=f"Photo {n}"), 201)["id"] for n in range(12)]
    thirteenth = photo(client, unit)
    ok(client.post(f"/api/documents/{twelve[0]}/remove", json={}), 204)
    again = photo(client, unit)
    # Another unit counts its own.
    other, _, _ = listed_unit(client, conn, pmc, "Unit 2")

    assert (pdf.status_code, pdf.json()["detail"]) == (422, "a listing photo is a JPEG or PNG")
    assert (thirteenth.status_code, thirteenth.json()["detail"]) == (
        409,
        "a unit shows at most 12 photos; remove one first",
    )
    assert again.status_code == 201
    assert photo(client, other).status_code == 201


def test_days_listed_count_the_day_it_was_listed(client, conn, staff, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    link = ok(
        client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": "1500.00"}),
        201,
    )["id"]
    before = ok(client.get("/api/leasing/vacancies"))["items"][0]
    conn.execute(
        "INSERT INTO trust_listings (pmc_id, link_id, listed_by, listed_at)"
        " VALUES (%s, %s, %s, now() - interval '9 days')",
        (pmc.pmc_id, link, staff.person_id),
    )

    (row,) = ok(client.get("/api/leasing/vacancies"))["items"]

    assert (before["listed_at"], before["days_listed"], before["photos"]) == (None, None, 0)
    assert row["days_listed"] == 10


def test_another_companys_unit_has_no_photos_to_see_or_add(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id, "Unit 9")

    seen = client.get(f"/api/leasing/units/{unit}/photos")
    added = photo(client, unit)

    assert (seen.status_code, seen.json()["detail"]) == (404, "no such unit")
    assert added.status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_a_units_photos(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")

    answers = [client.get(f"/api/leasing/units/{unit}/photos"), photo(client, unit)]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2


def test_bad_ids_are_refused(client, other_client, staff, pmc):
    assert client.get("/api/leasing/units/not-a-unit/photos").status_code == 422
    assert other_client.get(f"/api/listings/{pmc.pmc_id}/photos/not-a-photo").status_code == 422
    assert other_client.get(f"/api/listings/not-a-pmc/photos/{uuid.uuid4()}").status_code == 422
