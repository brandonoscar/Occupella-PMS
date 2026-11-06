-- migrate:up
-- The chart's second-pass lines (the founder's chart of accounts pass, part 2, 2026-11-06), and the
-- owner's statement's memo lines.
--
--   trust_chart_lines()        three lines more, on the codes kept for them since the first pass:
--                              2150 owner-held deposits (a memo: the owner holds them, never in
--                              trust; migration 20261106000241), 2250 unapplied cash (migration
--                              20261106000240) and 4010 concessions, a contra to rent (migration
--                              20261106000242). No code is kept for later any more
--                              (trust_reserved_account_code names none), so
--                              trust_check_account_code no longer asks.
--   charge type names          a new kind of fee isn't named "Concessions", an income statement
--                              line's own name now; one a company named so before keeps its name.
--   trust_report_owner_statement  a posting from unapplied cash names it ("unapplied_cash:
--                              received, not yet applied"); after each property's closing balance,
--                              memo lines that change no balance: the concessions given in the
--                              period at its leases, and each deposit the owner holds at the
--                              period's end.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The codes' lines, now with the second pass's. Every existing row names a line of the old list,
-- which the new one holds.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_account_codes ADD CONSTRAINT trust_account_codes_line_v2 CHECK (
    line IN (
        'bank_cash', 'owner_property', 'tenant_deposit', 'pet_deposit', 'key_deposit',
        'owner_held_deposits', 'prepaid_rent', 'unapplied_cash', 'vendor_payable', 'pmc_income',
        'rent', 'concessions', 'recurring_charges', 'late_fees', 'nsf_fees', 'other_charges',
        'unassigned_bills', 'leasing_fees', 'management_fees', 'deposit_interest'
    )
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_account_codes DROP CONSTRAINT trust_account_codes_line_check;

-- A kind of fee named as the income statement's concessions line would read as it.
ALTER TABLE trust_charge_types ADD CONSTRAINT trust_charge_types_not_concessions
CHECK (lower(name) <> 'concessions') NOT VALID;
ALTER TABLE trust_charge_type_names ADD CONSTRAINT trust_charge_type_names_not_concessions
CHECK (lower(name) <> 'concessions') NOT VALID;

CREATE OR REPLACE FUNCTION trust_chart_lines() RETURNS TABLE(line text, section text, name text, account_type text, code text, schedule_e integer)
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    VALUES
        ('bank_cash', 'asset', 'Trust bank cash', 'Bank cash', '1000', NULL::integer),
        ('owner_property', 'liability', 'Owner funds', 'Owner funds', '2000', NULL),
        ('tenant_deposit', 'liability', 'Tenant security deposits', 'Tenant deposits', '2100',
         NULL),
        ('pet_deposit', 'liability', 'Tenant pet deposits', 'Tenant deposits', '2110', NULL),
        ('key_deposit', 'liability', 'Tenant key deposits', 'Tenant deposits', '2120', NULL),
        -- Held by owners outside trust: a memo, in no trust balance (migration 20261106000241).
        ('owner_held_deposits', 'liability', 'Owner-held deposits (memo)', 'Memo, not in trust',
         '2150', NULL),
        ('prepaid_rent', 'liability', 'Prepaid rent', 'Prepaid rent', '2200', NULL),
        ('unapplied_cash', 'liability', 'Unapplied cash', 'Unapplied cash', '2250', NULL),
        ('vendor_payable', 'liability', 'Vendor payables', 'Vendor payables', '2300', NULL),
        ('pmc_income', 'liability', 'Company fees held in trust', 'Company fees', '2400', NULL),
        ('rent', 'income', 'Rent', 'Income line', '4000', NULL),
        -- A contra to rent (migration 20261106000242).
        ('concessions', 'income', 'Concessions', 'Contra income', '4010', NULL),
        ('recurring_charges', 'income', 'Recurring charges', 'Income line', '4100', NULL),
        ('late_fees', 'income', 'Late fees', 'Income line', '4200', NULL),
        ('nsf_fees', 'income', 'NSF fees', 'Income line', '4300', NULL),
        ('other_charges', 'income', 'Other charges', 'Income line', '4400', NULL),
        ('unassigned_bills', 'expense', 'Bills not in an expense account', 'Expense line',
         '5000', NULL),
        ('leasing_fees', 'expense', 'Leasing fees', 'Expense line', '5800', 8),
        ('management_fees', 'expense', 'Management fees', 'Expense line', '6100', 11),
        ('deposit_interest', 'expense', 'Security deposit interest', 'Expense line', '6900', 19)
$$;

-- No code is kept for later: the second pass's lines have theirs.
CREATE OR REPLACE FUNCTION trust_reserved_account_code(p_code text) RETURNS text
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT NULL::text
$$;

-- A code given is no longer asked whether it is kept for later, as none is: the first pass's check
-- of that could refuse no code now.
CREATE OR REPLACE FUNCTION trust_check_account_code() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_section text;
    v_from integer;
    v_through integer;
    v_taken text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('account codes of ' || NEW.pmc_id::text, 0));
    -- As the column's check says, but before the code is read as a number.
    IF NEW.code IS NULL OR NEW.code !~ '^[1-9][0-9]{3}$' THEN
        RAISE EXCEPTION 'trust: a code is four digits, from 1000, not %', NEW.code
            USING ERRCODE = 'check_violation';
    END IF;
    v_section := CASE
        WHEN NEW.charge_type_id IS NOT NULL THEN 'income'
        WHEN NEW.expense_account_id IS NOT NULL THEN 'expense'
        ELSE (SELECT l.section FROM trust_chart_lines() AS l WHERE l.line = NEW.line)
    END;
    SELECT r.f, r.t INTO v_from, v_through
    FROM (VALUES ('asset', 1000, 1999), ('liability', 2000, 2999), ('income', 4000, 4999),
                 ('expense', 5000, 6999)) AS r (s, f, t)
    WHERE r.s = v_section;
    IF NEW.code::integer NOT BETWEEN v_from AND v_through THEN
        RAISE EXCEPTION 'trust: % account''s code is from % to %, not %',
            CASE v_section WHEN 'asset' THEN 'an asset' WHEN 'liability' THEN 'a liability'
                           WHEN 'income' THEN 'an income' ELSE 'an expense' END,
            v_from, v_through, NEW.code
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.code = (
        SELECT c.code FROM trust_account_codes_of(NEW.pmc_id) AS c
        WHERE c.line IS NOT DISTINCT FROM NEW.line
          AND c.charge_type_id IS NOT DISTINCT FROM NEW.charge_type_id
          AND c.expense_account_id IS NOT DISTINCT FROM NEW.expense_account_id
    ) THEN
        RAISE EXCEPTION 'trust: its code is % already', NEW.code
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(
               (SELECT l.name FROM trust_chart_lines() AS l WHERE l.line = c.line),
               trust_charge_type_name(c.charge_type_id),
               trust_expense_account_name(c.expense_account_id)
           )
    INTO v_taken
    FROM trust_account_codes_of(NEW.pmc_id) AS c
    WHERE c.code = NEW.code
      AND (c.line IS DISTINCT FROM NEW.line
           OR c.charge_type_id IS DISTINCT FROM NEW.charge_type_id
           OR c.expense_account_id IS DISTINCT FROM NEW.expense_account_id)
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: code % is %''s already', NEW.code, v_taken
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_owner_statement(p_pmc_id uuid, p_owner_id uuid, p_period_start timestamp with time zone, p_period_end timestamp with time zone) RETURNS TABLE(line integer, property text, posted_on text, item text, detail text, amount numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a statement period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS property_name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    entries AS (
        SELECT a.account, e.amount AS change, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.account
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < p_period_end
    ),
    openings AS (
        SELECT a.account, a.property_name,
               coalesce(sum(x.change) FILTER (WHERE x.event_at < p_period_start), 0.00) AS opening,
               coalesce(sum(x.change), 0.00) AS closing
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.account
        GROUP BY a.account, a.property_name
    ),
    postings AS (
        SELECT x.account, o.property_name, x.event_at, x.version, x.change,
               o.opening + sum(x.change) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.kind || ': ' || CASE c.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN ow.display_name || ' / ' || cp.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   -- A payment from money received before it found its lease (migration
                   -- 20261106000240).
                   WHEN 'unapplied_cash' THEN 'received, not yet applied'
                   ELSE tn.display_name
               END || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN openings AS o ON o.account = x.account
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = x.other
        JOIN trust_bank_accounts AS b ON b.id = c.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = c.pmc_id
        LEFT JOIN trust_owners AS ow ON ow.id = c.owner_id
        LEFT JOIN trust_properties AS cp ON cp.id = c.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = c.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = c.vendor_id
        WHERE x.event_at >= p_period_start
    ),
    lines AS (
        -- (section, property, account, step, event_at, version) orders the statement;
        -- names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS account, 1 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version,
               'PMC' AS item, pmc.display_name AS detail, NULL::numeric AS change,
               NULL::numeric AS balance
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, NULL, NULL, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, NULL, NULL, 'period',
               to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
               || to_char(p_period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, o.property_name, o.account, 0, NULL, NULL, 'opening balance', NULL, NULL,
               o.opening
        FROM openings AS o
        UNION ALL
        SELECT 1, p.property_name, p.account, 1, p.event_at, p.version, 'posting',
               p.description, p.change, p.running
        FROM postings AS p
        UNION ALL
        SELECT 1, o.property_name, o.account, 2, NULL, NULL, 'closing balance', NULL, NULL,
               o.closing
        FROM openings AS o
        UNION ALL
        -- Memo lines after each property's closing balance, changing no balance (migration
        -- 20261106000243): the concessions given in the period at its leases (migration
        -- 20261106000242), a credit once or monthly, less a lease end's taking back part of a
        -- monthly one...
        SELECT 1, a.property_name, a.account, 3, NULL, NULL, 'memo: concessions',
               'given in the period, credits off rent (not money moved)', g.given, NULL
        FROM accounts AS a
        JOIN LATERAL (
            SELECT sum(CASE c.kind WHEN 'credit' THEN c.amount ELSE -c.amount END) AS given
            FROM trust_charges AS c
            JOIN trust_leases AS l ON l.id = c.lease_id
            JOIN trust_units AS u ON u.id = l.unit_id
            LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
            LEFT JOIN trust_charges AS o ON o.id = adj.charge_id
            LEFT JOIN trust_recurring_charges AS rc
                ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
            WHERE c.pmc_id = p_pmc_id AND u.property_id = a.property_id
              AND c.due_on >= (p_period_start AT TIME ZONE 'UTC')::date
              AND c.due_on < (p_period_end AT TIME ZONE 'UTC')::date
              AND (c.concession OR coalesce(o.concession, false) OR coalesce(rc.concession, false))
        ) AS g ON g.given IS NOT NULL AND g.given <> 0
        UNION ALL
        -- ...and each deposit the owner holds at the period's end, outside trust (migration
        -- 20261106000241): held by then, and not yet returned or kept.
        SELECT 1, a.property_name, a.account, 4, NULL,
               row_number() OVER (
                   PARTITION BY a.account
                   ORDER BY un.display_name COLLATE "C", t.display_name COLLATE "C",
                            CASE h.deposit_kind WHEN 'security' THEN 0 WHEN 'key' THEN 1 ELSE 2 END,
                            h.id
               ),
               'memo: deposit held by owner',
               t.display_name || '''s ' || h.deposit_kind || ' deposit, ' || un.display_name
               || ': you hold it, not in trust',
               h.amount, NULL
        FROM accounts AS a
        JOIN trust_units AS un ON un.property_id = a.property_id
        JOIN trust_leases AS l ON l.unit_id = un.id
        JOIN trust_owner_held_deposits AS h ON h.lease_id = l.id
        JOIN trust_tenants AS t ON t.id = h.tenant_id
        WHERE h.held_since < (p_period_end AT TIME ZONE 'UTC')::date
          AND NOT EXISTS (
              SELECT 1 FROM trust_owner_held_deposit_outcomes AS o
              WHERE o.held_id = h.id AND o.on_day < (p_period_end AT TIME ZONE 'UTC')::date
          )
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'all properties: opening balance', NULL, NULL,
               coalesce((SELECT sum(o.opening) FROM openings AS o), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 1, NULL, NULL, 'all properties: money in', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change > 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 2, NULL, NULL, 'all properties: money out', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change < 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 3, NULL, NULL, 'all properties: closing balance', NULL, NULL,
               coalesce((SELECT sum(o.closing) FROM openings AS o), 0.00)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.account, l.step,
                        l.event_at, l.version
           )::integer,
           l.property_name,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'),
           l.item,
           l.detail,
           l.change,
           l.balance
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM trust_account_codes
        WHERE line IN ('owner_held_deposits', 'unapplied_cash', 'concessions')
           OR code IN ('2150', '2250', '4010')
    ) THEN
        RAISE EXCEPTION 'refusing to undo the second pass''s chart lines: an account has one of '
            'their codes';
    END IF;
END;
$$;

-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_account_codes ADD CONSTRAINT trust_account_codes_line_check CHECK (
    line IN (
        'bank_cash', 'owner_property', 'tenant_deposit', 'pet_deposit', 'key_deposit',
        'prepaid_rent', 'vendor_payable', 'pmc_income', 'rent', 'recurring_charges', 'late_fees',
        'nsf_fees', 'other_charges', 'unassigned_bills', 'leasing_fees', 'management_fees',
        'deposit_interest'
    )
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_account_codes DROP CONSTRAINT trust_account_codes_line_v2;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_charge_type_names DROP CONSTRAINT trust_charge_type_names_not_concessions;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_charge_types DROP CONSTRAINT trust_charge_types_not_concessions;

CREATE OR REPLACE FUNCTION trust_chart_lines() RETURNS TABLE(line text, section text, name text, account_type text, code text, schedule_e integer)
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    VALUES
        ('bank_cash', 'asset', 'Trust bank cash', 'Bank cash', '1000', NULL::integer),
        ('owner_property', 'liability', 'Owner funds', 'Owner funds', '2000', NULL),
        ('tenant_deposit', 'liability', 'Tenant security deposits', 'Tenant deposits', '2100',
         NULL),
        ('pet_deposit', 'liability', 'Tenant pet deposits', 'Tenant deposits', '2110', NULL),
        ('key_deposit', 'liability', 'Tenant key deposits', 'Tenant deposits', '2120', NULL),
        ('prepaid_rent', 'liability', 'Prepaid rent', 'Prepaid rent', '2200', NULL),
        ('vendor_payable', 'liability', 'Vendor payables', 'Vendor payables', '2300', NULL),
        ('pmc_income', 'liability', 'Company fees held in trust', 'Company fees', '2400', NULL),
        ('rent', 'income', 'Rent', 'Income line', '4000', NULL),
        ('recurring_charges', 'income', 'Recurring charges', 'Income line', '4100', NULL),
        ('late_fees', 'income', 'Late fees', 'Income line', '4200', NULL),
        ('nsf_fees', 'income', 'NSF fees', 'Income line', '4300', NULL),
        ('other_charges', 'income', 'Other charges', 'Income line', '4400', NULL),
        ('unassigned_bills', 'expense', 'Bills not in an expense account', 'Expense line',
         '5000', NULL),
        ('leasing_fees', 'expense', 'Leasing fees', 'Expense line', '5800', 8),
        ('management_fees', 'expense', 'Management fees', 'Expense line', '6100', 11),
        ('deposit_interest', 'expense', 'Security deposit interest', 'Expense line', '6900', 19)
$$;

CREATE OR REPLACE FUNCTION trust_reserved_account_code(p_code text) RETURNS text
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT CASE p_code
        WHEN '2150' THEN 'owner-held deposits'
        WHEN '2250' THEN 'unapplied cash'
        WHEN '4010' THEN 'concessions'
    END
$$;

CREATE OR REPLACE FUNCTION trust_check_account_code() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_section text;
    v_from integer;
    v_through integer;
    v_taken text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('account codes of ' || NEW.pmc_id::text, 0));
    -- As the column's check says, but before the code is read as a number.
    IF NEW.code IS NULL OR NEW.code !~ '^[1-9][0-9]{3}$' THEN
        RAISE EXCEPTION 'trust: a code is four digits, from 1000, not %', NEW.code
            USING ERRCODE = 'check_violation';
    END IF;
    v_section := CASE
        WHEN NEW.charge_type_id IS NOT NULL THEN 'income'
        WHEN NEW.expense_account_id IS NOT NULL THEN 'expense'
        ELSE (SELECT l.section FROM trust_chart_lines() AS l WHERE l.line = NEW.line)
    END;
    SELECT r.f, r.t INTO v_from, v_through
    FROM (VALUES ('asset', 1000, 1999), ('liability', 2000, 2999), ('income', 4000, 4999),
                 ('expense', 5000, 6999)) AS r (s, f, t)
    WHERE r.s = v_section;
    IF NEW.code::integer NOT BETWEEN v_from AND v_through THEN
        RAISE EXCEPTION 'trust: % account''s code is from % to %, not %',
            CASE v_section WHEN 'asset' THEN 'an asset' WHEN 'liability' THEN 'a liability'
                           WHEN 'income' THEN 'an income' ELSE 'an expense' END,
            v_from, v_through, NEW.code
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_reserved_account_code(NEW.code) IS NOT NULL THEN
        RAISE EXCEPTION 'trust: code % is kept for %', NEW.code,
            trust_reserved_account_code(NEW.code)
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.code = (
        SELECT c.code FROM trust_account_codes_of(NEW.pmc_id) AS c
        WHERE c.line IS NOT DISTINCT FROM NEW.line
          AND c.charge_type_id IS NOT DISTINCT FROM NEW.charge_type_id
          AND c.expense_account_id IS NOT DISTINCT FROM NEW.expense_account_id
    ) THEN
        RAISE EXCEPTION 'trust: its code is % already', NEW.code
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(
               (SELECT l.name FROM trust_chart_lines() AS l WHERE l.line = c.line),
               trust_charge_type_name(c.charge_type_id),
               trust_expense_account_name(c.expense_account_id)
           )
    INTO v_taken
    FROM trust_account_codes_of(NEW.pmc_id) AS c
    WHERE c.code = NEW.code
      AND (c.line IS DISTINCT FROM NEW.line
           OR c.charge_type_id IS DISTINCT FROM NEW.charge_type_id
           OR c.expense_account_id IS DISTINCT FROM NEW.expense_account_id)
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: code % is %''s already', NEW.code, v_taken
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_owner_statement(p_pmc_id uuid, p_owner_id uuid, p_period_start timestamp with time zone, p_period_end timestamp with time zone) RETURNS TABLE(line integer, property text, posted_on text, item text, detail text, amount numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a statement period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS property_name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    entries AS (
        SELECT a.account, e.amount AS change, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.account
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < p_period_end
    ),
    openings AS (
        SELECT a.account, a.property_name,
               coalesce(sum(x.change) FILTER (WHERE x.event_at < p_period_start), 0.00) AS opening,
               coalesce(sum(x.change), 0.00) AS closing
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.account
        GROUP BY a.account, a.property_name
    ),
    postings AS (
        SELECT x.account, o.property_name, x.event_at, x.version, x.change,
               o.opening + sum(x.change) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.kind || ': ' || CASE c.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN ow.display_name || ' / ' || cp.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN openings AS o ON o.account = x.account
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = x.other
        JOIN trust_bank_accounts AS b ON b.id = c.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = c.pmc_id
        LEFT JOIN trust_owners AS ow ON ow.id = c.owner_id
        LEFT JOIN trust_properties AS cp ON cp.id = c.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = c.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = c.vendor_id
        WHERE x.event_at >= p_period_start
    ),
    lines AS (
        -- (section, property, account, step, event_at, version) orders the statement;
        -- names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS account, 1 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version,
               'PMC' AS item, pmc.display_name AS detail, NULL::numeric AS change,
               NULL::numeric AS balance
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, NULL, NULL, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, NULL, NULL, 'period',
               to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
               || to_char(p_period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, o.property_name, o.account, 0, NULL, NULL, 'opening balance', NULL, NULL,
               o.opening
        FROM openings AS o
        UNION ALL
        SELECT 1, p.property_name, p.account, 1, p.event_at, p.version, 'posting',
               p.description, p.change, p.running
        FROM postings AS p
        UNION ALL
        SELECT 1, o.property_name, o.account, 2, NULL, NULL, 'closing balance', NULL, NULL,
               o.closing
        FROM openings AS o
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'all properties: opening balance', NULL, NULL,
               coalesce((SELECT sum(o.opening) FROM openings AS o), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 1, NULL, NULL, 'all properties: money in', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change > 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 2, NULL, NULL, 'all properties: money out', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change < 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 3, NULL, NULL, 'all properties: closing balance', NULL, NULL,
               coalesce((SELECT sum(o.closing) FROM openings AS o), 0.00)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.account, l.step,
                        l.event_at, l.version
           )::integer,
           l.property_name,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'),
           l.item,
           l.detail,
           l.change,
           l.balance
    FROM lines AS l
    ORDER BY 1;
END;
$$;
