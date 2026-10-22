-- migrate:up
-- The 1099 summary lists the vendors marked for a 1099 (the founder's review q060): a vendor's
-- latest profile (migration 0073) says whether they get one. Each of those paid at least the
-- IRS's threshold in the year is a vendor line, and the total is theirs; those paid less come
-- after it, under the threshold. A vendor with no profile is listed apart, no 1099 setting, so
-- staff can say; those marked for none are one line, how many and what they were paid. The
-- threshold (the founder left it to us): 600.00 for payments through 2025, 2000.00 from 2026
-- (the One Big Beautiful Bill Act, section 70433, for payments made after December 31, 2025),
-- adjusted for inflation from 2027 by amounts this does not know, which its line says. Read
-- only; it files nothing and holds no tax ID.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_report_vendor_payments(
    p_pmc_id uuid,
    p_year integer
) RETURNS TABLE (
    line integer,
    item text,
    vendor text,
    payments bigint,
    paid numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
    v_threshold numeric;
    v_why text;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: vendor payments are for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';
    -- The IRS's threshold for a 1099-NEC or 1099-MISC in the year.
    IF p_year <= 2025 THEN
        v_threshold := 600.00;
        v_why := 'a 1099 for a vendor marked for one and paid this much or more'
            || ' (IRS, payments through 2025)';
    ELSIF p_year = 2026 THEN
        v_threshold := 2000.00;
        v_why := 'a 1099 for a vendor marked for one and paid this much or more'
            || ' (IRS, payments from 2026)';
    ELSE
        v_threshold := 2000.00;
        v_why := 'a 1099 for a vendor marked for one and paid this much or more'
            || ' (IRS, payments from 2026; its adjustment for inflation from 2027 is not known'
            || ' here)';
    END IF;

    RETURN QUERY
    WITH here AS (
        SELECT b.vendor_id, count(*) AS payments, sum(tr.amount) AS total
        FROM trust_bill_payments AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND p.step = 'paid'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        GROUP BY b.vendor_id
    ),
    before AS (
        SELECT c.vendor_id, c.paid_before, c.amount
        FROM trust_vendor_payments_carried_over AS c
        WHERE c.pmc_id = p_pmc_id AND c.year = p_year
    ),
    -- Each vendor's latest profile: whether they get a 1099.
    profiles AS (
        SELECT DISTINCT ON (pr.vendor_id) pr.vendor_id, pr.gets_1099
        FROM trust_vendor_profiles AS pr
        WHERE pr.pmc_id = p_pmc_id
        ORDER BY pr.vendor_id, pr.created_at DESC, pr.id DESC
    ),
    totals AS (
        SELECT v.id AS vendor_id, v.display_name, coalesce(h.payments, 0) AS payments,
               coalesce(h.total, 0) + coalesce(c.amount, 0) AS total,
               c.paid_before, c.amount AS carried, pf.gets_1099
        FROM trust_vendors AS v
        LEFT JOIN here AS h ON h.vendor_id = v.id
        LEFT JOIN before AS c ON c.vendor_id = v.id
        LEFT JOIN profiles AS pf ON pf.vendor_id = v.id
        WHERE v.pmc_id = p_pmc_id AND (h.vendor_id IS NOT NULL OR c.vendor_id IS NOT NULL)
    ),
    -- Where each one goes: 1 a 1099 vendor at or over the threshold, 3 under it, 4 no 1099
    -- setting, 5 marked for none.
    paid AS (
        SELECT t.*,
               CASE
                   WHEN t.gets_1099 IS NULL THEN 4
                   WHEN NOT t.gets_1099 THEN 5
                   WHEN t.total >= v_threshold THEN 1
                   ELSE 3
               END AS section
        FROM totals AS t
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS name, NULL::uuid AS id, 0 AS part, 'PMC' AS item,
               pmc.display_name AS label, NULL::bigint AS payments, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'year', p_year::text || ', paid January 1 to December 31 UTC',
               NULL, NULL
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'threshold', v_why, NULL, v_threshold
        UNION ALL
        SELECT p.section, p.display_name, p.vendor_id, 0,
               CASE p.section
                   WHEN 1 THEN 'vendor' WHEN 3 THEN 'under the threshold'
                   ELSE 'no 1099 setting'
               END,
               p.display_name, p.payments, p.total
        FROM paid AS p WHERE p.section <> 5
        UNION ALL
        -- What of the vendor's total the old system paid, under the vendor's line.
        SELECT p.section, p.display_name, p.vendor_id, 1, 'carried over',
               'paid in the old system before ' || to_char(p.paid_before, 'YYYY-MM-DD'),
               NULL, p.carried
        FROM paid AS p WHERE p.carried IS NOT NULL AND p.section <> 5
        UNION ALL
        -- The 1099 vendors' total, right after them.
        SELECT 2, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(p.payments) FILTER (WHERE p.section = 1), 0)::bigint,
               coalesce(sum(p.total) FILTER (WHERE p.section = 1), 0)
        FROM paid AS p
        UNION ALL
        SELECT 5, NULL, NULL, 0, 'not marked for a 1099',
               count(*)::text || CASE count(*) WHEN 1 THEN ' vendor' ELSE ' vendors' END,
               coalesce(sum(p.payments), 0)::bigint, coalesce(sum(p.total), 0)
        FROM paid AS p WHERE p.section = 5
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.id, l.part
           )::integer,
           l.item,
           -- The PMC's name, the year and the threshold sit in the vendor column of the first
           -- three lines.
           l.label,
           l.payments,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_report_vendor_payments(
    p_pmc_id uuid,
    p_year integer
) RETURNS TABLE (
    line integer,
    item text,
    vendor text,
    payments bigint,
    paid numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: vendor payments are for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH here AS (
        SELECT b.vendor_id, count(*) AS payments, sum(tr.amount) AS total
        FROM trust_bill_payments AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND p.step = 'paid'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        GROUP BY b.vendor_id
    ),
    before AS (
        SELECT c.vendor_id, c.paid_before, c.amount
        FROM trust_vendor_payments_carried_over AS c
        WHERE c.pmc_id = p_pmc_id AND c.year = p_year
    ),
    paid AS (
        SELECT v.id AS vendor_id, v.display_name, coalesce(h.payments, 0) AS payments,
               coalesce(h.total, 0) + coalesce(c.amount, 0) AS total,
               c.paid_before, c.amount AS carried
        FROM trust_vendors AS v
        LEFT JOIN here AS h ON h.vendor_id = v.id
        LEFT JOIN before AS c ON c.vendor_id = v.id
        WHERE v.pmc_id = p_pmc_id AND (h.vendor_id IS NOT NULL OR c.vendor_id IS NOT NULL)
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS name, NULL::uuid AS id, 0 AS part, 'PMC' AS item,
               pmc.display_name AS label, NULL::bigint AS payments, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'year', p_year::text || ', paid January 1 to December 31 UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, p.display_name, p.vendor_id, 0, 'vendor', p.display_name, p.payments, p.total
        FROM paid AS p
        UNION ALL
        -- What of the vendor's total the old system paid, under the vendor's line.
        SELECT 1, p.display_name, p.vendor_id, 1, 'carried over',
               'paid in the old system before ' || to_char(p.paid_before, 'YYYY-MM-DD'),
               NULL, p.carried
        FROM paid AS p WHERE p.carried IS NOT NULL
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'total', NULL, coalesce(sum(p.payments), 0)::bigint,
               coalesce(sum(p.total), 0)
        FROM paid AS p
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.id, l.part
           )::integer,
           l.item,
           -- The PMC's name and the year sit in the vendor column of the first two lines.
           l.label,
           l.payments,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

