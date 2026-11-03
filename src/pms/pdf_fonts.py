"""The font every PDF of the PMS prints in: IBM Plex Sans, the portal's own, embedded.

A tenant's, owner's or vendor's name prints as they wrote it: accented and other Latin letters
(Nguyễn, Łukasz, Dvořák), Greek and Cyrillic, and the punctuation phones and word processors
type (O’Brien, “quoted”, – and —, €). The PDF format's built-in Helvetica held Latin-1 only, so
each of those printed "?" (11/03). What Plex has no letter for (Arabic, Chinese, Japanese,
Korean...) still prints "?", never an empty box, and never silently disappears.

The fonts are Plex Sans 3.201, Regular and Bold, from the Google Fonts build that
@expo-google-fonts/ibm-plex-sans 0.4.1 carries; they and their license (SIL Open Font License
1.1, which lets software bundle them) are in pms/fonts/, recorded in THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.fonts import TTFFont

FONTS = Path(__file__).parent / "fonts"
FAMILY = "Plex"
REGULAR = FONTS / "IBMPlexSans-Regular.ttf"
BOLD = FONTS / "IBMPlexSans-Bold.ttf"


def use(pdf: FPDF) -> None:
    """Makes the PMS's font, regular and bold, available to the PDF."""
    pdf.add_font(FAMILY, "", str(REGULAR))
    pdf.add_font(FAMILY, "B", str(BOLD))


class Pdf(FPDF):
    """A PDF with the PMS's font already added, its wrapped text set left (ragged right).

    fpdf2 justifies wrapped text unless told otherwise. With the built-in font a justified line
    stretched its spaces; with an embedded font it places each word apart, and the line copies,
    searches and reads aloud with doubled spaces ("Questions  about  this"). Letters are set
    left anyway."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        use(self)

    def multi_cell(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("align", "L")
        return super().multi_cell(*args, **kwargs)


@cache
def covered() -> frozenset[int]:
    """The characters both the regular and the bold font have a letter for."""
    pdf = FPDF()
    use(pdf)
    maps = []
    for key in (FAMILY.lower(), FAMILY.lower() + "B"):
        font = pdf.fonts[key]
        assert isinstance(font, TTFFont)  # added from a file, never a built-in font
        maps.append(set(font.cmap))
    return frozenset(maps[0] & maps[1])


def printable(value: str) -> str:
    """The text as the PDF prints it: a character the font has no letter for reads "?". Line
    breaks and other control characters pass through, as the layout reads them."""
    letters = covered()
    return "".join(c if ord(c) < 32 or ord(c) in letters else "?" for c in value)
