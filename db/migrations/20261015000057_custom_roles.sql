-- migrate:up
-- Custom staff roles (founder, 2026-10-15, the fifth gap against Buildium). An admin names a
-- role and the areas of the work it may change (rent, bills, owners' money, budgets, journal,
-- month-end, switching, setup, move-out, documents, leasing, maintenance, people), and gives
-- it to staff members. A staff member given custom roles may change only what their roles
-- allow; one with none works everywhere, as before. Every staff member still sees every page.
-- The three roles the database holds itself (admin, approve, disburse; migration 0034) are
-- unchanged and still needed for what they guard.
--
--   trust_custom_roles          a role of the PMC, by a name it gives once.
--   trust_custom_role_areas     whether a role allows an area: its latest row (the ids order
--                               them), a change being a new row.
--   trust_staff_custom_roles    whether a staff member holds a role: its latest row likewise.
--   trust_staff_may             whether a staff member may change an area.
--
-- Only an admin changes any of them; all are append-only and in the audit log.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_custom_roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    name text NOT NULL CHECK (name ~ '\S' AND length(name) <= 100),
    changed_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, name),
    FOREIGN KEY (pmc_id, changed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_custom_role_areas (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    role_id uuid NOT NULL,
    area text NOT NULL CHECK (area IN (
        'rent', 'bills', 'owners', 'budgets', 'journal', 'month-end', 'switching', 'setup',
        'move-out', 'documents', 'leasing', 'maintenance', 'people'
    )),
    granted boolean NOT NULL,
    changed_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, role_id) REFERENCES trust_custom_roles (pmc_id, id),
    FOREIGN KEY (pmc_id, changed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_staff_custom_roles (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    staff_id uuid NOT NULL,
    role_id uuid NOT NULL,
    granted boolean NOT NULL,
    changed_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, role_id) REFERENCES trust_custom_roles (pmc_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_custom_role_areas_role ON trust_custom_role_areas (role_id, area);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_staff_custom_roles_staff ON trust_staff_custom_roles (staff_id, role_id);

-- Who made the change: the staff member the request names (nobody, from the command line).
CREATE FUNCTION trust_name_custom_role_changer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.changed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_changed_by BEFORE INSERT ON trust_custom_roles
FOR EACH ROW EXECUTE FUNCTION trust_name_custom_role_changer();
CREATE TRIGGER trust_changed_by BEFORE INSERT ON trust_custom_role_areas
FOR EACH ROW EXECUTE FUNCTION trust_name_custom_role_changer();
CREATE TRIGGER trust_changed_by BEFORE INSERT ON trust_staff_custom_roles
FOR EACH ROW EXECUTE FUNCTION trust_name_custom_role_changer();
CREATE TRIGGER trust_admin BEFORE INSERT ON trust_custom_roles
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('admin', 'change a custom role');
CREATE TRIGGER trust_admin BEFORE INSERT ON trust_custom_role_areas
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('admin', 'change a custom role');
CREATE TRIGGER trust_admin BEFORE INSERT ON trust_staff_custom_roles
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('admin', 'change a custom role');
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_custom_roles
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_custom_role_areas
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_staff_custom_roles
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_custom_roles
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_custom_role_areas
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_staff_custom_roles
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_custom_roles, trust_custom_role_areas, trust_staff_custom_roles FROM PUBLIC;
GRANT SELECT, INSERT ON trust_custom_roles, trust_custom_role_areas, trust_staff_custom_roles
TO trust_app;
GRANT SELECT ON trust_custom_roles, trust_custom_role_areas, trust_staff_custom_roles
TO trust_ai_agent;

-- Whether a staff member may change an area: yes when they hold no custom role, else when one
-- of the roles they hold allows it.
CREATE FUNCTION trust_staff_may(p_pmc_id uuid, p_staff_id uuid, p_area text) RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH held AS (
        SELECT DISTINCT ON (s.role_id) s.role_id, s.granted
        FROM trust_staff_custom_roles AS s
        WHERE s.pmc_id = p_pmc_id AND s.staff_id = p_staff_id
        ORDER BY s.role_id, s.id DESC
    )
    SELECT NOT EXISTS (SELECT h.role_id FROM held AS h WHERE h.granted)
        OR EXISTS (
            SELECT h.role_id FROM held AS h
            WHERE h.granted AND coalesce((
                SELECT a.granted FROM trust_custom_role_areas AS a
                WHERE a.role_id = h.role_id AND a.area = p_area
                ORDER BY a.id DESC
                LIMIT 1
            ), false)
        )
$$;

REVOKE ALL ON FUNCTION trust_staff_may(uuid, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_staff_may(uuid, uuid, text) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'custom roles cannot be rolled back: who could do what is a record';
END;
$$;
