-- migrate:up
-- A company keeps an admin who can sign in (docs/edge-cases.md U-051). "A PMC keeps an admin"
-- counted an admin whose login was disabled, so the only admin who could still sign in could drop
-- admin while another's login was disabled, and then nobody could change a role again: the
-- operator's make-admin, which names nobody, refused because an admin "existed". Now an admin
-- counts while their login isn't disabled (one not invited yet still counts: the operator can
-- invite them), both for the last admin and for make-admin.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
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

-- migrate:down
SET LOCAL lock_timeout = '5s';

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
    v_has_admin := EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
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
    ) THEN
        RAISE EXCEPTION 'trust: staff member % is the PMC''s last admin', NEW.staff_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Make another staff member an admin first.';
    END IF;
    RETURN NEW;
END;
$$;
