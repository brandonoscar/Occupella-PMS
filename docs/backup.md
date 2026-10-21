# Backups

How a company that self-hosts the PMS backs up its books and gets them back. The PMS has no
hosted database (CLAUDE.md, rule 4): each company runs its own Postgres 16, so each company keeps
its own backups. Nothing here runs by itself; the company sets it up on its own server.

The database keeps every record for good: trust records are append-only, and nothing in a PMC is
deleted (archived instead). A backup isn't how records are kept; it is how they come back when
the server, its disk or the database is lost, or when a release goes wrong (docs/release.md).

## What a backup is

`scripts/backup.sh dump` writes the whole database, in pg_dump's custom format, from one
consistent snapshot taken while the PMS keeps running. `scripts/backup.sh restore` puts one back
into a new, empty database, all or nothing, and then checks that its ledger ties out.

```sh
export PGHOST=db.example.internal PGUSER=postgres PGDATABASE=pms   # password in ~/.pgpass
scripts/backup.sh dump pms.dump        # or "dump -" to write to standard output
createdb pms_restored
PGDATABASE=pms_restored scripts/backup.sh restore pms.dump   # or "restore -" to read standard input
```

- **Which database:** libpq's own variables (`PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE`) and the
  password from `~/.pgpass` (or `PGPASSFILE`). The script never takes a URL or a password on its
  command line, where any user of the machine can read it.
- **As whom:** the role that owns the database and ran its migrations (`postgres` in
  `compose.yaml`). It reads every table, including the ones `trust_app` can't.
- **Tools:** `pg_dump`, `pg_restore` and `psql` 16 (`postgresql-client-16`). The script refuses
  any other version: pg_restore 16 can't read what a later pg_dump writes.
- **A dump** is written to `FILE.partial` and renamed to `FILE` only once `pg_restore` can read it
  back, so a dump that failed halfway never sits where a good one would. The file is readable by
  its owner only.
- **A restore** refuses a database that holds anything ("never over books that are running").
  On a new server it makes the roles the books grant to (`trust_app`, `trust_ai_agent`) without a
  login, as the migrations do; the grants come from the backup. It refuses a backup whose objects
  belong to a role the server lacks: restore as that role. It runs in one transaction, so a
  failure leaves the new database empty. Then it runs `ANALYZE` and the self-host smoke's ledger
  checks (every transfer balances, every balance is its entries' sum, nothing held is below zero,
  every trust bank account's book cash is what it holds for others), and says how many transfers
  came back and which migration the books are at.

A backup holds everything the PMS keeps: tenants', owners' and vendors' names and contact
details, applicants, documents, the audit log, and logins' password hashes and authenticator
secrets. Treat every copy as you would the database itself.

## How often

- **Every day**, after the nightly run (06:00 UTC, `src/pms/nightly.py`) has posted the day's
  rent and fees: say 07:00 UTC. A daily backup loses at most a day: whatever staff recorded after
  it was taken has to be entered again from the bank's records and the day's paperwork.
- **Before every upgrade**, whatever the time (docs/release.md, step 3).
- **More often** if a day's work is too much to enter again: the dump runs while the PMS keeps
  serving. Measured on a laptop for the demo company plus 50 doors (1.6 MB): 0.3 s to dump,
  2 s to restore. NOT VERIFIED for a 5,000-door company.

Losing less than the time since the last dump needs Postgres's continuous archiving (WAL
archiving and point-in-time recovery, for example with pgBackRest). It is not set up or tested
here: NOT VERIFIED.

## How long to keep them

- every daily backup for 35 days, so a mistake found at the next month-end can still be traced;
- the backup of the first of each month for at least five years, the longest record-keeping
  rule `docs/law.md` found (West Virginia; California asks three);
- the backup taken before each upgrade until the release after it has run a whole month.

## Where

- **Off the database's server**, so losing the server doesn't lose its backups, and at least one
  copy with a different provider or in a different place from the server.
- In storage that keeps old versions and can't be overwritten or deleted early (an S3-compatible
  bucket with versioning and object lock, for example), so whoever breaks into the server can't
  take the backups with it.

## Encrypted at rest

Encrypt each backup before it leaves the database's server, to a key that isn't on the server.
Encryption by the storage provider alone isn't enough: the provider holds that key. With
[age](https://age-encryption.org) (BSD-3-Clause), for example:

```sh
# once, on a computer of your own (not the server): a key pair
age-keygen -o pms-backup-key.txt     # prints the public key, age1...
```

Keep `pms-backup-key.txt` off the server, in two places (a password manager, and a copy in a
safe). Lose it and every backup is lost with it. Only the public key goes on the server.

A nightly job on the server (cron, as the user with `~/.pgpass`), which writes no unencrypted
file:

```sh
#!/usr/bin/env bash
set -euo pipefail
export PGHOST=localhost PGUSER=postgres PGDATABASE=pms
out=/var/backups/pms/pms-$(date -u +%Y-%m-%d).dump.age
/opt/occupella-pms/scripts/backup.sh dump - | age -r age1yourpublickey >"$out.partial"
mv "$out.partial" "$out"
# then copy "$out" to the off-site storage above, and delete local copies older than a week
```

With `pipefail`, a dump that fails fails the job and its `.partial` file is never renamed. Delete
leftover `.partial` files; never restore one.

With `compose.yaml`, whose database publishes no port, run the script inside the Postgres image,
which carries pg_dump 16:

```sh
docker compose run --rm --no-deps -T -v "$PWD/scripts:/scripts:ro" \
  -e PGHOST=db -e PGUSER=postgres -e PGDATABASE=pms --entrypoint /scripts/backup.sh \
  db dump - | age -r age1yourpublickey >pms.dump.age
```

## Getting the books back

1. Stop the app (`docker compose stop app`), so nothing is posted while you restore.
2. Make a new, empty database on Postgres 16: `createdb pms_restored`. The script never restores
   over books that are running.
3. Restore the latest good backup into it:
   `age -d -i pms-backup-key.txt pms-2026-10-21.dump.age | PGDATABASE=pms_restored scripts/backup.sh restore -`.
   It ends with `restored: ... the ledger ties out.` or says what doesn't. With `compose.yaml`,
   make the database with `docker compose exec db createdb -U postgres pms_restored` and pipe the
   backup into the same `docker compose run ... db restore -` as above, with
   `PGDATABASE=pms_restored`.
4. Point the app at it (`DATABASE_URL`), or swap the names
   (`ALTER DATABASE pms RENAME TO pms_lost; ALTER DATABASE pms_restored RENAME TO pms;`).
5. Run the migrations of the release you run (`docker compose run --rm migrate`). If the backup
   was taken before an upgrade, this applies that release's migrations to it, as the upgrade did.
6. Start the app, then enter again what happened after the backup was taken, from the bank's
   records, and reconcile.

## Test a restore

A backup that was never restored is a hope. Every month, and after every upgrade, restore the
latest backup into a scratch database on another machine (steps 2 and 3) and keep the line it
ends with. Then drop the scratch database: it holds everything the original does.

CI proves the script itself on every PR and push to main (`tools/check_backup.py`, db.yml's
`migrations` job, and `tests/tools/test_check_backup.py`): it builds a database holding money
(the self-host seed and the demo company: leases, charges, bills, reconciliations, logins,
documents and the audit log), dumps it, restores it into an empty database, and checks the copy
has the same schema (tables, functions, triggers, grants and policies), every table and sequence
row for row, and a ledger that ties out. The tests also restore through a pipe, onto a server
without the PMS's roles, and refuse a database that isn't empty or a ledger that doesn't tie out.
