#!/usr/bin/env bash
# A backup of the PMS's database every day at 07:00 UTC, after the nightly run at 06:00
# (compose.selfhost.yaml's `backup` service; docs/install.md, "Every day: backups").
#
#   scripts/backup_daily.sh DIR          runs until stopped: each day's backup into DIR
#   scripts/backup_daily.sh DIR --now    one backup now, then exits (before an upgrade; the tests)
#
# Each is scripts/backup.sh dump, so it is kept only once pg_restore can read it back, under its
# day's name (pms-YYYY-MM-DD.dump; a second the same day replaces the first). The newest
# PMS_BACKUP_KEEP (14 unless set) are kept and older ones deleted. A backup that fails says so on
# standard error, where `docker compose logs backup` shows it, and the next day's still runs.
# The database comes from libpq's own variables (PGHOST, PGUSER, PGDATABASE, the password from
# PGPASSWORD or a pgpass file), never the command line. The backups hold everything the PMS
# keeps: copy them off the server and keep them encrypted (docs/backup.md).
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ] || { [ $# -eq 2 ] && [ "$2" != --now ]; }; then
  echo "usage: $0 DIR [--now]" >&2
  exit 2
fi
dir=$1
keep=${PMS_BACKUP_KEEP:-14}
case "$keep" in
  '' | *[!0-9]* | 0)
    echo "FAIL: PMS_BACKUP_KEEP is \"$keep\"; it is how many daily backups to keep, 1 or more." >&2
    exit 2
    ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$dir"

backup() {
  local name
  name="$dir/pms-$(date -u +%Y-%m-%d).dump"
  "$here/backup.sh" dump "$name"
  # Names sort by their day: keep the newest $keep.
  find "$dir" -maxdepth 1 -name 'pms-????-??-??.dump' -print | sort -r | tail -n "+$((keep + 1))" |
    while read -r old; do
      rm -f -- "$old"
      echo "deleted: $old, past the newest $keep" >&2
    done
  echo "backed up: $name" >&2
}

if [ "${2:-}" = --now ]; then
  backup
  exit 0
fi

while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today 07:00" +%s)
  if [ "$now" -ge "$next" ]; then
    next=$(date -u -d "tomorrow 07:00" +%s)
  fi
  sleep $((next - now))
  backup || echo "FAIL: the backup of $(date -u +%F) failed (above); the next runs tomorrow at 07:00 UTC." >&2
done
