"""Numbers sealed by the app (src/pms/sealed.py): AES-GCM under the server's PMS_FIELD_KEY,
bound to what they are about. A sealed value opens only with its key and its own context, never
once changed; without a key nothing is sealed or opened."""

import base64
import stat

import pytest

from pms import sealed

KEY = base64.b64decode(sealed.new_key())
OTHER = base64.b64decode(sealed.new_key())


def test_a_sealed_number_opens_with_its_key_and_context_alone():
    blob = sealed.seal("000123456789", "payee bank account:p:vendor:v", KEY)

    assert b"000123456789" not in blob and blob[:1] == sealed.VERSION
    assert sealed.open_sealed(blob, "payee bank account:p:vendor:v", KEY) == "000123456789"
    with pytest.raises(sealed.CannotOpen):
        sealed.open_sealed(blob, "payee bank account:p:vendor:w", KEY)
    with pytest.raises(sealed.CannotOpen):
        sealed.open_sealed(blob, "payee bank account:p:vendor:v", OTHER)
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(sealed.CannotOpen):
        sealed.open_sealed(tampered, "payee bank account:p:vendor:v", KEY)
    with pytest.raises(sealed.CannotOpen, match="not a sealed value"):
        sealed.open_sealed(b"\x02" + blob[1:], "payee bank account:p:vendor:v", KEY)


def test_the_same_number_seals_differently_each_time():
    assert sealed.seal("1234", "c", KEY) != sealed.seal("1234", "c", KEY)


def test_the_key_comes_from_the_environment_whole(monkeypatch):
    key = sealed.new_key()
    monkeypatch.setenv(sealed.ENV, key)
    assert sealed.configured()
    assert sealed.open_sealed(sealed.seal("5678", "c"), "c") == "5678"

    for bad in ("", "not base64!", base64.b64encode(b"short").decode()):
        monkeypatch.setenv(sealed.ENV, bad)
        assert not sealed.configured()
        with pytest.raises(sealed.NoKey):
            sealed.seal("5678", "c")
    monkeypatch.delenv(sealed.ENV)
    with pytest.raises(sealed.NoKey, match="the server has no PMS_FIELD_KEY"):
        sealed.current_key()


def test_a_new_key_is_written_for_its_owner_alone_and_never_over_another(tmp_path, capsys):
    path = tmp_path / "field.key"

    assert sealed.main(["sealed", "new-key", str(path)]) == 0
    written = path.read_text().strip()
    assert len(base64.b64decode(written)) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # The key is in the file, never on the screen.
    assert written not in capsys.readouterr().out
    assert sealed.main(["sealed", "new-key", str(path)]) == 1
    assert path.read_text().strip() == written
    assert sealed.main(["sealed"]) == 2
