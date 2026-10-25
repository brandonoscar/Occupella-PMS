"""The HTML part of each email the PMS sends (docs/adr/0007-email.md): the email's own text, laid
out by one Jinja2 template (src/pms/templates/email.html) in the portal's colours and type.

The text is the email. It is what the outbox keeps and staff read, and it goes as the plain-text
part. The HTML part says nothing the text doesn't, and is made from it when the email is handed
to the sender: each paragraph as a paragraph; a web address alone on its line as a button, for
the kinds that ask the reader to do one thing, with the address under it; every other web
address as a link; and lines of figures ("  Money in   $250.00") as a table. Everything is text
to Jinja2, which escapes it, so a name or a message can't add markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import jinja2

TEMPLATES = Path(__file__).parent / "templates"

# The one thing some kinds of email ask the reader to do, said on its button. The link stays the
# email's own: the button only shows it.
BUTTONS = {
    "invite": "Set your password",
    "password_reset": "Set a new password",
    "signing_request": "Open and sign",
    "bill_approval": "Sign in to approve or decline",
    "bill_funds_needed": "Sign in to the owner portal",
    "rent_reminder": "Sign in to the tenant portal",
    "late_notice": "Sign in to the tenant portal",
    "owner_statement": "Sign in to the owner portal",
    "reserve_low": "Sign in to the owner portal",
    "emergency_request": "Open Work orders",
    "autopay_payment": "Sign in to the tenant portal",
    "autopay_not_sent": "Sign in to the tenant portal",
    "autopay_stopped": "Sign in to the tenant portal",
}

WEB_ADDRESS = re.compile(r"https?://[^\s<>\"']+")
# Punctuation after an address ends the sentence, not the address.
SENTENCE_END = ".,;:!?)]"
# A line of figures: indented two spaces, its label and its amount two or more spaces apart.
FIGURE = re.compile(r"  (\S.*?\S|\S) {2,}(\S.*)")

_templates = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES),
    autoescape=True,
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


@dataclass(frozen=True)
class Span:
    text: str
    href: str | None = None


@dataclass(frozen=True)
class Block:
    """A paragraph (its lines), a button (its link and words) or a table of figures."""

    lines: tuple[tuple[Span, ...], ...] = ()
    href: str = ""
    label: str = ""
    figures: tuple[tuple[str, str], ...] = ()


def spans(line: str) -> tuple[Span, ...]:
    """A line's text and web addresses, each address a link."""
    found: list[Span] = []
    at = 0
    for match in WEB_ADDRESS.finditer(line):
        address = match.group().rstrip(SENTENCE_END)
        if match.start() > at:
            found.append(Span(line[at : match.start()]))
        found.append(Span(address, address))
        at = match.start() + len(address)
    if at < len(line):
        found.append(Span(line[at:]))
    return tuple(found)


def blocks(text: str, kind: str) -> list[Block]:
    """The text's paragraphs, the first address alone on a line of a kind with a button as that
    button, and a paragraph of figures as a table."""
    label = BUTTONS.get(kind)
    found: list[Block] = []
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip("\n")):
        lines = paragraph.split("\n")
        figures = [FIGURE.fullmatch(line.rstrip()) for line in lines]
        if all(figures):
            found.append(Block(figures=tuple((f[1], f[2]) for f in figures if f)))
            continue
        kept: list[tuple[Span, ...]] = []
        for line in lines:
            alone = line.strip()
            if label and WEB_ADDRESS.fullmatch(alone) and alone[-1] not in SENTENCE_END:
                if kept:
                    found.append(Block(lines=tuple(kept)))
                    kept = []
                found.append(Block(href=alone, label=label))
                label = None  # one button an email
            else:
                kept.append(spans(line))
        if kept:
            found.append(Block(lines=tuple(kept)))
    return found


def render(text: str, kind: str, subject: str, company: str, address: tuple[str, ...]) -> str:
    """The HTML part of an email of this kind, its text as written (any link already in it)."""
    return _templates.get_template("email.html").render(
        blocks=blocks(text, kind), subject=subject, company=company, address=address
    )
