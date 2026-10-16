"""Refused input, on any endpoint (src/pms/api/app.py): a 422 with FastAPI's own list of what
was wrong, written as ASCII JSON, so even input that can't be encoded as UTF-8 (a lone
surrogate, echoed back as the input refused) is answered 422, never a server error."""

import json

import pytest
from people import join_as_staff


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def test_refused_input_says_where_and_why(client, staff):
    answer = client.post("/api/property-groups", json={"name": " "})
    assert answer.status_code == 422
    [error] = answer.json()["detail"]
    assert (error["loc"], error["type"]) == (["body", "name"], "string_too_short")


def test_a_lone_surrogate_is_refused_not_a_server_error(client, staff):
    raw = '{"name": "North \\ud800"}'
    answer = client.post(
        "/api/property-groups", content=raw, headers={"Content-Type": "application/json"}
    )
    assert answer.status_code == 422
    assert "\\ud800" in answer.text  # escaped, as ASCII JSON writes it
    assert json.loads(answer.text)["detail"][0]["loc"] == ["body", "name"]
