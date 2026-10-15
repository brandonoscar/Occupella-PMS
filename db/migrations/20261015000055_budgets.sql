-- migrate:up
-- Budgets (founder, 2026-10-15, the last of the fourth gap against Buildium): what an owner's
-- property is expected to bring in and cost each month, on the income statement's own lines,
-- and a report that lays the budget beside what happened. A budget moves no money.
--
--   trust_budgets                    a month's budget of one owner's property account for one
--                                    line: an income class (rent, recurring charges, late fees,
--                                    NSF fees, other charges), a fee (management, leasing), an
--                                    expense account, or bills in no expense account. A line's
--                                    latest row is its budget (a change is a new row; the ids
--                                    order them). Append-only.
--   trust_report_budget_vs_actual    an owner's budget against their income statement over days:
--                                    per property and across them, each line's budget (the months
--                                    that start within the days), what happened and the
--                                    difference, then totals and net income.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_budgets (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    month date NOT NULL CHECK (month = date_trunc('month', month)::date),
    line text NOT NULL CHECK (line IN (
        'rent', 'recurring charges', 'late fees', 'NSF fees', 'other charges',
        'management fees', 'leasing fees', 'expense account', 'bills not in an expense account'
    )),
    expense_account_id uuid,
    amount numeric NOT NULL
        CHECK (amount >= 0 AND amount = round(amount, 2) AND amount <= 100000000),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((line = 'expense account') = (expense_account_id IS NOT NULL)),
    FOREIGN KEY (pmc_id, expense_account_id) REFERENCES trust_expense_accounts (pmc_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_budgets_account_month ON trust_budgets (ledger_account_id, month);

-- A budget is of an owner's property account of its own PMC.
CREATE FUNCTION trust_check_budget() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account in PMC %',
            NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_budget_account BEFORE INSERT ON trust_budgets
FOR EACH ROW EXECUTE FUNCTION trust_check_budget();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_budgets
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_budgets
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_budgets FROM PUBLIC;
GRANT SELECT, INSERT ON trust_budgets TO trust_app;
GRANT SELECT ON trust_budgets TO trust_ai_agent;

CREATE FUNCTION trust_report_budget_vs_actual(
    p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date
) RETURNS TABLE (
    line integer, property text, item text, budget numeric, actual numeric,
    actual_less_budget numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    -- The income statement checks the PMC, the owner and the days, and says what is wrong.
    RETURN QUERY
    WITH statement AS (
        SELECT s.line AS n, s.property AS place, s.item AS what, s.amount
        FROM trust_report_income_statement(p_pmc_id, p_owner_id, p_from, p_through) AS s
    ),
    accounts AS (
        SELECT t.ledger_account_id, pr.display_name AS place
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- Each line's budget for each month that starts within the days: its latest row.
    latest AS (
        SELECT DISTINCT ON (b.ledger_account_id, b.month, b.line, b.expense_account_id)
               b.ledger_account_id, b.line AS kind, b.expense_account_id, b.amount
        FROM trust_budgets AS b
        WHERE b.pmc_id = p_pmc_id AND b.month >= p_from AND b.month <= p_through
          AND b.ledger_account_id IN (SELECT a.ledger_account_id FROM accounts AS a)
        ORDER BY b.ledger_account_id, b.month, b.line, b.expense_account_id, b.id DESC
    ),
    budgeted AS (
        SELECT a.place,
               CASE WHEN l.kind IN ('rent', 'recurring charges', 'late fees', 'NSF fees',
                                    'other charges') THEN 'income: ' || l.kind
                    WHEN l.kind = 'expense account' THEN 'expense: ' || e.name
                    ELSE 'expense: ' || l.kind END AS what,
               sum(l.amount) AS amount
        FROM latest AS l
        JOIN accounts AS a ON a.ledger_account_id = l.ledger_account_id
        LEFT JOIN trust_expense_accounts AS e ON e.id = l.expense_account_id
        GROUP BY 1, 2
    ),
    happened AS (
        SELECT s.place, s.what, s.amount FROM statement AS s
        WHERE s.place IS NOT NULL AND (s.what LIKE 'income: %' OR s.what LIKE 'expense: %')
    ),
    items AS (
        SELECT coalesce(h.place, b.place) AS place, coalesce(h.what, b.what) AS what,
               coalesce(b.amount, 0.00) AS budget, coalesce(h.amount, 0.00) AS actual
        FROM happened AS h
        FULL JOIN budgeted AS b ON b.place = h.place AND b.what = h.what
    ),
    -- Income in the statement's order, then the fees, each expense account by name, and bills
    -- in none last.
    sorted AS (
        SELECT i.*,
               CASE i.what
                   WHEN 'income: rent' THEN 1
                   WHEN 'income: recurring charges' THEN 2
                   WHEN 'income: late fees' THEN 3
                   WHEN 'income: NSF fees' THEN 4
                   WHEN 'income: other charges' THEN 5
                   WHEN 'expense: management fees' THEN 11
                   WHEN 'expense: leasing fees' THEN 12
                   WHEN 'expense: bills not in an expense account' THEN 14
                   ELSE 13
               END AS step
        FROM items AS i
    ),
    places AS (
        SELECT DISTINCT s.place FROM sorted AS s
    ),
    lines AS (
        -- The statement's own heading: the PMC, the owner and the days.
        SELECT 0 AS part, NULL::text AS place, s.n AS step, NULL::text AS name,
               s.place AS label, s.what, NULL::numeric AS budget, NULL::numeric AS actual
        FROM statement AS s
        WHERE s.what IN ('PMC', 'owner', 'period')
        UNION ALL
        SELECT 0, NULL, 4, NULL, 'budgets of the months that start within these days',
               'budget', NULL, NULL
        UNION ALL
        SELECT 1, s.place, s.step, s.what, s.place, s.what, s.budget, s.actual
        FROM sorted AS s
        UNION ALL
        SELECT 1, p.place, 6, NULL, p.place, 'total income',
               coalesce(sum(s.budget) FILTER (WHERE s.step < 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step < 10), 0.00)
        FROM places AS p JOIN sorted AS s ON s.place = p.place
        GROUP BY p.place
        UNION ALL
        SELECT 1, p.place, 15, NULL, p.place, 'total expenses',
               coalesce(sum(s.budget) FILTER (WHERE s.step > 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step > 10), 0.00)
        FROM places AS p JOIN sorted AS s ON s.place = p.place
        GROUP BY p.place
        UNION ALL
        SELECT 1, p.place, 16, NULL, p.place, 'net income',
               coalesce(sum(CASE WHEN s.step < 10 THEN s.budget ELSE -s.budget END), 0.00),
               coalesce(sum(CASE WHEN s.step < 10 THEN s.actual ELSE -s.actual END), 0.00)
        FROM places AS p JOIN sorted AS s ON s.place = p.place
        GROUP BY p.place
        UNION ALL
        SELECT 2, NULL, s.step, s.what, NULL, 'all properties: ' || s.what, sum(s.budget),
               sum(s.actual)
        FROM sorted AS s
        GROUP BY s.step, s.what
        UNION ALL
        SELECT 2, NULL, 6, NULL, NULL, 'all properties: total income',
               coalesce(sum(s.budget) FILTER (WHERE s.step < 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step < 10), 0.00)
        FROM sorted AS s
        UNION ALL
        SELECT 2, NULL, 15, NULL, NULL, 'all properties: total expenses',
               coalesce(sum(s.budget) FILTER (WHERE s.step > 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step > 10), 0.00)
        FROM sorted AS s
        UNION ALL
        SELECT 2, NULL, 16, NULL, NULL, 'all properties: net income',
               coalesce(sum(CASE WHEN s.step < 10 THEN s.budget ELSE -s.budget END), 0.00),
               coalesce(sum(CASE WHEN s.step < 10 THEN s.actual ELSE -s.actual END), 0.00)
        FROM sorted AS s
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.step, l.name COLLATE "C"
            ))::integer,
           l.label, l.what, l.budget, l.actual, l.actual - l.budget
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_budget_vs_actual(uuid, uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_budget_vs_actual(uuid, uuid, date, date)
TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'budgets cannot be rolled back: budgets are records';
END;
$$;
