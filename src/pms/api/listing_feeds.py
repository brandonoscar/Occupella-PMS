"""Listing syndication (migration 0250, the founder's decisions of 10/23): the company's listings
on its public vacancy page that staff chose to send to listing sites, as the feeds the sites
fetch: Zillow's rental listing feed and the MITS Property Marketing/ILS format
(src/pms/listing_feeds.py writes them).

Staff choose per listing on Leasing, Listings (Send to listing sites, Stop sending), and keep at
Settings, Listing feeds the leasing contact every listing names (the sites ask an email and a
phone), where each feed's URL is shown to give the site. Signing up with each site is the
company's own step; the PMS only publishes the feed, logs in nowhere and sends nothing.

A listing is in a feed while it is on the public page and staff chose it, its property has its
street address and its unit its bedrooms and bathrooms (the sites ask all three); a feed lists
nothing until the company has its mailing address (Company setup) and a leasing contact. The
feeds need no login, as the public page doesn't: they say what the page says, with the street
address and the photos' and apply page's full addresses.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, BeforeValidator, StringConstraints

from pms import listing_feeds
from pms.api import company, outbox
from pms.api.deps import Conn, Staff
from pms.api.listings import LISTED, NEEDS, PHOTOS

router = APIRouter()

Site = Literal["zillow", "mits"]
# Each feed: who takes it, in words, and what writes it.
SITES: dict[Site, tuple[str, Callable[[listing_feeds.Feed], bytes]]] = {
    "zillow": ("Zillow, Trulia and HotPads: Zillow's rental listing feed", listing_feeds.zillow),
    "mits": ("Sites that take MITS (Property Marketing/ILS), Zillow's too", listing_feeds.mits),
}

ContactName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
# A US phone number, its ten digits whatever is around them: (202) 555-0142.
Phone = Annotated[
    str,
    BeforeValidator(
        lambda value: "".join(c for c in value if c.isdigit()) if isinstance(value, str) else value
    ),
    StringConstraints(pattern=r"^[0-9]{10}$"),
]


class Contact(BaseModel):
    contact_name: str | None
    email: str
    phone: str


class NewContact(BaseModel):
    contact_name: ContactName | None = None
    email: Email
    phone: Phone


class FeedLink(BaseModel):
    site: Site
    # Who takes it, in words.
    takes: str
    url: str


class FeedSettings(BaseModel):
    contact: Contact | None
    feeds: list[FeedLink]
    # What the company still needs before a feed lists anything, in words; empty when none.
    missing: list[str]
    # Listings in the feeds now.
    listings: int


class NewChoice(BaseModel):
    in_feed: bool


class Choice(BaseModel):
    in_feed: bool
    changed: bool


def base() -> str:
    """The server's own address, as a site reaches it (PMS_PUBLIC_URL)."""
    return outbox.PUBLIC_URL.get().rstrip("/")


def feed_url(pmc_id: UUID, site: Site) -> str:
    return f"{base()}/api/listings/{pmc_id}/feeds/{site}.xml"


def contact_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Contact | None:
    row = conn.execute(
        "SELECT contact_name, email, phone FROM trust_listing_feed_contacts WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return None if row is None else Contact(contact_name=row[0], email=row[1], phone=row[2])


def company_missing(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[str]:
    missing = []
    if company.address_of(company.profile(conn, pmc_id)) is None:
        missing.append("the company's mailing address (Settings, Company setup)")
    if contact_of(conn, pmc_id) is None:
        missing.append("a leasing contact's email and phone (here)")
    return missing


# A listing's unit's latest details and its property's address (on x), as NEEDS reads them.
DETAILS = """
LEFT JOIN LATERAL (
    SELECT d.bedrooms, d.bathrooms, d.square_feet, d.created_at FROM trust_unit_details AS d
    WHERE d.unit_id = x.unit_id ORDER BY d.created_at DESC, d.id DESC LIMIT 1
) AS ud ON true
LEFT JOIN LATERAL trust_property_address(x.property_id) AS a ON true
"""

# A PMC's listings in its feeds: on the public page, chosen, with what the sites ask, by
# property and unit. Each with its property's units not archived and the first day from today
# that no lease holds its unit.
IN_FEED = f"""
WITH x AS (
    SELECT s.id AS listing_id, s.description, s.listed_at, l.monthly_rent,
           u.id AS unit_id, u.display_name AS unit, p.id AS property_id, p.display_name AS property
    FROM {LISTED} AND trust_listing_in_feed(s.id) AND NOT trust_is_archived(NULL, u.id)
)
SELECT x.listing_id, x.unit_id, x.unit, x.property_id, x.property, x.monthly_rent, x.description,
       a.line_1, a.line_2, a.city, a.state, a.zip, g.latitude, g.longitude,
       ud.bedrooms, ud.bathrooms, ud.square_feet,
       (SELECT min(c.day) FROM (
            SELECT %s::date AS day
            UNION SELECT l.ends_on + 1 FROM trust_leases AS l
            WHERE l.unit_id = x.unit_id AND l.ends_on >= %s
        ) AS c
        WHERE NOT EXISTS (
            SELECT 1 FROM trust_leases AS l
            WHERE l.unit_id = x.unit_id AND l.starts_on <= c.day
              AND (l.ends_on IS NULL OR l.ends_on >= c.day)
        )),
       (SELECT count(*) FROM trust_units AS o
        WHERE o.property_id = x.property_id AND NOT trust_is_archived(NULL, o.id)),
       array(SELECT row(d.id, d.content_type)::text FROM trust_documents AS d
             WHERE d.id IN ({PHOTOS.format(unit="x.unit_id")}) ORDER BY d.created_at, d.id),
       greatest(
           x.listed_at, a.created_at, g.created_at, ud.created_at,
           (SELECT max(c.created_at) FROM trust_listing_feed_choices AS c
            WHERE c.listing_id = x.listing_id),
           (SELECT max(greatest(d.created_at, r.created_at)) FROM trust_documents AS d
            LEFT JOIN trust_document_removals AS r ON r.document_id = d.id
            WHERE d.unit_id = x.unit_id AND d.kind = 'listing_photo')
       )
FROM x
{DETAILS}
LEFT JOIN LATERAL trust_property_location(x.property_id) AS g ON true
WHERE cardinality({NEEDS}) = 0
ORDER BY x.property COLLATE "C", x.unit COLLATE "C", x.listing_id
"""  # noqa: S608


def photo_of(pmc_id: UUID, row: str) -> listing_feeds.Photo:
    """A photo from its row's text, "(<id>,image/png)"."""
    photo_id, content_type = row.strip("()").split(",")
    return listing_feeds.Photo(
        id=UUID(photo_id),
        url=f"{base()}/api/listings/{pmc_id}/photos/{photo_id}",
        content_type=content_type,
    )


def feed_of(conn: psycopg.Connection[Any], pmc_id: UUID, today: date) -> listing_feeds.Feed:
    """What the PMC's feeds carry today: nothing until it has its mailing address and a leasing
    contact."""
    found = company.profile(conn, pmc_id)
    mailing = company.address_of(found)
    contact = contact_of(conn, pmc_id)
    name = company.name_of(conn, pmc_id)
    public = f"{base()}/listings?pmc={pmc_id}"
    rows = (
        []
        if mailing is None or contact is None
        else conn.execute(IN_FEED, (pmc_id, today, today)).fetchall()
    )
    properties: dict[UUID, listing_feeds.Property] = {}
    for (
        listing_id,
        unit_id,
        unit,
        property_id,
        prop,
        rent,
        description,
        line_1,
        line_2,
        city,
        state,
        zip_code,
        latitude,
        longitude,
        bedrooms,
        bathrooms,
        square_feet,
        free_on,
        unit_count,
        photos,
        updated_at,
    ) in rows:
        listed = listing_feeds.Unit(
            listing_id=listing_id,
            unit_id=unit_id,
            name=unit,
            rent=Decimal(rent),
            available_on=free_on,
            bedrooms=bedrooms,
            bathrooms=Decimal(bathrooms),
            square_feet=square_feet,
            description=description,
            photos=tuple(photo_of(pmc_id, photo) for photo in photos),
            apply_url=f"{base()}/apply?listing={listing_id}",
            updated_at=updated_at,
        )
        held = properties.get(property_id)
        properties[property_id] = listing_feeds.Property(
            id=property_id,
            name=prop,
            address=listing_feeds.Address(
                line_1, line_2, city, state, zip_code, latitude, longitude
            ),
            unit_count=unit_count,
            units=(*(held.units if held else ()), listed),
        )
    return listing_feeds.Feed(
        company=listing_feeds.Company(
            id=pmc_id,
            name=name,
            line_1=mailing.address_line_1 if mailing else "",
            line_2=mailing.address_line_2 if mailing else None,
            city=mailing.city if mailing else "",
            state=mailing.state if mailing else "",
            zip=mailing.postal_code if mailing else "",
            website=public,
        ),
        contact=listing_feeds.Contact(
            name=(contact.contact_name if contact else None) or name,
            email=contact.email if contact else "",
            phone=contact.phone if contact else "",
        ),
        properties=tuple(properties.values()),
        today=today,
    )


def settings_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> FeedSettings:
    today = datetime.now(UTC).date()
    return FeedSettings(
        contact=contact_of(conn, pmc_id),
        feeds=[
            FeedLink(site=site, takes=takes, url=feed_url(pmc_id, site))
            for site, (takes, _) in SITES.items()
        ],
        missing=company_missing(conn, pmc_id),
        listings=len(listing_feeds.listing_ids(feed_of(conn, pmc_id, today).properties)),
    )


@router.get("/api/leasing/feeds")
def feed_settings(session: Staff, conn: Conn) -> FeedSettings:
    """The company's listing feeds: each one's URL to give its site, the leasing contact they
    name, what they still need and how many listings they carry."""
    return settings_of(conn, session.pmc_id)


@router.post("/api/leasing/feeds")
def save_contact(session: Staff, conn: Conn, new: NewContact) -> FeedSettings:
    """Keeps the leasing contact every listing in the feeds names; the same again writes
    nothing."""
    if contact_of(conn, session.pmc_id) != Contact(**new.model_dump()):
        conn.execute(
            "INSERT INTO trust_listing_feed_contacts (pmc_id, contact_name, email, phone)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, new.contact_name, new.email, new.phone),
        )
    return settings_of(conn, session.pmc_id)


@router.post("/api/leasing/listings/{listing_id}/feed")
def choose(session: Staff, conn: Conn, listing_id: UUID, new: NewChoice) -> Choice:
    """Sends a listing on the public page to the listing sites through the feeds, or stops
    sending it; the same choice again writes nothing. One without what the sites ask (the
    property's street address, the unit's bedrooms and bathrooms) is refused, naming it."""
    row = conn.execute(
        "SELECT x.listing_id IS NOT NULL, trust_listing_in_feed(s.id)"  # noqa: S608
        f", {NEEDS} FROM trust_listings AS s"
        " LEFT JOIN LATERAL ("
        "   SELECT s.id AS listing_id, u.id AS unit_id, p.id AS property_id"
        f"  FROM {LISTED} AND s.id = %s"
        " ) AS x ON true"
        f" {DETAILS}"
        " WHERE s.pmc_id = %s AND s.id = %s",
        (session.pmc_id, listing_id, session.pmc_id, listing_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such listing")
    shown, in_feed, needs = row
    if new.in_feed == in_feed:
        return Choice(in_feed=in_feed, changed=False)
    if new.in_feed and not shown:
        raise HTTPException(409, "this listing is off the public page; list the unit again")
    if new.in_feed and needs:
        raise HTTPException(409, f"the listing sites ask {' and '.join(needs)}; add them first")
    conn.execute(
        "INSERT INTO trust_listing_feed_choices (pmc_id, listing_id, in_feed) VALUES (%s, %s, %s)",
        (session.pmc_id, listing_id, new.in_feed),
    )
    return Choice(in_feed=new.in_feed, changed=True)


def feed(conn: psycopg.Connection[Any], pmc_id: UUID, site: Site) -> Response:
    if conn.execute("SELECT 1 FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)).fetchone() is None:
        raise HTTPException(404, "no such PMC")
    _, write = SITES[site]
    return Response(
        write(feed_of(conn, pmc_id, datetime.now(UTC).date())),
        media_type="application/xml; charset=utf-8",
        headers={"Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.get("/api/listings/{pmc_id}/feeds/zillow.xml")
def zillow_feed(conn: Conn, pmc_id: UUID) -> Response:
    """The company's listings in the feeds, in Zillow's rental listing feed format. No login."""
    return feed(conn, pmc_id, "zillow")


@router.get("/api/listings/{pmc_id}/feeds/mits.xml")
def mits_feed(conn: Conn, pmc_id: UUID) -> Response:
    """The company's listings in the feeds, in the MITS Property Marketing/ILS format. No
    login."""
    return feed(conn, pmc_id, "mits")
