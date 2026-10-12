"""E-signing a lease offer through a partner, behind one interface.

The founder picks the real partner at deploy time; its keys and terms come then (ADR 0002).
Until then the only partner is FakeESign. It keeps its envelopes in the server process's memory
(shared by every app in the process, gone when it restarts, and not shared between worker
processes: run one) and its signing page is part of the portal (/esign#<token>), shown only
while the fake is on. A signer "signs" there by typing their name; nothing about it
is a real signature, and it says so.

PMS_ESIGN_PARTNER picks the partner (pms.api.Settings): "fake", or unset for none, and then no
offer can be sent for signing.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class Signer:
    position: int
    name: str
    email: str


class Partner(Protocol):
    name: str

    def send(self, title: str, document: bytes, signers: tuple[Signer, ...]) -> str:
        """Sends a document for signing; the answer is the partner's reference for it."""
        ...

    def signing_link(self, reference: str, position: int) -> str:
        """Where the signer at `position` signs."""
        ...

    def signed(self, reference: str) -> dict[int, datetime]:
        """Who has signed so far, by position, and when."""
        ...


class NotSigned(Exception):
    """The fake partner refused a signature: wrong link, or not the signer's name."""


@dataclass
class _Envelope:
    title: str
    sha256: str
    signers: dict[int, Signer]
    tokens: dict[int, str]
    signed: dict[int, datetime] = field(default_factory=dict)


class FakeESign:
    """A stand-in partner for development and tests (see the module's docstring)."""

    name = "Fake e-sign partner"
    # One store for the process, as a partner's own service would be one place.
    _envelopes: dict[str, _Envelope] = {}
    _tokens: dict[str, tuple[str, int]] = {}
    _lock = threading.Lock()

    def __init__(self, public_url: str) -> None:
        self.public_url = public_url.rstrip("/")

    def send(self, title: str, document: bytes, signers: tuple[Signer, ...]) -> str:
        reference = f"fake-esign-{uuid.uuid4().hex}"
        tokens = {s.position: secrets.token_urlsafe(32) for s in signers}
        with self._lock:
            self._envelopes[reference] = _Envelope(
                title=title,
                sha256=hashlib.sha256(document).hexdigest(),
                signers={s.position: s for s in signers},
                tokens=tokens,
            )
            for position, token in tokens.items():
                self._tokens[token] = (reference, position)
        return reference

    def signing_link(self, reference: str, position: int) -> str:
        with self._lock:
            token = self._envelopes[reference].tokens[position]
        return f"{self.public_url}/esign#{token}"

    def signed(self, reference: str) -> dict[int, datetime]:
        with self._lock:
            envelope = self._envelopes.get(reference)
            return dict(envelope.signed) if envelope else {}

    # --- the fake's own signing page (src/pms/api/fake_esign.py) ---------------------------

    def view(self, token: str) -> tuple[str, str, str, bool]:
        """(document title, signer's name, document SHA-256, signed already?) for a link."""
        with self._lock:
            reference, position = self._found(token)
            envelope = self._envelopes[reference]
            return (
                envelope.title,
                envelope.signers[position].name,
                envelope.sha256,
                position in envelope.signed,
            )

    def sign(self, token: str, typed_name: str) -> None:
        with self._lock:
            reference, position = self._found(token)
            envelope = self._envelopes[reference]
            if typed_name.strip().casefold() != envelope.signers[position].name.casefold():
                raise NotSigned("type your name as it is on the lease")
            envelope.signed.setdefault(position, datetime.now(UTC))

    def _found(self, token: str) -> tuple[str, int]:
        if token not in self._tokens:
            raise NotSigned("this signing link is not one of ours")
        return self._tokens[token]


class NoPartner(Exception):
    """An e-sign partner was named that doesn't exist."""


def partner_named(name: str, public_url: str) -> Partner | None:
    """The partner called `name` ("fake"), or None for an empty name: no partner."""
    chosen = name.strip()
    if not chosen:
        return None
    if chosen != "fake":
        raise NoPartner(f"no e-sign partner called {chosen!r}; known: fake")
    return FakeESign(public_url)
