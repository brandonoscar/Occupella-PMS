-- migrate:up
-- The AI role reads no audit row of a record it may not read (founder decision, 2026-10-13).
--
-- trust_ai_agent reads the audit log, to run trust_report_audit_log, and the log copies every
-- audited row: also those of tables the AI is never granted (AI_NEVER_READS in
-- tests/test_privileges.py): logins, invites and sessions; application links, applications,
-- applicants, screenings, decisions and adverse action notices; lease offers, their signers,
-- signature requests, signatures and completions. Row-level security now shows the AI an audit
-- row only when it may read the table the row came from, so whatever it is not granted stays
-- out of its log too, a table added later included. trust_app, which reads those tables, sees
-- the whole log as before, and the owner role (which writes the log through trust_audit) is not
-- subject to it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY trust_audit_log_app ON trust_audit_log
FOR SELECT TO trust_app
USING (true);

-- A table that no longer exists (to_regclass is NULL) shows nothing.
CREATE POLICY trust_audit_log_ai ON trust_audit_log
FOR SELECT TO trust_ai_agent
USING (has_table_privilege(to_regclass('public.' || quote_ident(table_name)), 'SELECT'));

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DROP POLICY trust_audit_log_ai ON trust_audit_log;
DROP POLICY trust_audit_log_app ON trust_audit_log;
ALTER TABLE trust_audit_log DISABLE ROW LEVEL SECURITY;
