"""What a real server refuses: the test partners, and an address nobody opens it at.

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

A live server (https at a host other than this machine's, PMS_FAKE_PARTNERS not "allow") also
refuses Stripe's test keys, from the environment (PMS_STRIPE_SECRET_KEY) or entered by a company
(src/pms/api/payment_keys.py): a test payment would go into the trust books as real money
(docs/edge-cases.md P-048). https://localhost and 127.0.0.1 are the machine itself, nobody's
real money.

PMS_PUBLIC_URL is the address people open the PMS at (docs/install.md, step 2). Every change a
browser sends is refused unless it comes from that origin, and every emailed link starts with it,
so a server at the wrong one lets nobody sign in and emails links that go nowhere
(docs/edge-cases.md H-059, H-060). A server refuses to start at .env.example's placeholder, or at
anything but an http or https address. It may carry a path, where an app mounts the PMS
(Occupella's hosted app serves it at https://app.occupella.com/books, and the portal is built for
that path, frontend/src/base.ts), but never a query or a fragment: every link is the address and
then a path of its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

FAKE = "fake"
# A path an app mounts the PMS at, like /books or /pms/portal: words between single slashes.
PLAIN_PATH = re.compile(r"(/[A-Za-z0-9_~-]+(\.[A-Za-z0-9_~-]+)*)*/?")
ALLOW = "allow"

# Each setting that may name a fake partner.
PARTNERS = ("PMS_PAYMENTS", "PMS_SCREENING_PARTNER", "PMS_GEOCODER", "PMS_EMAIL_SENDER")


# .env.example's PMS_PUBLIC_URL, which an install must replace (tests/test_deployment.py reads
# both).
PLACEHOLDER = "https://pms.example.com"


class PublicUrlRefused(ValueError):
    """PMS_PUBLIC_URL is no address a browser opens the PMS at."""


def refuse_public_url(public_url: str) -> None:
    """Raise unless `public_url` is an http or https address other than the example's: an
    origin, maybe with a plain path (/books), never a query or a fragment."""
    url = public_url.strip()
    if url.rstrip("/").lower() == PLACEHOLDER:
        raise PublicUrlRefused(
            f"PMS_PUBLIC_URL is still .env.example's {PLACEHOLDER}: set it to the address people"
            " open the PMS at (docs/install.md, step 2). Until then every sign-in is refused as"
            " coming from another site, and emailed links go to example.com."
        )
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        # Every emailed link would carry them (docs/edge-cases.md H-167); never repeated here.
        raise PublicUrlRefused(
            "PMS_PUBLIC_URL names a user or password: every emailed link would carry them. Set"
            " it to the origin people open the PMS at, like https://rentals.example.com"
            " (docs/install.md, step 2)."
        )
    if parts.scheme.lower() not in ("https", "http"):
        why = "starts with https:// or http://"
    elif not parts.hostname:
        why = "names no host"
    elif parts.query or parts.fragment or "#" in url or url.rstrip().endswith("?"):
        why = "carries a query or a fragment, which every link would be cut by"
    elif not PLAIN_PATH.fullmatch(parts.path):
        why = "has a path that isn't plain words between slashes"
    else:
        return
    raise PublicUrlRefused(
        f"PMS_PUBLIC_URL {public_url!r} is not an address a browser opens the PMS at: it"
        f" {why}. Set it to the address people open it at, like https://rentals.example.com"
        " (docs/install.md, step 2)."
    )


def refuse_invite_url(invite_url: str) -> None:
    """Raise unless `invite_url` (PMS_INVITE_URL, docs/adr/0013-sign-in-through-a-host.md) is an
    http or https address a browser opens: an invite's token is added to it as its fragment, so
    it carries none of its own, nor a query, a user or a password, which every invite would
    carry."""
    parts = urlsplit(invite_url.strip())
    if parts.username is not None or parts.password is not None:
        why = "names a user or password, which every invite link would carry"
    elif parts.scheme.lower() not in ("https", "http"):
        why = "doesn't start with https:// or http://"
    elif not parts.hostname:
        why = "names no host"
    elif parts.query or parts.fragment or invite_url.strip().endswith(("?", "#")):
        why = "has a query or a fragment: an invite's token is added as the fragment"
    else:
        return
    # The address itself isn't repeated: it may name a password.
    raise PublicUrlRefused(
        f"PMS_INVITE_URL {why}. Set it to the page people accept an invite at, like"
        " https://app.example.com/invited, or leave it unset for the PMS's own /invite."
    )


# Hosts that are the machine itself: a server there takes nobody's real money.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

# A Stripe secret key, or a restricted one, for real money. Anything else is a test key.
LIVE_KEYS = ("sk_live_", "rk_live_")


def is_live(public_url: str, allow: str = "") -> bool:
    """Whether a server at `public_url` takes real people's money: one at an https address of a
    host other than this machine, unless allowed as a demo of a synthetic company
    (PMS_FAKE_PARTNERS=allow)."""
    parts = urlsplit(public_url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or host in LOCAL_HOSTS or host.endswith(".localhost"):
        return False
    return allow.strip().lower() != ALLOW


def live_key(secret_key: str) -> bool:
    """Whether a Stripe secret key moves real money (sk_live_, rk_live_)."""
    return secret_key.strip().startswith(LIVE_KEYS)


class KeysNotLive(ValueError):
    """Stripe's test keys on a live server: their payments would be booked as real money."""


def refuse_test_keys(public_url: str, payments: str, secret_key: str, allow: str = "") -> None:
    """Raise if a live server's own payments partner is Stripe under a test key. The key is
    never repeated."""
    if payments.strip() == "stripe" and is_live(public_url, allow) and not live_key(secret_key):
        raise KeysNotLive(
            "PMS_STRIPE_SECRET_KEY is not a live key (sk_live_ or rk_live_) on a server at an https"
            " address real people use: a test payment would go into the trust books as real"
            " money. Set the live key, or leave PMS_PAYMENTS unset (docs/edge-cases.md P-048)."
        )


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
    """refuse_public_url, refuse_fakes and refuse_test_keys, read from a server's environment (the
    command-line tools). Unset, PMS_PUBLIC_URL is each command's own default."""
    if environ.get("PMS_PUBLIC_URL", "").strip():
        refuse_public_url(environ["PMS_PUBLIC_URL"])
    refuse_fakes(
        environ.get("PMS_PUBLIC_URL", ""),
        {name: environ.get(name, "") for name in PARTNERS},
        environ.get("PMS_FAKE_PARTNERS", ""),
    )
    refuse_test_keys(
        environ.get("PMS_PUBLIC_URL", ""),
        environ.get("PMS_PAYMENTS", ""),
        environ.get("PMS_STRIPE_SECRET_KEY", ""),
        environ.get("PMS_FAKE_PARTNERS", ""),
    )
