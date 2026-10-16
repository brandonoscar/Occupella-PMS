-- migrate:up
-- Property groups (the founder's punch list, 2026-10-16): a PMC's named sets of its properties
-- ("North portfolio"), which staff narrow the list pages to. A group is renamed (an audited
-- update of its name), never deleted. A property joins or leaves a group by a row of
-- `trust_property_group_members` (append-only, audited, naming who did it): the latest row for a
-- group and property holds, and each row changes something. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_property_groups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    display_name text NOT NULL
        CHECK (display_name <> '' AND display_name = btrim(display_name)
               AND length(display_name) <= 100),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id)
);
-- A name once per PMC, whatever its case.
CREATE UNIQUE INDEX trust_property_groups_name ON trust_property_groups (pmc_id, lower(display_name));

CREATE TABLE trust_property_group_members (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    group_id uuid NOT NULL,
    property_id uuid NOT NULL,
    member boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, group_id) REFERENCES trust_property_groups (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_property_group_members_group
ON trust_property_group_members (group_id, property_id, created_at);
CREATE INDEX trust_property_group_members_property
ON trust_property_group_members (pmc_id, property_id);
CREATE INDEX trust_property_group_members_recorded_by
ON trust_property_group_members (pmc_id, recorded_by);

-- The properties in a group now: those whose latest row for the group says they are in it.
CREATE FUNCTION trust_group_properties(p_group_id uuid)
RETURNS SETOF uuid
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT latest.property_id
    FROM (
        SELECT DISTINCT ON (m.property_id) m.property_id, m.member
        FROM trust_property_group_members AS m
        WHERE m.group_id = p_group_id
        ORDER BY m.property_id, m.created_at DESC, m.id DESC
    ) AS latest
    WHERE latest.member
$$;

REVOKE ALL ON FUNCTION trust_group_properties(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_group_properties(uuid) TO trust_app;

-- A row changes something: a property joins a group it is not in, or leaves one it is in. Rows
-- for one group wait for each other (the group's row lock), so two at once can't both join.
CREATE FUNCTION trust_check_group_member() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_group text;
    v_property text;
BEGIN
    SELECT g.display_name INTO v_group FROM trust_property_groups AS g
    WHERE g.id = NEW.group_id FOR UPDATE;
    IF (NEW.property_id IN (SELECT trust_group_properties(NEW.group_id))) = NEW.member THEN
        SELECT p.display_name INTO v_property FROM trust_properties AS p
        WHERE p.id = NEW.property_id;
        RAISE EXCEPTION 'trust: % is % in %', v_property,
            CASE WHEN NEW.member THEN 'already' ELSE 'not' END, v_group
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_group_member BEFORE INSERT ON trust_property_group_members
FOR EACH ROW EXECUTE FUNCTION trust_check_group_member();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_property_group_members
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_property_group_members
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_property_groups
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_property_group_members
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_property_groups, trust_property_group_members FROM PUBLIC;
GRANT SELECT, INSERT ON trust_property_groups, trust_property_group_members TO trust_app;
-- A group is renamed; nothing else of it changes.
GRANT UPDATE (display_name) ON trust_property_groups TO trust_app;
GRANT SELECT ON trust_property_groups, trust_property_group_members TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'property groups cannot be rolled back: they are records';
END;
$$;
