"""The API built from the settings it is handed (pms.api.Settings.from_env).

The hosted product mounts the PMS inside AgenticHelixis's app (AgenticHelixis
docs/decisions/ADR-one-product.md, phase 3), whose process may hold a DATABASE_URL and PMS_*
settings of its own. It hands the PMS the books' settings, and the app the PMS builds from them
uses those alone: its pool connects to the books, never to the other app's database."""

import pytest

from pms.api import Settings, create_app

BOOKS = {
    "DATABASE_URL": "postgresql://books@localhost/books",
    "PMS_PUBLIC_URL": "https://rentals.example.com",
    "PMS_NIGHTLY": "off",
}


def test_an_app_built_from_given_settings_uses_them_alone(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://another-app@localhost/not-the-books")
    monkeypatch.setenv("PMS_WEBHOOKS", "off")

    app = create_app(Settings.from_env(BOOKS))

    assert app.state.settings.database_url == "postgresql://books@localhost/books"
    assert app.state.pool.conninfo == "postgresql://books@localhost/books"
    assert app.state.settings.origin == "https://rentals.example.com"
    assert app.state.settings.nightly is False
    assert app.state.settings.webhooks is True  # its own default: the process's isn't read


def test_with_none_given_the_process_environment_is_read(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://self-hosted@localhost/pms")
    monkeypatch.setenv("PMS_WEBHOOKS", "off")

    settings = Settings.from_env()

    assert settings.database_url == "postgresql://self-hosted@localhost/pms"
    assert settings.webhooks is False


def test_given_settings_without_a_database_are_refused():
    with pytest.raises(KeyError, match="DATABASE_URL"):
        Settings.from_env({"PMS_PUBLIC_URL": "https://rentals.example.com"})
