"""PDFs from outside the company that run or open something by themselves (src/pms/api/documents.py,
`scripted`; docs/edge-cases.md F-010): a tenant's own document or an applicant's proof of income
goes out to staff and co-tenants, who open it in a desktop reader the download's sandbox doesn't
reach. Every PDF here is made on the spot and synthetic."""

import io
import warnings

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from pms.api import documents


def pdf(change=None) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    if change is not None:
        change(writer)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def open_action(kind):
    def change(writer):
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject(kind), NameObject("/F"): TextStringObject("x")}
        )

    return change


def with_script(writer):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        writer.add_js("app.alert('synthetic')")


def test_a_plain_pdf_and_one_that_opens_at_a_page_are_taken():
    assert documents.scripted(pdf()) is None
    assert documents.scripted(pdf(open_action("/GoTo"))) is None


@pytest.mark.parametrize(
    "change",
    [
        with_script,
        lambda writer: writer.add_attachment("synthetic.txt", b"synthetic"),
        open_action("/Launch"),
        open_action("/SubmitForm"),
        open_action("/JavaScript"),
    ],
    ids=["script", "attachment", "launch", "submit form", "script action"],
)
def test_a_pdf_that_runs_or_opens_something_by_itself_is_refused(change):
    assert documents.scripted(pdf(change)) == documents.SCRIPTED


def test_a_script_named_in_hex_is_read_as_itself():
    hidden = pdf(with_script).replace(b"/JavaScript", b"/J#61vaScript")
    assert documents.scripted(hidden) == documents.SCRIPTED


def test_one_locked_with_a_password_is_refused():
    def lock(writer):
        writer.encrypt(user_password="synthetic", owner_password="synthetic")

    assert documents.scripted(pdf(lock)) == documents.LOCKED


def test_one_that_cant_be_read_is_refused_only_when_its_bytes_could_hide_a_script():
    assert documents.scripted(b"%PDF-1.4\n% synthetic\n") is None
    assert documents.scripted(b"%PDF-1.4\n<< /S /JavaScript /JS (x) >>\n") == documents.UNREADABLE
    assert documents.scripted(b"%PDF-1.4\n<< /Type /ObjStm >>\n") == documents.UNREADABLE
    assert documents.scripted(b"%PDF-1.4\n<< /J#53 (x) >>\n") == documents.UNREADABLE


def test_one_with_more_objects_than_are_read_is_refused(monkeypatch):
    # A PDF too big to read through isn't taken from outside: what it hides can't be told.
    monkeypatch.setattr(documents, "MOST_OBJECTS", 1)
    assert documents.scripted(pdf()) == documents.UNREADABLE
