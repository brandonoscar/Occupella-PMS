-- migrate:up
-- Tenant reports (founder decision, 2026-10-10: Buildium/AppFolio parity, database first).
--
--   trust_report_delinquency    as of the end of a day (UTC), every lease that owes money, with
--                               what it owes by how long it has been past due: current, 1-30,
--                               31-60, 61-90 and over 90 days. Credits and payments toward
--                               charges not yet due come off the oldest charges first, so each
--                               lease's total is its balance due on the rent roll.
--   trust_report_tenant_ledger  one lease's charges, payments and bounced payments through the
--                               end of a day (UTC), in date order, with the balance after each;
--                               the last balance is the lease's balance on the rent roll.
--
-- Both read what the rent roll reads: charges due by the day (credits take off), payments
-- matched to the lease's charges and dated by the end of the day, and bounces dated by then.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Report: as of the end of a day (UTC), each lease that owes money, by property and unit (byte
-- order), with its tenants and what it owes in each aging bucket; then the totals.
CREATE FUNCTION trust_report_delinquency(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    property text,
    unit text,
    tenants text,
    current_due numeric,
    days_1_30 numeric,
    days_31_60 numeric,
    days_61_90 numeric,
    over_90 numeric,
    total numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: delinquency is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, oldest first.
        SELECT c.lease_id, c.due_on, c.amount - p.amount AS left_owing,
               sum(c.amount - p.amount) OVER (
                   PARTITION BY c.lease_id ORDER BY c.due_on, c.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit' AND c.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day, and payments
        -- by then toward charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.lease_id, p_as_of - o.due_on AS late,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),
    by_lease AS (
        SELECT a.lease_id,
               sum(a.owed) FILTER (WHERE a.late = 0) AS b0,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 1 AND 30) AS b1,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 31 AND 60) AS b2,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 61 AND 90) AS b3,
               sum(a.owed) FILTER (WHERE a.late > 90) AS b4,
               sum(a.owed) AS owed
        FROM aged AS a
        GROUP BY a.lease_id
        HAVING sum(a.owed) > 0
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::date AS starts_on, 1 AS step, 'PMC' AS item, pmc.display_name AS names,
               NULL::numeric AS b0, NULL::numeric AS b1, NULL::numeric AS b2,
               NULL::numeric AS b3, NULL::numeric AS b4, NULL::numeric AS owed
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, pr.display_name, u.display_name, l.starts_on, 0, 'lease',
               (SELECT string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C",
                                  tn.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
                WHERE lt.lease_id = l.id),
               coalesce(b.b0, 0), coalesce(b.b1, 0), coalesce(b.b2, 0), coalesce(b.b3, 0),
               coalesce(b.b4, 0), b.owed
        FROM by_lease AS b
        JOIN trust_leases AS l ON l.id = b.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(b.b0), 0), coalesce(sum(b.b1), 0), coalesce(sum(b.b2), 0),
               coalesce(sum(b.b3), 0), coalesce(sum(b.b4), 0), coalesce(sum(b.owed), 0)
        FROM by_lease AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.starts_on, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           -- The PMC's name and the day sit in the tenants column of the first two lines.
           l.names,
           round(l.b0, 2), round(l.b1, 2), round(l.b2, 2), round(l.b3, 2), round(l.b4, 2),
           round(l.owed, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_delinquency(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_delinquency(uuid, date) TO trust_app, trust_ai_agent;

-- Report: one lease's charges (by due date), payments and bounced payments (by the date of
-- their transfer, UTC) through the end of a day, oldest first, each with the balance after it;
-- then the balance. On one day, charges come before payments and payments before bounces.
CREATE FUNCTION trust_report_tenant_ledger(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    on_date date,
    item text,
    detail text,
    charged numeric,
    paid numeric,
    balance numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant ledger is through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT l.id FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH entries AS (
        SELECT c.due_on AS on_date, 1 AS step, c.created_at AS at, c.id::text AS id, c.kind AS item,
               c.memo AS detail,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS charged,
               0::numeric AS paid
        FROM trust_charges AS c
        WHERE c.lease_id = p_lease_id AND c.due_on <= p_as_of
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 2, tr.event_at, tr.id, 'payment',
               tr.metadata ->> 'memo', 0, sum(cp.amount)
        FROM trust_charge_payments AS cp
        JOIN trust_charges AS c ON c.id = cp.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 3, tr.event_at, tr.id, 'bounced payment',
               tr.metadata ->> 'memo', 0, -sum(r.amount)
        FROM trust_payment_reversals AS r
        JOIN trust_charges AS c ON c.id = r.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
    ),
    lines AS (
        SELECT 0 AS section, NULL::date AS on_date, 0 AS step, NULL::timestamptz AS at,
               NULL::text AS id, 'lease' AS item,
               u.display_name || ', ' || to_char(l.starts_on, 'YYYY-MM-DD')
               || CASE WHEN l.ends_on IS NULL THEN ', month to month'
                       ELSE ' to ' || to_char(l.ends_on, 'YYYY-MM-DD') END AS detail,
               NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
        WHERE l.id = p_lease_id
        UNION ALL
        SELECT 1, e.on_date, e.step, e.at, e.id, e.item, e.detail, e.charged, e.paid
        FROM entries AS e
        UNION ALL
        SELECT 2, p_as_of, 0, NULL, NULL, 'balance', 'end of day UTC',
               coalesce((SELECT sum(e.charged) FROM entries AS e), 0),
               coalesce((SELECT sum(e.paid) FROM entries AS e), 0)
    )
    SELECT row_number() OVER w::integer,
           l.on_date,
           l.item,
           l.detail,
           round(l.charged, 2),
           round(l.paid, 2),
           CASE WHEN l.section > 0 THEN round(coalesce(sum(
               coalesce(l.charged, 0) - coalesce(l.paid, 0)
           ) FILTER (WHERE l.section = 1) OVER w, 0), 2) END
    FROM lines AS l
    WINDOW w AS (ORDER BY l.section, l.on_date, l.step, l.at, l.id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_tenant_ledger(uuid, uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_tenant_ledger(uuid, uuid, date)
TO trust_app, trust_ai_agent;

-- migrate:down
-- Reports read the ledger and write nothing; no other function calls them.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_tenant_ledger(uuid, uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_delinquency(uuid, date);
