-- migrate:up
-- Account codes (the founder's chart of accounts pass, 2026-11-05): every account of a company's
-- chart has a code of its own, our own numbering, never a third party's.
--
--   trust_chart_lines()        the chart's fixed lines: one control account per ledger kind (a
--                              pet and a key deposit each have one of their own beside the
--                              security deposit) and the income statement's own income and
--                              expense lines, each with the code it starts with.
--   trust_account_codes        a code given to a fixed line, a charge type or an expense account:
--                              append-only, the latest row of each holding, naming who gave it.
--                              A charge type or an expense account is given the next free code
--                              of its range as it is added (trust_give_account_code); a fixed
--                              line keeps its starting code until staff give it another.
--   trust_account_codes_of()   each account's code today.
--
-- The scheme. Codes are four digits, unique among a company's codes today:
--   1000-1999 assets       1000 trust bank cash.
--   2000-2999 liabilities  what the trust holds for others: 2000 owner funds, 2100 tenant
--                          security deposits, 2110 pet deposits, 2120 key deposits, 2200 prepaid
--                          rent, 2300 vendor payables, 2400 the company's fees held in trust.
--                          Kept for a later pass: 2150 owner-held deposits (a memo) and 2250
--                          unapplied cash.
--   3000-3999 equity       none: the trust ledger holds no money of its own.
--   4000-4999 income       4000 rent, 4100 recurring charges, 4200 late fees, 4300 NSF fees,
--                          4400 other charges; charge types from 4501 on. Kept for a later pass:
--                          4010 concessions, a contra to rent.
--   5000-6999 expenses     5000 bills not in an expense account; then a block of a hundred for
--                          each line of Schedule E, 5000 + 100 x the line (5500 advertising
--                          through 6900 other), its first code the statement's own line where it
--                          has one (5800 leasing fees, 6100 management fees, 6900 security
--                          deposit interest), and its expense accounts from the next code on
--                          (6401, 6402... for repairs). A full block gives the next free code
--                          from 5001.
-- A code describes an account; changing it rewrites nothing: no transfer, entry or report row
-- holds one. The AI role reads them, as it reads the expense accounts and charge types; the
-- helpers below are the app's (the AI role calls no function but the reports).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_chart_lines()
RETURNS TABLE (
    line text, section text, name text, account_type text, code text, schedule_e integer
)
LANGUAGE sql
IMMUTABLE
SET search_path = public, pg_temp
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
REVOKE ALL ON FUNCTION trust_chart_lines() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_chart_lines() TO trust_app;

-- What a code is kept for, if a later pass will give it to a line of its own.
CREATE FUNCTION trust_reserved_account_code(p_code text) RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE p_code
        WHEN '2150' THEN 'owner-held deposits'
        WHEN '2250' THEN 'unapplied cash'
        WHEN '4010' THEN 'concessions'
    END
$$;
REVOKE ALL ON FUNCTION trust_reserved_account_code(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_reserved_account_code(text) TO trust_app;

-- An expense account's or charge type's name today. Renaming them comes with the next migration;
-- until then it is the name it was added with.
CREATE FUNCTION trust_expense_account_name(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT e.name FROM trust_expense_accounts AS e WHERE e.id = p_id
$$;
REVOKE ALL ON FUNCTION trust_expense_account_name(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_expense_account_name(uuid) TO trust_app;

CREATE FUNCTION trust_charge_type_name(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT t.name FROM trust_charge_types AS t WHERE t.id = p_id
$$;
REVOKE ALL ON FUNCTION trust_charge_type_name(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_type_name(uuid) TO trust_app;

CREATE TABLE trust_account_codes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Exactly one of: a fixed line of the chart, a charge type, an expense account.
    line text CHECK (
        line IN (
            'bank_cash', 'owner_property', 'tenant_deposit', 'pet_deposit', 'key_deposit',
            'prepaid_rent', 'vendor_payable', 'pmc_income', 'rent', 'recurring_charges',
            'late_fees', 'nsf_fees', 'other_charges', 'unassigned_bills', 'leasing_fees',
            'management_fees', 'deposit_interest'
        )
    ),
    charge_type_id uuid,
    expense_account_id uuid,
    code text NOT NULL CHECK (code ~ '^[1-9][0-9]{3}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(line, charge_type_id, expense_account_id) = 1),
    FOREIGN KEY (pmc_id, charge_type_id) REFERENCES trust_charge_types (pmc_id, id),
    FOREIGN KEY (pmc_id, expense_account_id) REFERENCES trust_expense_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_account_codes_line ON trust_account_codes (pmc_id, line, id)
WHERE line IS NOT NULL;
CREATE INDEX trust_account_codes_charge_type ON trust_account_codes (charge_type_id, id)
WHERE charge_type_id IS NOT NULL;
CREATE INDEX trust_account_codes_expense_account ON trust_account_codes (expense_account_id, id)
WHERE expense_account_id IS NOT NULL;
CREATE INDEX trust_account_codes_recorded_by ON trust_account_codes (pmc_id, recorded_by);

-- Each account of a company's chart with its code today: a fixed line's latest code or its
-- starting one, a charge type's or an expense account's latest.
CREATE FUNCTION trust_account_codes_of(p_pmc_id uuid)
RETURNS TABLE (line text, charge_type_id uuid, expense_account_id uuid, code text)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT l.line, NULL::uuid, NULL::uuid,
           coalesce((SELECT c.code FROM trust_account_codes AS c
                     WHERE c.pmc_id = p_pmc_id AND c.line = l.line
                     ORDER BY c.id DESC LIMIT 1), l.code)
    FROM trust_chart_lines() AS l
    UNION ALL
    SELECT NULL, t.id, NULL,
           (SELECT c.code FROM trust_account_codes AS c
            WHERE c.charge_type_id = t.id ORDER BY c.id DESC LIMIT 1)
    FROM trust_charge_types AS t
    WHERE t.pmc_id = p_pmc_id
    UNION ALL
    SELECT NULL, NULL, e.id,
           (SELECT c.code FROM trust_account_codes AS c
            WHERE c.expense_account_id = e.id ORDER BY c.id DESC LIMIT 1)
    FROM trust_expense_accounts AS e
    WHERE e.pmc_id = p_pmc_id
$$;
REVOKE ALL ON FUNCTION trust_account_codes_of(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_account_codes_of(uuid) TO trust_app;

-- The lowest code from p_from through p_through no account of the company has today and no
-- later pass keeps; NULL when there is none.
CREATE FUNCTION trust_next_account_code(p_pmc_id uuid, p_from integer, p_through integer)
RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT n::text
    FROM generate_series(p_from, p_through) AS n
    WHERE trust_reserved_account_code(n::text) IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM trust_account_codes_of(p_pmc_id) AS c WHERE c.code = n::text
      )
    ORDER BY n
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION trust_next_account_code(uuid, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_next_account_code(uuid, integer, integer) TO trust_app;

-- The code a new charge type or expense account starts with: a charge type's from 4501, an
-- expense account's from the first after its Schedule E line's block start, else the next free
-- one of its section.
CREATE FUNCTION trust_starting_account_code(
    p_pmc_id uuid, p_charge_type_id uuid, p_expense_account_id uuid
) RETURNS text
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_line integer;
    v_code text;
BEGIN
    IF p_charge_type_id IS NOT NULL THEN
        v_code := coalesce(
            trust_next_account_code(p_pmc_id, 4501, 4999),
            trust_next_account_code(p_pmc_id, 4001, 4499)
        );
    ELSE
        SELECT e.schedule_e_line INTO v_line
        FROM trust_expense_accounts AS e WHERE e.id = p_expense_account_id;
        v_code := coalesce(
            trust_next_account_code(p_pmc_id, 5000 + 100 * v_line + 1, 5000 + 100 * v_line + 99),
            trust_next_account_code(p_pmc_id, 5001, 6999)
        );
    END IF;
    IF v_code IS NULL THEN
        RAISE EXCEPTION 'trust: no code is free in the % range; give an account another code first',
            CASE WHEN p_charge_type_id IS NOT NULL THEN 'income' ELSE 'expense' END
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN v_code;
END;
$$;
REVOKE ALL ON FUNCTION trust_starting_account_code(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_starting_account_code(uuid, uuid, uuid) TO trust_app;

-- A code given: within its section's range, not kept for a later pass, no other account's today,
-- and a change. Codes of one company are given one at a time.
CREATE FUNCTION trust_check_account_code() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
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
REVOKE ALL ON FUNCTION trust_check_account_code() FROM PUBLIC;

-- A charge type or an expense account added is given the next free code of its range.
CREATE FUNCTION trust_give_account_code() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('account codes of ' || NEW.pmc_id::text, 0));
    IF TG_TABLE_NAME = 'trust_charge_types' THEN
        INSERT INTO trust_account_codes (pmc_id, charge_type_id, code)
        VALUES (NEW.pmc_id, NEW.id, trust_starting_account_code(NEW.pmc_id, NEW.id, NULL));
    ELSE
        INSERT INTO trust_account_codes (pmc_id, expense_account_id, code)
        VALUES (NEW.pmc_id, NEW.id, trust_starting_account_code(NEW.pmc_id, NULL, NEW.id));
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_give_account_code() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_account_codes
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_account_code BEFORE INSERT ON trust_account_codes
FOR EACH ROW EXECUTE FUNCTION trust_check_account_code();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_account_codes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_account_codes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_account_codes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_account_codes TO trust_app;
GRANT SELECT ON trust_account_codes TO trust_ai_agent;

-- Every company's charge types and expense accounts today get their codes, oldest first: the
-- charge types from 4501, the expense accounts each in its Schedule E line's block.
DO $$
DECLARE
    v_row record;
BEGIN
    FOR v_row IN
        SELECT t.pmc_id, t.id FROM trust_charge_types AS t ORDER BY t.pmc_id, t.created_at, t.id
    LOOP
        INSERT INTO trust_account_codes (pmc_id, charge_type_id, code)
        VALUES (v_row.pmc_id, v_row.id, trust_starting_account_code(v_row.pmc_id, v_row.id, NULL));
    END LOOP;
    FOR v_row IN
        SELECT e.pmc_id, e.id FROM trust_expense_accounts AS e
        ORDER BY e.pmc_id, e.schedule_e_line, e.created_at, e.id
    LOOP
        INSERT INTO trust_account_codes (pmc_id, expense_account_id, code)
        VALUES (v_row.pmc_id, v_row.id, trust_starting_account_code(v_row.pmc_id, NULL, v_row.id));
    END LOOP;
END;
$$;

CREATE TRIGGER trust_account_code AFTER INSERT ON trust_charge_types
FOR EACH ROW EXECUTE FUNCTION trust_give_account_code();
CREATE TRIGGER trust_account_code AFTER INSERT ON trust_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_give_account_code();

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DROP TRIGGER trust_account_code ON trust_expense_accounts;
DROP TRIGGER trust_account_code ON trust_charge_types;
-- squawk-ignore ban-drop-table
DROP TABLE trust_account_codes;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_give_account_code();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_account_code();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_starting_account_code(uuid, uuid, uuid);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_next_account_code(uuid, integer, integer);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_account_codes_of(uuid);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_charge_type_name(uuid);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_expense_account_name(uuid);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_reserved_account_code(text);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_chart_lines();
