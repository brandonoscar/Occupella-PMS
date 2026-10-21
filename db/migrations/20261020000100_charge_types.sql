-- migrate:up
-- Charge types (the founder's review q133): the company's own kinds of fee a lease is charged
-- (an application fee, a key replaced, cleaning, pet rent, parking), each counted on a line of
-- its own in the income statement instead of with the other charges or the recurring charges.
--
--   trust_charge_types   the PMC's types, a name once per PMC whatever its case and none of the
--                        statement's own income lines; append-only, naming who added it.
--   charge_type_id       on trust_charges and trust_recurring_charges: a fee's type, if it has
--                        one, of the fee's PMC; a charge the rent run writes for a recurring
--                        charge (or a lease end's adjustment of one) counts under that one's.
--
-- A type decides nothing and moves no money; like expense accounts, the AI role reads it. The
-- budget beside the statement shows a type's line after the other income, with no budget.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_charge_types (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    name text NOT NULL CHECK (
        name = btrim(name) AND name ~ '\S' AND char_length(name) <= 100
        AND lower(name) NOT IN (
            'rent', 'recurring charges', 'late fees', 'nsf fees', 'other charges'
        )
    ),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE UNIQUE INDEX trust_charge_types_name ON trust_charge_types (pmc_id, lower(name));
CREATE INDEX trust_charge_types_recorded_by ON trust_charge_types (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_charge_types
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charge_types
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_charge_types
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_charge_types FROM PUBLIC;
GRANT SELECT, INSERT ON trust_charge_types TO trust_app;
GRANT SELECT ON trust_charge_types TO trust_ai_agent;

-- Every charge so far has no type, so each holds the checks; added NOT VALID, checked for every
-- new charge.
ALTER TABLE trust_charges ADD COLUMN charge_type_id uuid;
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_charge_type_fee
CHECK (charge_type_id IS NULL OR kind = 'fee') NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_charges ADD CONSTRAINT trust_charges_pmc_id_charge_type_id_fkey
FOREIGN KEY (pmc_id, charge_type_id) REFERENCES trust_charge_types (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_charges_charge_type_id ON trust_charges (charge_type_id);

ALTER TABLE trust_recurring_charges ADD COLUMN charge_type_id uuid;
ALTER TABLE trust_recurring_charges ADD CONSTRAINT trust_recurring_charges_charge_type_fee
CHECK (charge_type_id IS NULL OR kind = 'fee') NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_recurring_charges ADD CONSTRAINT trust_recurring_charges_pmc_id_charge_type_fkey
FOREIGN KEY (pmc_id, charge_type_id) REFERENCES trust_charge_types (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_recurring_charges_charge_type_id ON trust_recurring_charges (charge_type_id);

-- The income statement: each charge type on a line of its own.
CREATE OR REPLACE FUNCTION trust_report_income_statement(
    p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date
) RETURNS TABLE (line integer, property text, item text, schedule_e integer, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: an income statement runs from a day through a day no earlier, not % to %',
            p_from, p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := p_from::timestamp AT TIME ZONE 'UTC';
    v_end := (p_through + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH schedule_e (n, label) AS (
        VALUES (3, 'Rents received'), (5, 'Advertising'), (6, 'Auto and travel'),
               (7, 'Cleaning and maintenance'), (8, 'Commissions'), (9, 'Insurance'),
               (10, 'Legal and other professional fees'), (11, 'Management fees'),
               (12, 'Mortgage interest paid to banks, etc.'), (13, 'Other interest'),
               (14, 'Repairs'), (15, 'Supplies'), (16, 'Taxes'), (17, 'Utilities'),
               (18, 'Depreciation expense or depletion'), (19, 'Other')
    ),
    incomes (part, label) AS (
        VALUES (1, 'income: rent'), (2, 'income: recurring charges'), (3, 'income: late fees'),
               (4, 'income: NSF fees'), (5, 'income: other charges')
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge's money is: rent, a recurring charge (pet rent, parking), a late fee, an
    -- NSF fee, or another charge, and the company's own type of it if it has one (its own, or
    -- its recurring charge's). A lease end's adjustment of a rent or recurring charge
    -- (migration 0051) is what it adjusts.
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = adj.charge_id
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = c.id
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = c.id
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Collected from tenants in the days: payments matched to charges, less what bounced back,
    -- as the year-end package counts them (migration 0039).
    collected AS (
        SELECT tr.to_account_id AS account, k.part, k.charge_type, cp.amount
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        JOIN classes AS k ON k.id = cp.charge_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, k.part, k.charge_type, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        JOIN classes AS k ON k.id = r.charge_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A housing authority's part of the rent, as it paid it (migration 0095): rent.
        SELECT tr.to_account_id, 1, NULL::uuid, ap.amount
        FROM trust_assistance_payments AS ap
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    fees AS (
        SELECT f.ledger_account_id AS account, 0 AS ord, tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 1, tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees.
        SELECT tr.from_account_id, 0, tr.amount
        FROM trust_bill_markup_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills, when their money left the owner's account (set aside to pay them): a split bill
    -- (migration 0069) by its lines, each in its expense account, which come to the bill.
    spent AS (
        SELECT tr.from_account_id AS account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, tr.amount) AS amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        LEFT JOIN categorized AS cz ON cz.bill_id = b.bill_id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM collected AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        SELECT c.account, 1, 6, 'income: ' || ct.name, 3, sum(c.amount)
        FROM collected AS c
        JOIN accounts AS a ON a.account = c.account
        JOIN trust_charge_types AS ct ON ct.id = c.charge_type
        GROUP BY c.account, ct.id, ct.name
        UNION ALL
        SELECT a.account, 3, f.ord,
               CASE f.ord WHEN 0 THEN 'expense: management fees' ELSE 'expense: leasing fees' END,
               CASE f.ord WHEN 0 THEN 11 ELSE 8 END,
               coalesce((SELECT sum(x.amount) FROM fees AS x
                         WHERE x.account = a.account AND x.ord = f.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (0), (1)) AS f (ord)
        UNION ALL
        SELECT s.account, 3, coalesce(ea.schedule_e_line, 99),
               CASE WHEN ea.id IS NULL THEN 'expense: bills not in an expense account'
                    ELSE 'expense: ' || ea.name END,
               ea.schedule_e_line,
               sum(s.amount)
        FROM spent AS s
        JOIN accounts AS a ON a.account = s.account
        LEFT JOIN trust_expense_accounts AS ea ON ea.id = s.expense
        GROUP BY s.account, ea.id, ea.name, ea.schedule_e_line
    ),
    sums AS (
        SELECT i.account,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 1), 0) AS income,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 3), 0) AS expenses
        FROM items AS i
        GROUP BY i.account
    ),
    -- The same by Schedule E line (group 6): rents received, and each expense line with money
    -- on it; bills in no expense account are on no line until they are given one.
    schedule AS (
        SELECT i.account, 6 AS grp, i.sched AS ord,
               'Schedule E line ' || i.sched || ': ' || e.label AS item, i.sched,
               sum(i.total) AS total
        FROM items AS i
        JOIN schedule_e AS e ON e.n = i.sched
        GROUP BY i.account, i.sched, e.label
        HAVING i.sched = 3 OR sum(i.total) <> 0
        UNION ALL
        SELECT i.account, 6, 99, 'Schedule E: on no line, bills not in an expense account',
               NULL::integer, sum(i.total)
        FROM items AS i
        WHERE i.grp = 3 AND i.sched IS NULL
        GROUP BY i.account
    ),
    rows AS (
        SELECT i.account, i.grp, i.ord, i.item, i.sched, i.total FROM items AS i
        UNION ALL
        SELECT s.account, 2, 0, 'total income', NULL::integer, s.income FROM sums AS s
        UNION ALL
        SELECT s.account, 4, 0, 'total expenses', NULL::integer, s.expenses FROM sums AS s
        UNION ALL
        SELECT s.account, 5, 0, 'net income', NULL::integer, s.income - s.expenses
        FROM sums AS s
        UNION ALL
        SELECT h.account, h.grp, h.ord, h.item, h.sched, h.total FROM schedule AS h
    ),
    lines AS (
        -- (section, property, account, group, order, item) orders the statement; names sort
        -- by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 0 AS grp, 1 AS ord,
               'PMC' AS item, pmc.display_name AS label, NULL::integer AS sched,
               NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 2, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 3, 'period',
               p_from::text || ' to ' || p_through::text || ' UTC, cash basis', NULL, NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.grp, r.ord, r.item, a.name, r.sched, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.grp, r.ord, 'all properties: ' || r.item, NULL, r.sched,
               sum(r.total)
        FROM rows AS r
        GROUP BY r.grp, r.ord, r.item, r.sched
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.grp, l.ord,
                        l.item COLLATE "C"
           )::integer,
           -- The PMC's, owner's and period's names sit in the property column of the first
           -- lines.
           l.label,
           l.item,
           l.sched,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- Budget beside the statement: a charge type's line is income.
CREATE OR REPLACE FUNCTION trust_report_budget_vs_actual(
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
                   -- One of the company's charge types, after the other income.
                   ELSE CASE WHEN i.what LIKE 'income: %' THEN 6 ELSE 13 END
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

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'charge_types cannot be rolled back: the type of each charge is a record';
END;
$$;
