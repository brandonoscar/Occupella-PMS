"""Numbers sealed by the app before they reach the database (the founder, 2026-10-23: bank
account numbers, then taxpayer IDs, kept encrypted). A value is sealed with AES-GCM under the
server's PMS_FIELD_KEY (32 random bytes, base64) and bound to what it is about (its context: the
company, the kind of number and the person), so a sealed value copied onto another row doesn't
open. The database holds only the sealed bytes: the key never reaches it, its backups or the
audit log. Lose the key and the sealed numbers are gone; staff enter them again.

A sealed value is: version (1 byte), nonce (12 bytes), ciphertext and tag.

usage: python -m pms.sealed new-key PATH    (writes a new key to PATH, readable by its owner
                                             alone; set PMS_FIELD_KEY to its contents)
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV = "PMS_FIELD_KEY"
VERSION = b"\x01"
NONCE = 12


class NoKey(RuntimeError):
    """The server has no PMS_FIELD_KEY, so it can neither seal nor open a number."""


class CannotOpen(ValueError):
    """A sealed value that this key and context don't open: another key, another row, or
    tampered with."""


def key_from(text: str) -> bytes:
    try:
        key = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise NoKey(f"{ENV} is not base64") from None
    if len(key) != 32:
        raise NoKey(f"{ENV} must be 32 bytes, base64")
    return key


def current_key() -> bytes:
    text = os.environ.get(ENV, "")
    if not text:
        raise NoKey(f"the server has no {ENV}")
    return key_from(text)


def configured() -> bool:
    try:
        current_key()
    except NoKey:
        return False
    return True


def seal(plain: str, context: str, key: bytes | None = None) -> bytes:
    nonce = secrets.token_bytes(NONCE)
    sealed = AESGCM(key or current_key()).encrypt(nonce, plain.encode(), context.encode())
    return VERSION + nonce + sealed


def open_sealed(blob: bytes, context: str, key: bytes | None = None) -> str:
    blob = bytes(blob)
    if blob[:1] != VERSION or len(blob) < 1 + NONCE + 16:
        raise CannotOpen("not a sealed value of this version")
    nonce, sealed = blob[1 : 1 + NONCE], blob[1 + NONCE :]
    try:
        return AESGCM(key or current_key()).decrypt(nonce, sealed, context.encode()).decode()
    except InvalidTag:
        raise CannotOpen("this key and context don't open it") from None


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "new-key":
        print(__doc__.split("usage:")[1].strip(), file=sys.stderr)
        return 2
    path = Path(argv[2])
    if path.exists():
        print(f"{path} exists; a new key never overwrites one", file=sys.stderr)
        return 1
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(new_key() + "\n")
    print(f"wrote a new key to {path}; keep it with the server's other settings and a copy apart")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
