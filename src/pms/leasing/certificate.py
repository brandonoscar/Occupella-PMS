"""A lease offer's signing certificate (ADR 0008): a PDF, beside the lease it is about, saying
what happened on the PMS's signing page. The lease: its title, home, terms and the SHA-256 of the
PDF sent to sign. Each signer: their name, email and role, when their first link was made, when
and from where (address, browser) they agreed to sign electronically, opened the lease, asked for
paper and signed, the name they typed and the signature they drew; then the disclosures they
agreed to, word for word. It reads what the database recorded; it carries no digital seal.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from datetime import UTC, datetime

from fpdf import FPDF

PNG = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Step:
    """Something a signer did through their link: when, and the address and browser it came
    from (None when not known)."""

    at: datetime
    ip: str | None
    user_agent: str | None


@dataclass(frozen=True)
class Signer:
    name: str
    email: str
    role: str
    first_link_at: datetime | None
    consented: Step | None
    opened: Step | None
    paper: Step | None
    signed: Step | None
    typed_name: str | None
    drawing: bytes | None
    disclosure: str | None


@dataclass(frozen=True)
class Certificate:
    company: str
    document_title: str
    home: str
    terms: str
    document_sha256: str
    sent_at: datetime
    sent_by: str
    status: str
    signers: tuple[Signer, ...]


def drawing_fits(png: bytes) -> bool:
    """Whether bytes are a PNG whose header says 1 to 2000 pixels wide and 1 to 1000 high: the
    signature drawn on the page. Anything else is refused before it is kept."""
    if len(png) < 24 or not png.startswith(PNG) or png[12:16] != b"IHDR":
        return False
    width, height = struct.unpack(">II", png[16:24])
    return bool(1 <= width <= 2000 and 1 <= height <= 1000)


def when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%m/%d/%Y %H:%M:%S UTC")


def _latin1(value: str) -> str:
    """The PDF's built-in font has Latin-1 only; anything else prints as "?"."""
    return value.encode("latin-1", "replace").decode("latin-1")


def _step(step: Step) -> str:
    where = ", ".join(
        part
        for part in (
            f"from {step.ip}" if step.ip else None,
            f"browser: {step.user_agent}" if step.user_agent else None,
        )
        if part
    )
    return f"{when(step.at)}" + (f" ({where})" if where else "")


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-30)
        self.set_font("Helvetica", size=7)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def render(certificate: Certificate, generated: datetime) -> bytes:
    pdf = _Pdf(orientation="portrait", unit="pt", format="letter")
    pdf.set_creation_date(generated)
    pdf.set_title("Signing certificate")
    pdf.set_author("Occupella PMS")
    pdf.set_margins(48, 48, 48)
    pdf.set_auto_page_break(True, margin=48)
    pdf.add_page()

    def heading(text: str, size: int = 11) -> None:
        pdf.set_font("Helvetica", style="B", size=size)
        pdf.multi_cell(0, size + 4, _latin1(text), new_x="LMARGIN", new_y="NEXT")

    def line(label: str, value: str) -> None:
        pdf.set_font("Helvetica", style="B", size=8)
        pdf.cell(130, 11, _latin1(label))
        pdf.set_font("Helvetica", size=8)
        pdf.multi_cell(0, 11, _latin1(value), new_x="LMARGIN", new_y="NEXT")

    heading("Signing certificate", 14)
    pdf.set_font("Helvetica", size=8)
    pdf.multi_cell(
        0,
        11,
        _latin1(
            f"{certificate.company}. Made {when(generated)} from the records of the PMS's signing"
            " page. It carries no digital seal: the lease below is identified by its SHA-256."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(6)
    heading("The lease")
    line("Document", certificate.document_title)
    line("Home", certificate.home)
    line("Terms", certificate.terms)
    line("SHA-256 of the PDF", certificate.document_sha256)
    line("Sent to sign", f"{when(certificate.sent_at)} by {certificate.sent_by}")
    line("Status", certificate.status)

    disclosures: list[str] = []
    for n, signer in enumerate(certificate.signers, 1):
        pdf.ln(6)
        heading(f"Signer {n}: {signer.name}")
        line("Email", signer.email)
        line("Signs as", signer.role)
        if signer.first_link_at is not None:
            line("First link made", when(signer.first_link_at))
        line("Agreed to sign electronically", _step(signer.consented) if signer.consented else "-")
        line("Opened the lease", _step(signer.opened) if signer.opened else "-")
        if signer.paper is not None:
            line("Asked to sign on paper", _step(signer.paper))
        line("Signed", _step(signer.signed) if signer.signed else "Not yet")
        if signer.typed_name is not None:
            line("Name typed", signer.typed_name)
        if signer.drawing is not None:
            pdf.set_font("Helvetica", style="B", size=8)
            pdf.cell(130, 11, "Signature drawn")
            try:
                pdf.image(io.BytesIO(signer.drawing), h=48)
            except Exception:  # noqa: BLE001 - a PNG fpdf2 can't read still leaves the record
                pdf.set_font("Helvetica", size=8)
                pdf.multi_cell(0, 11, "(kept, but it can't be shown here)", new_x="LMARGIN")
        if signer.disclosure is not None:
            if signer.disclosure not in disclosures:
                disclosures.append(signer.disclosure)
            line("Disclosures agreed to", f"text {disclosures.index(signer.disclosure) + 1} below")

    for n, text in enumerate(disclosures, 1):
        pdf.ln(6)
        heading(f"Disclosures, text {n}")
        pdf.set_font("Helvetica", size=8)
        pdf.multi_cell(0, 11, _latin1(text), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
