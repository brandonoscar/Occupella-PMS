-- migrate:up
-- What a bill holds, by the company's day (docs/edge-cases.md M-097, D-098, D-099). trust_bill_holds
-- counted the bills dated by UTC's today and the reserve of the agreement in force on it: from 8 pm
-- Eastern, a bill dated the company's tomorrow already held the owner's money and read Ready to
-- pay or Short hours early, and an agreement starting tomorrow already kept its reserve. It now
-- counts both by the company's day (trust_pmc_day, migration 20261104000900), at a moment that is
-- now unless a caller names one (p_at, for the tests of an evening). No money moves.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_bill_holds(uuid);

CREATE FUNCTION trust_bill_holds(p_pmc_id uuid, p_at timestamptz DEFAULT now())
RETURNS TABLE(bill_id uuid, held numeric, short numeric, needed numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH today AS (
        SELECT trust_pmc_day(p_pmc_id, p_at) AS day
    ),
    open AS (
        SELECT b.id, b.ledger_account_id, b.due_on, b.bill_date, b.reference,
               b.amount + coalesce(m.amount, 0) AS need
        FROM trust_bills AS b
        LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
        WHERE b.pmc_id = p_pmc_id
          AND b.bill_date <= (SELECT t.day FROM today AS t)
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                          WHERE p.bill_id = b.id AND p.step = 'set_aside')
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = b.id)
    ),
    running AS (
        -- What the account holds above the reserve its agreement in force on the company's day
        -- keeps: the reserve stays in the account, so a bill is never laid over it.
        SELECT o.id, o.need,
               greatest(a.balance - coalesce((
                   SELECT g.reserve FROM trust_management_agreements AS g
                   WHERE g.ledger_account_id = o.ledger_account_id
                     AND g.starts_on <= (SELECT t.day FROM today AS t)
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
REVOKE ALL ON FUNCTION trust_bill_holds(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_bill_holds(uuid, timestamptz) TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_bill_holds(uuid, timestamptz);

CREATE FUNCTION trust_bill_holds(p_pmc_id uuid)
RETURNS TABLE(bill_id uuid, held numeric, short numeric, needed numeric)
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
REVOKE ALL ON FUNCTION trust_bill_holds(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_bill_holds(uuid) TO trust_app;
