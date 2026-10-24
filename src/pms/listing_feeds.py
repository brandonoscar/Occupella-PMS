"""A company's listings as the files listing sites fetch (the founder's decisions of 10/23): its
units on the public vacancy page that staff chose to send, in Zillow's rental listing feed
(hotPadsItems 2.1, the Rental Listing Bulk Feed Guide of 2026-08-13; Zillow publishes it on
Zillow, Trulia and HotPads) and in the MITS Property Marketing/ILS format (version 4.1's
elements, as the ILS sites and Zillow take it).

The PMS sends nothing: a site fetches the feed's URL on its own schedule, and a listing that
leaves the feed leaves the site the next time it does (the feed is the whole list each time).
These functions only write the XML, from what src/pms/api/listing_feeds.py read; they touch no
database.

How the formats are used:
- Zillow: a property with one unit is one listing (its propertyType HOUSE); a property with more
  is one multifamily listing whose units are unit models under a floorplan model for each size
  (bedrooms, bathrooms, square feet) listed. Its propertyType goes by the property's units:
  2 to 4 DIVIDED, 5 to 49 MEDIUM, 50 or more LARGE (the guide names the four kinds without
  their sizes: NOT VERIFIED with Zillow). IDs are the record's UUID without dashes, which the
  guide asks to avoid.
- MITS: each listing is a Property of its own, with one Floorplan and one ILS_Unit, as
  scattered-site managers send them. Its elements follow a MITS 4.1 file read in a public parser
  (utensils/mits); the XSD itself could not be fetched here (NOT VERIFIED against it).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from xml.etree import ElementTree

CENT = Decimal("0.01")
# The code points XML 1.0 can't carry: control characters but tab and line breaks, lone
# surrogates and the two non-characters. A description with one would make the whole file
# unreadable to a site.
NOT_XML = frozenset(
    [*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), *range(0xD800, 0xE000), 0xFFFE, 0xFFFF]
)
LEFT_OUT = dict.fromkeys(NOT_XML)
DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'


@dataclass(frozen=True)
class Contact:
    """Whom a renter reaches about every listing: a name, an email, a phone of ten digits."""

    name: str
    email: str
    phone: str


@dataclass(frozen=True)
class Company:
    id: UUID
    name: str
    # Its mailing address; Zillow asks the company's city and state.
    line_1: str
    line_2: str | None
    city: str
    state: str
    zip: str
    # The company's public vacancy page.
    website: str


@dataclass(frozen=True)
class Address:
    line_1: str
    line_2: str | None
    city: str
    state: str
    zip: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None


@dataclass(frozen=True)
class Photo:
    id: UUID
    url: str
    content_type: str


@dataclass(frozen=True)
class Unit:
    listing_id: UUID
    unit_id: UUID
    name: str
    rent: Decimal
    # The first day no lease holds it; None when a lease runs on with no end.
    available_on: date | None
    bedrooms: int
    # In halves: 1.5 is one full bathroom and a half one.
    bathrooms: Decimal
    square_feet: int | None
    description: str | None
    photos: tuple[Photo, ...]
    # Where a renter applies: the listing's own apply page.
    apply_url: str
    updated_at: datetime


@dataclass(frozen=True)
class Property:
    id: UUID
    name: str
    address: Address
    # Its units in the PMS (not archived), listed or not.
    unit_count: int
    units: tuple[Unit, ...]


@dataclass(frozen=True)
class Feed:
    company: Company
    contact: Contact
    properties: tuple[Property, ...]
    today: date


def text(value: str) -> str:
    """Text as XML 1.0 carries it: what it can't is left out, and a line ends in a line feed (a
    reader turns a carriage return into one anyway)."""
    return value.replace("\r\n", "\n").replace("\r", "\n").translate(LEFT_OUT)


def ident(value: UUID) -> str:
    """An ID as the sites take it: letters and digits only."""
    return value.hex


def money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def baths(value: Decimal) -> str:
    """Bathrooms as they read: 1, 1.5 (as src/pms/api/unit_details.py shows them)."""
    return str(int(value)) if value == value.to_integral_value() else format(value, ".1f")


def full_and_half(value: Decimal) -> tuple[int, int]:
    full = int(value)
    return full, 0 if value == full else 1


def zillow_kind(unit_count: int) -> str:
    """The multifamily propertyType by the property's number of units (NOT VERIFIED)."""
    return "DIVIDED" if unit_count <= 4 else "MEDIUM" if unit_count < 50 else "LARGE"


def floorplan_id(unit: Unit) -> str:
    half_baths = int(unit.bathrooms * 2)
    return f"B{unit.bedrooms}H{half_baths}S{unit.square_feet or 0}"


def floorplan_name(unit: Unit) -> str:
    size = f", {unit.square_feet} sq ft" if unit.square_feet else ""
    return f"{unit.bedrooms} bd, {baths(unit.bathrooms)} ba{size}"


def timestamp(at: datetime) -> str:
    """A UTC time as Zillow's guide writes it: 2026-10-23T12:00:00.000Z."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def add(
    parent: ElementTree.Element, tag: str, value: object = None, **attributes: str
) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag, {name: text(v) for name, v in attributes.items()})
    if value is not None:
        element.text = text(str(value))
    return element


def written(root: ElementTree.Element) -> bytes:
    ElementTree.indent(root)
    return DECLARATION + ElementTree.tostring(root, encoding="unicode").encode() + b"\n"


# --- Zillow's rental listing feed ------------------------------------------------------------


def zillow(feed: Feed) -> bytes:
    """The feed in Zillow's format: the company, then a listing for each property with units in
    the feed."""
    root = ElementTree.Element("hotPadsItems", version="2.1")
    company = add(root, "Company", id=ident(feed.company.id))
    add(company, "name", feed.company.name)
    add(company, "website", feed.company.website)
    add(company, "city", feed.company.city)
    add(company, "state", feed.company.state)
    for prop in feed.properties:
        if not prop.units:
            continue
        if prop.unit_count <= 1 and len(prop.units) == 1:
            single_listing(root, feed, prop, prop.units[0])
        else:
            multifamily_listing(root, feed, prop)
    return written(root)


def where(listing: ElementTree.Element, address: Address, unit: str | None) -> None:
    if unit:
        add(listing, "unit", unit)
    add(listing, "street", address.line_1, hide="false")
    add(listing, "city", address.city)
    add(listing, "state", address.state)
    add(listing, "zip", address.zip)
    add(listing, "country", "US")
    if address.latitude is not None and address.longitude is not None:
        add(listing, "latitude", address.latitude)
        add(listing, "longitude", address.longitude)


def reach(listing: ElementTree.Element, feed: Feed, updated_at: datetime) -> None:
    add(listing, "lastUpdated", timestamp(updated_at))
    add(listing, "contactName", feed.contact.name)
    add(listing, "contactEmail", feed.contact.email)
    add(listing, "contactPhone", feed.contact.phone)


def rooms(model: ElementTree.Element, unit: Unit) -> None:
    full, half = full_and_half(unit.bathrooms)
    add(model, "numBedrooms", unit.bedrooms)
    add(model, "numFullBaths", full)
    if half:
        add(model, "numHalfBaths", half)
    if unit.square_feet:
        add(model, "squareFeet", unit.square_feet)


def single_listing(root: ElementTree.Element, feed: Feed, prop: Property, unit: Unit) -> None:
    listing = add(
        root,
        "listing",
        id=ident(unit.unit_id),
        type="RENTAL",
        companyId=ident(feed.company.id),
        propertyType="HOUSE",
    )
    add(listing, "name", prop.name)
    where(listing, prop.address, prop.address.line_2)
    reach(listing, feed, unit.updated_at)
    if unit.description:
        add(listing, "description", unit.description)
    add(listing, "website", unit.apply_url)
    tag = add(listing, "listingTag", type="AVAILABILITY_URL")
    add(tag, "tag", unit.apply_url)
    for photo in unit.photos:
        add(listing, "listingPhoto", source=photo.url)
    add(listing, "price", money(unit.rent))
    add(listing, "pricingFrequency", "MONTH")
    rooms(listing, unit)
    if unit.available_on is not None:
        add(listing, "dateAvailable", unit.available_on.isoformat())
    add(listing, "providerType", "propertyManager")


def multifamily_listing(root: ElementTree.Element, feed: Feed, prop: Property) -> None:
    listing = add(
        root,
        "listing",
        id=ident(prop.id),
        type="RENTAL",
        companyId=ident(feed.company.id),
        propertyType=zillow_kind(prop.unit_count),
    )
    add(listing, "name", prop.name)
    where(listing, prop.address, None)
    reach(listing, feed, max(unit.updated_at for unit in prop.units))
    add(listing, "website", feed.company.website)
    add(listing, "numUnits", prop.unit_count)
    plans: dict[str, list[Unit]] = {}
    for unit in prop.units:
        plans.setdefault(floorplan_id(unit), []).append(unit)
    for plan, units in sorted(plans.items()):
        floorplan(listing, plan, units)
    for unit in prop.units:
        unit_model(listing, unit)
    add(listing, "providerType", "propertyManager")


def priced(model: ElementTree.Element, rents: list[Decimal]) -> None:
    add(model, "lowPrice", money(min(rents)))
    if max(rents) != min(rents):
        add(model, "highPrice", money(max(rents)))
    add(model, "pricingFrequency", "MONTH")


def floorplan(listing: ElementTree.Element, plan: str, units: list[Unit]) -> None:
    rents = [unit.rent for unit in units]
    model = add(
        listing,
        "model",
        id=plan,
        type="floorplan",
        searchable="true",
        pricingType="FLAT" if max(rents) == min(rents) else "RANGE",
    )
    priced(model, rents)
    add(model, "name", floorplan_name(units[0]))
    rooms(model, units[0])
    days = [unit.available_on for unit in units if unit.available_on is not None]
    if days:
        add(model, "dateAvailable", min(days).isoformat())


def unit_model(listing: ElementTree.Element, unit: Unit) -> None:
    model = add(
        listing,
        "model",
        id=ident(unit.unit_id),
        type="unit",
        parentModelId=floorplan_id(unit),
        searchable="true",
        pricingType="FLAT",
    )
    add(model, "unitNumber", unit.name)
    priced(model, [unit.rent])
    add(model, "name", unit.name)
    rooms(model, unit)
    if unit.available_on is not None:
        add(model, "dateAvailable", unit.available_on.isoformat())
    if unit.description:
        add(model, "description", unit.description)
    tag = add(model, "modelTag", type="AVAILABILITY_URL")
    add(tag, "tag", unit.apply_url)
    for photo in unit.photos:
        add(model, "modelPhoto", source=photo.url)


# --- MITS Property Marketing/ILS -------------------------------------------------------------


def mits(feed: Feed) -> bytes:
    """The feed in the MITS format: the company as the management, then a Property for each
    listing in the feed."""
    root = ElementTree.Element("PhysicalProperty")
    management = add(root, "Management", IDValue=ident(feed.company.id), IDType="Company")
    companies = add(add(management, "PropertyContacts"), "Companies")
    add(companies, "Identification", IDValue=ident(feed.company.id), IDType="Company")
    add(companies, "CompanyName", feed.company.name)
    mailing = Address(
        feed.company.line_1,
        feed.company.line_2,
        feed.company.city,
        feed.company.state,
        feed.company.zip,
    )
    address(companies, "other", mailing)
    add(companies, "WebSite", feed.company.website)
    for prop in feed.properties:
        for unit in prop.units:
            mits_property(root, feed, prop, unit)
    return written(root)


def address(parent: ElementTree.Element, kind: str, at: Address) -> None:
    element = add(parent, "Address", AddressType=kind)
    add(element, "AddressLine1", at.line_1)
    if at.line_2:
        add(element, "AddressLine2", at.line_2)
    add(element, "City", at.city)
    add(element, "State", at.state)
    add(element, "PostalCode", at.zip)
    add(element, "Country", "US")


def day(parent: ElementTree.Element, tag: str, on: date) -> None:
    add(parent, tag, Month=str(on.month), Day=str(on.day), Year=str(on.year))


def mits_property(root: ElementTree.Element, feed: Feed, prop: Property, unit: Unit) -> None:
    unit_id = ident(unit.unit_id)
    company = feed.company.name
    marketed = f"{prop.name}, {unit.name}"
    element = add(root, "Property", IDValue=unit_id)
    identified = add(element, "PropertyID")
    add(identified, "Identification", IDValue=unit_id, IDType="property", OrganizationName=company)
    add(
        identified,
        "Identification",
        IDValue=ident(feed.company.id),
        IDType="Company",
        OrganizationName=company,
    )
    add(identified, "MarketingName", marketed)
    add(identified, "WebSite", unit.apply_url)
    address(identified, "property", prop.address)
    add(add(identified, "Phone", PhoneType="office"), "PhoneNumber", feed.contact.phone)
    add(identified, "Email", feed.contact.email)

    ils = add(
        element,
        "ILS_Identification",
        RentalType="Market Rate",
        ILS_IdentificationType="House for Rent" if prop.unit_count <= 1 else "Apartment",
    )
    if prop.address.latitude is not None and prop.address.longitude is not None:
        add(ils, "Latitude", prop.address.latitude)
        add(ils, "Longitude", prop.address.longitude)
    day(ils, "LastUpdate", unit.updated_at.astimezone(UTC).date())

    information = add(element, "Information")
    add(information, "StructureType", "Standard")
    add(information, "UnitCount", max(prop.unit_count, 1))
    add(information, "ShortDescription", marketed)
    if unit.description:
        add(information, "LongDescription", unit.description)
    add(add(information, "Rents"), "StandardRent", money(unit.rent))
    add(information, "PropertyAvailabilityURL", unit.apply_url)

    plan = add(element, "Floorplan", IDValue=unit_id)
    add(plan, "Name", unit.name)
    add(plan, "UnitCount", 1)
    add(add(plan, "Room", RoomType="Bedroom"), "Count", unit.bedrooms)
    add(add(plan, "Room", RoomType="Bathroom"), "Count", baths(unit.bathrooms))
    if unit.square_feet:
        add(plan, "SquareFeet", Min=str(unit.square_feet), Max=str(unit.square_feet))
    add(plan, "MarketRent", Min=money(unit.rent), Max=money(unit.rent))
    add(plan, "EffectiveRent", Min=money(unit.rent), Max=money(unit.rent))

    ils_unit = add(element, "ILS_Unit", IDValue=unit_id)
    one = add(add(ils_unit, "Units"), "Unit")
    add(one, "Identification", IDValue=unit_id, OrganizationName=company)
    add(one, "MarketingName", unit.name)
    add(one, "UnitBedrooms", unit.bedrooms)
    add(one, "UnitBathrooms", baths(unit.bathrooms))
    if unit.square_feet:
        add(one, "MinSquareFeet", unit.square_feet)
        add(one, "MaxSquareFeet", unit.square_feet)
        add(one, "SquareFootType", "internal")
    add(one, "UnitRent", money(unit.rent))
    add(one, "MarketRent", money(unit.rent))
    address(one, "property", prop.address)
    availability(ils_unit, unit, feed.today)

    for rank, photo in enumerate(unit.photos, start=1):
        file = add(element, "File", FileID=ident(photo.id), Active="true")
        add(file, "FileType", "Photo")
        add(file, "Description", "Unit photo")
        add(file, "Format", photo.content_type)
        add(file, "Src", photo.url)
        add(file, "Rank", rank)


def availability(ils_unit: ElementTree.Element, unit: Unit, today: date) -> None:
    """Free today: unoccupied and ready now. Free later: occupied until the day before."""
    element = add(ils_unit, "Availability")
    if unit.available_on is None:
        add(element, "VacancyClass", "Occupied")
        return
    if unit.available_on > today:
        day(element, "VacateDate", unit.available_on - timedelta(days=1))
        add(element, "VacancyClass", "Occupied")
    else:
        add(element, "VacancyClass", "Unoccupied")
    day(element, "MadeReadyDate", max(unit.available_on, today))


def listing_ids(properties: Iterable[Property]) -> list[UUID]:
    """The listings a feed carries, in its order."""
    return [unit.listing_id for prop in properties for unit in prop.units]
