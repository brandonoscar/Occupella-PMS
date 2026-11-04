-- migrate:up
-- A home's name as tenants, owners, vendors and applicants read it (the founder's review of
-- 11/03, item 5): "Cedar House", not "Cedar House, House". trust_unit_shown gives a unit's name
-- to show after its property's, or null when it adds nothing: a generic name (House, Home,
-- Main...) or the property's only unit not archived. Staff's own pages keep every unit's name.
-- It reads; it moves no money.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_unit_shown(p_unit_id uuid) RETURNS text
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE
        WHEN lower(btrim(u.display_name)) IN (
            'house', 'home', 'main', 'main house', 'unit', 'whole house', 'entire home',
            'single family', 'single-family', 'sfh', 'residence'
        ) THEN NULL
        WHEN NOT EXISTS (
            SELECT 1 FROM trust_units AS o
            WHERE o.property_id = u.property_id AND o.id <> u.id
              AND NOT trust_is_archived(NULL, o.id)
        ) THEN NULL
        ELSE u.display_name
    END
    FROM trust_units AS u
    WHERE u.id = p_unit_id
$$;

REVOKE ALL ON FUNCTION trust_unit_shown(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_unit_shown(uuid) TO trust_app;

-- migrate:down
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_unit_shown(uuid);
