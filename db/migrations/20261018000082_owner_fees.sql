-- migrate:up
-- Owners' fees (the founder's review, q049 to q051; the founder's answer of 2026-10-18 to
-- ADR 0006 decision 4, docs/adr/0006-buildium-punch-list.md):
--
--   trust_management_fee_settings   whether the nightly run takes each month's management fees
--                                   by itself, and from which day of the month (1 to 28): a
--                                   company turns it on in Company setup. Append-only, the
--                                   latest row holding, naming who changed it; none is off.
--   trust_take_monthly_management_fees
--                                   the nightly run's step: once the company's day has come,
--                                   last month's fee of each owner's property account with an
--                                   agreement in force, through trust_post_management_fee (once
--                                   per account and period). An account it can't be taken from
--                                   (the owner's balance short, terms starting mid-month, a
--                                   period overlapping one taken by hand) is left as it is: staff
--                                   see why on Management fees, and the next night tries again.
--   trust_owner_fees                 a fee staff take from an owner's property account besides the
--                                   management and leasing fees (q051): an inspection fee, a
--                                   lease-up fee or another, with what it is for, into the
--                                   company's fee account in the same trust bank account, once
--                                   per request key, through trust_take_owner_fee (staff who hold
--                                   disburse). Money stays in trust until the company takes its
--                                   fees out (migration 0059).
--
-- The income statement and the year-end package count a lease-up fee with the leasing fees
-- (Schedule E line 8) and an inspection or other fee with the management fees (line 11). The
-- owner's statement shows each with what it was for.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_management_fee_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    automatic boolean NOT NULL,
    -- From this day of the month the nightly run takes last month's fees.
    -- squawk-ignore prefer-bigint-over-int
    day_of_month integer NOT NULL CHECK (day_of_month BETWEEN 1 AND 28),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_management_fee_settings_pmc
ON trust_management_fee_settings (pmc_id, created_at);
CREATE INDEX trust_management_fee_settings_recorded_by
ON trust_management_fee_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_management_fee_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_management_fee_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_management_fee_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_management_fee_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_management_fee_settings TO trust_app;
GRANT SELECT ON trust_management_fee_settings TO trust_ai_agent;

-- Last month's management fees, once the company's day has come in the month of p_day; the
-- number of fees taken now. Each account in a savepoint of its own: one that can't be taken is
-- left for staff and the next night, and the others are taken.
CREATE FUNCTION trust_take_monthly_management_fees(p_pmc_id uuid, p_day date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_settings trust_management_fee_settings;
    v_start date := (date_trunc('month', p_day::timestamp) - interval '1 month')::date;
    v_end date := date_trunc('month', p_day::timestamp)::date;
    v_account text;
    v_taken integer := 0;
BEGIN
    IF p_day IS NULL THEN
        RAISE EXCEPTION 'trust: management fees are taken on a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.* INTO v_settings
    FROM trust_management_fee_settings AS s
    WHERE s.pmc_id = p_pmc_id
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1;
    IF NOT FOUND OR NOT v_settings.automatic
       OR extract(day FROM p_day) < v_settings.day_of_month THEN
        RETURN 0;
    END IF;

    FOR v_account IN
        SELECT a.ledger_account_id
        FROM trust_ledger_accounts AS a
        WHERE a.pmc_id = p_pmc_id AND a.kind = 'owner_property'
          AND EXISTS (SELECT 1 FROM trust_management_agreements AS g
                      WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on < v_end)
          AND NOT EXISTS (SELECT 1 FROM trust_management_fees AS f
                          WHERE f.ledger_account_id = a.ledger_account_id
                            AND f.period_start < v_end AND v_start < f.period_end)
        ORDER BY a.ledger_account_id
    LOOP
        BEGIN
            PERFORM trust_post_management_fee(
                p_pmc_id, v_account, v_start, v_end, p_day::timestamp AT TIME ZONE 'UTC'
            );
            v_taken := v_taken + 1;
        EXCEPTION
            WHEN check_violation OR exclusion_violation OR invalid_parameter_value THEN
                -- Left as it is (Management fees says why); on to the next account. A handler
                -- with a statement, so coverage sees it taken.
                CONTINUE;
        END;
    END LOOP;
    RETURN v_taken;
END;
$$;
REVOKE ALL ON FUNCTION trust_take_monthly_management_fees(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_take_monthly_management_fees(uuid, date) TO trust_app;

CREATE TABLE trust_owner_fees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (length(request_key) BETWEEN 1 AND 100),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    kind text NOT NULL CHECK (kind IN ('inspection', 'lease_up', 'other')),
    amount numeric NOT NULL CHECK (amount > 0 AND amount <= 1000000 AND amount = round(amount, 2)),
    memo text NOT NULL CHECK (btrim(memo) <> '' AND length(memo) <= 200),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, request_key),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_owner_fees_account ON trust_owner_fees (ledger_account_id);
CREATE INDEX trust_owner_fees_recorded_by ON trust_owner_fees (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_owner_fees
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_owner_fees
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'take a fee from an owner');
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_fees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_fees FROM PUBLIC;
GRANT SELECT ON trust_owner_fees TO trust_app, trust_ai_agent;

-- A fee from an owner's property account into the company's fee account of its trust bank
-- account, once per request key: the same key again returns the same fee; a different fee under
-- it is refused. The owner's balance must hold it (no account held for someone goes below zero).
CREATE FUNCTION trust_take_owner_fee(
    p_pmc_id uuid,
    p_account text,
    p_kind text,
    p_amount numeric,
    p_memo text,
    p_request_key text,
    p_event_at timestamptz
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_taken trust_owner_fees;
    v_label text;
    v_transfer_id text;
BEGIN
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: a fee from an owner needs a request key, so a retry can''t '
            'take it twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.* INTO v_taken
    FROM trust_owner_fees AS f
    WHERE f.pmc_id = p_pmc_id AND f.request_key = p_request_key;
    IF FOUND THEN
        IF (v_taken.ledger_account_id, v_taken.kind, v_taken.amount, v_taken.memo)
           IS DISTINCT FROM (p_account, p_kind, p_amount, btrim(p_memo)) THEN
            RAISE EXCEPTION 'trust: request key % already took a fee of % from %',
                p_request_key, round(v_taken.amount, 2), v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new fee; resend a key only to retry.';
        END IF;
        RETURN v_taken.transfer_id;  -- a retry: the fee already taken
    END IF;

    v_label := CASE p_kind
        WHEN 'inspection' THEN 'Inspection fee'
        WHEN 'lease_up' THEN 'Lease-up fee'
        WHEN 'other' THEN 'Fee'
    END;
    IF v_label IS NULL THEN
        RAISE EXCEPTION 'trust: a fee from an owner is an inspection, lease-up or other fee, '
            'not %', p_kind
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a fee from an owner is a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(btrim(p_memo), '') = '' THEN
        RAISE EXCEPTION 'trust: a fee from an owner says what it is for'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_transfer_id := trust_post_fee(
        v_account, p_amount, p_event_at, v_label || ': ' || btrim(p_memo)
    );
    INSERT INTO trust_owner_fees (pmc_id, request_key, ledger_account_id, kind, amount, memo,
                                  transfer_id)
    VALUES (p_pmc_id, p_request_key, p_account, p_kind, p_amount, btrim(p_memo), v_transfer_id);
    RETURN v_transfer_id;
END;
$$;
REVOKE ALL ON FUNCTION trust_take_owner_fee(uuid, text, text, numeric, text, text, timestamptz)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_take_owner_fee(uuid, text, text, numeric, text, text, timestamptz)
TO trust_app;

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
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
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

CREATE OR REPLACE FUNCTION trust_report_owner_year_end(
    p_pmc_id uuid,
    p_owner_id uuid,
    p_year integer
) RETURNS TABLE (
    line integer,
    property text,
    item text,
    amount numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: a year-end package is for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH labels AS (
        SELECT v.part, v.label
        FROM (VALUES
            (0, 'opening balance'),
            (1, 'collected from tenants'),
            (2, 'management fees'),
            (3, 'leasing fees'),
            (4, 'bills paid'),
            (5, 'owner draws'),
            (6, 'owner contributions'),
            (7, 'other'),
            (9, 'closing balance')
        ) AS v (part, label)
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What moved each account in the year, by part, signed as it moved the account.
    moved AS (
        -- 1: collected from tenants: payments matched to the leases' charges...
        SELECT tr.to_account_id AS account, 1 AS part, cp.amount AS change
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...less what of them bounced back.
        SELECT tr.from_account_id, 1, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 2, -tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 3, -tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 3 ELSE 2 END, -tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- 4: bills, when their money left the owner's account (set aside to pay them).
        SELECT tr.from_account_id, 4, -tr.amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT d.ledger_account_id, 5, -tr.amount
        FROM trust_owner_draws AS d
        JOIN pgledger_transfers AS tr ON tr.id = d.transfer_id
        WHERE d.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT c.ledger_account_id, 6, tr.amount
        FROM trust_owner_contributions AS c
        JOIN pgledger_transfers AS tr ON tr.id = c.transfer_id
        WHERE c.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    balances AS (
        SELECT a.account, a.name,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_start), 0) AS opening,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_end), 0) AS closing
        FROM accounts AS a
        LEFT JOIN pgledger_entries AS e ON e.account_id = a.account
        LEFT JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        GROUP BY a.account, a.name
    ),
    parts AS (
        SELECT b.account, b.name, p.part, p.label,
               CASE p.part
                   WHEN 0 THEN b.opening
                   WHEN 9 THEN b.closing
                   WHEN 7 THEN b.closing - b.opening - coalesce(
                       (SELECT sum(m.change) FROM moved AS m WHERE m.account = b.account), 0)
                   ELSE coalesce(
                       (SELECT sum(m.change) FROM moved AS m
                        WHERE m.account = b.account AND m.part = p.part), 0)
               END AS total
        FROM balances AS b
        CROSS JOIN labels AS p
    ),
    lines AS (
        -- (section, property, account, part) orders the package; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS part,
               'PMC' AS item, pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'year',
               p_year::text || ', January 1 to December 31 UTC', NULL
        UNION ALL
        SELECT 1, p.name, p.account, p.part, p.label, p.name, p.total
        FROM parts AS p
        UNION ALL
        SELECT 2, NULL, NULL, x.part, 'all properties: ' || x.label, NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = x.part), 0)
        FROM labels AS x
        UNION ALL
        SELECT 3, NULL, NULL, 0, '1099-MISC rents (box 1)', NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = 1), 0)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.part
           )::integer,
           -- The PMC's, owner's and year's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'owner_fees cannot be rolled back: fees taken and settings are records';
END;
$$;
