# Support

What happens when something goes wrong with a company's PMS: who hears of it, where to look, and
how to get the books back. There is no hosted PMS, no help desk and no one on call for it
(CLAUDE.md, rule 4): each company runs its own server (docs/release.md), so each company supports
its own, with this page. There are no response times to promise.

## Who looks

1. **The company's admins** (staff who hold the admin role, Settings, Staff). They hear first:
   the PMS emails each of them, at their login's email, when something it can't fix by itself
   fails (below). They decide what to do, and they can run the night's work again by hand.
2. **Whoever runs the server** for the company (maybe an admin too). They read the server's log,
   fix its settings, and restore a backup.
3. **The PMS's maintainers**, for a bug in the PMS itself: a GitHub issue on this repository
   (below). Never put a real person's name, address, email, phone, bank number or tax ID in an
   issue, a log you paste or a screenshot (CLAUDE.md, rule 2): replace them with made-up ones.

## What reaches a person by itself

While the server sends email (`PMS_EMAIL_SENDER=smtp` with `PMS_SMTP_HOST`, `PMS_SMTP_PORT`,
`PMS_SMTP_USERNAME`, `PMS_SMTP_PASSWORD` and `PMS_EMAIL_FROM`; docs/adr/0007-email.md), each admin
with an active login is emailed:

| When | Subject | Once |
|---|---|---|
| The company's nightly run failed (rent, late fees, recurring bills, monthly fees, recurring work orders and their emails) | "<company>: the nightly run of MM/DD/YYYY failed" | per admin and day |
| An email was given up on after its fifth failed attempt (an alert's own email aside) | "<company>: emails are not going out" | per admin and day |

Each alert is kept in `trust_admin_alerts`: who was told, what failed and on which day.

What does **not** reach anyone by itself:
- **Anything, while the server sends no email.** `compose.yaml` sets no sender, so out of the box
  the log is the only place a failure shows.
- **A mail server that is down.** The alert about it is an email too. It is tried five times like
  any other, then given up on. The log says so each time.
- **A database the server can't reach.** Nothing can be written then, so nothing is emailed.
  `GET /health` answers 503 `database unavailable` (200 `ok` otherwise); a monitoring service of
  the company's own can watch it. Nothing in the PMS does.
- **An error answering a request** (a 500). It is in the log, with its traceback.
- **A backup that failed.** That shows in the backup job's own output (docs/backup.md).

## Where the logs are

The server writes its log to standard output and standard error. With `compose.yaml`:
`docker compose logs app` (and `docker compose logs db` for Postgres). In it:

- one line per request (uvicorn's access log; a calendar link's token is printed as "…");
- the traceback of every error answering a request;
- `the nightly run of PMC <id> failed; the next run tries again`, with the traceback: one
  company's run failed, the others ran;
- `the nightly run failed; it is tried again in 300 seconds`: the run couldn't start at all
  (usually the database), and it tries again by itself;
- `the admins of PMC <id> could not be told its nightly run failed` (or `… an email was given up
  on`): the alert itself failed;
- `email <id> (<kind>) of PMC <id> was given up on after 5 failed attempts: <error>`.

Keep the log on the server: it holds ids and the paths people opened.

The books keep their own record, which staff read in the portal:
- each person's **Email** tab lists every email to them, whether it went and the last error;
- **Rent roll**'s run history lists the latest ten rent runs, nightly or by hand, with who ran them;
- the audit log (`trust_audit_log`) holds every write to a trust table and who made it.

## What to do

**The nightly run failed.** Read the traceback in the log.
- If the cause has passed (the database restarted, a disk filled and was cleared), an admin runs
  it now from **Rent roll**, ⋯, **Run rent now**, or leaves it to the next night (06:00 UTC).
- Either way it is safe: each part charges once per lease and date, so a run again charges
  nothing twice, and a failed run charged nothing at all (each company's run is one transaction).
- If it fails again with the same error, it is a bug: open an issue.

**Emails are not going out.**
1. Check the server's sender settings (above) and the mail server.
2. Then `python -m pms.email deliver` (`docker compose run --rm app python -m pms.email deliver`)
   sends what still waits.
3. An email given up on stays given up on: write it again from the person's Email tab, or send a
   new invite or reset. An invite's link was never written down (docs/adr/0007-email.md).

**The server is down, or `/health` says the database is unavailable.** Check
`docker compose ps` and `docker compose logs db`, then the disk and the database's own settings.
Nothing is lost while it is down: nothing is posted either.

**The books are wrong, or the database is lost.**
- A mistake in the money is corrected the way the ledger corrects anything: a new, reversing
  transfer (a journal entry, Accounting, General ledger), never by editing a row.
- A database that is lost or damaged comes back from the latest backup: docs/backup.md, "Getting
  the books back". Stop the app first, restore into a new database, check the line the restore
  ends with, then enter again from the bank's records what happened after the backup was taken,
  and reconcile.

**An upgrade went wrong.** docs/release.md, "No rollback: a bad release is fixed forward".
- The only way back is before anything was posted on the new release: restore the backup taken
  just before the upgrade (step 3 there) and run the tag you ran before.
- After that, the fix is a new release.

**A bug in the PMS.** Open an issue on this repository with:
- the release tag the server runs;
- what was done, what happened and what was expected;
- the log lines, with ids kept and people's details replaced.

There is no `SECURITY.md` yet, and whether this repository takes private vulnerability reports
is NOT VERIFIED. Until there is one, don't describe a way to get at another company's or person's
data in a public issue: ask the maintainers, in an issue that says only that you have a security
problem to report, how to send it to them privately.

## Every month

- An admin reads the alerts (or confirms there were none) and each Email tab that shows "Not
  sent".
- Whoever runs the server tests a restore (docs/backup.md, "Test a restore").
