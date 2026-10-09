-- migrate:up
-- The three-way reconciliation report lists its ledger lines in byte order (COLLATE "C"), as the
-- owner statement does, so every server prints them in the same order. Until now the order
-- followed the database's collation: a server that sorts by language rules put "owner a" before
-- "Owner b", and a C-locale server put it after. Only that ORDER BY changes; replacing the
-- function keeps its grants.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

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

-- migrate:down
-- The report as 20261009000009 created it.
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
SELECT 10 + row_number() OVER (ORDER BY ld.holder, ld.ledger_account_id)::integer,
       'ledger', ld.holder, ld.balance
FROM ledgers AS ld
ORDER BY 1
$$;
