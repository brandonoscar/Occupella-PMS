-- migrate:up
-- Deposit interest on the income statements (the q041 follow-up): interest added to a tenant's
-- deposit (migration 0122) leaves the owner's property account for the deposit, which the cash
-- and accrual income statements counted nowhere, so their net income was the interest too high.
-- Both now list it as an expense of its own, 'expense: security deposit interest', on Schedule E's
-- line 19 (Other), when it moved and once some was paid; their other lines are as they were (as
-- migration 0116 left them). Interest paid as a credit against the rent moves no money and stays
-- less rent, as a credit is. Read only; the budget beside the statement shows the line as an
-- expense with no budget.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION public.trust_report_income_statement(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date)
 RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
    -- No owner: every owner of the PMC (q108).
    IF p_owner_id IS NOT NULL AND NOT EXISTS (
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
        -- An owner's property accounts by their property; every owner's, each named by its
        -- property and owner, when no owner is given.
        SELECT t.ledger_account_id AS account,
               CASE WHEN p_owner_id IS NULL THEN pr.display_name || ', ' || o.display_name
                    ELSE pr.display_name END AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        JOIN trust_owners AS o ON o.id = t.owner_id
        WHERE t.pmc_id = p_pmc_id AND (p_owner_id IS NULL OR t.owner_id = p_owner_id)
          AND t.kind = 'owner_property'
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
        -- A housing authority's part of the rent, as it paid it (migration 0095): rent; its late
        -- fee a late fee; less what bounced back (migration 0116).
        SELECT tr.to_account_id, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END, NULL::uuid,
               ap.amount
        FROM trust_assistance_payments AS ap
        JOIN trust_assistance_charges AS ac ON ac.id = ap.assistance_charge_id
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END, NULL::uuid,
               -ar.amount
        FROM trust_assistance_payment_reversals AS ar
        JOIN trust_assistance_charges AS ac ON ac.id = ar.assistance_charge_id
        JOIN pgledger_transfers AS tr ON tr.id = ar.reversal_id
        WHERE ar.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
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
    -- Interest on tenants' deposits the owner's account paid into them (migration 0122), when it
    -- moved; interest paid as a rent credit moves no money and is less rent, as a credit is.
    interest AS (
        SELECT l.owner_account AS account, l.amount
        FROM trust_deposit_interest_lines AS l
        JOIN pgledger_transfers AS tr ON tr.id = l.transfer_id
        WHERE l.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
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
        UNION ALL
        -- Deposit interest, on Schedule E's line 19 (Other), once some was paid.
        SELECT d.account, 3, 19, 'expense: security deposit interest', 19, sum(d.amount)
        FROM interest AS d
        JOIN accounts AS a ON a.account = d.account
        GROUP BY d.account
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
        SELECT 0, NULL, NULL, 0, 2, 'owner', coalesce(o.display_name, 'all owners'), NULL, NULL
        FROM (SELECT 1) AS one
        LEFT JOIN trust_owners AS o ON o.id = p_owner_id
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
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date)
 RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge is: rent, a recurring charge (pet rent, parking), a late fee, an NSF
    -- fee, or another charge, and the company's own type of it if it has one (its own, or its
    -- recurring charge's), as the cash statement has it. A lease end's adjustment of a rent or
    -- recurring charge (migration 0051), and a waiver (migration 0079), are what they change. A
    -- credit given on its own comes off rent, as credits come off a lease's rent first
    -- (migration 0093).
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   WHEN coalesce(o.kind, c.kind) = 'credit' THEN 1
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charge_waivers AS w ON w.credit_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = coalesce(adj.charge_id, w.waived_charge_id)
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Each account's share of its property on a day: the account's percent of the ownership in
    -- force (migration 0090), all of it when the property has none.
    -- Charged in the days (accrual basis): each charge due in them of a lease at the account's
    -- property (a credit as less), and the housing authority's part of the rent and its
    -- adjustments (migration 0095) as rent, each at the account's share on its due day, to the
    -- cent.
    owed AS (
        SELECT l.id AS lease, u.property_id, c.id AS charge, NULL::integer AS part, c.due_on,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS amount
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE c.pmc_id = p_pmc_id AND c.due_on BETWEEN p_from AND p_through
        UNION ALL
        -- An authority's late fee (migration 0116) is a late fee.
        SELECT l.id, u.property_id, NULL, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END,
               ac.due_on, CASE ac.kind WHEN 'credit' THEN -ac.amount ELSE ac.amount END
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.due_on BETWEEN p_from AND p_through
    ),
    charged AS (
        SELECT a.account, coalesce(x.part, k.part) AS part, k.charge_type,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        LEFT JOIN classes AS k ON k.id = x.charge
    ),
    -- The owner's bills dated in the days, but those the owner declined by their end
    -- (migration 0041): one is never paid.
    entered AS (
        SELECT b.id, b.ledger_account_id AS account, b.amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date BETWEEN p_from AND p_through
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_through)
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
        -- A bill's markup (migration 0083), with the management fees, with its bill.
        SELECT b.account, 0, m.amount
        FROM trust_bill_markups AS m
        JOIN entered AS b ON b.id = m.bill_id
    ),
    -- Interest on tenants' deposits the owner's account paid into them (migration 0122), when it
    -- moved; interest paid as a rent credit moves no money and is less rent, as a credit is.
    interest AS (
        SELECT l.owner_account AS account, l.amount
        FROM trust_deposit_interest_lines AS l
        JOIN pgledger_transfers AS tr ON tr.id = l.transfer_id
        WHERE l.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills dated in the days (accrual basis): a split bill (migration 0069) by its lines,
    -- each in its expense account, which come to the bill.
    spent AS (
        SELECT b.account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, b.amount) AS amount
        FROM entered AS b
        LEFT JOIN categorized AS cz ON cz.bill_id = b.id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM charged AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        SELECT c.account, 1, 6, 'income: ' || ct.name, 3, sum(c.amount)
        FROM charged AS c
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
        UNION ALL
        -- Deposit interest, on Schedule E's line 19 (Other), once some was paid.
        SELECT d.account, 3, 19, 'expense: security deposit interest', 19, sum(d.amount)
        FROM interest AS d
        JOIN accounts AS a ON a.account = d.account
        GROUP BY d.account
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
               p_from::text || ' to ' || p_through::text || ' UTC, accrual basis', NULL, NULL
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
$function$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION public.trust_report_income_statement(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date)
 RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
    -- No owner: every owner of the PMC (q108).
    IF p_owner_id IS NOT NULL AND NOT EXISTS (
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
        -- An owner's property accounts by their property; every owner's, each named by its
        -- property and owner, when no owner is given.
        SELECT t.ledger_account_id AS account,
               CASE WHEN p_owner_id IS NULL THEN pr.display_name || ', ' || o.display_name
                    ELSE pr.display_name END AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        JOIN trust_owners AS o ON o.id = t.owner_id
        WHERE t.pmc_id = p_pmc_id AND (p_owner_id IS NULL OR t.owner_id = p_owner_id)
          AND t.kind = 'owner_property'
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
        -- A housing authority's part of the rent, as it paid it (migration 0095): rent; its late
        -- fee a late fee; less what bounced back (migration 0116).
        SELECT tr.to_account_id, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END, NULL::uuid,
               ap.amount
        FROM trust_assistance_payments AS ap
        JOIN trust_assistance_charges AS ac ON ac.id = ap.assistance_charge_id
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END, NULL::uuid,
               -ar.amount
        FROM trust_assistance_payment_reversals AS ar
        JOIN trust_assistance_charges AS ac ON ac.id = ar.assistance_charge_id
        JOIN pgledger_transfers AS tr ON tr.id = ar.reversal_id
        WHERE ar.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
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
        SELECT 0, NULL, NULL, 0, 2, 'owner', coalesce(o.display_name, 'all owners'), NULL, NULL
        FROM (SELECT 1) AS one
        LEFT JOIN trust_owners AS o ON o.id = p_owner_id
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
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date)
 RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge is: rent, a recurring charge (pet rent, parking), a late fee, an NSF
    -- fee, or another charge, and the company's own type of it if it has one (its own, or its
    -- recurring charge's), as the cash statement has it. A lease end's adjustment of a rent or
    -- recurring charge (migration 0051), and a waiver (migration 0079), are what they change. A
    -- credit given on its own comes off rent, as credits come off a lease's rent first
    -- (migration 0093).
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   WHEN coalesce(o.kind, c.kind) = 'credit' THEN 1
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charge_waivers AS w ON w.credit_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = coalesce(adj.charge_id, w.waived_charge_id)
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Each account's share of its property on a day: the account's percent of the ownership in
    -- force (migration 0090), all of it when the property has none.
    -- Charged in the days (accrual basis): each charge due in them of a lease at the account's
    -- property (a credit as less), and the housing authority's part of the rent and its
    -- adjustments (migration 0095) as rent, each at the account's share on its due day, to the
    -- cent.
    owed AS (
        SELECT l.id AS lease, u.property_id, c.id AS charge, NULL::integer AS part, c.due_on,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS amount
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE c.pmc_id = p_pmc_id AND c.due_on BETWEEN p_from AND p_through
        UNION ALL
        -- An authority's late fee (migration 0116) is a late fee.
        SELECT l.id, u.property_id, NULL, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END,
               ac.due_on, CASE ac.kind WHEN 'credit' THEN -ac.amount ELSE ac.amount END
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.due_on BETWEEN p_from AND p_through
    ),
    charged AS (
        SELECT a.account, coalesce(x.part, k.part) AS part, k.charge_type,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        LEFT JOIN classes AS k ON k.id = x.charge
    ),
    -- The owner's bills dated in the days, but those the owner declined by their end
    -- (migration 0041): one is never paid.
    entered AS (
        SELECT b.id, b.ledger_account_id AS account, b.amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date BETWEEN p_from AND p_through
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_through)
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
        -- A bill's markup (migration 0083), with the management fees, with its bill.
        SELECT b.account, 0, m.amount
        FROM trust_bill_markups AS m
        JOIN entered AS b ON b.id = m.bill_id
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills dated in the days (accrual basis): a split bill (migration 0069) by its lines,
    -- each in its expense account, which come to the bill.
    spent AS (
        SELECT b.account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, b.amount) AS amount
        FROM entered AS b
        LEFT JOIN categorized AS cz ON cz.bill_id = b.id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM charged AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        SELECT c.account, 1, 6, 'income: ' || ct.name, 3, sum(c.amount)
        FROM charged AS c
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
               p_from::text || ' to ' || p_through::text || ' UTC, accrual basis', NULL, NULL
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
$function$;
