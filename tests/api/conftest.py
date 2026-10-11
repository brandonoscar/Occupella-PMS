"""Fixtures for the API tests: the app on the test database, two browsers and a PMC. People
who sign in are made with the helpers in people.py.

Every endpoint is tested for success, for a caller who isn't allowed (no session, or a
session of the wrong kind or PMC), and for bad input (CLAUDE.md, standing rule 5).
"""

import pytest
from fastapi.testclient import TestClient
from helpers import make_pmc

from pms.api import Settings, create_app

PUBLIC_URL = "http://testserver"


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, public_url=PUBLIC_URL, cookie_secure=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def other_client(settings):
    """A second browser, with its own cookies."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)
