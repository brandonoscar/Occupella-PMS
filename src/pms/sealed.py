"""Bank account numbers, sealed before they reach the database (ADR 0009, rule 5).

An owner's or vendor's account number is kept for the ACH file the company takes to its bank
(src/pms/payouts/nacha.py). The app seals it with AES-GCM under the server's key
(PMS_DATA_KEY: 32 random bytes, base64) before it is written, and opens it only to write that
file; the database keeps the sealed bytes and the last four digits, to show. The key is never in
the repo, a workflow or the database: a server without one keeps no account numbers, and says
so. Rotating the key needs every sealed number opened under the old one and sealed again, which
no tool does yet: keep it.

A sealed value is a version byte (1), a 12-byte nonce, then the ciphertext with its tag. The
row's id goes in as associated data, so a sealed number copied onto another row doesn't open.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"\x01"
NONCE_BYTES = 12


class NoKey(Exception):
    """The server has no PMS_DATA_KEY, or one that isn't 32 bytes of base64."""


class Unreadable(Exception):
    """A sealed value that doesn't open under this key (another key, or tampered with)."""


def key_from(environ: Mapping[str, str] = os.environ) -> bytes:
    text = environ.get("PMS_DATA_KEY", "").strip()
    if not text:
        raise NoKey("this server keeps no bank account numbers: it has no PMS_DATA_KEY")
    try:
        key = base64.b64decode(text, validate=True)
    except binascii.Error:
        raise NoKey("PMS_DATA_KEY is not base64") from None
    if len(key) != 32:
        raise NoKey("PMS_DATA_KEY is not 32 bytes")
    return key


def seal(plain: str, row: str, key: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return VERSION + nonce + AESGCM(key).encrypt(nonce, plain.encode(), row.encode())


def unseal(sealed: bytes, row: str, key: bytes) -> str:
    if sealed[:1] != VERSION or len(sealed) <= 1 + NONCE_BYTES:
        raise Unreadable("not a sealed value this server writes")
    nonce, body = sealed[1 : 1 + NONCE_BYTES], sealed[1 + NONCE_BYTES :]
    try:
        return AESGCM(key).decrypt(nonce, body, row.encode()).decode()
    except InvalidTag:
        raise Unreadable("a sealed value that doesn't open under this server's key") from None
