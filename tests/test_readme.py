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
