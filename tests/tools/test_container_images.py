"""Every container image CI pulls comes from mirror.gcr.io, Google's mirror of Docker Hub.

GitHub's runners share IP addresses, and Docker Hub caps anonymous pulls per address: on
2026-10-09 every job that pulled postgres:16 from Docker Hub failed with 429 Too Many Requests
before a single test ran. The mirror serves the same images under the same digests (measured
for both pinned ones), without that cap, and needs no credential.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIRROR = "mirror.gcr.io/library/"
BUILT_HERE = {"occupella-pms:local"}  # compose.yaml's image of ./Dockerfile

IMAGE_KEY = re.compile(r"^\s*image:\s*(\S+)")
FROM_LINE = re.compile(r"^FROM\s+(\S+)", re.IGNORECASE)


def image_references() -> list[tuple[str, int, str]]:
    """(file, line, image) for each `image:` key in the workflows and compose.yaml, and each
    FROM line in our Dockerfiles."""
    found = []
    yaml_files = [*sorted((ROOT / ".github/workflows").glob("*.yml")), ROOT / "compose.yaml"]
    dockerfiles = [ROOT / "Dockerfile", *sorted((ROOT / "ci").glob("*.Dockerfile"))]
    for files, pattern in ((yaml_files, IMAGE_KEY), (dockerfiles, FROM_LINE)):
        for path in files:
            for number, line in enumerate(path.read_text().splitlines(), 1):
                match = pattern.match(line)
                if match:
                    found.append((str(path.relative_to(ROOT)), number, match.group(1)))
    return found


def test_every_image_comes_from_the_mirror():
    off_mirror = [
        reference
        for reference in image_references()
        if not reference[2].startswith(MIRROR) and reference[2] not in BUILT_HERE
    ]

    assert off_mirror == [], "pull these through mirror.gcr.io/library/ (see this file's docstring)"


def test_the_scan_finds_the_images_ci_pulls():
    # A scan that finds nothing would pass the test above without checking anything.
    files = {file for file, _, image in image_references() if image.startswith(MIRROR)}

    assert {
        ".github/workflows/ci.yml",
        ".github/workflows/db.yml",
        ".github/workflows/golden.yml",
        ".github/workflows/ledger-invariants.yml",
        ".github/workflows/mutation.yml",
        "compose.yaml",
        "Dockerfile",
        "ci/postgres-coverage.Dockerfile",
    } <= files


def test_pinned_images_keep_their_digest():
    # The mirror is only trusted because it serves the digest we pinned on Docker Hub.
    pinned = [image for _, _, image in image_references() if "@" in image]

    assert pinned
    for image in pinned:
        assert re.fullmatch(MIRROR + r"[\w.-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_the_email_job_builds_mailpit_with_the_go_image_the_dockerfile_pins():
    # Dependabot moves the Dockerfile's FROM lines; ci.yml's GO_IMAGE has to follow them.
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    (go_image,) = re.findall(r"^\s*GO_IMAGE:\s*(\S+)", workflow, re.MULTILINE)

    assert go_image.startswith(MIRROR + "golang:")
    assert go_image in [image for file, _, image in image_references() if file == "Dockerfile"]


def test_the_app_image_runs_the_python_every_job_tests_on():
    # The image the self-host guide runs takes its Python from its last FROM line. It must be
    # the version CI's jobs test on (setup-python) and mypy checks for (pyproject.toml), so a
    # scan's finding is fixed by a newer release of that version (a new digest), never by a
    # Python the tests never ran on (10/31: 3.11.17's CVE-2026-115557, fixed in 3.11.18).
    (app,) = [
        image
        for file, _, image in image_references()
        if file == "Dockerfile" and image.startswith(MIRROR + "python:")
    ]
    tested = set(
        re.findall(
            r'python-version:\s*"([\d.]+)"',
            "".join(p.read_text() for p in (ROOT / ".github/workflows").glob("*.yml")),
        )
    )
    (checked,) = re.findall(
        r'^python_version = "([\d.]+)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE
    )

    assert tested == {checked}
    assert re.fullmatch(MIRROR + rf"python:{re.escape(checked)}-slim@sha256:[0-9a-f]{{64}}", app)
