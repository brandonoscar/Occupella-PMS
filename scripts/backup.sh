#!/usr/bin/env bash
# Back up the PMS's database, or restore a backup into a new, empty one (docs/backup.md).
#
#   scripts/backup.sh dump FILE      the database, in pg_dump's custom format, to FILE
#   scripts/backup.sh dump -         the same to standard output, to pipe into encryption
#   scripts/backup.sh restore FILE   FILE (- for standard input) into an empty database, all or
#                                    nothing, then checks that its ledger ties out
#
# Which database comes from libpq's own variables: PGHOST, PGPORT, PGUSER and PGDATABASE (the
# database's name), and the password from ~/.pgpass or PGPASSFILE. Never a URL or a password on
# the command line, where anyone on the machine can read it. Dump and restore as the role that
# owns the database and ran its migrations. Needs pg_dump, pg_restore and psql 16
# (postgresql-client-16): a dump from a later pg_dump doesn't restore with pg_restore 16.
#
# A dump holds everything the PMS keeps: tenants' and owners' details, documents, and logins'
# password hashes and authenticator secrets. Keep it encrypted (docs/backup.md).
set -euo pipefail

usage() {
  echo "usage: $0 dump FILE|-    or    $0 restore FILE|-" >&2
  exit 2
}

need_16() {
  local version
  version=$("$1" --version)
  case "$version" in
    *" 16."*) ;;
    *)
      echo "FAIL: $version; install postgresql-client-16 and put its bin directory first on PATH." >&2
      exit 1
      ;;
  esac
}

# One value from the database, from SQL on standard input (psql variables allowed).
value() {
  psql -X -A -t -q --no-password -v ON_ERROR_STOP=1 "$@"
}

dump() {
  local target=$1
  need_16 pg_dump
  need_16 pg_restore
  if [ "$target" = - ]; then
    exec pg_dump --format=custom --no-password
  fi
  umask 077
  # A dump goes to its name only once pg_restore can read it back, so a dump that failed halfway
  # never sits where a good one would.
  pg_dump --format=custom --no-password --file="$target.partial"
  pg_restore --list "$target.partial" >/dev/null
  mv "$target.partial" "$target"
  echo "dumped: $(wc -c <"$target") bytes to $target" >&2
}

# The roles a backup's objects belong to, and the roles it grants to: these live in the server,
# not the database, so a new server has none of them.
roles_named() {
  local which=$1 archive=$2
  if [ "$which" = owners ]; then
    pg_restore --schema-only --file=- "$archive" | sed -nE 's/.* OWNER TO ([a-z_][a-z0-9_]*);$/\1/p'
  else
    pg_restore --schema-only --file=- "$archive" |
      sed -nE 's/^(GRANT .* TO|CREATE POLICY .* TO) ([a-z_][a-z0-9_]*)( .*|;)$/\2/p'
  fi | sort -u
}

restore() {
  local archive=$1 scratch database role
  need_16 pg_restore
  need_16 psql
  umask 077
  scratch=$(mktemp -d)
  # shellcheck disable=SC2064  # $scratch is fixed now; expand it now
  trap "rm -rf '$scratch'" EXIT
  if [ "$archive" = - ]; then
    archive="$scratch/backup.dump"
    cat >"$archive"
  fi
  pg_restore --list "$archive" >/dev/null

  database=$(value <<<"SELECT current_database()")
  if [ "$(value <<'SQL'
SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%')
     + (SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql')
SQL
  )" != 0 ]; then
    echo "FAIL: database \"$database\" is not empty. Restore only into a new one (createdb)," \
      "never over books that are running." >&2
    exit 1
  fi

  while read -r role; do
    if [ "$(value -v role="$role" <<<"SELECT count(*) FROM pg_roles WHERE rolname = :'role'")" = 0 ]; then
      echo "FAIL: this backup's objects belong to role \"$role\", which this server doesn't have." \
        "Restore as that role (create it first, as the owner of \"$database\")." >&2
      exit 1
    fi
  done < <(roles_named owners "$archive")
  # The PMS's own roles (trust_app, trust_ai_agent) are made the way its migrations make them:
  # without a login. Their grants come from the backup.
  while read -r role; do
    value -v role="$role" <<'SQL'
SELECT format('CREATE ROLE %I NOLOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SQL
  done < <(roles_named grantees "$archive")
  # A key's request runs as trust_ai_agent (migration 0201, ADR 0011): trust_app may set that
  # role and never inherits it. Like the roles, that membership lives in the server, not the
  # backup, so a new server gets it here (one that has it already is left as it is).
  value <<'SQL' >/dev/null
SELECT 'GRANT trust_ai_agent TO trust_app WITH INHERIT FALSE, SET TRUE'
WHERE to_regrole('trust_app') IS NOT NULL AND to_regrole('trust_ai_agent') IS NOT NULL
  AND NOT EXISTS (SELECT FROM pg_auth_members
                  WHERE roleid = to_regrole('trust_ai_agent') AND member = to_regrole('trust_app')) \gexec
SQL

  pg_restore --single-transaction --exit-on-error --no-password --dbname="$database" "$archive"
  value <<<"ANALYZE" >/dev/null

  # The same checks as the self-host smoke's health (selfhost/smoke.py): every transfer
  # balances, every balance is its entries' sum, nothing held is below zero, and every trust bank
  # account's book cash is what it holds for others.
  local problems
  problems=$(value <<'SQL'
SELECT label FROM (
  SELECT 'transfers whose entries don''t balance' AS label, count(*) AS found FROM (
    SELECT t.id FROM pgledger_transfers t JOIN pgledger_entries e ON e.transfer_id = t.id
    GROUP BY t.id HAVING count(*) <> 2 OR sum(e.amount) <> 0) bad
  UNION ALL
  SELECT 'accounts whose balance isn''t the sum of their entries', count(*) FROM (
    SELECT a.id FROM pgledger_accounts a LEFT JOIN pgledger_entries e ON e.account_id = a.id
    GROUP BY a.id, a.balance HAVING a.balance <> coalesce(sum(e.amount), 0)) bad
  UNION ALL
  SELECT 'held accounts below zero', count(*) FROM trust_ledger_accounts t
    JOIN pgledger_accounts a ON a.id = t.ledger_account_id
    WHERE t.kind <> 'bank_cash' AND a.balance < 0
  UNION ALL
  SELECT 'trust bank accounts whose book cash doesn''t equal what they hold for others', count(*)
  FROM (SELECT t.bank_account_id FROM trust_ledger_accounts t
        JOIN pgledger_accounts a ON a.id = t.ledger_account_id
        GROUP BY t.bank_account_id HAVING sum(a.balance) <> 0) bad
) checks WHERE found > 0
SQL
  )
  if [ -n "$problems" ]; then
    echo "FAIL: restored into \"$database\", but its ledger doesn't tie out:" >&2
    echo "$problems" >&2
    exit 1
  fi
  echo "restored: \"$database\", $(value <<<"SELECT count(*) FROM pgledger_transfers") transfers," \
    "migrations through $(value <<<"SELECT max(version) FROM schema_migrations");" \
    "the ledger ties out." >&2
}

[ $# = 2 ] || usage
case "$1" in
  dump) dump "$2" ;;
  restore) restore "$2" ;;
  *) usage ;;
esac
