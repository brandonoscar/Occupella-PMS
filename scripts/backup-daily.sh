#!/usr/bin/env bash
# The self-host install's daily backup (compose.selfhost.yaml's backup service, docs/install.md):
# the database dumped each day by scripts/backup.sh into PMS_BACKUP_DIR as pms-YYYY-MM-DD.dump
# (UTC), kept as docs/backup.md asks: every daily dump for 35 days, each month's first for good.
#
#   scripts/backup-daily.sh          today's dump if there is none yet, then one each day at
#                                    PMS_BACKUP_AT, for as long as it runs; a dump that fails is
#                                    tried again an hour later
#   scripts/backup-daily.sh once     one dump now (today's, replacing one made earlier today),
#                                    then the dumps past keeping removed
#   scripts/backup-daily.sh next     the seconds until the next daily dump
#   scripts/backup-daily.sh healthy  fails unless a good dump is less than 26 hours old: the
#                                    backup service's health check, so a backup that keeps
#                                    failing shows as unhealthy in `docker compose ps`
#
# PMS_BACKUP_DIR   where the dumps go (default /backups)
# PMS_BACKUP_AT    the time of day, HH:MM in UTC (default 07:00: after the nightly run has posted
#                  the day's rent and fees for a company in Eastern, Central or Mountain time; each
#                  company's run comes just after its own midnight, src/pms/nightly.py, so 11:00
#                  for Pacific to Hawaii)
#
# The database comes from libpq's own variables (PGHOST, PGUSER, PGDATABASE and the password from
# PGPASSWORD or a password file), as scripts/backup.sh takes it. A dump is readable by its owner
# only and holds everything the PMS keeps: copy it off the server, encrypted (docs/backup.md).
set -euo pipefail

dir=${PMS_BACKUP_DIR:-/backups}
at=${PMS_BACKUP_AT:-07:00}
keep_days=35
here=$(cd "$(dirname "$0")" && pwd)

if ! [[ "$at" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "refused: PMS_BACKUP_AT is a time of day in UTC, HH:MM (like 07:00), not \"$at\"" >&2
  exit 2
fi

# Every daily dump from before the 35 days up to $1 goes, except a month's first; and the
# .partial file of a dump that failed, which is never one to restore.
prune() {
  local cutoff file day
  cutoff=$(date -u -d "$1 - $keep_days days" +%F)
  for file in "$dir"/pms-????-??-??.dump; do
    [ -e "$file" ] || continue
    day=${file##*/pms-}
    day=${day%.dump}
    if [[ "$day" < "$cutoff" && "${day:8:2}" != 01 ]]; then
      rm -f -- "$file"
      echo "removed: $file (a daily dump past $keep_days days)" >&2
    fi
  done
  rm -f -- "$dir"/pms-*.dump.partial
}

# Bash ignores `set -e` in a function called as a condition (until, ||), so each step says so.
# A dump that failed still frees what it can (docs/edge-cases.md H-028): its .partial, and the
# dumps past keeping, so a disk the backups filled frees itself for the next try.
once() {
  local today
  today=$(date -u +%F)
  if ! "$here/backup.sh" dump "$dir/pms-$today.dump"; then
    prune "$today" || true
    return 1
  fi
  prune "$today" || return 1
}

# A good dump in the last 26 hours: a day between dumps, and two of the hourly tries after one
# that failed (docs/edge-cases.md H-031).
healthy() {
  if find "$dir" -maxdepth 1 -name 'pms-????-??-??.dump' -mmin -1560 | grep -q .; then
    return 0
  fi
  echo "unhealthy: no good dump in the last 26 hours" >&2
  return 1
}

seconds_until_next() {
  local now next
  now=$(date -u +%s)
  next=$(date -u -d "today $at" +%s)
  if [ "$next" -le "$now" ]; then
    next=$(date -u -d "tomorrow $at" +%s)
  fi
  echo $((next - now))
}

umask 077
mkdir -p "$dir"
case "${1:-daily}" in
  once) once ;;
  next) seconds_until_next ;;
  healthy) healthy ;;
  daily)
    echo "backing up to $dir each day at $at UTC" >&2
    if [ ! -e "$dir/pms-$(date -u +%F).dump" ]; then
      once || echo "FAIL: today's first dump; the next is at $at UTC" >&2
    fi
    while true; do
      sleep "$(seconds_until_next)"
      until once; do
        echo "FAIL: the dump of $(date -u +%F); trying again in an hour" >&2
        sleep 3600
      done
    done
    ;;
  *)
    echo "usage: $0 [once|next|healthy]" >&2
    exit 2
    ;;
esac
