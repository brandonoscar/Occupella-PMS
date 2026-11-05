-- migrate:up
-- When a session was last used (the One Occupella plan of 11/05, Phase 1 item 2): a session left
-- alone for accounts.IDLE_MINUTES ends, as well as at its twelve hours (expires_at). Each session
-- has at most one row, moved forward at most every few minutes while it is used
-- (accounts.session_for). It is state, not history: like trust_login_secrets it is updated in
-- place and never audited, since a row a few minutes apart for every signed-in person would bury
-- the audit log (the session itself, its start and its end, stays audited). The AI role never reads
-- it, as it never reads sessions.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_session_activity (
    session_id uuid PRIMARY KEY REFERENCES trust_sessions (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON trust_session_activity FROM PUBLIC;
GRANT SELECT, INSERT ON trust_session_activity TO trust_app;
GRANT UPDATE (last_seen_at) ON trust_session_activity TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
-- squawk-ignore ban-drop-table
DROP TABLE trust_session_activity;
