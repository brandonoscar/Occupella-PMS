-- migrate:up
-- Vendor insurance and move-in/move-out inspections (founder, 2026-10-15, the sixth gap against
-- Buildium). Neither moves money.
--
--   trust_vendor_insurance           a vendor's policy of one coverage: carrier, policy number,
--                                    the days it runs and its limit. A renewal is a new row; a
--                                    vendor is covered on a day by a policy running then.
--   trust_report_vendor_insurance    each vendor's coverage on a day: the policy running then,
--                                    or the latest that ran out, or none; and how it stands
--                                    (current, expiring within 30 days, expired).
--   trust_inspections                a lease's move-in or move-out inspection: the day, who did
--                                    it and what they noted. The latest of each kind counts.
--   trust_inspection_items           its rooms, each in a condition, maybe with a note.
--   trust_report_inspection          a lease's latest move-in and move-out inspections side by
--                                    side, room by room, and which rooms changed.
--
-- Records: append-only and in the audit log. trust_ai_agent reads them like the rest.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_vendor_insurance (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    coverage text NOT NULL CHECK (coverage IN (
        'general liability', 'workers compensation', 'commercial auto',
        'professional liability', 'other'
    )),
    carrier text NOT NULL CHECK (carrier ~ '\S' AND length(carrier) <= 200),
    policy_number text NOT NULL CHECK (policy_number ~ '\S' AND length(policy_number) <= 100),
    starts_on date NOT NULL,
    expires_on date NOT NULL,
    limit_amount numeric
        CHECK (limit_amount > 0 AND limit_amount = round(limit_amount, 2)
               AND limit_amount <= 1000000000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_on >= starts_on),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_inspections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('move-in', 'move-out')),
    inspected_on date NOT NULL,
    notes text CHECK (notes ~ '\S' AND length(notes) <= 2000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_inspection_items (
    inspection_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- squawk-ignore prefer-bigint-over-int
    line integer NOT NULL CHECK (line BETWEEN 1 AND 100),
    room text NOT NULL CHECK (room ~ '\S' AND length(room) <= 100),
    condition text NOT NULL CHECK (condition IN ('good', 'fair', 'poor', 'damaged', 'missing')),
    note text CHECK (note ~ '\S' AND length(note) <= 500),
    PRIMARY KEY (inspection_id, line),
    FOREIGN KEY (pmc_id, inspection_id) REFERENCES trust_inspections (pmc_id, id)
);
-- A room is named once in an inspection, whatever its case.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_inspection_items_room ON trust_inspection_items (inspection_id, lower(room));

-- Who recorded it: the staff member the request names (nobody, from the command line).
CREATE FUNCTION trust_name_recorder() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.recorded_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_vendor_insurance
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_inspections
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_vendor_insurance
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_inspections
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_inspection_items
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_vendor_insurance
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_inspections
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_inspection_items
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_vendor_insurance, trust_inspections, trust_inspection_items FROM PUBLIC;
GRANT SELECT, INSERT ON trust_vendor_insurance, trust_inspections, trust_inspection_items
TO trust_app;
GRANT SELECT ON trust_vendor_insurance, trust_inspections, trust_inspection_items
TO trust_ai_agent;

CREATE FUNCTION trust_report_vendor_insurance(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, vendor text, vendor_id uuid, coverage text, carrier text,
    policy_number text, starts_on date, expires_on date, limit_amount numeric, standing text
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vendor insurance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH policies AS (
        -- Each vendor's coverage as of the day: of the policies started by then, the one
        -- that expires last (so one running then, the one running longest, before any that
        -- had run out). One that starts later is not yet theirs. The vendors page reads this
        -- report for how each stands.
        SELECT DISTINCT ON (p.vendor_id, p.coverage) p.*
        FROM trust_vendor_insurance AS p
        WHERE p.pmc_id = p_pmc_id AND p.starts_on <= p_as_of
        ORDER BY p.vendor_id, p.coverage, p.expires_on DESC, p.created_at DESC, p.id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS vendor_id, NULL::text AS kind,
               'PMC' AS what, pmc.display_name AS label, NULL::text AS insurer,
               NULL::text AS number, NULL::date AS begins_on, NULL::date AS ends_on,
               NULL::numeric AS covered, NULL::text AS stands
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 'as of', to_char(p_as_of, 'YYYY-MM-DD'), NULL, NULL, NULL,
               NULL, NULL, NULL
        UNION ALL
        SELECT 1, v.display_name, v.id, p.coverage, 'policy', v.display_name, p.carrier,
               p.policy_number, p.starts_on, p.expires_on, p.limit_amount,
               CASE WHEN p.expires_on < p_as_of THEN 'expired'
                    WHEN p.expires_on = p_as_of THEN 'expires today'
                    WHEN p.expires_on - p_as_of <= 30 THEN 'expires in ' || (p.expires_on - p_as_of)
                         || CASE WHEN p.expires_on - p_as_of = 1 THEN ' day' ELSE ' days' END
                    ELSE 'current' END
        FROM trust_vendors AS v
        JOIN policies AS p ON p.vendor_id = v.id
        WHERE v.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, v.display_name, v.id, NULL, 'no insurance on file', v.display_name, NULL,
               NULL, NULL, NULL, NULL, NULL
        FROM trust_vendors AS v
        WHERE v.pmc_id = p_pmc_id
          AND NOT EXISTS (SELECT p.vendor_id FROM policies AS p WHERE p.vendor_id = v.id)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.who COLLATE "C", l.vendor_id, l.kind COLLATE "C"
            ))::integer,
           l.what, l.label, l.vendor_id, l.kind, l.insurer, l.number, l.begins_on, l.ends_on,
           l.covered, l.stands
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_inspection(p_pmc_id uuid, p_lease_id uuid)
RETURNS TABLE (
    line integer, item text, room text, move_in text, move_out text, changed boolean,
    detail text
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_in trust_inspections;
    v_out trust_inspections;
BEGIN
    IF NOT EXISTS (
        SELECT l.id FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The latest of each kind: by the day it was done, then as recorded.
    SELECT i.* INTO v_in FROM trust_inspections AS i
    WHERE i.lease_id = p_lease_id AND i.kind = 'move-in'
    ORDER BY i.inspected_on DESC, i.created_at DESC, i.id
    LIMIT 1;
    SELECT i.* INTO v_out FROM trust_inspections AS i
    WHERE i.lease_id = p_lease_id AND i.kind = 'move-out'
    ORDER BY i.inspected_on DESC, i.created_at DESC, i.id
    LIMIT 1;

    RETURN QUERY
    WITH rooms AS (
        -- A room as the move-in inspection names it, else as the move-out one does.
        SELECT lower(x.room) AS key,
               coalesce(max(x.room) FILTER (WHERE x.inspection_id = v_in.id),
                        max(x.room) FILTER (WHERE x.inspection_id = v_out.id)) AS name,
               min(x.line) FILTER (WHERE x.inspection_id = v_in.id) AS in_line,
               min(x.line) FILTER (WHERE x.inspection_id = v_out.id) AS out_line,
               max(x.condition) FILTER (WHERE x.inspection_id = v_in.id) AS at_in,
               max(x.condition) FILTER (WHERE x.inspection_id = v_out.id) AS at_out,
               max(x.note) FILTER (WHERE x.inspection_id = v_in.id) AS in_note,
               max(x.note) FILTER (WHERE x.inspection_id = v_out.id) AS out_note
        FROM trust_inspection_items AS x
        WHERE x.inspection_id IN (v_in.id, v_out.id)
        GROUP BY lower(x.room)
    ),
    lines AS (
        SELECT 0 AS part, 0 AS step, NULL::integer AS in_line, NULL::integer AS out_line,
               'PMC' AS what, pmc.display_name AS name, NULL::text AS at_in, NULL::text AS at_out,
               NULL::boolean AS moved, NULL::text AS said
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 1, NULL, NULL, 'lease', pr.display_name || ', ' || u.display_name, NULL,
               NULL, NULL,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id)
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.id = p_lease_id
        UNION ALL
        -- The days each was done, or that none was.
        SELECT 0, 2, NULL, NULL, 'inspected', NULL,
               coalesce(to_char(v_in.inspected_on, 'YYYY-MM-DD'), 'no move-in inspection'),
               coalesce(to_char(v_out.inspected_on, 'YYYY-MM-DD'), 'no move-out inspection'),
               NULL, NULL
        UNION ALL
        SELECT 0, 3, NULL, NULL, 'notes', NULL, v_in.notes, v_out.notes, NULL, NULL
        WHERE v_in.notes IS NOT NULL OR v_out.notes IS NOT NULL
        UNION ALL
        -- Rooms in the move-in inspection's order, then rooms seen only at move-out in theirs.
        SELECT 1, 0, r.in_line, r.out_line, 'room', r.name, r.at_in, r.at_out,
               CASE WHEN r.at_in IS NULL OR r.at_out IS NULL THEN NULL
                    ELSE r.at_in <> r.at_out END,
               nullif(concat_ws('; ', 'move-in: ' || r.in_note, 'move-out: ' || r.out_note), '')
        FROM rooms AS r
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.step, l.in_line NULLS LAST, l.out_line, l.name COLLATE "C"
            ))::integer,
           l.what, l.name, l.at_in, l.at_out, l.moved, l.said
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_vendor_insurance(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_inspection(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_vendor_insurance(uuid, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_inspection(uuid, uuid) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'vendor insurance and inspections cannot be rolled back: they are records';
END;
$$;
