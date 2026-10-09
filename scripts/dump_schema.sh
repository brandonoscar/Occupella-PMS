#!/bin/sh
# Rewrite db/schema.sql from the database in $DATABASE_URL.
#
# dbmate dumps with pg_dump, which stamps the exact server and pg_dump builds into two comment
# lines ("-- Dumped from database version 16.x (Ubuntu ...)"). Those differ between a laptop and
# CI even when the schema is identical, so this drops just those two lines. Everything else in
# the dump is compared byte for byte by CI.
set -eu
cd "$(dirname "$0")/.."
dbmate dump
sed -i.bak -e '/^-- Dumped from database version /d' -e '/^-- Dumped by pg_dump version /d' db/schema.sql
rm db/schema.sql.bak
