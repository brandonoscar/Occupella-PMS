"""Phone numbers as people type them, kept as E.164 (the founder's review of 11/03, item 14).

A number with its country code ("+44 20 7946 0000") keeps it; one without is taken as a US or
Canadian number (North American numbering), ten digits with its area code, or eleven starting with
1. Anything else is refused in words. The PMS sends no text: what it keeps is the number and, at
sign-up, whether the person said the company may text them, in the words they were shown.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

import psycopg

TYPED = re.compile(r"^\+?[0-9 ().-]{7,30}$")


class BadPhone(ValueError):
    pass


SAY = "Enter a phone number with its area code, like (555) 555-0123."


def e164(typed: str) -> str:
    """The number in E.164 ("+15555550123"), or BadPhone."""
    raw = typed.strip()
    if not TYPED.match(raw):
        raise BadPhone(SAY)
    digits = re.sub(r"[^0-9]", "", raw)
    if raw.startswith("+"):
        if not 8 <= len(digits) <= 15 or digits[0] == "0":
            raise BadPhone(SAY)
        if digits[0] == "1":
            north_american(digits[1:])
        return f"+{digits}"
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    north_american(digits)
    return f"+1{digits}"


def north_american(digits: str) -> None:
    """Ten digits: an area code and an exchange that start 2 to 9."""
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        raise BadPhone(SAY)


def text_consent(company: str) -> str:
    """The words beside the box at sign-up, the same shown and kept."""
    return (
        f"OK to text me. {company} may text this number about my rent, repairs and lease. "
        "How often varies. Message and data rates may apply. Reply STOP to stop, HELP for help."
    )


def record_signup(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    login_id: UUID,
    kind: str,
    person_id: UUID,
    phone: str,
    texts_ok: bool,
    wording: str,
) -> None:
    """A tenant's or an owner's answer at sign-up, kept with its words and time; the phone
    becomes their contact phone too (each of a tenant's records), as they gave it."""
    from pms.api import directory  # it reads the API's models; the API reads this module

    column: Literal["tenant_id", "owner_id"] = "tenant_id" if kind == "tenant" else "owner_id"
    conn.execute(
        f"INSERT INTO trust_text_consents (pmc_id, login_id, {column}, phone, texts_ok, wording)"  # noqa: S608
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, login_id, person_id, phone, texts_ok, wording),
    )
    records = (
        [
            r
            for (r,) in conn.execute(
                "SELECT trust_tenant_records(%s, %s) ORDER BY 1", (pmc_id, person_id)
            ).fetchall()
        ]
        if kind == "tenant"
        else [person_id]
    )
    for record in records:
        known = directory.contact_of(conn, column, record)
        directory.record_contact(
            conn, pmc_id, column, record, known.model_copy(update={"phone": phone}), by_person=True
        )
