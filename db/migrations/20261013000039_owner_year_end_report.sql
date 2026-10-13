-- migrate:up
-- Report: an owner's year-end package (founder decision, 2026-10-13: "do these 3", product
-- item 3). The owner's statement for the whole year is trust_report_owner_statement from
-- January 1 to the next January 1; this is its summary and the year's 1099-MISC figure.
--
--   trust_report_owner_year_end   for each of the owner's properties in a calendar year (UTC):
--                                 the balance at its start; what was collected from tenants
--                                 (payments matched to the leases' charges, less what bounced
--                                 back), the management and leasing fees taken, the bills set
--                                 aside to pay, the owner's draws and contributions, and
--                                 anything else (opening balances at a cutover, money moved
--                                 between accounts); and the balance at its end. Then the
--                                 same across the owner's properties, and the rents to report
--                                 on the owner's 1099-MISC (box 1): everything collected from
--                                 tenants in the year. It files nothing, holds no tax IDs and
--                                 applies no threshold.
--
-- An unknown owner, an owner of another PMC, or a year outside 1900 to 9998 is refused, so a
-- mistake never reads as an owner with nothing. Amounts show cents, 0.00 too.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_report_owner_year_end(
    p_pmc_id uuid,
    p_owner_id uuid,
    p_year integer
) RETURNS TABLE (
    line integer,
    property text,
    item text,
    amount numeric
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
        RAISE EXCEPTION 'trust: a year-end package is for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH labels AS (
        SELECT v.part, v.label
        FROM (VALUES
            (0, 'opening balance'),
            (1, 'collected from tenants'),
            (2, 'management fees'),
            (3, 'leasing fees'),
            (4, 'bills paid'),
            (5, 'owner draws'),
            (6, 'owner contributions'),
            (7, 'other'),
            (9, 'closing balance')
        ) AS v (part, label)
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What moved each account in the year, by part, signed as it moved the account.
    moved AS (
        -- 1: collected from tenants: payments matched to the leases' charges...
        SELECT tr.to_account_id AS account, 1 AS part, cp.amount AS change
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...less what of them bounced back.
        SELECT tr.from_account_id, 1, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 2, -tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 3, -tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- 4: bills, when their money left the owner's account (set aside to pay them).
        SELECT tr.from_account_id, 4, -tr.amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT d.ledger_account_id, 5, -tr.amount
        FROM trust_owner_draws AS d
        JOIN pgledger_transfers AS tr ON tr.id = d.transfer_id
        WHERE d.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT c.ledger_account_id, 6, tr.amount
        FROM trust_owner_contributions AS c
        JOIN pgledger_transfers AS tr ON tr.id = c.transfer_id
        WHERE c.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    balances AS (
        SELECT a.account, a.name,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_start), 0) AS opening,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_end), 0) AS closing
        FROM accounts AS a
        LEFT JOIN pgledger_entries AS e ON e.account_id = a.account
        LEFT JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        GROUP BY a.account, a.name
    ),
    parts AS (
        SELECT b.account, b.name, p.part, p.label,
               CASE p.part
                   WHEN 0 THEN b.opening
                   WHEN 9 THEN b.closing
                   WHEN 7 THEN b.closing - b.opening - coalesce(
                       (SELECT sum(m.change) FROM moved AS m WHERE m.account = b.account), 0)
                   ELSE coalesce(
                       (SELECT sum(m.change) FROM moved AS m
                        WHERE m.account = b.account AND m.part = p.part), 0)
               END AS total
        FROM balances AS b
        CROSS JOIN labels AS p
    ),
    lines AS (
        -- (section, property, account, part) orders the package; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS part,
               'PMC' AS item, pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'year',
               p_year::text || ', January 1 to December 31 UTC', NULL
        UNION ALL
        SELECT 1, p.name, p.account, p.part, p.label, p.name, p.total
        FROM parts AS p
        UNION ALL
        SELECT 2, NULL, NULL, x.part, 'all properties: ' || x.label, NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = x.part), 0)
        FROM labels AS x
        UNION ALL
        SELECT 3, NULL, NULL, 0, '1099-MISC rents (box 1)', NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = 1), 0)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.part
           )::integer,
           -- The PMC's, owner's and year's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_owner_year_end(uuid, uuid, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_owner_year_end(uuid, uuid, integer)
TO trust_app, trust_ai_agent;

-- migrate:down
-- A report reads the ledger and writes nothing; no other function calls it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_owner_year_end(uuid, uuid, integer);
