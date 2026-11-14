#!/bin/sh
# Rewrite db/schema.sql from the database in $DATABASE_URL.
#
# dbmate dumps with pg_dump, which writes what differs between servers when the schema doesn't:
# the server's and pg_dump's builds in two comment lines, and from PostgreSQL 16.16 each column's
# attver. tools/schema_dump.py drops just those; everything else in the dump is compared byte for
# byte by CI.
set -eu
cd "$(dirname "$0")/.."
dbmate dump
python -m tools.schema_dump db/schema.sql
