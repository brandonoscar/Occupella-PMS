"""The README is the project's front page on GitHub: every picture it shows is in the repository
and described in words, and every screen in it comes from the synthetic demo company."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
IMAGES = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", README)


def test_the_readme_shows_the_product():
    local = [src for _, src in IMAGES if not src.startswith("http")]
    assert "docs/screenshots/tour.gif" in local
    assert len(local) >= 5


def test_every_picture_is_in_the_repository_and_described():
    for alt, src in IMAGES:
        if src.startswith("http"):
            continue  # badges
        assert (ROOT / src).is_file(), f"{src} is not in the repository"
        assert len(alt) >= 30, f"{src} needs words a screen reader can read"


def test_it_says_the_screens_are_made_up():
    assert "Every name and figure below is made up" in README


DOCS = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "docs/proof.md", "docs/status.md")
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)\)")


def anchors(text: str) -> set[str]:
    # GitHub's: a heading's words, lower case, spaces to hyphens, punctuation dropped.
    return {
        re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
        for heading in re.findall(r"^#+ (.+)$", text, re.MULTILINE)
    }


def test_every_link_in_the_front_docs_leads_somewhere():
    for doc in DOCS:
        path = ROOT / doc
        text = path.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file, _, anchor = target.partition("#")
            page = (path.parent / file).resolve() if file else path
            assert page.exists(), f"{doc} links {target}, which isn't in the repository"
            if anchor:
                assert anchor in anchors(page.read_text(encoding="utf-8")), f"{doc}: no #{anchor}"


def test_the_readme_leads_to_its_proof_and_its_status_before_the_demo():
    demo = README.index("## Try the demo")
    assert -1 < README.index("docs/proof.md") < demo
    assert -1 < README.index("docs/status.md") < demo
    assert "issues/232" in (ROOT / "docs/status.md").read_text(encoding="utf-8")
    # Written for people outside: no private repository, no model as the example client.
    assert "AgenticHelixis" not in README
    assert "(Claude, say)" not in README


def test_proof_names_only_files_that_exist():
    proof = (ROOT / "docs/proof.md").read_text(encoding="utf-8")
    named = re.findall(r"`((?:tests/[\w/]+|\d{14}_\w+)\.(?:py|sql))`", proof)
    assert len(named) >= 10
    for name in named:
        path = ROOT / name if name.startswith("tests/") else ROOT / "db/migrations" / name
        assert path.is_file(), f"docs/proof.md names {name}, which doesn't exist"


def test_an_operators_command_names_the_stack_it_runs():
    # Bare `docker compose` reads compose.yaml, the development stack: an upgrade or a restore
    # run that way would touch the wrong database.
    operators = ("docs/install.md", "docs/release.md", "docs/backup.md", "docs/support.md")
    for doc in (*operators, "README.md"):
        text = " ".join((ROOT / doc).read_text(encoding="utf-8").split())
        assert not re.search(r"docker compose (?!-f )", text), f"{doc}: docker compose without -f"
