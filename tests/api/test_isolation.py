"""Company isolation, swept over every route of the API (CLAUDE.md, "The API": a report's PMC
always comes from the session; an owner's or a tenant's pages show only theirs; a record named
in a path that isn't the caller's reads as not found).

Two demo companies go in side by side (`pms.demo.build`), A and B, each with its manager, an
owner and a tenant signed in. Each gets the few records the demo doesn't make (a payment hold, a
notice, a payment file...), so every kind of id a route takes exists in both, and B's names get
a mark of their own (MARK). Then every route of the app is called by A's staff, owner or tenant:

- a route with ids in its path, with one of them B's and the rest A's own (and, with several,
  all of them B's): refused, as not found unless REFUSED says otherwise, never a 2xx or a 5xx;
- a write naming one of B's records in its body, the rest A's own: refused (a 4xx);
- each of those beside B's own request with B's ids, on an app whose every transaction rolls
  back: B's answer is not A's, so A's refusal is the company's and not a bad id or body; and a
  write by A leaves every row of B's (each trust_* table and B's part of pgledger's) as it was;
- every list, page and report: with no id, with B's in each id it takes (query or report
  argument) and with MARK as its search, nothing of B's in the answer (B's PMC id, the id of any
  row of B's, MARK), CSV, PDF, workbook and zip opened.

A path parameter, a body field, a query parameter or a report argument the sweep doesn't know
fails it, saying what to add, so a new route can't slip past. EXEMPT lists the few routes no
company's staff, owner or tenant calls, each with why.
"""

from __future__ import annotations

import base64
import io
import json
import re
import types
import typing
import uuid
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Union
from uuid import UUID

import psycopg
import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from people import PASSWORD, invite_token, totp_from
from psycopg import sql
from pydantic import BaseModel, TypeAdapter, ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from pms import demo
from pms.api import Settings, create_app, deps

PUBLIC_URL = "http://testserver"
TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
LAST_MONTH = demo.first_of_month(TODAY, 1)
THIS_MONTH = demo.first_of_month(TODAY, 0)
# What company B's names start with, so a name of B's in an answer is found as surely as an id.
MARK = "Bravo"

# --- the records ---------------------------------------------------------------------------
# One of a company's records of each kind (%(pmc)s is its PMC id), chosen by what the demo
# calls it, so both demos give the same one.

_LEASE_AT = (
    "SELECT l.id FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
    " JOIN trust_properties AS p ON p.id = u.property_id"
    " WHERE l.pmc_id = %(pmc)s AND p.display_name = '{0}' AND u.display_name = '{1}'"
)
_UNIT_AT = (
    "SELECT u.id FROM trust_units AS u JOIN trust_properties AS p ON p.id = u.property_id"
    " WHERE u.pmc_id = %(pmc)s AND p.display_name = '{0}' AND u.display_name = '{1}'"
)
_NAMED = "SELECT id FROM {0} WHERE pmc_id = %(pmc)s AND display_name = '{1}'"
_OWNER_ACCOUNT = (
    "SELECT a.ledger_account_id FROM trust_ledger_accounts AS a"
    " JOIN trust_properties AS p ON p.id = a.property_id"
    " WHERE a.pmc_id = %(pmc)s AND a.kind = 'owner_property' AND p.display_name = '{0}'"
)
_DOCUMENT = (
    "SELECT id FROM trust_documents WHERE pmc_id = %(pmc)s AND kind = '{0}'"
    " ORDER BY created_at DESC, id LIMIT 1"
)
_ONLY = "SELECT id FROM {0} WHERE pmc_id = %(pmc)s ORDER BY id LIMIT 1"
_LEASE = _LEASE_AT.format("Oak Terrace", "Unit 1")

RECORDS = {
    "company": "SELECT %(pmc)s::uuid",
    # Avery Demo and Jordan Sample's lease, ending in 45 days, its renewal half signed.
    "lease": _LEASE,
    "unit": _UNIT_AT.format("Oak Terrace", "Unit 1"),
    "property": _NAMED.format("trust_properties", "Oak Terrace"),
    "tenant": _NAMED.format("trust_tenants", "Avery Demo"),
    "other tenant": _NAMED.format("trust_tenants", "Jordan Sample"),
    "owner": _NAMED.format("trust_owners", "Demo Owner B"),
    "owner account": _OWNER_ACCOUNT.format("Oak Terrace"),
    "other owner account": _OWNER_ACCOUNT.format("Aspen Row"),
    "vendor": _NAMED.format("trust_vendors", "Demo Plumbing Co"),
    "staff": _NAMED.format("trust_staff", "Demo Bookkeeper"),
    "group": _NAMED.format("trust_property_groups", "North portfolio"),
    "bank": "SELECT id FROM trust_bank_accounts WHERE pmc_id = %(pmc)s AND kind = 'operating'",
    "deposit bank": "SELECT id FROM trust_bank_accounts"
    " WHERE pmc_id = %(pmc)s AND kind = 'security_deposit'",
    "bill": "SELECT id FROM trust_bills WHERE pmc_id = %(pmc)s AND reference = 'PLB-3002'",
    "expense account": "SELECT id FROM trust_expense_accounts"
    " WHERE pmc_id = %(pmc)s AND name = 'Repairs'",
    "charge type": "SELECT id FROM trust_charge_types WHERE pmc_id = %(pmc)s AND name = 'Cleaning'",
    # The plumber's, scheduled: open, so the plumber's link sees it.
    "work order": "SELECT id FROM trust_work_orders"
    " WHERE pmc_id = %(pmc)s AND summary = 'Water heater rumbles'",
    "document": _DOCUMENT.format("w9"),
    "lease document": _DOCUMENT.format("lease_document"),
    "listing photo": _DOCUMENT.format("listing_photo"),
    # The application declined on its screening, with its adverse action notice.
    "application": "SELECT a.id FROM trust_applications AS a"
    " JOIN trust_application_decisions AS d ON d.application_id = a.id"
    " WHERE a.pmc_id = %(pmc)s AND d.decision = 'declined'",
    "screening": "SELECT s.id FROM trust_screenings AS s"
    " JOIN trust_application_decisions AS d ON d.application_id = s.application_id"
    " WHERE s.pmc_id = %(pmc)s AND d.decision = 'declined'",
    "offer": "SELECT id FROM trust_lease_offers"
    f" WHERE pmc_id = %(pmc)s AND renews_lease_id = ({_LEASE})",
    "application link": "SELECT l.id FROM trust_application_links AS l"
    " JOIN trust_units AS u ON u.id = l.unit_id"
    " WHERE l.pmc_id = %(pmc)s AND u.display_name = 'Unit 409'",
    "listing": "SELECT s.id FROM trust_listings AS s"
    " JOIN trust_application_links AS l ON l.id = s.link_id"
    " JOIN trust_units AS u ON u.id = l.unit_id"
    " WHERE s.pmc_id = %(pmc)s AND u.display_name = 'Unit 409'",
    "listed unit": _UNIT_AT.format("Harbor Tower", "Unit 409"),
    "lead": _ONLY.format("trust_leads"),
    "showing": _ONLY.format("trust_showings"),
    "housing authority": _ONLY.format("trust_housing_authorities"),
    "journal entry": _ONLY.format("trust_journal_entries"),
    "custom role": _ONLY.format("trust_custom_roles"),
    # The owner signed in to the portal: Harbor Tower's, with its management agreement on file.
    "portal owner's login": "SELECT l.id FROM trust_logins AS l"
    " JOIN trust_owners AS o ON o.id = l.owner_id"
    " WHERE l.pmc_id = %(pmc)s AND o.display_name = 'Demo Owner C'",
    "portal owner's bill": "SELECT id FROM trust_bills"
    " WHERE pmc_id = %(pmc)s AND reference = 'LND-3001'",
    "portal owner's document": _DOCUMENT.format("owner_document"),
    "bank match": "SELECT id FROM trust_bank_matches WHERE pmc_id = %(pmc)s"
    " ORDER BY matched_at DESC, id LIMIT 1",
    "statement line": "SELECT l.id FROM trust_bank_statement_lines AS l"
    " JOIN trust_bank_statements AS s ON s.id = l.statement_id"
    " WHERE l.pmc_id = %(pmc)s ORDER BY s.last_day DESC, l.line_no DESC LIMIT 1",
    # The operating account's last month, waiting for the manager; the month before, approved.
    "preparation": "SELECT p.id FROM trust_reconciliation_preparations AS p"
    " JOIN trust_bank_accounts AS b ON b.id = p.bank_account_id"
    " WHERE p.pmc_id = %(pmc)s AND b.kind = 'operating'"
    " ORDER BY p.through DESC, p.created_at DESC LIMIT 1",
    "reconciliation": "SELECT id FROM trust_reconciliations WHERE pmc_id = %(pmc)s"
    " ORDER BY period_end DESC, id LIMIT 1",
    # This month's rent of the Harbor Tower tenant who paid none of it.
    "open charge": "SELECT id FROM trust_charges WHERE pmc_id = %(pmc)s AND lease_id = ("
    + _LEASE_AT.format("Harbor Tower", demo.HARBOR_UNPAID)
    + ") AND kind = 'rent' ORDER BY due_on DESC LIMIT 1",
    "fee": f"SELECT id FROM trust_charges WHERE pmc_id = %(pmc)s AND lease_id = ({_LEASE})"
    " AND kind = 'fee' ORDER BY due_on DESC, created_at DESC LIMIT 1",
    # The lease's latest payment.
    "payment": "SELECT p.transfer_id FROM trust_charge_payments AS p"
    " JOIN trust_charges AS c ON c.id = p.charge_id"
    " JOIN pgledger_transfers AS t ON t.id = p.transfer_id"
    f" WHERE c.pmc_id = %(pmc)s AND c.lease_id = ({_LEASE})"
    " ORDER BY t.event_at DESC, t.id LIMIT 1",
    "recurring charge": _ONLY.format("trust_recurring_charges"),
    "recurring bill": _ONLY.format("trust_recurring_bills"),
    # Made by fill_in: what the demo doesn't make.
    "authority payment": "SELECT transfer_id FROM trust_assistance_payments"
    " WHERE pmc_id = %(pmc)s ORDER BY transfer_id LIMIT 1",
    "recurring work order": _ONLY.format("trust_recurring_work_orders"),
    "payment hold": _ONLY.format("trust_payment_holds"),
    "notice template": "SELECT id FROM trust_notice_templates"
    " WHERE pmc_id = %(pmc)s AND used_for = 'notice'",
    "lease template": "SELECT id FROM trust_notice_templates"
    " WHERE pmc_id = %(pmc)s AND used_for = 'lease'",
    "notice": _ONLY.format("trust_notices"),
    "payment file": _ONLY.format("trust_payment_files"),
    "payment file line": _ONLY.format("trust_payment_file_lines"),
    "maintenance request": _ONLY.format("trust_maintenance_requests"),
    "bank deposit": _ONLY.format("trust_bank_deposits"),
    "occupant": _ONLY.format("trust_lease_occupants"),
    # Harbor Tower's ended lease, its deposit returned.
    "returned lease": _LEASE_AT.format("Harbor Tower", demo.HARBOR_ENDED),
    "returned lease's tenant": "SELECT tenant_id FROM trust_lease_tenants"
    " WHERE pmc_id = %(pmc)s AND lease_id = ("
    + _LEASE_AT.format("Harbor Tower", demo.HARBOR_ENDED)
    + ")",
    "online payment": _ONLY.format("trust_online_payments"),
    "autopay": _ONLY.format("trust_autopay"),
}

# A path parameter's record, by its name; then where one name means another record on a route.
PATH_IDS = {
    "lease_id": "lease",
    "unit_id": "unit",
    "property_id": "property",
    "tenant_id": "tenant",
    "owner_id": "owner",
    "vendor_id": "vendor",
    "staff_id": "staff",
    "group_id": "group",
    "bank_account_id": "bank",
    "bill_id": "bill",
    "work_order_id": "work order",
    "document_id": "document",
    "photo_id": "listing photo",
    "application_id": "application",
    "offer_id": "offer",
    "link_id": "application link",
    "listing_id": "listing",
    "lead_id": "lead",
    "showing_id": "showing",
    "authority_id": "housing authority",
    "entry_id": "journal entry",
    "role_id": "custom role",
    "login_id": "portal owner's login",
    "match_id": "bank match",
    "charge_id": "open charge",
    "transfer_id": "payment",
    "ledger_account_id": "owner account",
    "recurring_id": "recurring charge",
    "hold_id": "payment hold",
    "template_id": "notice template",
    "notice_id": "notice",
    "file_id": "payment file",
    "line_id": "payment file line",
    "request_id": "maintenance request",
    "deposit_id": "bank deposit",
    "item_id": "occupant",
    "payment_id": "online payment",
    "autopay_id": "autopay",
    "pmc_id": "company",
}
ROUTE_PATH_IDS = {
    ("/api/payables/recurring/{recurring_id}/expense-account", "recurring_id"): "recurring bill",
    ("/api/payables/recurring/{recurring_id}/end", "recurring_id"): "recurring bill",
    ("/api/work-orders/recurring/{recurring_id}/end", "recurring_id"): "recurring work order",
    ("/api/assistance/payments/{transfer_id}/bounce", "transfer_id"): "authority payment",
    ("/api/move-out/{lease_id}/fees/{charge_id}/receipts", "charge_id"): "fee",
    ("/api/owner/documents/{document_id}/file", "document_id"): "portal owner's document",
    ("/api/owner/bills/{bill_id}/approval", "bill_id"): "portal owner's bill",
    ("/api/owner/bills/{bill_id}/decline", "bill_id"): "portal owner's bill",
    ("/api/tenant/leases/{lease_id}/documents/{document_id}", "document_id"): "lease document",
    ("/api/move-out/{lease_id}/statement", "lease_id"): "returned lease",
    ("/api/move-out/{lease_id}/statement/send", "lease_id"): "returned lease",
}
# Path parameters that name no record, and what the route is sent.
NOT_ID_PARAMS = {
    # A report's name: test_no_report_shows_another_companys_books runs each one.
    ("/api/reports/{name}", "name"): None,
    # The kind of household record the item is: the occupant fill_in makes.
    ("/api/leases/{lease_id}/{kind}/{item_id}/remove", "kind"): "occupants",
}

# A body field's record, by its name; then where one name means another record on a route.
BODY_IDS = {
    "property_id": "property",
    "property_ids": "property",
    "unit_id": "unit",
    "lease_id": "lease",
    "tenant_id": "tenant",
    "tenant_ids": "tenant",
    "owner_id": "owner",
    "vendor_id": "vendor",
    "staff_id": "staff",
    "shown_by": "staff",
    "person_id": "staff",
    "group_id": "group",
    "bank_account_id": "bank",
    "bill_id": "bill",
    "bill_ids": "bill",
    "work_order_id": "work order",
    "account": "owner account",
    "owner_account": "owner account",
    "from_account": "owner account",
    "to_account": "other owner account",
    "expense_account_id": "expense account",
    "charge_type_id": "charge type",
    "housing_authority_id": "housing authority",
    "template_id": "notice template",
    "screening_id": "screening",
    "preparation_id": "preparation",
    "line_id": "statement line",
    "line_ids": "statement line",
    "transfer_ids": "payment",
    "charge_id": "open charge",
    "deposit_to": "other tenant",
    "about_id": "property",
}
ROUTE_BODY_IDS = {
    # A W-9 (the first kind of document) is about a vendor; an email (to a tenant, first) too.
    ("/api/documents", "about_id"): "vendor",
    ("/api/emails", "about_id"): "tenant",
    # Deposits are held in the security-deposit trust bank account.
    ("/api/rent/deposits", "bank_account_id"): "deposit bank",
    # A lease offer's text comes from a lease template.
    ("/api/leasing/applications/{application_id}/offer", "template_id"): "lease template",
    ("/api/leasing/leases/{lease_id}/renewal", "template_id"): "lease template",
}
# Body fields that are no record's id, and why.
NOT_ID_FIELDS = {
    "request_id": "a new key the page makes, so sending it again records nothing twice",
    "pmc_id": "the company someone signs in to, or asks a reset of, before any session: the"
    " company is what they name (test_one_companys_login_doesnt_sign_in_to_another)",
    "listing": "a unit listed on its company's public page, which anyone applies through",
}
# Id fields left out of every body, and why.
LEFT_OUT = {
    "cutover_item_ids": "the checks and deposits carried over at a switch: neither demo switched"
    " a trust bank account over (tests/api/test_switch.py covers them)",
}
# A query parameter's record, by the name the request gives it.
QUERY_IDS = {
    "property_id": "property",
    "property": "property",
    "group": "group",
    "owner": "owner",
    "owner_id": "owner",
    "tenant_id": "tenant",
    "vendor_id": "vendor",
    "vendor": "vendor",
    "lease": "lease",
    "lease_id": "lease",
    "application": "application",
    "account": "owner account",
    "about_id": "property",
}
ROUTE_QUERY_IDS = {
    ("/api/emails", "about_id"): "tenant",
    ("/api/move-out/{lease_id}/statement", "tenant_id"): "returned lease's tenant",
}
# A report argument's record, by its name.
REPORT_IDS = {
    "p_lease_id": "lease",
    "p_owner_id": "owner",
    "p_bank_account_id": "bank",
    "p_reconciliation_id": "reconciliation",
}

# Routes not swept, and why: no company's staff, owner or tenant calls them.
EXEMPT = {
    "GET /api/listings/{pmc_id}": "a company's public vacancy page: anyone reads it, and the"
    " company is the one its path names",
    "GET /api/calendar/{token}.ics": "a calendar app fetches it with no login: the token is the"
    " credential (tests/api/test_calendar.py)",
    "POST /api/payments/webhook/{name}": "the payments partner's signed report: the company is"
    " the payment's (tests/api/test_online_payments_api.py)",
    "GET /api/payments/fake/{payment_id}": "the fake payments partner's own page (development and"
    " tests only), standing in for the partner's: reached by the payment's id, with no caller",
    "POST /api/payments/fake/{payment_id}": "the fake payments partner's own page, as above",
    "GET /api/payments/fake-save/{autopay_id}": "the fake payments partner's own page, as above",
    "POST /api/payments/fake-save/{autopay_id}": "the fake payments partner's own page, as above",
    "GET /api/payments/fake": "the fake payments partner's queue of every payment it holds, as"
    " the partner's own dashboard lists them (development and tests only)",
}
# Where a route refuses another company's record in its path otherwise than as not found, and
# why (none does today).
REFUSED: dict[str, tuple[int, str]] = {}

# --- the routes ----------------------------------------------------------------------------

OWNER_ONLY = typing.get_args(deps.Owner)[1].dependency
TENANT_ONLY = typing.get_args(deps.Tenant)[1].dependency
READS = {"GET", "HEAD"}
REPORTS_ROUTE = "GET /api/reports/{name}"


def walk(found: list[Any]) -> Iterator[APIRoute]:
    for route in found:
        if hasattr(route, "original_router"):  # an included router, as FastAPI keeps it
            yield from walk(route.original_router.routes)
        elif isinstance(route, APIRoute):
            yield route


def calls(dependant: Any) -> Iterator[Any]:
    yield dependant.call
    for sub in dependant.dependencies:
        yield from calls(sub)


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    caller: str  # staff, owner, tenant, signed in (any of them), vendor (by its link) or public
    params: tuple[str, ...]  # the path's ids
    body: type[BaseModel] | None
    queries: tuple[Any, ...]

    @property
    def name(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def writes(self) -> bool:
        return self.method not in READS

    def query_record(self, name: str) -> str | None:
        return ROUTE_QUERY_IDS.get((self.path, name), QUERY_IDS.get(name))


def caller_of(route: APIRoute) -> str:
    found = set(calls(route.dependant))
    if deps.staff_only in found:
        return "staff"
    if OWNER_ONLY in found:
        return "owner"
    if TENANT_ONLY in found:
        return "tenant"
    if deps.current in found:
        return "signed in"
    if route.path.startswith("/api/vendor-link/"):
        return "vendor"
    return "public"


def routes_of(app: Any) -> list[Route]:
    found = []
    for route in walk(app.routes):
        names = re.findall(r"{(\w+)}", route.path)
        body = route.body_field.field_info.annotation if route.body_field is not None else None
        for method in sorted(route.methods):
            found.append(
                Route(
                    method,
                    route.path,
                    caller_of(route),
                    tuple(n for n in names if (route.path, n) not in NOT_ID_PARAMS),
                    body,
                    tuple(route.dependant.query_params),
                )
            )
    return found


ROUTES = routes_of(create_app(Settings(database_url="postgresql://unused")))
SWEPT = [r for r in ROUTES if r.name not in EXEMPT]


def query_name(query: Any) -> str:
    return query.alias or query.name


def unwrap(annotation: Any) -> tuple[Any, list[Any]]:
    """The type under Annotated, with its metadata."""
    extra: list[Any] = []
    while typing.get_origin(annotation) is Annotated:
        annotation, *more = typing.get_args(annotation)
        extra += more
    return annotation, extra


def takes_uuid(annotation: Any) -> bool:
    kind, _ = unwrap(annotation)
    if typing.get_origin(kind) in (Union, types.UnionType, list):
        return any(takes_uuid(arm) for arm in typing.get_args(kind))
    return kind is UUID


def unknown_query_ids(route: Route) -> list[str]:
    return [
        query_name(q)
        for q in route.queries
        if takes_uuid(q.field_info.annotation) and route.query_record(query_name(q)) is None
    ]


def path_cases() -> list[Any]:
    cases = []
    for route in SWEPT:
        if not route.params:
            continue
        ids = [("path", p) for p in route.params] + [
            ("query", query_name(q))
            for q in route.queries
            if q.field_info.is_required() and route.query_record(query_name(q)) is not None
        ]
        variants: list[Any] = list(ids)
        # Every id another company's is that company's own request when no company asks.
        if len(ids) > 1 and route.caller != "public":
            variants.append("every id")
        cases += [
            pytest.param(route, v, id=f"{route.name} [{v if isinstance(v, str) else v[1]}]")
            for v in variants
        ]
    return cases


# --- the bodies ----------------------------------------------------------------------------

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
# A field's value by its name, where any value of its type would not do.
TEXT = {
    "email": "isolation@example.com",
    "reply_to": "isolation@example.com",
    "phone": "555-0100",
    "alternate_phone": "555-0101",
    "state": "CA",
    "plate_state": "CA",
    "postal_code": "12345",
    "zip": "12345",
    "content_type": "application/pdf",
    "content_base64": base64.b64encode(PDF).decode(),
    "document_base64": base64.b64encode(PDF).decode(),
    "drawing_base64": base64.b64encode(PNG).decode(),
    "file_name": "isolation.csv",
    "visit_window": "8-10 am",
}
MONEY = re.compile(
    r"amount|rent|fee|price|balance|cash|deposit|limit|income|maximum|minimum|reserve|markup"
    r"|percent|allowance|liability|interest|usage|multiple|square"
)


@dataclass(frozen=True)
class Rec:
    """In a hand-made body: the company's record of this kind, swapped like any id field."""

    kind: str


# A route's body where what its model makes up would not reach what the route does: by field.
BODIES: dict[str, dict[str, Any]] = {
    "POST /api/vendor-link/work-orders/{work_order_id}/steps": {
        "step": "completed",
        "note": "Done",
    },
    # A lead is reached by an email or a phone.
    "POST /api/leasing/leads": {"email": "lead@example.com"},
    # Two owners' shares of one invoice.
    "POST /api/payables/shared-bills": {
        "shares": [
            {"account": Rec("owner account"), "amount": "10.00"},
            {"account": Rec("other owner account"), "amount": "10.00"},
        ]
    },
    "POST /api/payables/management-fees": {
        "period_start": str(LAST_MONTH),
        "period_end": str(THIS_MONTH - DAY),
    },
    # The routes no session calls: the token, code or digest each takes, made up (and refused).
    "POST /api/invites/confirm": {"code": "123456"},
    "POST /api/apply/view": {"token": "x" * 43},
    "POST /api/apply": {"token": "x" * 43},
    "POST /api/signing/consent": {"disclosure_sha256": "0" * 64},
    "POST /api/signing/sign": {"document_sha256": "0" * 64},
    "POST /api/budgets": {
        "lines": [
            {
                "line": "expense account",
                "expense_account_id": Rec("expense account"),
                "months": ["10.00"] * 12,
            }
        ]
    },
}


class BodyError(Exception):
    pass


def candidates(name: str, kind: Any) -> list[Any]:
    if name in TEXT:
        return [TEXT[name]]
    if kind is bool:
        return [False, True]
    if kind is int:
        return [1, 12, 28, TODAY.year, 100, 0]
    if kind is float:
        return [1.0]
    if kind is Decimal:
        return ["10.00", "1"]
    if kind is date:
        return [TODAY.isoformat()]
    if kind is datetime:
        return [datetime.now(UTC).isoformat()]
    if kind is UUID:
        return [str(uuid.uuid4())]
    words = ["Isolation check", "10.00", TODAY.isoformat(), "1", "isolation@example.com"]
    if MONEY.search(name):
        words.insert(0, "10.00")
    if name.endswith(("_on", "_day", "day")):
        words.insert(0, TODAY.isoformat())
    return words


class Bodies:
    """A route's body made from its model: each id field one of the company's own records (or
    another company's, where `ids` says), every other required field a value the model takes."""

    def __init__(self, route: Route, ids: Callable[[str, tuple[Any, ...]], Any]) -> None:
        self.route = route
        self.ids = ids
        self.sent: set[str] = set()

    def record(self, kind: str, at: tuple[Any, ...]) -> str:
        found = str(self.ids(kind, at))
        self.sent.add(found)
        return found

    def given(self, value: Any, at: tuple[Any, ...]) -> Any:
        if isinstance(value, Rec):
            return self.record(value.kind, at)
        if isinstance(value, dict):
            return {k: self.given(v, (*at, k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.given(v, (*at, n)) for n, v in enumerate(value)]
        return value

    def model(self, model: type[BaseModel], at: tuple[Any, ...]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        override = BODIES.get(self.route.name, {}) if not at else {}
        for name, info in model.model_fields.items():
            here = (*at, name)
            if name in override:
                body[name] = self.given(override[name], here)
                continue
            if name in LEFT_OUT:
                continue
            record = ROUTE_BODY_IDS.get((self.route.path, name), BODY_IDS.get(name))
            if record is not None or info.is_required():
                body[name] = self.value(name, info.annotation, info.metadata, here, record)
        try:
            model.model_validate(json.loads(json.dumps(body)))
        except ValidationError as error:
            first = error.errors()[0]
            raise BodyError(
                f"{self.route.name}: the body made from {model.__name__} is refused by it"
                f" ({first['loc']}: {first['msg']}); give the route what it takes in BODIES"
            ) from None
        return body

    def value(
        self, name: str, annotation: Any, meta: list[Any], at: tuple[Any, ...], record: Any
    ) -> Any:
        kind, extra = unwrap(annotation)
        meta = [*meta, *extra]
        origin = typing.get_origin(kind)
        if origin in (Union, types.UnionType):
            arms = [a for a in typing.get_args(kind) if a is not type(None)]
            return self.value(name, arms[0], meta, at, record)
        if origin is Literal:
            return typing.get_args(kind)[0]
        if origin in (list, tuple, set, frozenset):
            inner = typing.get_args(kind)[0]
            least = max([getattr(m, "min_length", 0) or 0 for m in meta] + [1])
            return [self.value(name, inner, [], (*at, n), record) for n in range(least)]
        if isinstance(kind, type) and issubclass(kind, BaseModel):
            return self.model(kind, at)
        if isinstance(kind, type) and issubclass(kind, Enum):
            return next(iter(kind)).value
        if record is not None:
            return self.record(record, at)
        if kind is UUID and name not in NOT_ID_FIELDS:
            raise BodyError(
                f"{self.route.name}: body field {name!r} is an id the sweep doesn't know: say"
                " whose record it is in BODY_IDS (or ROUTE_BODY_IDS), or why it is none in"
                " NOT_ID_FIELDS"
            )
        adapter = TypeAdapter(Annotated[kind, *meta] if meta else kind)
        for candidate in candidates(name, kind):
            try:
                adapter.validate_python(candidate)
            except ValidationError:
                continue
            return candidate
        raise BodyError(
            f"{self.route.name}: no value the sweep makes fits field {name!r}; give it one in"
            " TEXT or BODIES"
        )


def id_fields(route: Route) -> list[tuple[Any, ...]]:
    """Where the ids are in the body the route is sent: each a path of field names and list
    positions, as Bodies makes it."""
    if route.body is None:
        return []
    found: list[tuple[Any, ...]] = []

    def note(kind: str, at: tuple[Any, ...]) -> str:
        found.append(at)
        return str(UUID(int=len(found)))

    Bodies(route, note).model(route.body, ())
    return found


def id_fields_or_error(route: Route) -> list[Any]:
    try:
        return id_fields(route)
    except BodyError as error:
        return [str(error)]


def body_cases() -> list[Any]:
    cases = []
    for route in SWEPT:
        if route.params or not route.writes:
            continue
        found = id_fields_or_error(route)
        cases += [
            pytest.param(route, at, id=f"{route.name} [{'.'.join(map(str, at))}]")
            if isinstance(at, tuple)
            else pytest.param(route, at, id=f"{route.name} [its body]")
            for at in found
        ]
    return cases


def list_cases() -> list[Any]:
    """Each page and list (a GET with no id in its path), as each person it shows: as it is,
    with B's record in each id it takes, and searching for MARK."""
    cases = []
    for route in SWEPT:
        if route.params or route.writes or route.name == REPORTS_ROUTE:
            continue
        callers = ["staff", "owner", "tenant"] if route.caller == "signed in" else [route.caller]
        names = {query_name(q) for q in route.queries}
        variants: list[Any] = ["as it is"]
        variants += [("query", n) for n in sorted(names) if route.query_record(n) is not None]
        variants += [f"unknown id {n}" for n in unknown_query_ids(route)]
        if "q" in names:
            variants.append("search")
        cases += [
            pytest.param(
                route,
                caller,
                v,
                id=f"{route.name} as {caller} [{v if isinstance(v, str) else v[1]}]",
            )
            for caller in callers
            for v in variants
        ]
    return cases


# --- the two companies ---------------------------------------------------------------------


@dataclass
class Company:
    name: str
    made: demo.Demo
    # Each person's session cookie, by what they are to the company.
    sessions: dict[str, str] = field(default_factory=dict)
    vendor_token: str = ""
    records: dict[str, Any] = field(default_factory=dict)
    manager_email: str = ""
    manager_totp: Any = None

    @property
    def pmc_id(self) -> UUID:
        return self.made.pmc_id

    def record(self, kind: str) -> Any:
        if kind not in RECORDS:
            raise BodyError(f"no record kind {kind!r}: add its query to RECORDS")
        if kind not in self.records:
            raise BodyError(f"company {self.name} has no {kind!r}: make one in fill_in")
        return self.records[kind]


@dataclass
class World:
    live: TestClient
    dry: TestClient
    conn: psycopg.Connection[Any]
    a: Company
    b: Company
    # A third company, whose own writes that name no record go through (A's are all refused).
    c: Company
    # Every id of a row of B's; the digest of B's rows of each table, as last seen.
    b_ids: frozenset[str] = frozenset()
    b_books: dict[str, str] = field(default_factory=dict)


def rolled_back(request: Request) -> Iterator[psycopg.Connection[Any]]:
    """The request's connection, as deps.connection gives it, but its work rolled back."""
    with request.app.state.pool.connection() as conn, conn.transaction(force_rollback=True):
        yield conn


def send(
    client: TestClient,
    company: Company,
    caller: str,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    client.cookies.clear()
    if caller in company.sessions:
        client.cookies.set(deps.COOKIE, company.sessions[caller])
    if method in READS:
        return client.request(method, path, params=params)
    return client.request(method, path, params=params, json={} if body is None else body)


def ok(answer: Any, status: int = 200) -> Any:
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def sign_in(client: TestClient, company: Company, app_conn: psycopg.Connection[Any]) -> None:
    """The company's manager (an admin with every role), the owner of Harbor Tower and Avery
    Demo (by the email her renewal names her signer with), each signed in."""
    made = company.made
    tag = company.name.lower()
    client.cookies.clear()
    token = invite_token(app_conn, made.pmc_id, "staff", made.manager, f"manager.{tag}@example.com")
    enrolled = ok(client.post("/api/invites/accept", json={"token": token, "password": PASSWORD}))
    totp = totp_from(enrolled["totp_uri"])
    ok(client.post("/api/invites/confirm", json={"token": token, "code": totp.now()}))
    company.sessions["staff"] = company.sessions["signed in"] = client.cookies[deps.COOKIE]
    company.manager_email, company.manager_totp = f"manager.{tag}@example.com", totp
    for kind, person, email in (
        ("owner", made.owners["Demo Owner C"], f"owner.{tag}@example.com"),
        ("tenant", made.tenants["Avery Demo"], "avery.demo@example.com"),
    ):
        client.cookies.clear()
        token = invite_token(app_conn, made.pmc_id, kind, person, email)
        ok(client.post("/api/invites/accept", json={"token": token, "password": PASSWORD}))
        company.sessions[kind] = client.cookies[deps.COOKIE]
    client.cookies.clear()


def fill_in(client: TestClient, company: Company, conn: psycopg.Connection[Any]) -> None:
    """What the demo doesn't make, so each kind of id a route takes exists: made through the
    API, as the company's staff and tenant make it."""

    def staff(method: str, path: str, body: dict[str, Any] | None = None, status: int = 201):
        return ok(send(client, company, "staff", method, path, body=body), status)

    def tenant(method: str, path: str, body: dict[str, Any], status: int = 201):
        return ok(send(client, company, "tenant", method, path, body=body), status)

    def one(kind: str) -> Any:
        row = conn.execute(RECORDS[kind], {"pmc": company.pmc_id}).fetchone()
        assert row is not None, kind
        return row[0]

    lease, prop, vendor = one("lease"), one("property"), one("vendor")
    authority, bank = one("housing authority"), one("bank")
    unpaid = conn.execute(
        _LEASE_AT.format("Harbor Tower", demo.HARBOR_UNPAID), {"pmc": company.pmc_id}
    ).fetchone()[0]
    staff("POST", f"/api/rent/leases/{unpaid}/holds", {"starts_on": str(TODAY), "reason": "Filed"})
    staff(
        "POST",
        "/api/work-orders/recurring",
        {
            "property_id": str(prop),
            "summary": "Change the furnace filter",
            "every_months": 3,
            "day_of_month": 15,
            "starts_on": str(TODAY + 30 * DAY),
            "vendor_id": str(vendor),
        },
    )
    template = staff(
        "POST",
        "/api/notices/templates",
        {"name": "Entry notice", "body": "We will enter the home.", "used_for": "notice"},
    )["id"]
    staff(
        "POST",
        "/api/notices/templates",
        {"name": "Lease", "body": "The tenant rents the home.", "used_for": "lease"},
    )
    staff(
        "POST",
        "/api/notices",
        {"lease_id": str(lease), "template_id": str(template), "dated_on": str(TODAY)},
    )
    staff(
        "POST",
        "/api/rent/payment-files",
        {
            "file_name": "lockbox.csv",
            "content": "Date,Amount,Property,Unit,Payer,Reference,Method\n"
            f"{TODAY:%m/%d/%Y},10.00,Oak Terrace,Unit 1,Avery Demo,LB-1,check\n",
        },
    )
    staff(
        "POST",
        f"/api/rent/leases/{lease}/charges",
        {"kind": "fee", "amount": "25.00", "due_on": str(TODAY), "memo": "Cleaning"},
    )
    for kind, about, content, content_type in (
        ("lease_document", lease, PDF, "application/pdf"),
        ("listing_photo", one("listed unit"), PNG, "image/png"),
    ):
        staff(
            "POST",
            "/api/documents",
            {
                "kind": kind,
                "about_id": str(about),
                "title": "Isolation file",
                "content_type": content_type,
                "content_base64": base64.b64encode(content).decode(),
            },
        )
    staff("POST", f"/api/leases/{lease}/occupants", {"name": "Occupant 1", "relationship": "Child"})
    staff(
        "POST",
        f"/api/assistance/leases/{lease}/charges",
        {
            "kind": "charge",
            "amount": "100.00",
            "due_on": str(TODAY),
            "memo": "Housing assistance",
            "housing_authority_id": str(authority),
        },
    )
    staff(
        "POST",
        "/api/assistance/payments",
        {
            "request_id": str(uuid.uuid4()),
            "housing_authority_id": str(authority),
            "received_on": str(TODAY),
            "lines": [{"lease_id": str(lease), "amount": "100.00"}],
        },
    )
    # The authority's payment, at least, came in today and waits to go to the bank.
    waiting = staff("GET", f"/api/bank-accounts/{bank}/undeposited", status=200)
    staff(
        "POST",
        f"/api/bank-accounts/{bank}/deposits",
        {"deposited_on": str(TODAY), "transfer_ids": waiting[0]["transfer_ids"]},
    )
    staff(
        "POST",
        f"/api/utilities/properties/{prop}/plans",
        {"utility": "water", "starts_on": str(LAST_MONTH), "method": "units"},
    )
    staff("POST", "/api/notes", {"about": "property", "about_id": str(prop), "body": "Gate code"})
    tenant(
        "POST",
        "/api/tenant/requests",
        {"lease_id": str(lease), "summary": "Tap drips", "entry_allowed": True},
    )
    staff("POST", "/api/payments/settings", {"online": True, "ach_fee_payer": "tenant"}, 204)
    tenant("POST", f"/api/payments/leases/{lease}/checkout", {"amount": "10.00"})
    tenant("POST", f"/api/payments/leases/{lease}/autopay", {"rule": "fixed", "amount": "10.00"})
    company.vendor_token = staff("POST", f"/api/vendors/{vendor}/link")["url"].split("#", 1)[1]
    ended = conn.execute(RECORDS["returned lease"], {"pmc": company.pmc_id}).fetchone()[0]
    staff(
        "POST", f"/api/move-out/{ended}/return", {"request_id": str(uuid.uuid4()), "on": str(TODAY)}
    )


def resolve(company: Company, conn: psycopg.Connection[Any]) -> None:
    for kind, query in RECORDS.items():
        row = conn.execute(query, {"pmc": company.pmc_id}).fetchone()
        if row is not None:
            company.records[kind] = row[0]


def mark(conn: psycopg.Connection[Any], pmc_id: UUID) -> None:
    """B's names start with MARK, so a name of B's shows in an answer as surely as an id."""
    for table in (
        "trust_pmcs",
        "trust_properties",
        "trust_units",
        "trust_tenants",
        "trust_owners",
        "trust_vendors",
        "trust_staff",
        "trust_property_groups",
        "trust_bank_accounts",
    ):
        conn.execute(
            sql.SQL("UPDATE {} SET display_name = %s || display_name WHERE pmc_id = %s").format(
                sql.Identifier(table)
            ),
            (f"{MARK} ", pmc_id),
        )


def tables(conn: psycopg.Connection[Any]) -> list[str]:
    return [
        name
        for (name,) in conn.execute(
            "SELECT c.relname FROM pg_class AS c JOIN pg_attribute AS a"
            " ON a.attrelid = c.oid AND a.attname = 'pmc_id' AND NOT a.attisdropped"
            " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')"
            " AND c.relname LIKE 'trust\\_%%' ORDER BY 1"
        ).fetchall()
    ]


_MINE = sql.SQL(
    "(SELECT a.ledger_account_id FROM trust_ledger_accounts AS a WHERE a.pmc_id = %(pmc)s)"
)


def books_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> dict[str, str]:
    """A digest of the company's rows in each trust_* table and of its part of pgledger's."""
    digest = sql.SQL("md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))")
    parts = [
        sql.SQL("SELECT {}, {} FROM {} AS t WHERE t.pmc_id = %(pmc)s").format(
            sql.Literal(name), digest, sql.Identifier(name)
        )
        for name in tables(conn)
    ]
    parts += [
        sql.SQL(
            "SELECT 'pgledger_accounts', {} FROM pgledger_accounts AS t WHERE t.id IN {}"
        ).format(digest, _MINE),
        sql.SQL(
            "SELECT 'pgledger_transfers', {} FROM pgledger_transfers AS t"
            " WHERE t.from_account_id IN {} OR t.to_account_id IN {}"
        ).format(digest, _MINE, _MINE),
        sql.SQL(
            "SELECT 'pgledger_entries', {} FROM pgledger_entries AS t WHERE t.account_id IN {}"
        ).format(digest, _MINE),
    ]
    return dict(conn.execute(sql.SQL(" UNION ALL ").join(parts), {"pmc": pmc_id}).fetchall())


def ids_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> frozenset[str]:
    """The company's PMC id and the id of every row of its, as an answer would print them."""
    found = {str(pmc_id)}
    for name in tables(conn):
        kind = conn.execute(
            "SELECT format_type(a.atttypid, NULL) FROM pg_attribute AS a"
            " WHERE a.attrelid = %s::regclass AND a.attname = 'id' AND NOT a.attisdropped",
            (name,),
        ).fetchone()
        if kind is not None and kind[0] in ("uuid", "text"):
            query = sql.SQL("SELECT id::text FROM {} WHERE pmc_id = %s").format(
                sql.Identifier(name)
            )
            found |= {i for (i,) in conn.execute(query, (pmc_id,))}
    ledger = sql.SQL(
        "SELECT ledger_account_id FROM trust_ledger_accounts WHERE pmc_id = %(pmc)s"
        " UNION SELECT t.id FROM pgledger_transfers AS t"
        " WHERE t.from_account_id IN {0} OR t.to_account_id IN {0}"
    ).format(_MINE)
    found |= {i for (i,) in conn.execute(ledger, {"pmc": pmc_id})}
    return frozenset(found)


@pytest.fixture(scope="module")
def world(database_url) -> Iterator[World]:
    settings = Settings(
        database_url=database_url,
        public_url=PUBLIC_URL,
        cookie_secure=False,
        screening_partner="fake",
        payments="fake",
        geocoder="fake",
    )
    dry_app = create_app(settings)
    dry_app.dependency_overrides[deps.connection] = rolled_back
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        TestClient(create_app(settings)) as live,
        TestClient(dry_app) as dry,
    ):
        app_conn.execute("SET ROLE trust_app")
        a, b, c = (Company(n, demo.build(app_conn, public_url=PUBLIC_URL)) for n in "ABC")
        for company in (a, b):
            sign_in(live, company, app_conn)
            fill_in(live, company, conn)
            resolve(company, conn)
        sign_in(live, c, app_conn)
        mark(conn, b.pmc_id)
        yield World(live, dry, conn, a, b, c, ids_of(conn, b.pmc_id), books_of(conn, b.pmc_id))


# --- what an answer shows ------------------------------------------------------------------

UUIDS = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
LEDGER_IDS = re.compile(r"pgl[a-z]_[0-9A-Z]{26}")


def text_of(data: bytes) -> str:
    """An answer as text: a zip (a workbook too) by its members, a PDF by its pages' text."""
    if data.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "\n".join(text_of(archive.read(name)) for name in archive.namelist())
    if data.startswith(b"%PDF"):
        try:
            pages = PdfReader(io.BytesIO(data)).pages
            return "\n".join(page.extract_text() for page in pages) + data.decode("latin-1")
        except PdfReadError:  # a file someone uploaded, not one the PMS made: its bytes
            return data.decode("latin-1")
    return data.decode("utf-8", "replace")


def of_b(world: World, answer: Any, sent: set[str]) -> list[str]:
    """What of B's the answer shows, but for the ids the request sent."""
    text = text_of(answer.content)
    found = {m.lower() for m in UUIDS.findall(text)} | set(LEDGER_IDS.findall(text))
    shown: list[str] = sorted(found & world.b_ids - {s.lower() for s in sent} - sent)
    if MARK in text:
        at = text.index(MARK)
        shown.append(f"a name of B's: ...{text[max(0, at - 60) : at + 60]}...")
    return shown


def said(answer: Any) -> tuple[int, Any]:
    """The answer's status and what it said, to tell one answer from another."""
    try:
        return answer.status_code, answer.json()
    except ValueError:
        return answer.status_code, answer.content[:200]


def validation_error(answer: Any) -> bool:
    detail = said(answer)[1]
    return (
        answer.status_code == 422
        and isinstance(detail, dict)
        and isinstance(detail.get("detail"), list)
    )


# --- the requests --------------------------------------------------------------------------


def path_record(route: Route, name: str) -> str:
    record = ROUTE_PATH_IDS.get((route.path, name), PATH_IDS.get(name))
    if record is None:
        raise BodyError(
            f"{route.name}: path parameter {name!r} is an id the sweep doesn't know: say whose"
            " record it is in PATH_IDS (or ROUTE_PATH_IDS), or what it is in NOT_ID_PARAMS"
        )
    return record


def build(
    world: World, route: Route, own: Company, swapped: Callable[[tuple[Any, ...]], bool]
) -> tuple[str, dict[str, Any], dict[str, Any] | None, set[str]]:
    """The request the company makes of the route with its own ids, but where `swapped` says
    (at a place: ("path", name), ("query", name) or the body field's path) B's: its path,
    query and body, and every id it sends."""
    sent: set[str] = set()

    def theirs(kind: str, at: tuple[Any, ...]) -> Any:
        return (world.b if swapped(at) else own).record(kind)

    path = route.path
    for name in re.findall(r"{(\w+)}", route.path):
        if (route.path, name) in NOT_ID_PARAMS:
            value = str(NOT_ID_PARAMS[(route.path, name)])
        else:
            value = str(theirs(path_record(route, name), ("path", name)))
            sent.add(value)
        path = path.replace("{" + name + "}", value)
    params: dict[str, Any] = {}
    made = Bodies(route, theirs)
    for query in route.queries:
        name = query_name(query)
        if not query.field_info.is_required():
            continue
        record = route.query_record(name)
        if record is not None:
            params[name] = made.record(record, ("query", name))
        else:
            info = query.field_info
            params[name] = made.value(name, info.annotation, info.metadata, (), None)
    body = made.model(route.body, ()) if route.body is not None else None
    if route.caller == "vendor" and body is not None:
        body["token"] = own.vendor_token
    return path, params, body, sent | made.sent


def b_unchanged(world: World, asked: str) -> None:
    """B's books as last seen (a test that changes them fails, and the next starts from them)."""
    after = books_of(world.conn, world.b.pmc_id)
    changed = sorted(t for t in after if after[t] != world.b_books.get(t))
    world.b_books = after
    assert changed == [], f"{asked} changed B's {changed}"


def compare(
    world: World, route: Route, swapped: Callable[[tuple[Any, ...]], bool], in_path: bool
) -> None:
    """A's request with B's ids where `swapped` says, beside B's own: A's refused (as not found,
    for an id in the path), B's answered otherwise, and B's books as they were."""
    b_path, b_params, b_body, _ = build(world, route, world.b, lambda at: False)
    path, params, body, sent = build(world, route, world.a, swapped)
    theirs = send(world.dry, world.b, route.caller, route.method, b_path, b_params, b_body)
    answer = send(world.live, world.a, route.caller, route.method, path, params, body)
    asked = f"{route.method} {path} {params or ''} {json.dumps(body) if body else ''}"

    assert theirs.status_code < 500, f"B's own {asked} -> {theirs.status_code} {theirs.text}"
    assert not validation_error(theirs), f"B's own {asked} is refused: {theirs.text}"
    assert answer.status_code < 500, f"A's {asked} -> {answer.status_code} {answer.text}"
    if in_path:
        expected = REFUSED.get(route.name, (404, ""))[0]
        assert answer.status_code == expected, (
            f"A's {asked} -> {answer.status_code} {answer.text[:300]}, not {expected}"
        )
    else:
        assert 400 <= answer.status_code < 500, (
            f"A's {asked} -> {answer.status_code} {answer.text[:300]}, not a refusal"
        )
    assert said(answer) != said(theirs), (
        f"B's own request got A's answer ({answer.status_code} {answer.text[:200]}): its ids"
        " don't reach what the route does; pick records B's own request gets past its checks"
    )
    assert of_b(world, answer, sent) == [], f"A's {asked} shows B's: {answer.text[:300]}"
    b_unchanged(world, f"A's {asked}")


@pytest.mark.parametrize(("route", "variant"), path_cases())
def test_a_route_refuses_another_companys_record_in_its_path(world, route, variant):
    if variant == "every id":
        compare(world, route, lambda at: at[0] in ("path", "query"), in_path=True)
    else:
        compare(world, route, lambda at: at == variant, in_path=True)


@pytest.mark.parametrize(("route", "at"), body_cases())
def test_a_write_refuses_another_companys_record_in_its_body(world, route, at):
    if isinstance(at, str):
        raise BodyError(at)
    compare(world, route, lambda here: here[: len(at)] == at, in_path=False)


@pytest.mark.parametrize(("route", "caller", "variant"), list_cases())
def test_a_list_or_page_shows_nothing_of_another_companys(world, route, caller, variant):
    if isinstance(variant, str) and variant.startswith("unknown id"):
        raise BodyError(
            f"{route.name}: query parameter {variant.split()[-1]!r} is an id the sweep doesn't"
            " know: say whose record it is in QUERY_IDS (or ROUTE_QUERY_IDS)"
        )
    path, params, _, sent = build(world, route, world.a, lambda at: at == variant)
    if isinstance(variant, tuple):
        params[variant[1]] = world.b.record(route.query_record(variant[1]))
        sent.add(str(params[variant[1]]))
    if variant == "search":
        params["q"] = MARK
    answer = send(world.live, world.a, caller, route.method, path, params)

    assert answer.status_code < 500, f"{route.name} {params} -> {answer.text}"
    assert of_b(world, answer, sent) == [], f"{route.name} {params} as A's {caller}"
    b_unchanged(world, f"{route.name} {params} as A's {caller}")


def own_write_cases() -> list[Any]:
    return [
        pytest.param(route, id=route.name)
        for route in SWEPT
        if route.writes and not route.params and not id_fields_or_error(route)
    ]


@pytest.mark.parametrize("route", own_write_cases())
def test_a_write_naming_no_record_changes_nothing_of_another_companys(world, route):
    """A write that names no record (the company's own settings, Run rent now) by company C,
    whose own writes go through: B's books stay as they were."""
    path, params, body, _ = build(world, route, world.c, lambda at: False)
    answer = send(world.live, world.c, route.caller, route.method, path, params, body)

    assert answer.status_code < 500, f"{route.name} {body} -> {answer.text}"
    b_unchanged(world, f"C's {route.name} {body}")


def report_cases(conn: psycopg.Connection[Any]) -> list[tuple[str, list[str], list[str]]]:
    """Each report with its arguments' names and types, after the PMC."""
    return [
        (name, names[1 : len(kinds)], kinds[1:])
        for name, names, kinds in conn.execute(
            "SELECT p.proname, p.proargnames, array(SELECT format_type(t, NULL)"
            " FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS a (t, n) ORDER BY n)"
            " FROM pg_proc AS p WHERE p.pronamespace = 'public'::regnamespace"
            " AND p.proname LIKE 'trust\\_report\\_%%' AND p.proretset ORDER BY p.proname"
        ).fetchall()
    ]


def report_arg(company: Company, name: str, kind: str) -> str:
    if kind == "uuid":
        if name not in REPORT_IDS:
            raise BodyError(f"report argument {name!r}: say whose record it is in REPORT_IDS")
        return str(company.record(REPORT_IDS[name]))
    if kind == "integer":
        return str(TODAY.year)
    start = demo.first_of_month(TODAY, 3)
    if kind == "date" and name == "p_through":
        return str(THIS_MONTH - DAY)  # last month's end, which the demo's bank statements cover
    if kind == "date":
        return str(start if name in ("p_from", "p_period_start") else TODAY)
    if kind == "timestamp with time zone":
        day = start if name in ("p_from", "p_period_start") else TODAY + DAY
        return datetime.combine(day, time(), UTC).isoformat()
    raise BodyError(f"report argument {name!r} of type {kind}: give it a value in report_arg")


def report(client: TestClient, company: Company, name: str, query: dict[str, Any]) -> Any:
    return send(client, company, "staff", "GET", f"/api/reports/{name}", {**query, "format": "csv"})


def test_no_report_shows_another_companys_books(world):
    """Every report, as A's staff: with A's own records, with B's in each record it takes, and
    narrowed to B's property, group or owner. B's own, with its records, shows B's names: the
    sweep would see them."""
    problems, seen_by_b = [], 0
    reports = report_cases(world.conn)
    assert len(reports) > 30
    for name, names, kinds in reports:
        args = list(zip(names, kinds, strict=True))
        own = [report_arg(world.a, n, k) for n, k in args]
        asks: list[tuple[dict[str, Any], set[str]]] = [({"arg": own}, set())]
        for n, (arg, kind) in enumerate(args):
            if kind == "uuid":
                theirs = report_arg(world.b, arg, kind)
                asks.append(({"arg": [*own[:n], theirs, *own[n + 1 :]]}, {theirs}))
        if any(kind == "uuid" for _, kind in args):
            theirs_all = [report_arg(world.b, n, k) for n, k in args]
            b_own = report(world.dry, world.b, name, {"arg": theirs_all})
            assert b_own.status_code < 500, f"B's own {name}: {b_own.text}"
            seen_by_b += MARK in text_of(b_own.content)
        for query, record in (("property_id", "property"), ("group", "group"), ("owner", "owner")):
            narrowed = str(world.b.record(record))
            asks.append(({query: narrowed, "arg": own}, {narrowed}))
        for query, sent in asks:
            answer = report(world.live, world.a, name, query)
            if answer.status_code >= 500 or of_b(world, answer, sent):
                problems.append((name, query, answer.status_code, of_b(world, answer, sent)))
    assert problems == []
    assert seen_by_b > 0
    b_unchanged(world, "A's reports")


def test_the_sweep_knows_every_route_and_nothing_stale():
    """Each route is swept (by its path's ids, its body's, or as a list or page) or exempt, and
    every name the sweep's tables give is still a route, a path or a record."""
    paths = {r.path for r in ROUTES}
    names = {r.name for r in ROUTES}
    cases = path_cases() + body_cases() + list_cases() + own_write_cases()
    swept = {p.values[0].name for p in cases}

    by_path = [*NOT_ID_PARAMS, *ROUTE_PATH_IDS, *ROUTE_BODY_IDS, *ROUTE_QUERY_IDS]

    assert set(EXEMPT) | set(REFUSED) | set(BODIES) <= names
    assert {path for path, _ in by_path} <= paths
    used = [*PATH_IDS.values(), *ROUTE_PATH_IDS.values(), *BODY_IDS.values()]
    used += [*ROUTE_BODY_IDS.values(), *QUERY_IDS.values(), *REPORT_IDS.values()]
    assert set(used) <= set(RECORDS)
    assert names - set(EXEMPT) - swept == {REPORTS_ROUTE}


def test_one_companys_login_doesnt_sign_in_to_another(world):
    """A login is its company's: A's manager's password and code, naming B, sign in to nothing,
    while B's manager's own do."""

    def sign_in_to(client: TestClient, pmc: Company, who: Company) -> Any:
        code = who.manager_totp.at(int(datetime.now(UTC).timestamp()) + 30)
        body = {"pmc_id": str(pmc.pmc_id), "email": who.manager_email, "password": PASSWORD}
        return send(client, pmc, "public", "POST", "/api/sign-in", body={**body, "code": code})

    assert sign_in_to(world.dry, world.b, world.b).status_code == 200
    refused = sign_in_to(world.live, world.b, world.a)

    assert refused.status_code == 401, refused.text
    assert deps.COOKIE not in refused.cookies
    b_unchanged(world, "A's sign-in to B")
