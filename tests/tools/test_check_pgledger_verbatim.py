"""scripts/check_pgledger_verbatim.py: the pgledger blocks must match upstream byte for byte."""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_pgledger_verbatim", ROOT / "scripts/check_pgledger_verbatim.py"
)
verbatim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verbatim)


@pytest.fixture
def upstream(tmp_path):
    """A stand-in for the upstream checkout, rebuilt from the migration's own blocks."""
    text = verbatim.MIGRATION.read_text()
    for match in verbatim.BLOCK.finditer(text):
        body = match["body"]
        if match["path"].endswith("ulid-to-uuid.sql"):
            body = body[:-1]  # upstream has no final newline here
        target = tmp_path / match["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return tmp_path


def test_blocks_match_upstream(upstream, capsys):
    assert verbatim.main(["--upstream", str(upstream)]) == 0
    assert capsys.readouterr().out.count("ok ") == 3


def test_an_edited_block_fails(upstream, capsys):
    pgledger = upstream / "pgledger.sql"
    pgledger.write_text(pgledger.read_text().replace("ORDER BY unnest", "ORDER BY unnest DESC"))
    assert verbatim.main(["--upstream", str(upstream)]) == 1
    assert "FAIL  pgledger.sql differs" in capsys.readouterr().out


def test_missing_commit_or_reordered_blocks_fail(upstream, monkeypatch, tmp_path, capsys):
    migration = tmp_path / "migration.sql"
    original = verbatim.MIGRATION.read_text()
    monkeypatch.setattr(verbatim, "MIGRATION", migration)

    migration.write_text(original.replace(verbatim.PGLEDGER_COMMIT, "0" * 40))
    assert verbatim.main(["--upstream", str(upstream)]) == 1
    assert "does not name commit" in capsys.readouterr().out

    blocks = list(verbatim.BLOCK.finditer(original))
    first, second = blocks[0], blocks[1]
    swapped = (
        original[: first.start()] + second.group(0) + first.group(0) + original[second.end() :]
    )
    migration.write_text(swapped)
    assert verbatim.main(["--upstream", str(upstream)]) == 1
    assert re.search(r"expected blocks .* in that order", capsys.readouterr().out)


def test_upstream_text_fetches_the_pinned_commit(monkeypatch):
    urls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"body"

    def urlopen(url, timeout):
        urls.append(url)
        return Response()

    monkeypatch.setattr(verbatim.urllib.request, "urlopen", urlopen)
    assert verbatim.upstream_text("pgledger.sql", None) == "body"
    assert urls == [
        f"https://raw.githubusercontent.com/pgr0ss/pgledger/{verbatim.PGLEDGER_COMMIT}/pgledger.sql"
    ]
