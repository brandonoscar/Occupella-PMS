-- migrate:up
-- Lease expirations (founder decisions, 2026-10-13, product item 5): the leases of a PMC ending
-- in the next 30, 60 or 90 days, with whether a renewal offer is out.
--
-- trust_report_lease_expirations(pmc, as of) lists every lease whose last day is the as-of day
-- or one of the 90 after it, by how many days are left (0-30, 31-60, 61-90), soonest first:
-- its property, unit, tenants, last day, rent and renewal status as of the end of the day (UTC):
--   none      no renewal offer for it;
--   drafted   an offer made but not sent to sign;
--   sent      sent to sign, not signed by everyone yet;
--   signed    signed by everyone, not yet made the lease;
--   renewed   the renewal is the lease now.
-- (The furthest along of its offers, when it has more than one.) Each group and the whole list
-- end with a total: their rent, and how many of the leases have no offer out (none or
-- drafted). A month-to-month lease has no last day and is not listed.
--
-- It runs as the owner. The AI role never reads lease offers or their signers and signatures
-- (AI_NEVER_READS); the founder decided it may see each lease's renewal status word and
-- nothing else of them, so the report reads those tables for it and returns only the status.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_report_lease_expirations(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    property text,
    unit text,
    tenants text,
    ends_on date,
    days_left integer,
    bucket text,
    monthly_rent numeric,
    renewal text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: lease expirations are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH offers AS (
        -- Each renewal offer made by the day, and how far along it was then.
        SELECT o.renews_lease_id AS lease_id,
               CASE
                   WHEN EXISTS (
                       SELECT c.offer_id FROM trust_lease_offer_completions AS c
                       WHERE c.offer_id = o.id AND c.completed_at < v_cutoff
                   ) THEN 4
                   WHEN EXISTS (
                       SELECT r.offer_id FROM trust_signature_requests AS r
                       WHERE r.offer_id = o.id AND r.sent_at < v_cutoff
                   ) AND NOT EXISTS (
                       SELECT s.position FROM trust_lease_offer_signers AS s
                       WHERE s.offer_id = o.id AND NOT EXISTS (
                           SELECT g.position FROM trust_signatures AS g
                           WHERE g.offer_id = o.id AND g.position = s.position
                             AND g.recorded_at < v_cutoff
                       )
                   ) THEN 3
                   WHEN EXISTS (
                       SELECT r.offer_id FROM trust_signature_requests AS r
                       WHERE r.offer_id = o.id AND r.sent_at < v_cutoff
                   ) THEN 2
                   ELSE 1
               END AS stage
        FROM trust_lease_offers AS o
        WHERE o.pmc_id = p_pmc_id AND o.renews_lease_id IS NOT NULL
          AND o.offered_at < v_cutoff
    ),
    ending AS (
        SELECT l.id, pr.display_name AS place, u.display_name AS unit_name,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS names,
               l.ends_on AS last_day, l.ends_on - p_as_of AS left_days,
               CASE WHEN l.ends_on - p_as_of <= 30 THEN 1
                    WHEN l.ends_on - p_as_of <= 60 THEN 2
                    ELSE 3 END AS grp,
               l.monthly_rent AS rent,
               coalesce((SELECT max(f.stage) FROM offers AS f WHERE f.lease_id = l.id), 0) AS stage
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.pmc_id = p_pmc_id AND l.ends_on BETWEEN p_as_of AND p_as_of + 90
    ),
    spans (grp, label) AS (VALUES (1, '0-30'), (2, '31-60'), (3, '61-90')),
    lines AS (
        SELECT 0 AS section, 0 AS grp, 1 AS step, NULL::date AS last_day, NULL::text AS place,
               NULL::text AS unit_name, NULL::uuid AS id, 'PMC' AS item,
               pmc.display_name AS head, NULL::text AS names, NULL::integer AS left_days,
               NULL::numeric AS rent, NULL::text AS status
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 0, 2, NULL, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC; leases ending in the next '
               || '90 days', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, e.grp, 0, e.last_day, e.place, e.unit_name, e.id, 'lease', e.place, e.names,
               e.left_days, e.rent,
               (ARRAY['none', 'drafted', 'sent', 'signed', 'renewed'])[e.stage + 1]
        FROM ending AS e
        UNION ALL
        SELECT 1, g.grp, 1, NULL, NULL, NULL, NULL, 'group total', NULL, NULL, NULL,
               coalesce(sum(e.rent), 0.00),
               count(e.id) FILTER (WHERE e.stage < 2) || ' of ' || count(e.id)
               || ' with no renewal offer out'
        FROM spans AS g LEFT JOIN ending AS e ON e.grp = g.grp
        GROUP BY g.grp
        UNION ALL
        SELECT 2, 0, 0, NULL, NULL, NULL, NULL, 'total', NULL, NULL, NULL,
               coalesce(sum(e.rent), 0.00),
               count(e.id) FILTER (WHERE e.stage < 2) || ' of ' || count(e.id)
               || ' with no renewal offer out'
        FROM ending AS e
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.grp, l.step, l.last_day, l.place COLLATE "C",
                        l.unit_name COLLATE "C", l.id
           )::integer,
           l.item,
           -- The PMC's name and the day sit in the property column of the first two lines.
           l.head,
           l.unit_name,
           l.names,
           l.last_day,
           l.left_days,
           (SELECT g.label FROM spans AS g WHERE g.grp = l.grp),
           l.rent,
           l.status
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_lease_expirations(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_lease_expirations(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
-- A report reads and writes nothing; no other function calls it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_lease_expirations(uuid, date);
