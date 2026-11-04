-- migrate:up
-- A bill's shortfall keeps the reserve (the founder's review of 11/03, item 3): trust_bill_holds
-- lays each owner's property account's balance less the reserve its agreement in force today
-- keeps (none without one) over its open bills, the one due first first, as before. So what a
-- bill is short of is its amount (and markup) less what is left above the reserve once the
-- bills due before it are held: RF-3006's $24,000.00 against $3,290.00 with a $250.00 reserve is
-- $20,960.00 short, not $20,710.00. The owner portal, the staff's Bills (a short bill isn't paid
-- until the owner sends the money) and the email asking for it all read it here. No money moves.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_bill_holds(p_pmc_id uuid) RETURNS TABLE(bill_id uuid, held numeric, short numeric, needed numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH open AS (
        SELECT b.id, b.ledger_account_id, b.due_on, b.bill_date, b.reference,
               b.amount + coalesce(m.amount, 0) AS need
        FROM trust_bills AS b
        LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
        WHERE b.pmc_id = p_pmc_id
          AND b.bill_date <= (now() AT TIME ZONE 'UTC')::date
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                          WHERE p.bill_id = b.id AND p.step = 'set_aside')
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = b.id)
    ),
    running AS (
        -- What the account holds above the reserve its agreement in force today keeps: the
        -- reserve stays in the account, so a bill is never laid over it.
        SELECT o.id, o.need,
               greatest(a.balance - coalesce((
                   SELECT g.reserve FROM trust_management_agreements AS g
                   WHERE g.ledger_account_id = o.ledger_account_id
                     AND g.starts_on <= (now() AT TIME ZONE 'UTC')::date
                   ORDER BY g.starts_on DESC LIMIT 1), 0), 0) AS balance,
               sum(o.need) OVER (
                   PARTITION BY o.ledger_account_id
                   ORDER BY o.due_on, o.bill_date, o.reference COLLATE "C", o.id
               ) AS through
        FROM open AS o
        JOIN pgledger_accounts AS a ON a.id = o.ledger_account_id
    ),
    laid AS (
        SELECT r.id, r.need, r.through, r.balance,
               least(r.need, greatest(r.balance - (r.through - r.need), 0)) AS held
        FROM running AS r
    )
    SELECT l.id, l.held, l.need - l.held, greatest(l.through - l.balance, 0)
    FROM laid AS l
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_bill_holds(p_pmc_id uuid) RETURNS TABLE(bill_id uuid, held numeric, short numeric, needed numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH open AS (
        SELECT b.id, b.ledger_account_id, b.due_on, b.bill_date, b.reference,
               b.amount + coalesce(m.amount, 0) AS need
        FROM trust_bills AS b
        LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
        WHERE b.pmc_id = p_pmc_id
          AND b.bill_date <= (now() AT TIME ZONE 'UTC')::date
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                          WHERE p.bill_id = b.id AND p.step = 'set_aside')
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = b.id)
    ),
    running AS (
        SELECT o.id, o.need, greatest(a.balance, 0) AS balance,
               sum(o.need) OVER (
                   PARTITION BY o.ledger_account_id
                   ORDER BY o.due_on, o.bill_date, o.reference COLLATE "C", o.id
               ) AS through
        FROM open AS o
        JOIN pgledger_accounts AS a ON a.id = o.ledger_account_id
    ),
    laid AS (
        SELECT r.id, r.need, r.through, r.balance,
               least(r.need, greatest(r.balance - (r.through - r.need), 0)) AS held
        FROM running AS r
    )
    SELECT l.id, l.held, l.need - l.held, greatest(l.through - l.balance, 0)
    FROM laid AS l
$$;
