-- migrate:up
-- A unit never leased has a day it became vacant (the founder's review q084): the vacancy report
-- (migration 0054, last changed by 0061) counts it vacant from the day it was added to the PMS
-- (UTC), with a line under it saying so, and its days vacant from then. One added after the
-- report's day was not in the PMS on it: vacant since a day not known, which its line says. Read
-- only; the Dashboard's count of vacant units is the same.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION public.trust_report_vacancy(p_pmc_id uuid, p_as_of date)
 RETURNS TABLE(line integer, item text, property text, unit text, vacant_since date, days_vacant integer, last_rent numeric, next_lease_starts date)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vacancy is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

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
                WHERE l.unit_id = u.id AND l.starts_on > p_as_of) AS next_start
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
        WHERE u.pmc_id = p_pmc_id
    ),
    counted AS (
        SELECT count(*) AS total, count(*) FILTER (WHERE o.occupied) AS occupied FROM units AS o
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::uuid AS unit_id,
               0 AS step, 'PMC' AS what, pmc.display_name AS label, NULL::date AS since,
               NULL::integer AS days, NULL::numeric AS rent, NULL::date AS next_start
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, o.place, o.unit_name, o.id, 0, 'vacant', o.place, o.since,
               p_as_of - o.since + 1, o.rent, o.next_start
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
               NULL, NULL, NULL, NULL
        FROM units AS o
        WHERE NOT o.occupied AND o.never_leased
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total',
               CASE WHEN c.total = 0 THEN 'no units'
                    ELSE c.total || ' units: ' || c.occupied || ' occupied, '
                         || (c.total - c.occupied) || ' vacant ('
                         || round(100.0 * c.occupied / c.total, 1) || '% occupied)'
               END,
               NULL, NULL, NULL, NULL
        FROM counted AS c
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C", l.unit_id, l.step
            ))::integer,
           l.what, l.label, l.unit_name, l.since, l.days, l.rent, l.next_start
    FROM lines AS l
    ORDER BY 1;
END;
$function$;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION public.trust_report_vacancy(p_pmc_id uuid, p_as_of date)
 RETURNS TABLE(line integer, item text, property text, unit text, vacant_since date, days_vacant integer, last_rent numeric, next_lease_starts date)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vacancy is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH units AS (
        SELECT u.id, pr.display_name AS place, u.display_name AS unit_name,
               EXISTS (
                   SELECT l.id FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.starts_on <= p_as_of
                     AND (l.ends_on IS NULL OR l.ends_on >= p_as_of)
               ) AS occupied,
               last.ends_on + 1 AS since,
               last.monthly_rent AS rent,
               (SELECT min(l.starts_on) FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on > p_as_of) AS next_start
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
        WHERE u.pmc_id = p_pmc_id
    ),
    counted AS (
        SELECT count(*) AS total, count(*) FILTER (WHERE o.occupied) AS occupied FROM units AS o
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::uuid AS unit_id,
               0 AS step, 'PMC' AS what, pmc.display_name AS label, NULL::date AS since,
               NULL::integer AS days, NULL::numeric AS rent, NULL::date AS next_start
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        -- A unit never leased has no day it became vacant: unknown, not zero.
        SELECT 1, o.place, o.unit_name, o.id, 0, 'vacant', o.place, o.since,
               p_as_of - o.since + 1, o.rent, o.next_start
        FROM units AS o
        WHERE NOT o.occupied
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total',
               CASE WHEN c.total = 0 THEN 'no units'
                    ELSE c.total || ' units: ' || c.occupied || ' occupied, '
                         || (c.total - c.occupied) || ' vacant ('
                         || round(100.0 * c.occupied / c.total, 1) || '% occupied)'
               END,
               NULL, NULL, NULL, NULL
        FROM counted AS c
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C", l.unit_id, l.step
            ))::integer,
           l.what, l.label, l.unit_name, l.since, l.days, l.rent, l.next_start
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

