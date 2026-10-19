"""The whole API under generated requests: Schemathesis (MIT, tests only) reads the app's own
OpenAPI schema and, for every operation, sends requests Hypothesis makes up from it (valid ones
and ones that break the schema) as a signed-in staff member of a synthetic PMC. None may be
answered with a server error: a request the API can't take is refused in words (4xx), never a
500. ci/thresholds.toml [fuzz] sets how many requests each operation gets, more at night."""

import os
import tomllib
from pathlib import Path

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from people import join_as_staff
from schemathesis.checks import not_a_server_error

from pms.api import create_app

_fuzz = tomllib.loads((Path(__file__).parents[2] / "ci/thresholds.toml").read_text())["fuzz"]
_nightly = os.environ.get("HYPOTHESIS_PROFILE") == "nightly"
EXAMPLES = _fuzz["nightly_examples" if _nightly else "pr_examples"]


@pytest.fixture
def api_schema(settings):
    return schemathesis.openapi.from_asgi("/openapi.json", create_app(settings))


schema = schemathesis.pytest.from_fixture("api_schema")


@pytest.fixture
def signed_in(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    return dict(client.cookies)


@schema.parametrize()
@settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_no_generated_request_is_answered_with_a_server_error(case, signed_in):
    response = case.call(cookies=signed_in)

    case.validate_response(response, checks=[not_a_server_error])
