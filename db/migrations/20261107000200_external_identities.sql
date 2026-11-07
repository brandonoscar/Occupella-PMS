-- migrate:up
-- Signing in through a host app (docs/adr/0013-sign-in-through-a-host.md; AgenticHelixis
-- docs/plans/one-login-one-ui.md, phase 4). A host app that mounts the PMS, such as Occupella,
-- signs people in itself and hands the PMS a verifier (src/pms/external.py); its sign-in becomes a
-- session here through POST /api/external-sign-in.
--
--   trust_external_identities   a login of the PMC linked to a person the host signs in: the
--                               host's name for itself (issuer) and its own id of the person
--                               (subject). A person is linked once per PMC and host, a login
--                               once per host. Append-only: a login that should no longer sign
--                               in is disabled, as any login is.
--
-- A login linked this way may have no password at all (no trust_login_secrets row): it signs in
-- only through the host.
--
-- trust_staff_roles.host_issuer: the host's admins are the books' admins (the founder, 11/07).
-- A role change the host makes names no staff member but the host, and only the admin role, of a
-- staff member it signs in; the PMC still keeps an admin whose login isn't disabled.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_external_identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    login_id uuid NOT NULL,
    issuer text NOT NULL CHECK (issuer ~ '^[a-z][a-z0-9_-]{0,31}$'),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, issuer, subject),
    UNIQUE (login_id, issuer),
    FOREIGN KEY (pmc_id, login_id) REFERENCES trust_logins (pmc_id, id)
);
CREATE INDEX trust_external_identities_pmc_login ON trust_external_identities (pmc_id, login_id);

-- Credentials, as logins are: trust_ai_agent reads none of it.
REVOKE ALL ON trust_external_identities FROM PUBLIC;
GRANT SELECT, INSERT ON trust_external_identities TO trust_app;

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON trust_external_identities
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON trust_external_identities
FOR EACH ROW EXECUTE FUNCTION trust_audit();

ALTER TABLE trust_staff_roles
    ADD COLUMN host_issuer text CHECK (host_issuer ~ '^[a-z][a-z0-9_-]{0,31}$');

CREATE OR REPLACE FUNCTION trust_check_staff_role_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_has_admin boolean;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('staff roles of ' || NEW.pmc_id, 0));
    NEW.changed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    NEW.created_at := clock_timestamp();
    -- An admin counts while their login isn't disabled (one not invited yet still counts: the
    -- server's operator can invite them); docs/edge-cases.md U-051.
    v_has_admin := EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
          AND NOT EXISTS (SELECT 1 FROM trust_logins AS l
                          WHERE l.staff_id = s.id AND l.disabled_at IS NOT NULL)
    );
    IF NEW.host_issuer IS NOT NULL THEN
        -- The host's admins are the books' admins: it changes the admin role alone, naming no
        -- staff member, of a staff member whose login it signs in.
        IF NEW.changed_by IS NOT NULL OR NEW.role <> 'admin' THEN
            RAISE EXCEPTION 'trust: a host app changes only the admin role, and names no staff member'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM trust_external_identities AS e
            JOIN trust_logins AS l ON l.pmc_id = e.pmc_id AND l.id = e.login_id
            WHERE e.pmc_id = NEW.pmc_id AND e.issuer = NEW.host_issuer
              AND l.kind = 'staff' AND l.staff_id = NEW.staff_id AND l.disabled_at IS NULL
        ) THEN
            RAISE EXCEPTION 'trust: % signs in no login of staff member %', NEW.host_issuer,
                NEW.staff_id
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'A host app changes the roles only of the staff it signs in.';
        END IF;
    ELSIF NEW.changed_by IS NULL THEN
        IF v_has_admin OR NEW.role <> 'admin' OR NOT NEW.granted THEN
            RAISE EXCEPTION 'trust: name the admin who changes a role'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'With nobody named, only a PMC''s first admin is granted.';
        END IF;
    ELSE
        PERFORM trust_require_role(NEW.pmc_id, NEW.changed_by, 'admin', 'change a role');
    END IF;
    IF trust_staff_holds(NEW.pmc_id, NEW.staff_id, NEW.role) = NEW.granted THEN
        RAISE EXCEPTION 'trust: staff member % % the % role already', NEW.staff_id,
            CASE WHEN NEW.granted THEN 'holds' ELSE 'does not hold' END, NEW.role
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.granted AND NEW.role = 'admin' AND NOT EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND s.id <> NEW.staff_id
          AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
          AND NOT EXISTS (SELECT 1 FROM trust_logins AS l
                          WHERE l.staff_id = s.id AND l.disabled_at IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'trust: staff member % is the PMC''s last admin', NEW.staff_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Make another staff member an admin first.';
    END IF;
    RETURN NEW;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_check_staff_role_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_has_admin boolean;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('staff roles of ' || NEW.pmc_id, 0));
    NEW.changed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    NEW.created_at := clock_timestamp();
    -- An admin counts while their login isn't disabled (one not invited yet still counts: the
    -- server's operator can invite them); docs/edge-cases.md U-051.
    v_has_admin := EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
          AND NOT EXISTS (SELECT 1 FROM trust_logins AS l
                          WHERE l.staff_id = s.id AND l.disabled_at IS NOT NULL)
    );
    IF NEW.changed_by IS NULL THEN
        IF v_has_admin OR NEW.role <> 'admin' OR NOT NEW.granted THEN
            RAISE EXCEPTION 'trust: name the admin who changes a role'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'With nobody named, only a PMC''s first admin is granted.';
        END IF;
    ELSE
        PERFORM trust_require_role(NEW.pmc_id, NEW.changed_by, 'admin', 'change a role');
    END IF;
    IF trust_staff_holds(NEW.pmc_id, NEW.staff_id, NEW.role) = NEW.granted THEN
        RAISE EXCEPTION 'trust: staff member % % the % role already', NEW.staff_id,
            CASE WHEN NEW.granted THEN 'holds' ELSE 'does not hold' END, NEW.role
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.granted AND NEW.role = 'admin' AND NOT EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND s.id <> NEW.staff_id
          AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
          AND NOT EXISTS (SELECT 1 FROM trust_logins AS l
                          WHERE l.staff_id = s.id AND l.disabled_at IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'trust: staff member % is the PMC''s last admin', NEW.staff_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Make another staff member an admin first.';
    END IF;
    RETURN NEW;
END;
$$;

-- squawk-ignore ban-drop-column
ALTER TABLE trust_staff_roles DROP COLUMN host_issuer;
-- squawk-ignore ban-drop-table
DROP TABLE trust_external_identities;
