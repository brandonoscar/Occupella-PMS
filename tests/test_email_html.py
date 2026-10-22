"""The HTML part of each email (src/pms/email_html.py, src/pms/templates/email.html,
docs/adr/0007-email.md): the email's own text laid out in the portal's colours and type, its one
link a button for the kinds that ask one thing, every other web address a link, figures a table,
and nothing in it but the text, escaped. Every name and address is synthetic."""

import re
import smtplib
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from helpers import make_pmc

from pms import accounts, email, email_html

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / "src/pms/templates/email.html").read_text()
TOKENS = (ROOT / "frontend/src/styles/globals.css").read_text()
URL = "https://pms.example"
INVITE = (
    "Hello Owner One,\n\n"
    "Synthetic PMC invites you to sign in to the owner portal. Set your password with this link;"
    " it works once, until 10/30/2026:\nhttps://pms.example/invite#token-1\n\nSynthetic PMC\n"
)


def html(text, kind="message", company="Synthetic PMC", address=()):
    return email_html.render(text, kind, "Subject", company, address)


def paragraphs(page):
    return re.findall(r"<p style=\"margin:0 0 16px;\">(.*?)</p>", page, re.S)


def test_each_paragraph_and_line_of_the_text_is_kept():
    page = html("Hello Tenant 1,\n\nFirst line.\nSecond line.\n\nSynthetic PMC\n")

    assert paragraphs(page) == ["Hello Tenant 1,", "First line.<br>Second line.", "Synthetic PMC"]
    assert "<title>Subject</title>" in page


def test_text_is_escaped_so_a_name_or_a_message_adds_no_markup():
    page = html(
        'Hello <b>Ann</b> & "Bo",\n\n<script>alert(1)</script>\n<a href="x">x</a>',
        company="A & B <Homes>",
    )

    assert "<b>" not in page and "<script>" not in page and '<a href="x">' not in page
    assert "Hello &lt;b&gt;Ann&lt;/b&gt; &amp; &#34;Bo&#34;," in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert page.count("A &amp; B &lt;Homes&gt;") == 2  # the header and the footer


def test_the_one_link_an_email_asks_to_open_is_a_button_with_the_link_under_it():
    page = html(INVITE, kind="invite")

    assert paragraphs(page)[1] == (
        "Synthetic PMC invites you to sign in to the owner portal. Set your password with this"
        " link; it works once, until 10/30/2026:"
    )
    button = re.search(r'<a href="([^"]+)" style="display:inline-block;[^"]*">([^<]+)</a>', page)
    assert button is not None
    assert button.groups() == ("https://pms.example/invite#token-1", "Set your password")
    assert (
        'Or copy this link into your browser:<br><a href="https://pms.example/invite#token-1"'
        in page
    )
    assert paragraphs(page)[-1] == "Synthetic PMC"


@pytest.mark.parametrize(("kind", "label"), sorted(email_html.BUTTONS.items()))
def test_each_kind_that_asks_one_thing_says_it_on_its_button(kind, label):
    page = html(f"Hello,\n\nDo it here:\n{URL}/\n\nAnd here:\n{URL}/two\n", kind=kind)

    assert re.findall(r"display:inline-block;[^\"]*\">([^<]+)</a>", page) == [label]
    # Only the first address alone on its line: the next is a link.
    assert f'And here:<br><a href="{URL}/two" style="color:#1957a0;">{URL}/two</a>' in page


@pytest.mark.parametrize("kind", ["message", "work_order", "adverse_action"])
def test_other_kinds_have_no_button_and_their_addresses_are_links(kind):
    page = html(f"Hello,\n\nSee:\n{URL}/x\n", kind=kind)

    assert "display:inline-block" not in page
    assert f'See:<br><a href="{URL}/x" style="color:#1957a0;">{URL}/x</a>' in page


def test_a_web_address_in_a_sentence_is_a_link_without_the_sentence_end():
    page = html(f"Pay at {URL}/pay. Or ask us (at {URL}/ask), thanks.\n")

    assert paragraphs(page) == [
        f'Pay at <a href="{URL}/pay" style="color:#1957a0;">{URL}/pay</a>. Or ask us (at'
        f' <a href="{URL}/ask" style="color:#1957a0;">{URL}/ask</a>), thanks.'
    ]
    # Only http and https are links: no other scheme reaches an href.
    assert 'href="javascript' not in html("javascript:alert(1) ftp://x.example/\n")


def test_an_address_ending_a_sentence_alone_on_its_line_is_no_button():
    page = html(f"Sign in:\n{URL}/.\n", kind="invite")

    assert "display:inline-block" not in page
    assert f'<a href="{URL}/" style="color:#1957a0;">{URL}/</a>.' in page


def test_lines_of_figures_are_a_table():
    page = html(
        "Your statement:\n\n  Opening balance  $0.00\n  Money in         $250.00\n"
        "  Money out        $0.00\n  Closing balance  $250.00\n\nSynthetic PMC\n",
        kind="owner_statement",
    )

    rows = re.findall(r'<td style="[^"]*">([^<]+)</td>\s*<td align="right"[^>]*>([^<]+)</td>', page)
    assert rows == [
        ("Opening balance", "$0.00"),
        ("Money in", "$250.00"),
        ("Money out", "$0.00"),
        ("Closing balance", "$250.00"),
    ]
    # A paragraph with a line that isn't figures stays a paragraph.
    mixed = html("  Indented  $1.00\nnot figures\n")
    assert 'align="right"' not in mixed
    assert paragraphs(mixed) == ["  Indented  $1.00<br>not figures"]


def test_the_footer_names_the_company_and_its_mailing_address():
    page = html("Hi\n", address=("1 Synthetic Way", "Suite 2", "Anytown, ZZ 00000"))

    assert "Synthetic PMC<br>1 Synthetic Way<br>Suite 2<br>Anytown, ZZ 00000</td>" in page
    assert "Synthetic PMC</td>\n</tr>\n</table>\n</td>" in html("Hi\n")  # no address on file


def test_windows_line_ends_read_as_lines():
    assert paragraphs(html("One\r\n\r\nTwo\r\nThree\r\n")) == ["One", "Two<br>Three"]


def token_values(prefix):
    return set(re.findall(rf"--{prefix}-[a-z-]+:\s*([^;]+);", TOKENS))


def test_the_template_uses_only_the_portals_colours_sizes_and_fonts():
    colours = {value.lower() for value in token_values("color")}
    used = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", TEMPLATE))
    assert used and {c.lower() for c in used} <= colours, used - colours

    sizes = token_values("text")
    used_sizes = {f"{px}px" for px in re.findall(r"font-size:(\d+)px", TEMPLATE)}
    assert used_sizes and used_sizes <= sizes, used_sizes - sizes

    def stack(fonts):  # a font stack, its quotes and spaces aside
        return re.sub(r"[\"']|(?<=,) ", "", fonts)

    fonts = {stack(value) for value in token_values("font")}
    used_fonts = {stack(used) for used in re.findall(r"font-family:([^;\"]+)", TEMPLATE)}
    assert used_fonts and used_fonts <= fonts, used_fonts - fonts


def test_every_kind_with_a_button_is_a_kind_of_email(conn):
    (check,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'trust_emails_kind_check'"
    ).fetchone()
    kinds = set(re.findall(r"'([a-z_]+)'::text", check))

    assert set(email_html.BUTTONS) <= kinds
    # These ask the reader to do nothing in the PMS: a receipt and a deposit statement carry
    # their PDF and no link.
    assert kinds - set(email_html.BUTTONS) == {
        "message",
        "work_order",
        "adverse_action",
        "payment_receipt",
        "deposit_statement",
    }


# --- sending it -------------------------------------------------------------------------------


class Server:
    sent: list = []

    def __init__(self, host, port, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        Server.sent.append(message_from_bytes(message.as_bytes(), policy=policy.default))


def test_smtp_sends_the_text_and_the_html_as_alternatives(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", Server)
    Server.sent = []
    smtp = email.SmtpSender("mail.example.com", 587, "", "", "pms@example.com")
    page = html(INVITE, kind="invite")

    smtp.send(email.Message("owner@example.com", "Sign in", INVITE, "Synthetic PMC", page))
    smtp.send(email.Message("owner@example.com", "Plain", "Just text.\n"))

    both, plain = Server.sent
    assert both.get_content_type() == "multipart/alternative"
    text, rich = both.iter_parts()
    assert text.get_content_type() == "text/plain" and text.get_content() == INVITE
    assert rich.get_content_type() == "text/html" and rich.get_content() == page
    assert both["From"] == "Synthetic PMC <pms@example.com>"
    assert plain.get_content_type() == "text/plain" and plain.get_content() == "Just text.\n"


class Kept:
    name = "fake"

    def __init__(self):
        self.sent: list[email.Message] = []

    def send(self, message):
        self.sent.append(message)


def test_delivery_hands_the_sender_html_made_from_the_text_link_and_address(conn, app_conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].owner_id
    # The latest profile holds: the company moved.
    for street in ("9 Former Road", "1 Synthetic Way"):
        conn.execute(
            "INSERT INTO trust_company_profiles (pmc_id, address_line_1, city, state, postal_code)"
            " VALUES (%s, %s, 'Anytown', 'ZZ', '00000')",
            (pmc.pmc_id, street),
        )
    invite = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", owner, "h.owner@example.com", None
    )
    link = f"{URL}/invite#{invite.token}"
    email.write_invite(
        app_conn, pmc.pmc_id, "owner", owner, "h.owner@example.com", link, invite.expires_at
    )

    sender = Kept()
    email.deliver_pending(app_conn, sender, limit=1000)
    (sent,) = [m for m in sender.sent if m.to == "h.owner@example.com"]

    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert sent.html == email_html.render(
        sent.body, "invite", sent.subject, company, ("1 Synthetic Way", "Anytown, ZZ 00000")
    )
    assert f'<a href="{link}"' in sent.html and email.LINK not in sent.html
    # The outbox keeps the text alone: no HTML, and never the link.
    (kept,) = conn.execute(
        "SELECT body FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall()[0]
    assert invite.token not in kept and "<" not in kept
