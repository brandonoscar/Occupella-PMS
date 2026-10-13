-- migrate:up
-- The three-way reconciliation lists what had not cleared the bank (founder decision,
-- 2026-10-13: "List them"). Until now it showed the bank statement balance, the trust journal
-- and the beneficiary ledgers, and the difference between the statement and the journal with
-- nothing to say what it was: the checks written and deposits made that the bank had not cleared
-- by the period's end.
--
-- Lines 1 to 10 stay as they were. Lines 11 to 14 add the deposits in transit, the outstanding
-- withdrawals, the adjusted bank balance (the statement balance with both) and its difference
-- from the trust journal. Then each deposit in transit, outstanding withdrawal and bank line
-- the books lack, as the bank reconciliation of the period's last day (UTC) shows them, from
-- the imported statements and their matches; then the ledger lines, as before. A period approved
-- from imported statements has a difference of 0 by then. When no imported statement covers the
-- period's last day (a reconciliation approved with a statement balance typed in), lines 11 to
-- 14 say the uncleared items are not known, and none are listed.
--
-- The report now calls trust_report_bank_reconciliation, so it is written in PL/pgSQL; it takes
-- the same arguments, runs with the caller's grants and keeps its own.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_report_three_way_reconciliation(
    p_pmc_id uuid,
    p_reconciliation_id uuid
) RETURNS TABLE (line integer, item text, detail text, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_rec trust_reconciliations;
    v_through date;
    v_unknown text;
    v_journal numeric;
    v_ledgers numeric;
    v_in_transit numeric;
    v_outstanding numeric;
    v_items integer := 0;
BEGIN
    SELECT r.* INTO v_rec FROM trust_reconciliations AS r
    WHERE r.id = p_reconciliation_id AND r.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- The books at the period's end: the account's cash, and what it holds for everyone.
    SELECT coalesce(-sum(e.amount) FILTER (WHERE t.kind = 'bank_cash'), 0),
           coalesce(sum(e.amount) FILTER (WHERE t.kind <> 'bank_cash'), 0)
    INTO v_journal, v_ledgers
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end;

    -- What had not cleared the bank by the period's last day, from the imported statements and
    -- their matches, as the bank reconciliation shows it.
    v_through := (v_rec.period_end AT TIME ZONE 'UTC')::date - 1;
    IF (v_through + 1)::timestamp AT TIME ZONE 'UTC' <> v_rec.period_end THEN
        v_unknown := 'not known: the period does not end at midnight (UTC)';
    ELSIF NOT EXISTS (
        SELECT s.id FROM trust_bank_statements AS s
        WHERE s.bank_account_id = v_rec.bank_account_id
          AND v_through BETWEEN s.first_day AND s.last_day
    ) THEN
        v_unknown := 'not known: no imported bank statement covers '
            || to_char(v_through, 'YYYY-MM-DD');
    ELSE
        SELECT max(b.amount) FILTER (WHERE b.item = 'deposits in transit'),
               max(b.amount) FILTER (WHERE b.item = 'outstanding withdrawals')
        INTO v_in_transit, v_outstanding
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b;
    END IF;

    RETURN QUERY
    SELECT 1, 'PMC', p.display_name, NULL::numeric
    FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
    UNION ALL
    SELECT 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL
    FROM trust_bank_accounts AS b WHERE b.id = v_rec.bank_account_id
    UNION ALL
    SELECT 3, 'period',
           to_char(v_rec.period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
           || to_char(v_rec.period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 4, 'prepared by', v_rec.prepared_by, NULL
    UNION ALL
    SELECT 5, 'approved by',
           v_rec.approved_by || ' at '
           || to_char(v_rec.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 6, 'bank statement balance', NULL, v_rec.statement_balance
    UNION ALL
    SELECT 7, 'trust journal (book cash)', NULL, v_journal
    UNION ALL
    SELECT 8, 'beneficiary ledgers', NULL, v_ledgers
    UNION ALL
    SELECT 9, 'difference: bank statement - trust journal', NULL,
           v_rec.statement_balance - v_journal
    UNION ALL
    SELECT 10, 'difference: trust journal - beneficiary ledgers', NULL, v_journal - v_ledgers
    UNION ALL
    SELECT 11, 'deposits in transit', v_unknown, v_in_transit
    UNION ALL
    SELECT 12, 'outstanding withdrawals', v_unknown, v_outstanding
    UNION ALL
    SELECT 13, 'adjusted bank balance', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding
    UNION ALL
    SELECT 14, 'difference: adjusted bank balance - trust journal', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding - v_journal
    ORDER BY 1;

    -- Each item that had not cleared, and each bank line the books lack, oldest first.
    IF v_unknown IS NULL THEN
        RETURN QUERY
        SELECT (14 + row_number() OVER (ORDER BY b.line))::integer, b.item, b.detail, b.amount
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b
        WHERE b.item IN (
            'deposit in transit', 'outstanding withdrawal', 'bank line not in the books'
        )
        ORDER BY 1;
        GET DIAGNOSTICS v_items = ROW_COUNT;
    END IF;

    -- Each beneficiary ledger in the account at the period's end, in byte order.
    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id, t.kind, t.owner_id, t.property_id, t.tenant_id, t.vendor_id,
               sum(e.amount) AS balance
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end
          AND t.kind <> 'bank_cash'
        GROUP BY t.ledger_account_id
    ),
    ledgers AS (
        SELECT b.kind || ': ' || CASE b.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               b.balance,
               b.ledger_account_id
        FROM balances AS b
        CROSS JOIN trust_pmcs AS pmc
        LEFT JOIN trust_owners AS o ON o.id = b.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = b.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = b.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
        WHERE pmc.pmc_id = p_pmc_id
    )
    SELECT (14 + v_items + row_number() OVER (
               ORDER BY ld.holder COLLATE "C", ld.ledger_account_id
           ))::integer,
           'ledger', ld.holder, ld.balance
    FROM ledgers AS ld
    ORDER BY 1;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The report as 20261009000011 left it.
CREATE OR REPLACE FUNCTION trust_report_three_way_reconciliation(
    p_pmc_id uuid,
    p_reconciliation_id uuid
) RETURNS TABLE (line integer, item text, detail text, amount numeric)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
WITH reconciliation AS (
    SELECT r.*, b.display_name AS bank_name, b.kind AS bank_kind, p.display_name AS pmc_name
    FROM trust_reconciliations AS r
    JOIN trust_bank_accounts AS b ON b.id = r.bank_account_id
    JOIN trust_pmcs AS p ON p.pmc_id = r.pmc_id
    WHERE r.id = p_reconciliation_id AND r.pmc_id = p_pmc_id
),
balances AS (
    SELECT t.ledger_account_id, t.kind, t.owner_id, t.property_id, t.tenant_id, t.vendor_id,
           sum(e.amount) AS balance
    FROM reconciliation AS r
    JOIN trust_ledger_accounts AS t ON t.bank_account_id = r.bank_account_id
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE tr.event_at < r.period_end
    GROUP BY t.ledger_account_id
),
legs AS (
    SELECT r.statement_balance AS statement,
           coalesce(-(SELECT sum(b.balance) FROM balances AS b WHERE b.kind = 'bank_cash'), 0)
               AS journal,
           coalesce((SELECT sum(b.balance) FROM balances AS b WHERE b.kind <> 'bank_cash'), 0)
               AS ledgers
    FROM reconciliation AS r
),
ledgers AS (
    SELECT b.kind || ': ' || CASE b.kind
               WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
               WHEN 'vendor_payable' THEN v.display_name
               WHEN 'pmc_income' THEN r.pmc_name
               ELSE tn.display_name
           END AS holder,
           b.balance,
           b.ledger_account_id
    FROM balances AS b
    CROSS JOIN reconciliation AS r
    LEFT JOIN trust_owners AS o ON o.id = b.owner_id
    LEFT JOIN trust_properties AS pr ON pr.id = b.property_id
    LEFT JOIN trust_tenants AS tn ON tn.id = b.tenant_id
    LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
    WHERE b.kind <> 'bank_cash'
)
SELECT 1, 'PMC', r.pmc_name, NULL::numeric FROM reconciliation AS r
UNION ALL
SELECT 2, 'trust bank account', r.bank_name || ' (' || r.bank_kind || ')', NULL
FROM reconciliation AS r
UNION ALL
SELECT 3, 'period',
       to_char(r.period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
       || to_char(r.period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
       NULL
FROM reconciliation AS r
UNION ALL
SELECT 4, 'prepared by', r.prepared_by, NULL FROM reconciliation AS r
UNION ALL
SELECT 5, 'approved by',
       r.approved_by || ' at '
       || to_char(r.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
       NULL
FROM reconciliation AS r
UNION ALL
SELECT 6, 'bank statement balance', NULL, l.statement FROM legs AS l
UNION ALL
SELECT 7, 'trust journal (book cash)', NULL, l.journal FROM legs AS l
UNION ALL
SELECT 8, 'beneficiary ledgers', NULL, l.ledgers FROM legs AS l
UNION ALL
SELECT 9, 'difference: bank statement - trust journal', NULL, l.statement - l.journal
FROM legs AS l
UNION ALL
SELECT 10, 'difference: trust journal - beneficiary ledgers', NULL, l.journal - l.ledgers
FROM legs AS l
UNION ALL
SELECT 10 + row_number() OVER (ORDER BY ld.holder COLLATE "C", ld.ledger_account_id)::integer,
       'ledger', ld.holder, ld.balance
FROM ledgers AS ld
ORDER BY 1
$$;
