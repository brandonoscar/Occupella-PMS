-- migrate:up
-- Report: an owner's statement for a period (issue #8).
--
-- For each of the owner's properties: the opening balance at the period's start, each posting
-- dated inside the period in date order with the balance after it, and the closing balance.
-- Then the owner's totals across properties: opening, money in, money out, closing.
--
-- A posting reads as the account on its other side (`kind: holder`, as in the three-way
-- reconciliation report) and, when the transfer's metadata has one, its "memo". The period is
-- [start, end): a transfer dated exactly at the end belongs to the next statement. Dates and
-- times are shown in UTC, whatever the session's time zone.
--
-- An unknown owner, an owner of another PMC, or a period that doesn't end after it starts is
-- refused, so a mistake never reads as an owner holding nothing. Amounts show cents, 0.00 too.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_report_owner_statement(
    p_pmc_id uuid,
    p_owner_id uuid,
    p_period_start timestamptz,
    p_period_end timestamptz
) RETURNS TABLE (
    line integer,
    property text,
    posted_on text,
    item text,
    detail text,
    amount numeric,
    balance numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a statement period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS property_name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    entries AS (
        SELECT a.account, e.amount AS change, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.account
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < p_period_end
    ),
    openings AS (
        SELECT a.account, a.property_name,
               coalesce(sum(x.change) FILTER (WHERE x.event_at < p_period_start), 0.00) AS opening,
               coalesce(sum(x.change), 0.00) AS closing
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.account
        GROUP BY a.account, a.property_name
    ),
    postings AS (
        SELECT x.account, o.property_name, x.event_at, x.version, x.change,
               o.opening + sum(x.change) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.kind || ': ' || CASE c.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN ow.display_name || ' / ' || cp.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN openings AS o ON o.account = x.account
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = x.other
        JOIN trust_bank_accounts AS b ON b.id = c.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = c.pmc_id
        LEFT JOIN trust_owners AS ow ON ow.id = c.owner_id
        LEFT JOIN trust_properties AS cp ON cp.id = c.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = c.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = c.vendor_id
        WHERE x.event_at >= p_period_start
    ),
    lines AS (
        -- (section, property, account, step, event_at, version) orders the statement;
        -- names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS account, 1 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version,
               'PMC' AS item, pmc.display_name AS detail, NULL::numeric AS change,
               NULL::numeric AS balance
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, NULL, NULL, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, NULL, NULL, 'period',
               to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
               || to_char(p_period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, o.property_name, o.account, 0, NULL, NULL, 'opening balance', NULL, NULL,
               o.opening
        FROM openings AS o
        UNION ALL
        SELECT 1, p.property_name, p.account, 1, p.event_at, p.version, 'posting',
               p.description, p.change, p.running
        FROM postings AS p
        UNION ALL
        SELECT 1, o.property_name, o.account, 2, NULL, NULL, 'closing balance', NULL, NULL,
               o.closing
        FROM openings AS o
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'all properties: opening balance', NULL, NULL,
               coalesce((SELECT sum(o.opening) FROM openings AS o), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 1, NULL, NULL, 'all properties: money in', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change > 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 2, NULL, NULL, 'all properties: money out', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change < 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 3, NULL, NULL, 'all properties: closing balance', NULL, NULL,
               coalesce((SELECT sum(o.closing) FROM openings AS o), 0.00)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.account, l.step,
                        l.event_at, l.version
           )::integer,
           l.property_name,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'),
           l.item,
           l.detail,
           l.change,
           l.balance
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_owner_statement(uuid, uuid, timestamptz, timestamptz)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_owner_statement(uuid, uuid, timestamptz, timestamptz)
TO trust_app;

-- migrate:down
-- A report reads the ledger and writes nothing; no other function calls it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_owner_statement(uuid, uuid, timestamptz, timestamptz);
