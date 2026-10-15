-- migrate:up
-- Expense categories and an income statement (founder, 2026-10-15: "do the ones that don't
-- require me"; the first gap against Buildium: owners' accountants need expenses by category
-- for Schedule E, and a profit and loss per property).
--
--   trust_expense_accounts                 a PMC's expense accounts (Repairs, Landscaping,
--                                          Water), each on the line of Schedule E (Form 1040)
--                                          it is reported on: 5 to 19.
--   trust_bill_expense_accounts            which expense account a bill is. The latest row of a
--                                          bill is its account: a bill entered before this, or
--                                          one put in the wrong account, takes a new row, and
--                                          nothing is rewritten.
--   trust_recurring_bill_expense_accounts  the same for a recurring bill; each month's bill it
--                                          enters takes its account.
--   trust_enter_recurring_bills            now also gives each bill it enters its recurring
--                                          bill's expense account.
--   trust_report_income_statement          an owner's income statement over days (UTC), per
--                                          property account and across them, on a cash basis:
--                                          what was collected from tenants (net of bounces) by
--                                          what it paid, the management and leasing fees, the
--                                          bills set aside by expense account, net income, and
--                                          the same by Schedule E line.
--
-- An expense account describes money that already moved; it moves nothing. Records: kept for
-- good, as every bill is.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_expense_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    name text NOT NULL CHECK (name ~ '\S' AND length(name) <= 200),
    -- Schedule E (Form 1040), Part I: 5 advertising through 19 other.
    -- squawk-ignore prefer-bigint-over-int
    schedule_e_line integer NOT NULL CHECK (schedule_e_line BETWEEN 5 AND 19),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, name)
);

CREATE TABLE trust_bill_expense_accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bill_id uuid NOT NULL,
    expense_account_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, expense_account_id) REFERENCES trust_expense_accounts (pmc_id, id)
);
CREATE INDEX trust_bill_expense_accounts_bill ON trust_bill_expense_accounts (bill_id, id);

CREATE TABLE trust_recurring_bill_expense_accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    recurring_bill_id uuid NOT NULL,
    expense_account_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, recurring_bill_id) REFERENCES trust_recurring_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, expense_account_id) REFERENCES trust_expense_accounts (pmc_id, id)
);
CREATE INDEX trust_recurring_bill_expense_accounts_bill
ON trust_recurring_bill_expense_accounts (recurring_bill_id, id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_expense_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bill_expense_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_bill_expense_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bill_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE
ON trust_recurring_bill_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_expense_accounts, trust_bill_expense_accounts,
    trust_recurring_bill_expense_accounts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_expense_accounts, trust_bill_expense_accounts,
    trust_recurring_bill_expense_accounts TO trust_app;
-- What a bill was for: the AI reads bills, so it reads what kind of expense each is.
GRANT SELECT ON trust_expense_accounts, trust_bill_expense_accounts,
    trust_recurring_bill_expense_accounts TO trust_ai_agent;

-- Each month's bill of a recurring bill, as before (migration 0027), and now in its recurring
-- bill's expense account, if it has one.
CREATE OR REPLACE FUNCTION trust_enter_recurring_bills(p_pmc_id uuid, p_through date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_entered integer;
BEGIN
    IF p_through IS NULL THEN
        RAISE EXCEPTION 'trust: enter recurring bills through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    WITH due AS (
        SELECT r.id AS recurring_bill_id, r.pmc_id, r.vendor_id, r.ledger_account_id,
               r.reference_prefix || '-' || to_char(d.due_on, 'YYYY-MM') AS reference,
               d.due_on, r.amount, r.memo
        FROM trust_recurring_bills AS r
        LEFT JOIN trust_recurring_bill_ends AS e ON e.recurring_bill_id = r.id
        CROSS JOIN LATERAL generate_series(
            date_trunc('month', r.starts_on), p_through::timestamp, interval '1 month'
        ) AS m (month)
        CROSS JOIN LATERAL (SELECT (m.month::date + r.day_of_month - 1) AS due_on) AS d
        WHERE r.pmc_id = p_pmc_id
          AND d.due_on >= r.starts_on
          AND d.due_on <= p_through
          AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
    ),
    entered AS (
        INSERT INTO trust_bills (
            pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
        )
        SELECT d.pmc_id, d.vendor_id, d.ledger_account_id, d.reference, d.due_on, d.due_on,
               d.amount, d.memo
        FROM due AS d
        ORDER BY d.recurring_bill_id, d.due_on
        ON CONFLICT (vendor_id, reference) DO NOTHING
        RETURNING id, pmc_id, vendor_id, reference
    ),
    categorized AS (
        INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
        SELECT b.pmc_id, b.id, c.expense_account_id
        FROM entered AS b
        JOIN due AS d ON d.vendor_id = b.vendor_id AND d.reference = b.reference
        CROSS JOIN LATERAL (
            SELECT x.expense_account_id
            FROM trust_recurring_bill_expense_accounts AS x
            WHERE x.recurring_bill_id = d.recurring_bill_id
            ORDER BY x.id DESC
            LIMIT 1
        ) AS c
        RETURNING bill_id
    )
    SELECT count(*)::integer INTO v_entered FROM entered;
    RETURN v_entered;
END;
$$;

CREATE FUNCTION trust_report_income_statement(
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
    -- NSF fee, or another charge. A lease end's adjustment of a rent or recurring charge
    -- (migration 0051) is what it adjusts.
    classes AS (
        SELECT c.id,
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
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Collected from tenants in the days: payments matched to charges, less what bounced back,
    -- as the year-end package counts them (migration 0039).
    collected AS (
        SELECT tr.to_account_id AS account, k.part, cp.amount
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        JOIN classes AS k ON k.id = cp.charge_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, k.part, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        JOIN classes AS k ON k.id = r.charge_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
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
    ),
    -- Each bill's expense account: its latest.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills, when their money left the owner's account (set aside to pay them).
    spent AS (
        SELECT tr.from_account_id AS account, cz.expense_account_id AS expense, tr.amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        LEFT JOIN categorized AS cz ON cz.bill_id = b.bill_id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM collected AS c
                         WHERE c.account = a.account AND c.part = i.part), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
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

REVOKE ALL ON FUNCTION trust_report_income_statement(uuid, uuid, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_income_statement(uuid, uuid, date, date)
TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'expense accounts cannot be rolled back: what a bill was for is a record';
END;
$$;
