-- migrate:up
-- Logins for the API and the portals (founder decision, 2026-10-11:
-- docs/adr/0002-portals-and-exports.md).
--
--   trust_logins          who may sign in to a PMC: one of its staff, owners or tenants, by
--                         email, at most one login per person and per email in a PMC. A login
--                         is never deleted: disabling one sets disabled_at.
--   trust_invites         a link staff send to a login's owner. Only the SHA-256 of its token
--                         is kept; it expires and is accepted once. Accepting sets the
--                         password, so a new invite is also how a forgotten password is reset.
--   trust_sessions        a signed-in browser. Only the SHA-256 of its cookie's token is kept;
--                         it expires, and signing out ends it.
--   trust_login_secrets   the password hash (argon2id) and, for staff, the authenticator
--                         secret (TOTP), with the count of failed sign-ins. Never audited, so
--                         no hash or secret reaches the audit log.
--
-- The API (src/pms/api) writes these as trust_app; it hashes and checks passwords itself.
-- trust_ai_agent reads none of the four: the role behind Occupella's key never sees who can
-- sign in, a session or a credential.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_logins (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN ('staff', 'owner', 'tenant')),
    staff_id uuid,
    owner_id uuid,
    tenant_id uuid,
    -- Kept lowercase, so one address is one login.
    email text NOT NULL CHECK (email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    disabled_at timestamptz,
    CHECK (
        (kind = 'staff' AND staff_id IS NOT NULL AND owner_id IS NULL AND tenant_id IS NULL)
        OR (kind = 'owner' AND owner_id IS NOT NULL AND staff_id IS NULL AND tenant_id IS NULL)
        OR (kind = 'tenant' AND tenant_id IS NOT NULL AND staff_id IS NULL AND owner_id IS NULL)
    ),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, email),
    UNIQUE (pmc_id, staff_id),
    UNIQUE (pmc_id, owner_id),
    UNIQUE (pmc_id, tenant_id),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id)
);

CREATE TABLE trust_invites (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    login_id uuid NOT NULL,
    token_hash bytea NOT NULL UNIQUE CHECK (length(token_hash) = 32),
    -- NULL: made on the server's command line (python -m pms.admin), as for the first staff.
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    accepted_at timestamptz,
    CHECK (expires_at > created_at),
    FOREIGN KEY (pmc_id, login_id) REFERENCES trust_logins (pmc_id, id),
    FOREIGN KEY (pmc_id, created_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    login_id uuid NOT NULL,
    token_hash bytea NOT NULL UNIQUE CHECK (length(token_hash) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    ended_at timestamptz,
    CHECK (expires_at > created_at),
    FOREIGN KEY (pmc_id, login_id) REFERENCES trust_logins (pmc_id, id)
);

CREATE INDEX trust_sessions_login ON trust_sessions (login_id);

CREATE TABLE trust_login_secrets (
    login_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    password_hash text NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    totp_secret text,
    totp_confirmed_at timestamptz,
    -- The last authenticator time step used, so a code works once.
    totp_last_step bigint,
    failed_sign_ins integer NOT NULL DEFAULT 0 CHECK (failed_sign_ins >= 0),
    locked_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (totp_confirmed_at IS NULL OR totp_secret IS NOT NULL),
    FOREIGN KEY (pmc_id, login_id) REFERENCES trust_logins (pmc_id, id)
);

-- A login, invite or session is the access record a trust account's auditor asks for.
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_logins
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_invites
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_sessions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_logins, trust_invites, trust_sessions, trust_login_secrets FROM PUBLIC;
GRANT SELECT, INSERT ON trust_logins, trust_invites, trust_sessions, trust_login_secrets
TO trust_app;
GRANT UPDATE (disabled_at) ON trust_logins TO trust_app;
GRANT UPDATE (accepted_at) ON trust_invites TO trust_app;
GRANT UPDATE (ended_at) ON trust_sessions TO trust_app;
GRANT UPDATE (
    password_hash, totp_secret, totp_confirmed_at, totp_last_step, failed_sign_ins,
    locked_until, updated_at
) ON trust_login_secrets TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'logins cannot be rolled back: they are access records';
END;
$$;
