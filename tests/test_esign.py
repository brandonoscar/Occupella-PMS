"""The e-sign partner interface and its fake partner (src/pms/leasing/esign.py)."""

import hashlib

import pytest

from pms.api import Settings
from pms.leasing import esign

DOCUMENT = b"%PDF-1.4 a synthetic lease"
SIGNERS = (
    esign.Signer(1, "Signer One", "one@example.com"),
    esign.Signer(2, "Signer Two", "two@example.com"),
)


def token_of(link):
    return link.split("#", 1)[1]


def test_the_fake_partner_sends_each_signer_their_own_link():
    fake = esign.FakeESign("http://pms.example/")
    reference = fake.send("Lease", DOCUMENT, SIGNERS)

    one, two = (fake.signing_link(reference, n) for n in (1, 2))

    assert reference.startswith("fake-esign-")
    assert one.startswith("http://pms.example/esign#") and one != two
    assert fake.view(token_of(one)) == (
        "Lease",
        "Signer One",
        hashlib.sha256(DOCUMENT).hexdigest(),
        False,
    )
    assert fake.signed(reference) == {}


def test_a_signer_signs_with_their_name_once_on_their_own_link():
    fake = esign.FakeESign("http://pms.example")
    reference = fake.send("Lease", DOCUMENT, SIGNERS)
    link = token_of(fake.signing_link(reference, 2))

    with pytest.raises(esign.NotSigned, match="type your name as it is on the lease"):
        fake.sign(link, "Signer One")
    fake.sign(link, "  signer two ")
    first = fake.signed(reference)
    fake.sign(link, "Signer Two")

    assert list(first) == [2]
    assert fake.signed(reference) == first
    assert fake.view(link)[3] is True
    assert fake.view(token_of(fake.signing_link(reference, 1)))[3] is False


def test_the_fake_knows_only_its_own_links_and_references():
    fake = esign.FakeESign("http://pms.example")

    with pytest.raises(esign.NotSigned, match="not one of ours"):
        fake.view("x" * 40)
    with pytest.raises(esign.NotSigned, match="not one of ours"):
        fake.sign("x" * 40, "Signer One")
    assert fake.signed("fake-esign-unknown") == {}


def test_every_fake_in_the_process_shares_one_store():
    reference = esign.FakeESign("http://a.example").send("Lease", DOCUMENT, SIGNERS)
    other = esign.FakeESign("http://b.example")

    other.sign(token_of(other.signing_link(reference, 1)), "Signer One")

    assert list(esign.FakeESign("http://a.example").signed(reference)) == [1]


def test_a_partner_is_chosen_by_name():
    assert esign.partner_named("", "http://pms.example") is None
    assert esign.partner_named("  ", "http://pms.example") is None
    fake = esign.partner_named("fake", "http://pms.example/")
    assert isinstance(fake, esign.FakeESign) and fake.public_url == "http://pms.example"
    with pytest.raises(esign.NoPartner, match="no e-sign partner called 'acme'"):
        esign.partner_named("acme", "http://pms.example")


def test_the_server_reads_its_partner_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://example.invalid/pms")
    monkeypatch.setenv("PMS_ESIGN_PARTNER", "fake")
    assert Settings.from_env().esign_partner == "fake"
    monkeypatch.delenv("PMS_ESIGN_PARTNER")
    assert Settings.from_env().esign_partner == ""
