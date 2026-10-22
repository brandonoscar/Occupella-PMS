"""Email sent for real (src/pms/email.py, src/pms/email_html.py, docs/adr/0007-email.md): the
PMS's SMTP sender hands each kind of email to Mailpit (MIT, a mail server for tests that keeps
what it receives), over STARTTLS with a certificate the test's own authority signed, and Mailpit
says what arrived: from whom, to whom, the text as written and the HTML made from it, nothing
attached. Mailpit's HTML check scores the HTML against what mail programs support, and the score
may not fall below its floor (ci/thresholds.toml [email]).

These need the mailpit program (ci.yml's email job builds it; locally,
`go install github.com/axllent/mailpit@v1.31.6`, or set MAILPIT to its path), so the default run
leaves them out and `pytest -m mailpit` runs them. Every name and address is synthetic.
"""

import json
import os
import shutil
import socket
import subprocess
import time
import tomllib
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from helpers import add_agreement, add_staff, contribute, enter_bill, make_pmc

from pms import accounts, email, email_html, notices

pytestmark = pytest.mark.mailpit

ROOT = Path(__file__).resolve().parent.parent
FLOOR = tomllib.loads((ROOT / "ci/thresholds.toml").read_text())["email"]
URL = "https://pms.example"
ADDRESS = ("1 Synthetic Way", "Anytown, ZZ 00000")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def certificates(folder):
    """A throwaway certificate authority, and a certificate for localhost it signed."""
    key = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    openssl(
        "req", "-x509", *key, "-days", "1", "-subj", "/CN=Synthetic test CA",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign",
        "-keyout", "ca.key", "-out", "ca.pem", cwd=folder,
    )  # fmt: skip
    openssl("req", *key, "-subj", "/CN=localhost", "-keyout", "server.key", "-out", "server.csr",
            cwd=folder)  # fmt: skip
    (folder / "server.ext").write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
        "extendedKeyUsage=serverAuth\n"
    )
    openssl(
        "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
        "-CAcreateserial", "-days", "1", "-extfile", "server.ext", "-out", "server.pem",
        cwd=folder,
    )  # fmt: skip
    return folder / "ca.pem"


class Mailpit:
    def __init__(self, smtp_port, web_port, authority):
        self.smtp_port = smtp_port
        self.web = f"http://127.0.0.1:{web_port}"
        self.authority = authority

    def get(self, path, **query):
        address = f"{self.web}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        with urllib.request.urlopen(address, timeout=10) as answer:  # noqa: S310 (http, local)
            return json.load(answer)

    def to(self, address):
        """Every message Mailpit received for this address, whole, oldest first."""
        found = self.get("/api/v1/search", query=f'to:"{address}"')["messages"]
        return [self.get(f"/api/v1/message/{m['ID']}") for m in reversed(found)]

    def sender(self):
        return email.SmtpSender("localhost", self.smtp_port, "pms", "synthetic", "pms@example.com")


@pytest.fixture(scope="module")
def mailpit(tmp_path_factory):
    program = shutil.which(os.environ.get("MAILPIT", "mailpit"))
    if program is None:
        raise RuntimeError(
            "these tests need Mailpit: go install github.com/axllent/mailpit@v1.31.6"
            " (or set MAILPIT to its path)"
        )
    folder = tmp_path_factory.mktemp("mailpit")
    authority = certificates(folder)
    smtp_port, web_port = free_port(), free_port()
    server = subprocess.Popen(  # noqa: S603 (the mailpit program, our own arguments)
        [
            program,
            "--smtp",
            f"127.0.0.1:{smtp_port}",
            "--listen",
            f"127.0.0.1:{web_port}",
            "--database",
            str(folder / "mailpit.db"),
            "--smtp-tls-cert",
            str(folder / "server.pem"),
            "--smtp-tls-key",
            str(folder / "server.key"),
            "--smtp-require-starttls",
            "--smtp-auth-accept-any",
            "--smtp-disable-rdns",
            "--disable-version-check",
        ],  # fmt: skip
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    running = Mailpit(smtp_port, web_port, authority)
    try:
        for _ in range(100):
            try:
                running.get("/api/v1/info")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Mailpit did not start")
        yield running
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture
def trusted(mailpit, monkeypatch):
    """The PMS's sender trusts the test's certificate authority, as it would a real one."""
    monkeypatch.setenv("SSL_CERT_FILE", str(mailpit.authority))
    return mailpit


def turn_on_statements(conn, pmc_id):
    settings = {**dict.fromkeys(notices.FIELDS), "owner_statement_day": 5}
    for flag in ("late_notices", "work_order_notices", "adverse_action_notices", "reserve_alerts"):
        settings[flag] = bool(settings[flag])
    conn.execute(
        f"INSERT INTO trust_email_settings (pmc_id, {', '.join(settings)})"  # noqa: S608
        f" VALUES (%s, {', '.join(['%s'] * len(settings))})",
        (pmc_id, *settings.values()),
    )


def company_of(conn, pmc):
    conn.execute(
        "INSERT INTO trust_company_profiles"
        " (pmc_id, address_line_1, city, state, postal_code, reply_to)"
        " VALUES (%s, '1 Synthetic Way', 'Anytown', 'ZZ', '00000', 'office@example.com')",
        (pmc.pmc_id,),
    )
    return conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def outbox(conn, pmc):
    return conn.execute(
        "SELECT kind, to_address, subject, body FROM trust_emails WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()


def test_each_kind_of_email_arrives_as_its_text_and_html_over_starttls(conn, app_conn, trusted):
    pmc = make_pmc(conn)
    company = company_of(conn, pmc)
    tag = pmc.pmc_id.hex[:8]
    first, second = pmc.owners
    # An owner's invite, its link held only in memory until it is sent.
    invite = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", first.owner_id, f"invite.{tag}@example.com", None
    )
    link = f"{URL}/invite#{invite.token}"
    email.write_invite(
        app_conn, pmc.pmc_id, "owner", first.owner_id, f"invite.{tag}@example.com", link,
        invite.expires_at,
    )  # fmt: skip
    # A bill over the owner's limit, to the owner's portal login.
    add_agreement(conn, pmc.pmc_id, second.account, date(2026, 1, 1), approval_limit="500")
    login = accounts.create_invite(
        conn, pmc.pmc_id, "owner", second.owner_id, f"bill.{tag}@example.com", None
    )
    accounts.accept_invite(conn, login.token, "a long synthetic passphrase")
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, second.account, "ROOF-1", date(2026, 1, 10),
        date(2026, 1, 20), "600.00",
    )  # fmt: skip
    email.write_bill_approval(app_conn, pmc.pmc_id, bill, URL)
    # A staff member's message to a vendor, with a web address in it.
    staff = add_staff(conn, pmc.pmc_id, "Writer Synthetic")
    email.write_message(
        app_conn, pmc.pmc_id, "vendor", pmc.vendor_id, f"vendor.{tag}@example.com",
        "Gate code", f"The gate code is 0000 & the map is at {URL}/map.", staff,
    )  # fmt: skip
    # Last month's statement in brief, its figures a table.
    turn_on_statements(conn, pmc.pmc_id)
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, owner_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, first.owner_id, f"statement.{tag}@example.com"),
    )
    contribute(conn, pmc.pmc_id, first.account, "c-1", "250.00", datetime(2026, 9, 10, tzinfo=UTC))
    # One to each owner: the first at their contact email, the second at their login's, with
    # the portal's link.
    assert notices.write_owner_statements(app_conn, pmc.pmc_id, date(2026, 10, 5), 5, URL) == 2
    # The first owner's account below its reserve (q054), told at their contact email.
    add_agreement(conn, pmc.pmc_id, first.account, date(2026, 1, 1), reserve="1000.00")
    assert notices.write_reserve_alerts(app_conn, pmc.pmc_id, date(2026, 10, 5), URL) == 1
    # The company's admin told the nightly run failed (q149), at their login's email.
    conn.execute(
        "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
        " VALUES (%s, %s, 'admin', true)",
        (pmc.pmc_id, staff),
    )
    made = conn.execute(
        "INSERT INTO trust_logins (pmc_id, kind, staff_id, email)"
        " VALUES (%s, 'staff', %s, %s) RETURNING id",
        (pmc.pmc_id, staff, f"admin.{tag}@example.com"),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_login_secrets (login_id, pmc_id, password_hash)"
        " VALUES (%s, %s, '$argon2id$synthetic')",
        (made, pmc.pmc_id),
    )
    assert (
        email.write_admin_alert(
            app_conn, pmc.pmc_id, "nightly_failed", date(2026, 10, 5), "Broken: synthetic", URL
        )
        == 1
    )
    written = outbox(conn, pmc)

    done = email.deliver_pending(app_conn, trusted.sender(), limit=1000)

    assert done.failed == 0
    for kind, to, subject, body in written:
        (got,) = [m for m in trusted.to(to) if m["Subject"] == subject]
        text = body.replace(email.LINK, link)
        assert got["From"] == {"Name": company, "Address": "pms@example.com"}
        # Replies reach the company's own email (q086).
        assert [a["Address"] for a in got["ReplyTo"]] == ["office@example.com"]
        assert [a["Address"] for a in got["To"]] == [to]
        assert got["Subject"] == subject
        assert got["Username"] == "pms"  # it signed in, after STARTTLS (Mailpit requires it)
        assert got["Text"].replace("\r\n", "\n") == text
        assert got["HTML"].replace("\r\n", "\n") == email_html.render(
            text, kind, subject, company, ADDRESS
        )
        assert got["Attachments"] == [] and got["Inline"] == []
    assert sorted(kind for kind, *_ in written) == [
        "admin_alert",
        "bill_approval",
        "invite",
        "message",
        "owner_statement",
        "owner_statement",
        "reserve_low",
    ]
    (invited,) = trusted.to(f"invite.{tag}@example.com")
    assert invite.token in invited["Text"] and f'<a href="{link}"' in invited["HTML"]


def test_mail_programs_support_the_html(conn, app_conn, trusted):
    pmc = make_pmc(conn)
    company_of(conn, pmc)
    to = f"check.{pmc.pmc_id.hex[:8]}@example.com"
    invite = accounts.create_invite(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, to, None)
    email.write_invite(
        app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, to, f"{URL}/invite#{invite.token}",
        invite.expires_at,
    )  # fmt: skip
    email.deliver_pending(app_conn, trusted.sender(), limit=1000)
    (got,) = trusted.to(to)

    total = trusted.get(f"/api/v1/message/{got['ID']}/html-check")["Total"]

    assert total["Supported"] >= FLOOR["html_check_supported_min"], total


def test_a_server_whose_certificate_is_not_trusted_gets_nothing(conn, app_conn, mailpit, tmp_path):
    pmc = make_pmc(conn)
    to = f"untrusted.{pmc.pmc_id.hex[:8]}@example.com"
    staff = add_staff(conn, pmc.pmc_id, "Writer Synthetic")
    made = email.write_message(
        app_conn, pmc.pmc_id, "vendor", pmc.vendor_id, to, "Hi", "Hi.", staff
    )
    # Another authority than the one that signed Mailpit's certificate.
    other = tmp_path / "other"
    other.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("SSL_CERT_FILE", str(certificates(other)))
        email.deliver_pending(app_conn, mailpit.sender(), limit=1000)

    (error,) = conn.execute(
        "SELECT error FROM trust_email_deliveries WHERE email_id = %s", (made,)
    ).fetchone()
    assert error.startswith("SSLCertVerificationError: ") and "certificate verify failed" in error
    assert mailpit.to(to) == []
