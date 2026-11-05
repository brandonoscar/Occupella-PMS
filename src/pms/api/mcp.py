"""The MCP server (ADR 0011's fourth piece): any MCP client, an AI assistant such as Claude, plugs
into the PMS at `/mcp` with an API key as its bearer, and works through tools.

Model Context Protocol over its Streamable HTTP transport, stateless: each POST carries one
JSON-RPC message (`initialize`, `ping`, `tools/list`, `tools/call`; a notification is taken with
202) and is answered with one JSON-RPC message as JSON. No session, no event stream (a GET is
refused with 405). The request runs as the API key's does (`ai_keys.with_key`): its company is
the key's, it reads as trust_ai_agent, it is rate-limited, and a key taken back opens nothing.

Read tools find the company's records with their ids (`find`), run its reports, read a lease's
ledger, delinquency, the rent roll, work orders, its events and its own drafts. Propose tools
write drafts (src/pms/api/drafts.py), which do nothing until a staff member approves one: an
email, a notice, a work order opened, assigned or scheduled, a note. No tool moves money, and the
AI's role can't.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from pms import exports
from pms.api import drafts, events
from pms.api.ai_keys import Key, WithKey
from pms.api.deps import Conn

router = APIRouter()

VERSIONS = ("2025-06-18", "2025-03-26")
SERVER = {"name": "occupella-pms", "title": "Occupella PMS", "version": "1"}
INSTRUCTIONS = (
    "The books of one property management company, read through its API key. Find records and"
    " their ids with find; read reports, ledgers and events; propose emails, notices, work"
    " orders and notes as drafts, which a staff member approves before anything is done. Nothing"
    " here moves money."
)
FINDS = ("tenant", "owner", "vendor", "property", "unit", "lease", "work_order")
FOUND_AT_MOST = 50

# JSON-RPC's own error codes (a body that is no JSON at all is refused by the API, 422).
INVALID_REQUEST, NO_METHOD, INVALID_PARAMS = -32600, -32601, -32602


class ToolError(Exception):
    """A tool that could not do what it was asked: told to the client as the tool's result."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    run: Callable[[psycopg.Connection[Any], Key, dict[str, Any]], Any]
    read_only: bool = True


def obj(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


UUID_S = {"type": "string", "format": "uuid"}
DAY = {
    "type": "string",
    "format": "date",
    "description": "YYYY-MM-DD; the company's today if left out",
}


def today(key: Key) -> date:
    """The key's company's day, as the key's lookup read it."""
    return key.today or datetime.now(UTC).date()


def day_of(
    conn: psycopg.Connection[Any], key: Key, args: dict[str, Any], name: str = "as_of"
) -> date:
    value = args.get(name)
    if value is None:
        return today(key)
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ToolError(f"{name} is a day as YYYY-MM-DD") from None


def uuid_of(args: dict[str, Any], name: str) -> UUID:
    try:
        return UUID(str(args[name]))
    except (KeyError, ValueError):
        raise ToolError(f"{name} is an id from find") from None


def report(conn: psycopg.Connection[Any], key: Key, name: str, *args: Any) -> dict[str, Any]:
    """A report of the key's company, its company always the key's first argument."""
    try:
        table = exports.run_report(conn, f"trust_report_{name}", [key.pmc_id, *args])
    except exports.ExportError as wrong:
        raise ToolError(str(wrong)) from None
    except psycopg.errors.InsufficientPrivilege:
        raise ToolError(f"this key may not run the {name} report") from None
    except (psycopg.errors.DataError, psycopg.errors.InvalidParameterValue) as bad:
        raise ToolError(str(bad).splitlines()[0].removeprefix("trust: ")) from None
    return {
        "columns": list(table.columns),
        "rows": [[None if v is None else exports.text(v) for v in row] for row in table.rows],
    }


# --- read tools -------------------------------------------------------------------------------

FIND = {
    "tenant": "SELECT t.id, t.display_name, NULL FROM trust_tenants AS t WHERE t.pmc_id = %(pmc)s"
    " AND (%(like)s::text IS NULL OR t.display_name ILIKE %(like)s)"
    ' ORDER BY t.display_name COLLATE "C", t.id LIMIT %(n)s',
    "owner": "SELECT o.id, o.display_name, NULL FROM trust_owners AS o WHERE o.pmc_id = %(pmc)s"
    " AND (%(like)s::text IS NULL OR o.display_name ILIKE %(like)s)"
    ' ORDER BY o.display_name COLLATE "C", o.id LIMIT %(n)s',
    "vendor": "SELECT v.id, v.display_name, NULL FROM trust_vendors AS v WHERE v.pmc_id = %(pmc)s"
    " AND (%(like)s::text IS NULL OR v.display_name ILIKE %(like)s)"
    ' ORDER BY v.display_name COLLATE "C", v.id LIMIT %(n)s',
    "property": "SELECT p.id, p.display_name, NULL FROM trust_properties AS p"
    " WHERE p.pmc_id = %(pmc)s AND (%(like)s::text IS NULL OR p.display_name ILIKE %(like)s)"
    ' ORDER BY p.display_name COLLATE "C", p.id LIMIT %(n)s',
    "unit": "SELECT u.id, p.display_name || ', ' || u.display_name, p.id FROM trust_units AS u"
    " JOIN trust_properties AS p ON p.id = u.property_id WHERE u.pmc_id = %(pmc)s"
    " AND (%(like)s::text IS NULL OR p.display_name || ', ' || u.display_name ILIKE %(like)s)"
    ' ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", u.id LIMIT %(n)s',
    "lease": "SELECT l.id, p.display_name || ', ' || u.display_name || ' (' || coalesce(("
    "   SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE \"C\")"
    "   FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
    "   WHERE lt.lease_id = l.id), '') || ')', u.id"
    " FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
    " JOIN trust_properties AS p ON p.id = u.property_id WHERE l.pmc_id = %(pmc)s"
    " AND (l.ends_on IS NULL OR l.ends_on >= %(today)s)"
    " AND (%(like)s::text IS NULL OR p.display_name || ', ' || u.display_name || ' ' || coalesce(("
    "   SELECT string_agg(t.display_name, ' ') FROM trust_lease_tenants AS lt"
    "   JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id), '')"
    "   ILIKE %(like)s)"
    ' ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id'
    " LIMIT %(n)s",
    "work_order": "SELECT w.id, 'Work order ' || w.number || ': ' || w.summary, w.property_id"
    " FROM ("
    "   SELECT id, summary, property_id, created_at,"
    "     row_number() OVER (ORDER BY created_at, id) AS number"
    "   FROM trust_work_orders WHERE pmc_id = %(pmc)s) AS w"
    " WHERE (%(like)s::text IS NULL OR w.summary ILIKE %(like)s)"
    " ORDER BY w.created_at DESC, w.id LIMIT %(n)s",
}
WITHIN = {"unit": "property_id", "lease": "unit_id", "work_order": "property_id"}


def find(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    kind = args.get("kind")
    if kind not in FINDS:
        raise ToolError(f"kind is one of {', '.join(FINDS)}")
    words = str(args.get("name") or "").strip()
    like = None
    if words:
        like = "%" + words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = conn.execute(
        FIND[kind],
        {"pmc": key.pmc_id, "like": like, "n": FOUND_AT_MOST, "today": today(key)},
    ).fetchall()
    found = []
    for record, name, within in rows:
        item: dict[str, Any] = {"id": str(record), "name": name}
        if within is not None:
            item[WITHIN[kind]] = str(within)
        found.append(item)
    return {"kind": kind, "found": found}


def list_reports(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    rows = conn.execute(
        "SELECT p.proname, coalesce(p.proargnames, '{}'), array(SELECT format_type(a.type, NULL)"
        "   FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS a (type, n) ORDER BY a.n)"
        " FROM pg_proc AS p WHERE p.pronamespace = 'public'::regnamespace AND p.proretset"
        " AND p.proname LIKE 'trust\\_report\\_%%'"
        " AND has_function_privilege(current_user, p.oid, 'EXECUTE')"
        ' ORDER BY p.proname COLLATE "C"'
    ).fetchall()
    return {
        "reports": [
            {
                "name": name.removeprefix("trust_report_"),
                # The company comes first, always the key's: the rest are the tool's args.
                "args": [
                    {
                        "name": (names[n] if n < len(names) else f"arg{n}").removeprefix("p_"),
                        "type": t,
                    }
                    for n, t in enumerate(types)
                ][1:],
            }
            for name, names, types in rows
        ]
    }


def run_report(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    name = str(args.get("name") or "").removeprefix("trust_report_")
    given = args.get("args") or []
    if not isinstance(given, list) or not all(isinstance(a, str) for a in given):
        raise ToolError("args is a list of text, one for each of the report's arguments")
    return report(conn, key, name, *given)


def rent_roll(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    return report(conn, key, "rent_roll", day_of(conn, key, args))


def delinquency(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    return report(conn, key, "delinquency", day_of(conn, key, args))


def work_orders(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    return report(conn, key, "work_orders", day_of(conn, key, args))


def lease_ledger(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    lease = uuid_of(args, "lease_id")
    if (
        conn.execute(
            "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s", (key.pmc_id, lease)
        ).fetchone()
        is None
    ):
        raise ToolError("no such lease in this company")
    return report(conn, key, "tenant_ledger", lease, day_of(conn, key, args))


def recent_events(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    after = args.get("after")
    if after is not None and (not isinstance(after, int) or after < 1):
        raise ToolError("after is an event's id from an earlier answer")
    if after is not None and (
        conn.execute(
            "SELECT 1 FROM trust_events WHERE pmc_id = %s AND id = %s", (key.pmc_id, after)
        ).fetchone()
        is None
    ):
        raise ToolError("after names no event of this company")
    found = events.events_after(conn, key.pmc_id, after, 100)
    return events.Events(events=found, next=found[-1].id if found else after).model_dump(
        mode="json"
    )


def my_drafts(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
    return drafts.my_drafts(key, conn).model_dump(mode="json")


# --- propose tools ----------------------------------------------------------------------------


def proposing(kind: str) -> Callable[[psycopg.Connection[Any], Key, dict[str, Any]], Any]:
    def run(conn: psycopg.Connection[Any], key: Key, args: dict[str, Any]) -> Any:
        why = args.pop("why", None)
        try:
            new = drafts.NewDraft.model_validate({"proposal": {**args, "kind": kind}, "why": why})
        except ValidationError as wrong:
            raise ToolError(
                "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'][2:]) or 'proposal'}: {e['msg']}"
                    for e in wrong.errors()
                )
            ) from None
        made = drafts.propose(key, conn, new)
        return {
            "draft_id": str(made.id),
            "status": "waiting",
            "says": "A staff member approves or declines it in the PMS; nothing is done before.",
        }

    return run


WHY = {"type": "string", "maxLength": 2000, "description": "Why, shown to staff beside it"}
TO = {
    "type": "array",
    "minItems": 1,
    "maxItems": 100,
    "items": obj(
        {"about": {"type": "string", "enum": ["tenant", "owner", "vendor"]}, "id": UUID_S},
        ("about", "id"),
    ),
}

TOOLS = [
    Tool(
        "find",
        "The company's tenants, owners, vendors, properties, units, leases in effect or to come,"
        " or work orders, with their ids, by a part of the name (none: the first 50).",
        obj(
            {"kind": {"type": "string", "enum": list(FINDS)}, "name": {"type": "string"}}, ("kind",)
        ),
        find,
    ),
    Tool(
        "list_reports", "The reports this key may run, with their arguments.", obj({}), list_reports
    ),
    Tool(
        "run_report",
        "Runs one of list_reports' reports: its columns and rows, as text. The company is always"
        " the key's; args are the rest of its arguments, in order.",
        obj(
            {"name": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}},
            ("name",),
        ),
        run_report,
    ),
    Tool(
        "rent_roll",
        "Every lease with its rent, deposits and balance, on a day.",
        obj({"as_of": DAY}),
        rent_roll,
    ),
    Tool(
        "delinquency", "What each lease owes past due, on a day.", obj({"as_of": DAY}), delinquency
    ),
    Tool(
        "work_orders",
        "Each work order, its status and vendor, on a day.",
        obj({"as_of": DAY}),
        work_orders,
    ),
    Tool(
        "lease_ledger",
        "A lease's charges and payments with the running balance, through a day.",
        obj({"lease_id": UUID_S, "as_of": DAY}, ("lease_id",)),
        lease_ledger,
    ),
    Tool(
        "recent_events",
        "What happened in the books after the event named (none: from the first), in order: kinds"
        " and ids only. Ask again with `next` for what came after.",
        obj({"after": {"type": "integer", "minimum": 1}}),
        recent_events,
    ),
    Tool("my_drafts", "The drafts this key proposed and what became of each.", obj({}), my_drafts),
    Tool(
        "propose_email",
        "Proposes an email to tenants, owners or vendors, by id; the PMS fills in each address.",
        obj(
            {
                "to": TO,
                "subject": {"type": "string", "maxLength": 200},
                "body": {"type": "string", "maxLength": 8000},
                "why": WHY,
            },
            ("to", "subject", "body"),
        ),
        proposing("email"),
        read_only=False,
    ),
    Tool(
        "propose_notice",
        "Proposes a notice from one of the company's notice templates for leases, dated a day.",
        obj(
            {
                "template_id": UUID_S,
                "lease_ids": {"type": "array", "items": UUID_S, "minItems": 1, "maxItems": 100},
                "dated_on": {"type": "string", "format": "date"},
                "why": WHY,
            },
            ("template_id", "lease_ids", "dated_on"),
        ),
        proposing("notice"),
        read_only=False,
    ),
    Tool(
        "propose_work_order",
        "Proposes opening a work order at a property, maybe one of its units.",
        obj(
            {
                "property_id": UUID_S,
                "unit_id": UUID_S,
                "summary": {"type": "string", "maxLength": 500},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "description": {"type": "string", "maxLength": 5000},
                "due_on": {"type": "string", "format": "date"},
                "why": WHY,
            },
            ("property_id", "summary"),
        ),
        proposing("work_order_open"),
        read_only=False,
    ),
    Tool(
        "propose_vendor",
        "Proposes assigning a work order to a vendor.",
        obj(
            {"work_order_id": UUID_S, "vendor_id": UUID_S, "why": WHY},
            ("work_order_id", "vendor_id"),
        ),
        proposing("work_order_assign"),
        read_only=False,
    ),
    Tool(
        "propose_visit",
        'Proposes scheduling a work order\'s visit for a day, maybe a window ("8-10 am").',
        obj(
            {
                "work_order_id": UUID_S,
                "scheduled_for": {"type": "string", "format": "date"},
                "visit_window": {"type": "string", "maxLength": 60},
                "why": WHY,
            },
            ("work_order_id", "scheduled_for"),
        ),
        proposing("work_order_schedule"),
        read_only=False,
    ),
    Tool(
        "propose_note",
        "Proposes a note about a property, unit, lease, tenant, owner or vendor.",
        obj(
            {
                "about": {
                    "type": "string",
                    "enum": ["property", "unit", "lease", "tenant", "owner", "vendor"],
                },
                "about_id": UUID_S,
                "body": {"type": "string", "maxLength": 5000},
                "why": WHY,
            },
            ("about", "about_id", "body"),
        ),
        proposing("note"),
        read_only=False,
    ),
]
BY_NAME = {tool.name: tool for tool in TOOLS}


def listed(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.schema,
        "annotations": {
            "readOnlyHint": tool.read_only,
            # A proposal changes nothing until a person approves it; nothing here deletes.
            "destructiveHint": False,
            "openWorldHint": False,
        },
    }


def answer(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def call(conn: psycopg.Connection[Any], key: Key, params: dict[str, Any]) -> dict[str, Any]:
    """A tool's result: what it found or did as text and as structured content, or why not."""
    tool = BY_NAME.get(str(params.get("name")))
    if tool is None:
        raise LookupError(f"no tool named {params.get('name')}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise LookupError("arguments are an object")
    try:
        with conn.transaction():  # a savepoint: a tool refused leaves the request usable
            found = tool.run(conn, key, dict(args))
    except (ToolError, HTTPException) as refused:
        said = refused.detail if isinstance(refused, HTTPException) else str(refused)
        return {"content": [{"type": "text", "text": str(said)}], "isError": True}
    return {
        "content": [{"type": "text", "text": json.dumps(found, sort_keys=True)}],
        "structuredContent": found,
        "isError": False,
    }


@router.post("/mcp", response_model=None)
def mcp(key: WithKey, conn: Conn, message: Annotated[Any, Body()]) -> Response:
    """One JSON-RPC message of the Model Context Protocol, answered as JSON."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return JSONResponse(
            error(None, INVALID_REQUEST, "one JSON-RPC 2.0 message, an object"), status_code=400
        )
    method = message.get("method")
    if "id" not in message:
        return Response(status_code=202)  # a notification (initialized, cancelled...): taken
    message_id = message["id"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(error(message_id, INVALID_PARAMS, "params are an object"))
    if method == "initialize":
        asked = params.get("protocolVersion")
        return JSONResponse(
            answer(
                message_id,
                {
                    "protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {**SERVER, "company": key.company},
                    "instructions": INSTRUCTIONS,
                },
            )
        )
    if method == "ping":
        return JSONResponse(answer(message_id, {}))
    if method == "tools/list":
        return JSONResponse(answer(message_id, {"tools": [listed(tool) for tool in TOOLS]}))
    if method == "tools/call":
        try:
            return JSONResponse(answer(message_id, call(conn, key, params)))
        except LookupError as unknown:
            return JSONResponse(error(message_id, INVALID_PARAMS, str(unknown)))
    return JSONResponse(error(message_id, NO_METHOD, f"no method {method}"))
