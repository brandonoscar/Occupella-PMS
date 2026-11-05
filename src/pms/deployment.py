"""What a real server refuses: the test partners.

The fake partners exist for development, CI, the browser tests and the demo, and each would do
harm on a server real people use:

- the fake payments partner (PMS_PAYMENTS=fake) records a payment as paid when its page's button
  is pressed, and its queue (`GET /api/payments/fake`) lists every company's payments started
  through it, with no sign-in;
- the fake screening partner (PMS_SCREENING_PARTNER=fake) makes up an applicant's
  recommendation;
- the fake geocoder (PMS_GEOCODER=fake) makes up where a property is;
- the fake email sender (PMS_EMAIL_SENDER=fake) sends nothing, so invites and notices never
  arrive.

Every place that runs them does so over http (the browser tests at http://127.0.0.1:8123, the
demo at http://localhost:8100, the tests at http://testserver), while a server real people use is
reached over https (docs/install.md, step 4: the session cookie is Secure). So a server whose
PMS_PUBLIC_URL is https refuses to start with any of them, unless PMS_FAKE_PARTNERS is "allow":
for a public demo of a synthetic company, and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

FAKE = "fake"
ALLOW = "allow"

# Each setting that may name a fake partner.
PARTNERS = ("PMS_PAYMENTS", "PMS_SCREENING_PARTNER", "PMS_GEOCODER", "PMS_EMAIL_SENDER")


class FakePartnerRefused(ValueError):
    """A fake partner named on a server reached over https, which real people use."""


def fakes_named(partners: Mapping[str, str]) -> list[str]:
    """The settings among `partners` that name a fake partner, in a fixed order."""
    return sorted(name for name, value in partners.items() if value.strip() == FAKE)


def refuse_fakes(public_url: str, partners: Mapping[str, str], allow: str = "") -> None:
    """Raise if a server at an https address names a fake partner, unless allowed for a demo.

    `partners` maps each setting's name (PARTNERS) to its value."""
    if urlsplit(public_url.strip()).scheme.lower() != "https":
        return
    if allow.strip().lower() == ALLOW:
        return
    fakes = fakes_named(partners)
    if fakes:
        raise FakePartnerRefused(
            f"{', '.join(fakes)} {'names' if len(fakes) == 1 else 'name'} the fake partner on a"
            " server at an https address, which real people use. The fake partners are for"
            " development, CI and the demo: unset them, or set PMS_FAKE_PARTNERS=allow only for"
            " a public demo of a synthetic company."
        )


def refuse_fakes_in(environ: Mapping[str, str]) -> None:
    """refuse_fakes, read from a server's environment (the command-line tools)."""
    refuse_fakes(
        environ.get("PMS_PUBLIC_URL", ""),
        {name: environ.get(name, "") for name in PARTNERS},
        environ.get("PMS_FAKE_PARTNERS", ""),
    )
