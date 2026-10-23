"""Text messages (migration 20261023000204; src/pms/texts.py): a person's word on being texted,
what staff text a tenant, an owner or a vendor from that person's Texts tab, the texts the PMS
writes by itself, a tenant's own word in the portal, and the replies Twilio posts back (STOP and
START). A text goes only to the number its person last agreed to, while they agree (the database
refuses any other), and only while the server sends texts. Staff need the 'email' area to write a
text, record someone's word or change the settings (custom roles). The AI role reads none of it.
Nothing here moves money.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from pms import email, texts
from pms.api.deps import Conn, Staff, Tenant

router = APIRouter(prefix="/api")

About = texts.Kind
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=30)]
# One text: no NUL, and short enough that with the company's name, who wrote it and how to stop
# it stays within texts.LONGEST (checked again as it is written).
Text = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300, pattern=r"^[^\x00]*$"),
]


class ConsentOut(BaseModel):
    phone: str
    consented: bool
    how: str
    at: datetime
    # The staff member who recorded it; none for the person's own word or a reply.
    by: str | None


class TextLine(BaseModel):
    id: UUID
    kind: str
    to: str
    body: str
    at: datetime
    by: str | None
    status: Literal["sent", "waiting", "failed"]
    error: str | None


class PersonTexts(BaseModel):
    # Whether this server sends texts: until it does, none is written.
    sending: bool
    consent: ConsentOut | None
    # The phone on file, as a start for the number they agree to.
    phone_on_file: str | None
    texts: list[TextLine]


class NewConsent(BaseModel):
    about: About
    about_id: UUID
    phone: Phone
    consented: bool
    how: Literal["in writing", "verbally"]


class NewText(BaseModel):
    about: About
    about_id: UUID
    text: Text


class Written(BaseModel):
    id: UUID
    to: str


def name_of(conn: psycopg.Connection[Any], pmc: UUID, about: About, person_id: UUID) -> str:
    named = conn.execute(email.PEOPLE[about][0], (pmc, person_id)).fetchone()
    if named is None:
        raise HTTPException(404, f"no such {about}")
    return str(named[0])


def mobile(typed: str) -> str:
    phone = texts.us_mobile(typed)
    if phone is None:
        raise HTTPException(422, "that isn't a US mobile number: give its ten digits")
    return phone


def consent_out(found: texts.Consent | None) -> ConsentOut | None:
    if found is None:
        return None
    return ConsentOut(
        phone=found.phone, consented=found.consented, how=found.how, at=found.at, by=found.by
    )


def phone_on_file(conn: psycopg.Connection[Any], about: About, person_id: UUID) -> str | None:
    row = conn.execute(
        f"SELECT phone FROM trust_contacts WHERE {texts.COLUMN[about]} = %s"  # noqa: S608
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    return None if row is None or row[0] is None else texts.us_mobile(row[0])


TEXTS = f"""
SELECT t.id, t.kind, t.to_phone, t.body, t.created_at, s.display_name,
       CASE WHEN EXISTS (SELECT 1 FROM trust_text_deliveries AS d
                         WHERE d.text_id = t.id AND d.error IS NULL) THEN 'sent'
            WHEN (SELECT count(*) FROM trust_text_deliveries AS d WHERE d.text_id = t.id)
                 >= {texts.MAX_ATTEMPTS}
              OR EXISTS (SELECT 1 FROM trust_text_deliveries AS d
                         WHERE d.text_id = t.id AND d.error = ANY(%(final)s)) THEN 'failed'
            ELSE 'waiting' END,
       (SELECT d.error FROM trust_text_deliveries AS d WHERE d.text_id = t.id
        ORDER BY d.attempted_at DESC, d.id DESC LIMIT 1)
FROM trust_texts AS t
LEFT JOIN trust_staff AS s ON s.id = t.sent_by
WHERE t.pmc_id = %(pmc)s AND t.{{column}} = %(person)s
ORDER BY t.created_at DESC, t.id DESC
LIMIT 100
"""  # noqa: S608


def person_texts(
    conn: psycopg.Connection[Any], pmc: UUID, about: About, person_id: UUID
) -> PersonTexts:
    rows = conn.execute(
        TEXTS.format(column=texts.COLUMN[about]),
        {"pmc": pmc, "person": person_id, "final": list(texts.FINAL)},
    ).fetchall()
    return PersonTexts(
        sending=texts.TEXTING.get(),
        consent=consent_out(texts.consent_of(conn, pmc, about, person_id)),
        phone_on_file=phone_on_file(conn, about, person_id),
        texts=[
            TextLine(id=i, kind=k, to=to, body=b, at=at, by=by, status=st, error=er)
            for i, k, to, b, at, by, st, er in rows
        ],
    )


@router.get("/texts")
def texts_of(
    session: Staff, conn: Conn, about: Annotated[About, Query()], about_id: UUID
) -> PersonTexts:
    """A tenant's, owner's or vendor's word on texts and every text the PMS wrote them, newest
    first, each with whether it went."""
    name_of(conn, session.pmc_id, about, about_id)
    return person_texts(conn, session.pmc_id, about, about_id)


@router.post("/texts/consent", status_code=201)
def record_consent(session: Staff, conn: Conn, new: NewConsent) -> PersonTexts:
    """Records the word a person gave staff, in writing or by word of mouth: yes to texts at a
    mobile number, or no. The same word again writes nothing."""
    name_of(conn, session.pmc_id, new.about, new.about_id)
    texts.record_consent(
        conn,
        session.pmc_id,
        new.about,
        new.about_id,
        mobile(new.phone),
        new.consented,
        new.how,
    )
    return person_texts(conn, session.pmc_id, new.about, new.about_id)


@router.post("/texts", status_code=201)
def send_text(session: Staff, conn: Conn, new: NewText) -> Written:
    """Writes a staff member's text to a tenant, an owner or a vendor who agreed to texts; it
    goes out after the request commits."""
    name = name_of(conn, session.pmc_id, new.about, new.about_id)
    if not texts.TEXTING.get():
        raise HTTPException(409, "this server sends no texts")
    names = conn.execute(
        "SELECT p.display_name, s.display_name FROM trust_pmcs AS p"
        " JOIN trust_staff AS s ON s.pmc_id = p.pmc_id WHERE p.pmc_id = %s AND s.id = %s",
        (session.pmc_id, session.person_id),
    ).fetchone()
    assert names is not None, "a staff session is a staff member of its company"
    company, staff = names
    body = texts.message_body(company, new.text, staff)
    if len(body) > texts.LONGEST:
        raise HTTPException(422, f"too long to text: shorten it by {len(body) - texts.LONGEST}")
    made = texts.write(
        conn,
        session.pmc_id,
        "message",
        new.about,
        new.about_id,
        body,
        datetime.now(UTC),
        sent_by=session.person_id,
    )
    if made is None:
        raise HTTPException(409, f"{name} hasn't agreed to texts: record their word first")
    consent = texts.consent_of(conn, session.pmc_id, new.about, new.about_id)
    assert consent is not None
    return Written(id=made, to=consent.phone)


# --- the texts the PMS writes by itself ---------------------------------------------------------


class TextSettings(BaseModel):
    # Days before rent is due that each tenant who agreed is texted a reminder; none texts none.
    rent_reminder_days: int | None = Field(default=None, ge=1, le=15)
    # Each tenant who agreed is texted when a late fee is charged to their lease.
    late_notices: bool = False
    # Each tenant of the home who agreed is texted a work order's visit once it is set.
    visits: bool = False


class TextSettingsOut(TextSettings):
    sending: bool


def settings_out(conn: psycopg.Connection[Any], pmc: UUID) -> TextSettingsOut:
    return TextSettingsOut(**vars(texts.settings_of(conn, pmc)), sending=texts.TEXTING.get())


@router.get("/texts/settings")
def text_settings(session: Staff, conn: Conn) -> TextSettingsOut:
    """Which texts the PMS writes by itself for the company, and whether this server sends
    texts."""
    return settings_out(conn, session.pmc_id)


@router.post("/texts/settings")
def save_text_settings(session: Staff, conn: Conn, wanted: TextSettings) -> TextSettingsOut:
    """Keeps the company's choice as a new settings row if it changed; the same again writes
    nothing."""
    if texts.Settings(**wanted.model_dump()) != texts.settings_of(conn, session.pmc_id):
        conn.execute(
            "INSERT INTO trust_text_settings (pmc_id, rent_reminder_days, late_notices, visits)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, wanted.rent_reminder_days, wanted.late_notices, wanted.visits),
        )
    return settings_out(conn, session.pmc_id)


# --- a tenant's own word ------------------------------------------------------------------------


class MyTexts(BaseModel):
    sending: bool
    consent: ConsentOut | None
    phone_on_file: str | None


class MyWord(BaseModel):
    phone: Phone
    consented: bool


def my_texts(conn: psycopg.Connection[Any], session: Any) -> MyTexts:
    return MyTexts(
        sending=texts.TEXTING.get(),
        consent=consent_out(texts.consent_of(conn, session.pmc_id, "tenant", session.person_id)),
        phone_on_file=phone_on_file(conn, "tenant", session.person_id),
    )


@router.get("/tenant/texts")
def tenant_texts(session: Tenant, conn: Conn) -> MyTexts:
    """The tenant's word on texts from the company."""
    return my_texts(conn, session)


@router.post("/tenant/texts", status_code=201)
def keep_tenant_word(session: Tenant, conn: Conn, word: MyWord) -> MyTexts:
    """The tenant's own yes to texts at a mobile number, or no; renting at more than one
    property, theirs at each. The same word again writes nothing."""
    phone = mobile(word.phone)
    for (record,) in conn.execute(
        "SELECT trust_tenant_records(%s, %s) ORDER BY 1", (session.pmc_id, session.person_id)
    ).fetchall():
        texts.record_consent(
            conn, session.pmc_id, "tenant", record, phone, word.consented, "portal"
        )
    return my_texts(conn, session)


# --- replies ------------------------------------------------------------------------------------

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


@router.post("/twilio/webhook")
async def twilio_webhook(request: Request, conn: Conn) -> Response:
    """A reply Twilio posts back, signed with the account's auth token: STOP or START changes
    the word of everyone texted at that number; nothing else is kept. Twilio sends its own
    confirmation of a STOP or a START. NOT VERIFIED against Twilio itself."""
    sender = request.app.state.texts
    if not isinstance(sender, texts.TwilioSender):
        raise HTTPException(404, "this server sends no texts through Twilio")
    form = dict(urllib.parse.parse_qsl((await request.body()).decode(), keep_blank_values=True))
    url = f"{request.app.state.settings.public_url.rstrip('/')}{request.url.path}"
    if not sender.signed(url, form, request.headers.get("x-twilio-signature", "")):
        raise HTTPException(403, "this didn't come from Twilio")
    phone = texts.us_mobile(form.get("From", ""))
    if phone is not None:
        texts.reply(conn, phone, form.get("Body", ""))
    return Response(EMPTY_TWIML, media_type="text/xml")
