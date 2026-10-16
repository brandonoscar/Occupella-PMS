"""`python -m pms.api`: the server reads its settings from the environment."""

import runpy

from pms.api import Settings


def test_the_server_reads_its_settings_from_the_environment(monkeypatch, database_url):
    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **options: served.update(app=app, **options))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example.com")
    monkeypatch.setenv("PMS_COOKIE_SECURE", "False")
    monkeypatch.setenv("PMS_HOST", "0.0.0.0")
    monkeypatch.setenv("PMS_PORT", "9000")
    monkeypatch.delenv("PMS_NIGHTLY", raising=False)

    runpy.run_module("pms.api", run_name="__main__")

    assert (served["host"], served["port"], served["proxy_headers"]) == ("0.0.0.0", 9000, False)
    # The nightly run is on unless PMS_NIGHTLY=off (src/pms/nightly.py).
    assert served["app"].state.settings == Settings(
        database_url=database_url,
        public_url="https://pms.example.com",
        cookie_secure=False,
        db_role="trust_app",
        nightly=True,
    )
    assert served["app"].state.settings.origin == "https://pms.example.com"


def test_by_default_cookies_are_secure_and_the_server_listens_locally(monkeypatch, database_url):
    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **options: served.update(app=app, **options))
    monkeypatch.setenv("DATABASE_URL", database_url)
    for name in ("PMS_PUBLIC_URL", "PMS_COOKIE_SECURE", "PMS_HOST", "PMS_PORT", "PMS_DB_ROLE"):
        monkeypatch.delenv(name, raising=False)

    runpy.run_module("pms.api", run_name="__main__")

    settings = served["app"].state.settings
    assert (served["host"], served["port"]) == ("127.0.0.1", 8000)
    assert settings.cookie_secure and settings.public_url == "http://localhost:8000"
