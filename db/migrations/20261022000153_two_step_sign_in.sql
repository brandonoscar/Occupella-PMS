-- migrate:up
-- Two-step sign-in for owners and tenants (the founder, 2026-10-22). Staff have always signed in
-- with a code from an authenticator app (migration 0028). Now an owner or a tenant may turn it
-- on for their own login in the portal: its secret is kept on their trust_login_secrets row, as
-- staff's is (never audited, never read by the AI role), and once its first code confirms it,
-- every sign-in of that login asks for a code too. A company may also require it of every owner
-- and tenant:
--
--   trust_sign_in_settings   whether the company requires two-step sign-in of its owners and
--                            tenants (none: not required). Append-only, the latest row holding,
--                            naming who changed it; staff change it from Settings, Logins.
--                            Audited. Like the logins it rules, the AI role never reads it.
--
-- While it is required, an owner or a tenant without an authenticator sets one up at their next
-- sign-in, after their password and before they get a session; a session already open stays
-- open. Recovery stays an admin's new invite: an invite a staff member made clears the
-- authenticator, while a reset the person asked for by email keeps it. The app does all of this
-- (src/pms/accounts.py): trust_app already updates the authenticator's columns of
-- trust_login_secrets, so no other grant changes.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_sign_in_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    require_two_step boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_sign_in_settings_pmc ON trust_sign_in_settings (pmc_id, created_at);
CREATE INDEX trust_sign_in_settings_recorded_by ON trust_sign_in_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_sign_in_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_sign_in_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_sign_in_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_sign_in_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_sign_in_settings TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'sign-in settings cannot be rolled back: they are access records';
END;
$$;
