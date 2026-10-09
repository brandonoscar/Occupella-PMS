"""Check that the pgledger blocks in the ledger_core migration match upstream byte for byte.

Each block sits between a `-- BEGIN VERBATIM <path>` line and a `-- END VERBATIM <path>` line.
The block must equal the upstream file at the pinned commit. The one allowed difference: when
the upstream file has no final newline, the block adds one so the END line starts on its own
line.

Usage:
    python scripts/check_pgledger_verbatim.py                  # fetch upstream from GitHub
    python scripts/check_pgledger_verbatim.py --upstream DIR   # compare with a local checkout
"""

import argparse
import re
import sys
import urllib.request
from pathlib import Path

PGLEDGER_COMMIT = "d591b7693a89ddcdb527c2e59f4f702983235f69"
RAW_URL = "https://raw.githubusercontent.com/pgr0ss/pgledger/{commit}/{path}"
MIGRATION = Path(__file__).resolve().parent.parent / "db/migrations/20261009000001_ledger_core.sql"
EXPECTED_PATHS = [
    "vendor/scoville-pgsql-ulid/ulid-to-uuid.sql",
    "vendor/scoville-pgsql-ulid/uuid-to-ulid.sql",
    "pgledger.sql",
]
BLOCK = re.compile(
    r"^-- BEGIN VERBATIM (?P<path>\S+)\n(?P<body>.*?)^-- END VERBATIM (?P=path)\n",
    re.MULTILINE | re.DOTALL,
)


def upstream_text(path: str, upstream_dir: Path | None) -> str:
    if upstream_dir is not None:
        return (upstream_dir / path).read_bytes().decode("utf-8")
    url = RAW_URL.format(commit=PGLEDGER_COMMIT, path=path)
    with urllib.request.urlopen(url, timeout=30) as response:
        body: bytes = response.read()
    return body.decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream", type=Path, help="local pgledger checkout at the pinned SHA")
    args = parser.parse_args(argv)

    migration = MIGRATION.read_bytes().decode("utf-8")
    if PGLEDGER_COMMIT not in migration:
        print(f"FAIL: {MIGRATION.name} does not name commit {PGLEDGER_COMMIT}")
        return 1

    blocks = [(m["path"], m["body"]) for m in BLOCK.finditer(migration)]
    paths = [path for path, _ in blocks]
    if paths != EXPECTED_PATHS:
        print(f"FAIL: expected blocks {EXPECTED_PATHS} in that order, found {paths}")
        return 1

    failed = False
    for path, body in blocks:
        expected = upstream_text(path, args.upstream)
        if not expected.endswith("\n"):
            expected += "\n"
        if body == expected:
            print(f"ok    {path}")
        else:
            print(f"FAIL  {path} differs from upstream at {PGLEDGER_COMMIT}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
