"""How much a request may send (docs/edge-cases.md H-072): at most MAX_BODY bytes, whether it
says its length or streams it, so no one, signed in or not, makes the one process read hundreds
of megabytes into memory. The largest thing the PMS takes, a 5 MB file as base64 in JSON, fits."""

import json

from pms.api import app as app_module

TOO_BIG = "this request is larger than the PMS takes (10 MB)"


def test_a_body_larger_than_the_limit_is_refused_before_it_is_read(client):
    body = b'{"email": "' + b"x" * app_module.MAX_BODY + b'"}'
    refused = client.post(
        "/api/sign-in", content=body, headers={"Content-Type": "application/json"}
    )
    assert refused.status_code == 413
    assert refused.json() == {"detail": TOO_BIG}


def test_a_streamed_body_is_counted_as_it_comes(client):
    def chunks():
        yield b'{"email": "'
        for _ in range(11):
            yield b"x" * (1024 * 1024)
        yield b'"}'

    refused = client.post(
        "/api/sign-in", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert refused.status_code == 413
    assert refused.json() == {"detail": TOO_BIG}


def test_the_largest_file_the_pms_takes_still_fits(client):
    # A 5 MB file as base64 is about 7 million characters: the API reads it (and here refuses it
    # for want of a session, not for its size).
    body = json.dumps({"about": "vendor", "content_base64": "A" * 7_000_000}).encode()
    answer = client.post(
        "/api/documents", content=body, headers={"Content-Type": "application/json"}
    )
    assert answer.status_code == 401
