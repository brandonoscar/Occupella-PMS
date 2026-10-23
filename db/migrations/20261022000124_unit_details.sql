-- migrate:up
-- A unit's details, as every PMS a company switches from keeps them (the founder's regroup,
-- 10/22): its bedrooms, bathrooms (in halves), square feet and market rent, the rent it would ask
-- today. They are append-only rows of `trust_unit_details`, the latest for a unit holding, each
-- naming who recorded it; a correction is a new row, and one with nothing in it clears them.
-- The vacancy report adds them to each vacant unit as recorded by the end of its day (UTC), so a
-- past day's report prints the same after a later change. Details decide nothing and move no money; a tax credit unit's
-- bedrooms stay its designation's (migration 0123). The AI role reads them.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_unit_details (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    unit_id uuid NOT NULL,
    -- squawk-ignore prefer-bigint-over-int
    bedrooms integer CHECK (bedrooms BETWEEN 0 AND 20),
    -- A half bath counts as a half: 1.5, 2.5.
    bathrooms numeric CHECK (bathrooms BETWEEN 0 AND 20 AND bathrooms * 2 = trunc(bathrooms * 2)),
    -- squawk-ignore prefer-bigint-over-int
    square_feet integer CHECK (square_feet BETWEEN 1 AND 100000),
    market_rent numeric CHECK (
        market_rent >= 0 AND market_rent <= 10000000 AND market_rent = round(market_rent, 2)
    ),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_unit_details_unit ON trust_unit_details (pmc_id, unit_id, created_at);
CREATE INDEX trust_unit_details_recorded_by ON trust_unit_details (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_unit_details
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_unit_details
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_unit_details
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_unit_details FROM PUBLIC;
GRANT SELECT, INSERT ON trust_unit_details TO trust_app;
GRANT SELECT ON trust_unit_details TO trust_ai_agent;

-- The vacancy report (migration 0054, last changed by 0114) adds each vacant unit's details at
-- the end of its lines: as recorded by the end of the report's day (UTC). Its other columns and
-- lines are as they were.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_vacancy(uuid, date);
CREATE FUNCTION trust_report_vacancy(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, property text, unit text, vacant_since date, days_vacant integer,
    last_rent numeric, next_lease_starts date, bedrooms integer, bathrooms numeric,
    square_feet integer, market_rent numeric
)
LANGUAGE plpgsql STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_end timestamptz;
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vacancy is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The end of the report's day (UTC): details recorded later are not in it.
    v_end := ((p_as_of + 1)::timestamp AT TIME ZONE 'UTC') - interval '1 microsecond';

    RETURN QUERY
    WITH units AS (
        SELECT u.id, pr.display_name AS place, u.display_name AS unit_name,
               EXISTS (
                   SELECT l.id FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.starts_on <= p_as_of
                     AND (l.ends_on IS NULL OR l.ends_on >= p_as_of)
               ) AS occupied,
               -- Vacant from the day after its last lease ended; a unit never leased by the
               -- day, from the day it was added to the PMS (UTC), if that is by the day.
               coalesce(
                   last.ends_on + 1,
                   CASE WHEN (u.created_at AT TIME ZONE 'UTC')::date <= p_as_of
                        THEN (u.created_at AT TIME ZONE 'UTC')::date END
               ) AS since,
               last.ends_on IS NULL AS never_leased,
               last.monthly_rent AS rent,
               (SELECT min(l.starts_on) FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on > p_as_of) AS next_start,
               d.bedrooms AS beds, d.bathrooms AS baths, d.square_feet AS size,
               d.market_rent AS market
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN LATERAL (
            SELECT l.ends_on,
                   coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                    WHERE rc.lease_id = l.id AND rc.effective_on <= l.ends_on
                                    ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS monthly_rent
            FROM trust_leases AS l
            WHERE l.unit_id = u.id AND l.ends_on < p_as_of
            ORDER BY l.ends_on DESC
            LIMIT 1
        ) AS last ON true
        -- The latest details recorded by the end of the day.
        LEFT JOIN LATERAL (
            SELECT x.bedrooms, x.bathrooms, x.square_feet, x.market_rent
            FROM trust_unit_details AS x
            WHERE x.unit_id = u.id AND x.created_at <= v_end
            ORDER BY x.created_at DESC, x.id DESC
            LIMIT 1
        ) AS d ON true
        WHERE u.pmc_id = p_pmc_id
    ),
    counted AS (
        SELECT count(*) AS total, count(*) FILTER (WHERE o.occupied) AS occupied FROM units AS o
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::uuid AS unit_id,
               0 AS step, 'PMC' AS what, pmc.display_name AS label, NULL::date AS since,
               NULL::integer AS days, NULL::numeric AS rent, NULL::date AS next_start,
               NULL::integer AS beds, NULL::numeric AS baths, NULL::integer AS size,
               NULL::numeric AS market
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL,
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, o.place, o.unit_name, o.id, 0, 'vacant', o.place, o.since,
               p_as_of - o.since + 1, o.rent, o.next_start, o.beds, o.baths, o.size, o.market
        FROM units AS o
        WHERE NOT o.occupied
        UNION ALL
        -- A unit never leased by the day says so under it: vacant since it was added, or, added
        -- after the day, since a day not known (q084).
        SELECT 1, o.place, o.unit_name, o.id, 1, 'never leased',
               CASE WHEN o.since IS NULL
                    THEN 'added to the PMS after this day: vacant since a day not known'
                    ELSE 'vacant since it was added to the PMS'
               END,
               NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
        FROM units AS o
        WHERE NOT o.occupied AND o.never_leased
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total',
               CASE WHEN c.total = 0 THEN 'no units'
                    ELSE c.total || ' units: ' || c.occupied || ' occupied, '
                         || (c.total - c.occupied) || ' vacant ('
                         || round(100.0 * c.occupied / c.total, 1) || '% occupied)'
               END,
               NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
        FROM counted AS c
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C", l.unit_id, l.step
            ))::integer,
           l.what, l.label, l.unit_name, l.since, l.days, l.rent, l.next_start, l.beds, l.baths,
           l.size, l.market
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_vacancy(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_vacancy(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'unit_details cannot be rolled back: details are records';
END;
$$;
