"""A throwaway database on the Postgres server in $DATABASE_URL, for checks that need their own.

The database gets a random name, so checks can run side by side on one server, and is dropped
when the block ends, even after a failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql


def url_for(server_url: str, database: str) -> str:
    return urlunsplit(urlsplit(server_url)._replace(path="/" + database))


@contextmanager
def scratch_database(server_url: str, prefix: str) -> Iterator[str]:
    """Create an empty database, yield its URL, then drop it."""
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin_url = url_for(server_url, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield url_for(server_url, name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
