-- migrate:up
-- The general ledger and trial balance by property (the founder's review q109): each line of an
-- account now names the property it is held for (an owner's property account's property, or a
-- tenant's prepaid rent's or deposit's: the tenant's property), so Reports narrows them to a
-- property group, a property or an owner's properties as it does the rent roll. Cash, vendors'
-- accounts and the company's fees are held for no one property: a narrowed report leaves them
-- out, as it does every line across properties. The column comes last; nothing else changes.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A report's columns are its return type, which only a new function changes.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_general_ledger(uuid, date, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_trial_balance(uuid, date);

CREATE FUNCTION trust_report_general_ledger(
    p_pmc_id uuid,
    p_from date,
    p_to date
) RETURNS TABLE (
    line integer,
    item text,
    bank text,
    account text,
    posted_at text,
    description text,
    debit numeric,
    credit numeric,
    balance numeric,
    property text
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: a general ledger runs from a day to the same day or a later '
            'one (got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The holder's name, for an entry on the other side of this account.
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               -- +1 where a credit grows the balance (money held for someone), -1 for cash.
               CASE t.kind WHEN 'bank_cash' THEN -1 ELSE 1 END AS sign,
               -- The property it is held for: an owner's property account's, or a tenant's.
               coalesce(pr.display_name, tp.display_name) AS property_name
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    entries AS (
        SELECT a.id AS account, e.amount, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < v_end
    ),
    openings AS (
        SELECT a.*,
               a.sign * coalesce(sum(x.amount) FILTER (WHERE x.event_at < v_start), 0)
                   AS opening,
               count(x.account) FILTER (WHERE x.event_at >= v_start) AS posted
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.id
        GROUP BY a.id, a.kind, a.bank_account_id, a.bank_name, a.account_name, a.holder, a.sign,
                 a.property_name
    ),
    shown AS (
        SELECT o.* FROM openings AS o
        WHERE o.opening <> 0 OR o.posted > 0 OR o.kind = 'bank_cash'
    ),
    postings AS (
        SELECT x.account, x.event_at, x.version,
               greatest(-x.amount, 0) AS dr, greatest(x.amount, 0) AS cr,
               s.opening + s.sign * sum(x.amount) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.holder || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN shown AS s ON s.id = x.account
        JOIN accounts AS c ON c.id = x.other
        WHERE x.event_at >= v_start
    ),
    lines AS (
        -- (section, bank, bank id, cash first, account, account id, step, event_at, version)
        -- orders the report; names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS cash_first,
               NULL::text AS account_name, NULL::text AS id, 0 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS description, NULL::numeric AS dr,
               NULL::numeric AS cr, NULL::numeric AS balance, NULL::text AS property_name
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, NULL, NULL, 1, NULL, NULL, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 0, NULL, NULL, 'opening balance', s.account_name, NULL,
               NULL, NULL, s.opening, s.property_name
        FROM shown AS s
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 1, p.event_at, p.version, 'entry', s.account_name,
               p.description, p.dr, p.cr, p.running, s.property_name
        FROM postings AS p
        JOIN shown AS s ON s.id = p.account
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 2, NULL, NULL, 'closing balance', s.account_name, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p WHERE p.account = s.id), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p WHERE p.account = s.id), 0),
               s.opening + s.sign * coalesce((
                   SELECT sum(p.cr) - sum(p.dr) FROM postings AS p WHERE p.account = s.id
               ), 0),
               s.property_name
        FROM shown AS s
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 0, NULL, NULL, 'total', NULL, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p), 0),
               NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.cash_first,
                        l.account_name COLLATE "C", l.id, l.step, l.event_at, l.version
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the period sit in the account column of the first two lines.
           l.label,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
           l.description,
           round(l.dr, 2),
           round(l.cr, 2),
           round(l.balance, 2),
           l.property_name
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_general_ledger(uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_general_ledger(uuid, date, date)
TO trust_app, trust_ai_agent;

CREATE FUNCTION trust_report_trial_balance(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    bank text,
    account text,
    debit numeric,
    credit numeric,
    property text
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
        RAISE EXCEPTION 'trust: a trial balance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The property it is held for: an owner's property account's, or a tenant's.
               coalesce(pr.display_name, tp.display_name) AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    -- A balance on the debit side is money the bank holds (-balance of its cash account); every
    -- other balance is money held for someone, on the credit side.
    sides AS (
        SELECT b.*, greatest(-b.balance, 0) AS dr, greatest(b.balance, 0) AS cr
        FROM balances AS b
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS account_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS dr, NULL::numeric AS cr,
               NULL::text AS property_name
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 'account', s.account_name, s.dr, s.cr, s.property_name
        FROM sides AS s
        WHERE s.balance <> 0 OR s.kind = 'bank_cash'
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, 2, NULL, NULL, 'bank total', NULL,
               sum(s.dr), sum(s.cr), NULL
        FROM sides AS s
        GROUP BY s.bank_name, s.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total', NULL,
               coalesce(sum(s.dr), 0), coalesce(sum(s.cr), 0), NULL
        FROM sides AS s
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.account_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the account column of the first two lines.
           l.label,
           round(l.dr, 2),
           round(l.cr, 2),
           l.property_name
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_trial_balance(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_trial_balance(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
-- The reports as they were, without the property column.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_general_ledger(uuid, date, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_trial_balance(uuid, date);

CREATE FUNCTION trust_report_general_ledger(
    p_pmc_id uuid,
    p_from date,
    p_to date
) RETURNS TABLE (
    line integer,
    item text,
    bank text,
    account text,
    posted_at text,
    description text,
    debit numeric,
    credit numeric,
    balance numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: a general ledger runs from a day to the same day or a later '
            'one (got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The holder's name, for an entry on the other side of this account.
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               -- +1 where a credit grows the balance (money held for someone), -1 for cash.
               CASE t.kind WHEN 'bank_cash' THEN -1 ELSE 1 END AS sign
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    entries AS (
        SELECT a.id AS account, e.amount, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < v_end
    ),
    openings AS (
        SELECT a.*,
               a.sign * coalesce(sum(x.amount) FILTER (WHERE x.event_at < v_start), 0)
                   AS opening,
               count(x.account) FILTER (WHERE x.event_at >= v_start) AS posted
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.id
        GROUP BY a.id, a.kind, a.bank_account_id, a.bank_name, a.account_name, a.holder, a.sign
    ),
    shown AS (
        SELECT o.* FROM openings AS o
        WHERE o.opening <> 0 OR o.posted > 0 OR o.kind = 'bank_cash'
    ),
    postings AS (
        SELECT x.account, x.event_at, x.version,
               greatest(-x.amount, 0) AS dr, greatest(x.amount, 0) AS cr,
               s.opening + s.sign * sum(x.amount) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.holder || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN shown AS s ON s.id = x.account
        JOIN accounts AS c ON c.id = x.other
        WHERE x.event_at >= v_start
    ),
    lines AS (
        -- (section, bank, bank id, cash first, account, account id, step, event_at, version)
        -- orders the report; names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS cash_first,
               NULL::text AS account_name, NULL::text AS id, 0 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS description, NULL::numeric AS dr,
               NULL::numeric AS cr, NULL::numeric AS balance
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, NULL, NULL, 1, NULL, NULL, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 0, NULL, NULL, 'opening balance', s.account_name, NULL,
               NULL, NULL, s.opening
        FROM shown AS s
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 1, p.event_at, p.version, 'entry', s.account_name,
               p.description, p.dr, p.cr, p.running
        FROM postings AS p
        JOIN shown AS s ON s.id = p.account
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 2, NULL, NULL, 'closing balance', s.account_name, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p WHERE p.account = s.id), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p WHERE p.account = s.id), 0),
               s.opening + s.sign * coalesce((
                   SELECT sum(p.cr) - sum(p.dr) FROM postings AS p WHERE p.account = s.id
               ), 0)
        FROM shown AS s
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 0, NULL, NULL, 'total', NULL, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p), 0),
               NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.cash_first,
                        l.account_name COLLATE "C", l.id, l.step, l.event_at, l.version
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the period sit in the account column of the first two lines.
           l.label,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
           l.description,
           round(l.dr, 2),
           round(l.cr, 2),
           round(l.balance, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_general_ledger(uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_general_ledger(uuid, date, date)
TO trust_app, trust_ai_agent;


CREATE FUNCTION trust_report_trial_balance(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    bank text,
    account text,
    debit numeric,
    credit numeric
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
        RAISE EXCEPTION 'trust: a trial balance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    -- A balance on the debit side is money the bank holds (-balance of its cash account); every
    -- other balance is money held for someone, on the credit side.
    sides AS (
        SELECT b.*, greatest(-b.balance, 0) AS dr, greatest(b.balance, 0) AS cr
        FROM balances AS b
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS account_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS dr, NULL::numeric AS cr
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 'account', s.account_name, s.dr, s.cr
        FROM sides AS s
        WHERE s.balance <> 0 OR s.kind = 'bank_cash'
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, 2, NULL, NULL, 'bank total', NULL,
               sum(s.dr), sum(s.cr)
        FROM sides AS s
        GROUP BY s.bank_name, s.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total', NULL,
               coalesce(sum(s.dr), 0), coalesce(sum(s.cr), 0)
        FROM sides AS s
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.account_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the account column of the first two lines.
           l.label,
           round(l.dr, 2),
           round(l.cr, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_trial_balance(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_trial_balance(uuid, date) TO trust_app, trust_ai_agent;
