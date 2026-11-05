-- migrate:up
-- Renaming and archiving expense accounts and charge types (the founder's chart of accounts pass,
-- 2026-11-05). Nothing is rewritten: a name or an archival is a new row.
--
--   trust_expense_account_names      an expense account's new name: append-only, the latest row
--   trust_charge_type_names          holding, naming who gave it. A name is once among the
--                                    company's names today, whatever its case (a charge type's
--                                    never one of the income statement's own lines); the names
--                                    accounts were added with stop counting once renamed.
--   trust_expense_account_archivals  archived or brought back: append-only, the latest holding,
--   trust_charge_type_archivals      naming who. An expense account is archived only while no
--                                    open bill (neither paid in full nor declined) is in it, by
--                                    its latest expense account row or that row's split lines,
--                                    and no recurring bill running through today enters its
--                                    bills in it; a charge type only while no recurring charge
--                                    of it runs past today. Bringing one back is always allowed.
--                                    An archived account takes no new bill, split or recurring
--                                    bill, and an archived type no new fee or monthly charge;
--                                    each keeps its history and its lines on the reports.
--
-- The income statements and the budget beside one print each account's and type's name today
-- (written out in each: the AI role runs them and calls no function but the reports; the app
-- reads them through trust_expense_account_name and trust_charge_type_name). The AI role reads
-- all four tables, as it reads the accounts and types themselves.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_expense_account_names (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    expense_account_id uuid NOT NULL,
    name text NOT NULL CHECK (name = btrim(name) AND name ~ '\S' AND char_length(name) <= 200),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, expense_account_id) REFERENCES trust_expense_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_expense_account_names_account
ON trust_expense_account_names (expense_account_id, id);
CREATE INDEX trust_expense_account_names_recorded_by
ON trust_expense_account_names (pmc_id, recorded_by);

CREATE TABLE trust_charge_type_names (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    charge_type_id uuid NOT NULL,
    name text NOT NULL CHECK (
        name = btrim(name) AND name ~ '\S' AND char_length(name) <= 100
        AND lower(name) NOT IN (
            'rent', 'recurring charges', 'late fees', 'nsf fees', 'other charges'
        )
    ),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, charge_type_id) REFERENCES trust_charge_types (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_charge_type_names_type ON trust_charge_type_names (charge_type_id, id);
CREATE INDEX trust_charge_type_names_recorded_by ON trust_charge_type_names (pmc_id, recorded_by);

CREATE TABLE trust_expense_account_archivals (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    expense_account_id uuid NOT NULL,
    archived boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, expense_account_id) REFERENCES trust_expense_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_expense_account_archivals_account
ON trust_expense_account_archivals (expense_account_id, id);
CREATE INDEX trust_expense_account_archivals_recorded_by
ON trust_expense_account_archivals (pmc_id, recorded_by);

CREATE TABLE trust_charge_type_archivals (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    charge_type_id uuid NOT NULL,
    archived boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, charge_type_id) REFERENCES trust_charge_types (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_charge_type_archivals_type ON trust_charge_type_archivals (charge_type_id, id);
CREATE INDEX trust_charge_type_archivals_recorded_by
ON trust_charge_type_archivals (pmc_id, recorded_by);

-- Names today, and whether each is archived today.
CREATE OR REPLACE FUNCTION trust_expense_account_name(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT n.name FROM trust_expense_account_names AS n
         WHERE n.expense_account_id = p_id ORDER BY n.id DESC LIMIT 1),
        (SELECT e.name FROM trust_expense_accounts AS e WHERE e.id = p_id)
    )
$$;

CREATE OR REPLACE FUNCTION trust_charge_type_name(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT n.name FROM trust_charge_type_names AS n
         WHERE n.charge_type_id = p_id ORDER BY n.id DESC LIMIT 1),
        (SELECT t.name FROM trust_charge_types AS t WHERE t.id = p_id)
    )
$$;

CREATE FUNCTION trust_expense_account_archived(p_id uuid) RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT a.archived FROM trust_expense_account_archivals AS a
         WHERE a.expense_account_id = p_id ORDER BY a.id DESC LIMIT 1),
        false
    )
$$;
REVOKE ALL ON FUNCTION trust_expense_account_archived(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_expense_account_archived(uuid) TO trust_app;

CREATE FUNCTION trust_charge_type_archived(p_id uuid) RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT a.archived FROM trust_charge_type_archivals AS a
         WHERE a.charge_type_id = p_id ORDER BY a.id DESC LIMIT 1),
        false
    )
$$;
REVOKE ALL ON FUNCTION trust_charge_type_archived(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_type_archived(uuid) TO trust_app;

-- The names a company's accounts and types go by today are each once, whatever their case: an
-- account or a type added, or a new name. A new name changes it. Names of one company are given
-- one at a time.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_expense_accounts DROP CONSTRAINT trust_expense_accounts_pmc_id_name_key;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_charge_types_name;

CREATE FUNCTION trust_check_chart_name() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_id uuid;
    v_types boolean := TG_TABLE_NAME IN ('trust_charge_types', 'trust_charge_type_names');
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('chart names of ' || NEW.pmc_id::text, 0));
    -- Each branch reads only its own table's columns.
    IF TG_TABLE_NAME = 'trust_expense_account_names' THEN
        v_id := NEW.expense_account_id;
    ELSIF TG_TABLE_NAME = 'trust_charge_type_names' THEN
        v_id := NEW.charge_type_id;
    ELSE
        v_id := NEW.id;
    END IF;
    IF TG_TABLE_NAME IN ('trust_expense_account_names', 'trust_charge_type_names')
       AND NEW.name = (CASE WHEN v_types THEN trust_charge_type_name(v_id)
                            ELSE trust_expense_account_name(v_id) END)
    THEN
        RAISE EXCEPTION 'trust: it is named % already', NEW.name
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_types AND EXISTS (
        SELECT 1 FROM trust_charge_types AS t
        WHERE t.pmc_id = NEW.pmc_id AND t.id <> v_id
          AND lower(trust_charge_type_name(t.id)) = lower(NEW.name)
    ) THEN
        RAISE EXCEPTION 'trust: the company has a charge type named % already', NEW.name
            USING ERRCODE = 'unique_violation';
    END IF;
    IF NOT v_types AND EXISTS (
        SELECT 1 FROM trust_expense_accounts AS e
        WHERE e.pmc_id = NEW.pmc_id AND e.id <> v_id
          AND lower(trust_expense_account_name(e.id)) = lower(NEW.name)
    ) THEN
        RAISE EXCEPTION 'trust: the company has an expense account named % already', NEW.name
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_chart_name() FROM PUBLIC;

-- Archived or brought back: a change; archiving only once nothing open still uses it.
CREATE FUNCTION trust_check_chart_archival() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'UTC')::date;
    v_expense boolean := TG_TABLE_NAME = 'trust_expense_account_archivals';
    v_id uuid;
    v_name text;
BEGIN
    IF v_expense THEN
        v_id := NEW.expense_account_id;
        v_name := trust_expense_account_name(v_id);
    ELSE
        v_id := NEW.charge_type_id;
        v_name := trust_charge_type_name(v_id);
    END IF;
    -- Waits for bills, splits and charges being put in it (trust_check_chart_choice).
    PERFORM pg_advisory_xact_lock(hashtextextended('chart account ' || v_id::text, 0));
    IF NEW.archived = (CASE WHEN v_expense THEN trust_expense_account_archived(v_id)
                            ELSE trust_charge_type_archived(v_id) END)
    THEN
        RAISE EXCEPTION 'trust: % is % already', v_name,
            CASE WHEN NEW.archived THEN 'archived' ELSE 'not archived' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.archived THEN
        RETURN NEW;
    END IF;
    IF v_expense THEN
        IF EXISTS (
            SELECT 1
            FROM trust_bills AS bill
            CROSS JOIN LATERAL (
                SELECT x.id, x.expense_account_id FROM trust_bill_expense_accounts AS x
                WHERE x.bill_id = bill.id ORDER BY x.id DESC LIMIT 1
            ) AS latest
            WHERE bill.pmc_id = NEW.pmc_id
              AND (latest.expense_account_id = v_id OR EXISTS (
                  SELECT 1 FROM trust_bill_splits AS sp
                  WHERE sp.categorized_id = latest.id AND sp.expense_account_id = v_id
              ))
              -- Not paid in full (a bill may be paid in parts, migration 0152), nor declined.
              AND (SELECT coalesce(sum(t.amount), 0) FROM trust_bill_payments AS p
                   JOIN pgledger_transfers AS t ON t.id = p.transfer_id
                   WHERE p.bill_id = bill.id AND p.step = 'paid') < bill.amount
              AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = bill.id)
        ) THEN
            RAISE EXCEPTION 'trust: % has a bill not paid in it; pay or void the bill, or put it '
                'in another expense account, before archiving', v_name
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (
            SELECT 1
            FROM trust_recurring_bills AS r
            CROSS JOIN LATERAL (
                SELECT y.expense_account_id FROM trust_recurring_bill_expense_accounts AS y
                WHERE y.recurring_bill_id = r.id ORDER BY y.id DESC LIMIT 1
            ) AS latest
            WHERE r.pmc_id = NEW.pmc_id AND latest.expense_account_id = v_id
              AND NOT EXISTS (SELECT 1 FROM trust_recurring_bill_ends AS e
                              WHERE e.recurring_bill_id = r.id AND e.ends_on < v_today)
        ) THEN
            RAISE EXCEPTION 'trust: % has a recurring bill still running in it; stop it or put '
                'it in another expense account before archiving', v_name
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM trust_recurring_charges AS r
        WHERE r.charge_type_id = v_id
          AND NOT EXISTS (SELECT 1 FROM trust_recurring_charge_ends AS e
                          WHERE e.recurring_charge_id = r.id AND e.ends_on <= v_today)
    ) THEN
        RAISE EXCEPTION 'trust: % has a monthly charge still running; stop it before archiving',
            v_name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_chart_archival() FROM PUBLIC;

-- An archived expense account takes no new bill, split or recurring bill, and an archived
-- charge type no new fee or monthly charge.
CREATE FUNCTION trust_check_chart_choice() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_expense boolean := TG_TABLE_NAME IN (
        'trust_bill_expense_accounts', 'trust_bill_splits', 'trust_recurring_bill_expense_accounts'
    );
    v_id uuid;
BEGIN
    IF v_expense THEN
        v_id := NEW.expense_account_id;
    ELSE
        v_id := NEW.charge_type_id;
    END IF;
    -- Waits for an archival of it in flight (trust_check_chart_archival).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('chart account ' || v_id::text, 0));
    IF v_expense AND trust_expense_account_archived(v_id) THEN
        RAISE EXCEPTION 'trust: % is archived; bring it back or choose another expense account',
            trust_expense_account_name(v_id)
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT v_expense AND trust_charge_type_archived(v_id) THEN
        RAISE EXCEPTION 'trust: % is archived; bring it back or choose another charge type',
            trust_charge_type_name(v_id)
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_chart_choice() FROM PUBLIC;

CREATE TRIGGER trust_chart_name BEFORE INSERT ON trust_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_name();
CREATE TRIGGER trust_chart_name BEFORE INSERT ON trust_charge_types
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_name();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_expense_account_names
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_chart_name BEFORE INSERT ON trust_expense_account_names
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_name();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_expense_account_names
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_expense_account_names
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_charge_type_names
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_chart_name BEFORE INSERT ON trust_charge_type_names
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_name();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charge_type_names
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_charge_type_names
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_expense_account_archivals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_chart_archival BEFORE INSERT ON trust_expense_account_archivals
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_archival();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_expense_account_archivals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_expense_account_archivals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_charge_type_archivals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_chart_archival BEFORE INSERT ON trust_charge_type_archivals
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_archival();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charge_type_archivals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_charge_type_archivals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_chart_choice BEFORE INSERT ON trust_bill_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_choice();
CREATE TRIGGER trust_chart_choice BEFORE INSERT ON trust_bill_splits
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_choice();
CREATE TRIGGER trust_chart_choice BEFORE INSERT ON trust_recurring_bill_expense_accounts
FOR EACH ROW EXECUTE FUNCTION trust_check_chart_choice();
CREATE TRIGGER trust_chart_choice BEFORE INSERT ON trust_charges
FOR EACH ROW WHEN (NEW.charge_type_id IS NOT NULL)
EXECUTE FUNCTION trust_check_chart_choice();
CREATE TRIGGER trust_chart_choice BEFORE INSERT ON trust_recurring_charges
FOR EACH ROW WHEN (NEW.charge_type_id IS NOT NULL)
EXECUTE FUNCTION trust_check_chart_choice();

REVOKE ALL ON trust_expense_account_names FROM PUBLIC;
GRANT SELECT, INSERT ON trust_expense_account_names TO trust_app;
GRANT SELECT ON trust_expense_account_names TO trust_ai_agent;
REVOKE ALL ON trust_charge_type_names FROM PUBLIC;
GRANT SELECT, INSERT ON trust_charge_type_names TO trust_app;
GRANT SELECT ON trust_charge_type_names TO trust_ai_agent;
REVOKE ALL ON trust_expense_account_archivals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_expense_account_archivals TO trust_app;
GRANT SELECT ON trust_expense_account_archivals TO trust_ai_agent;
REVOKE ALL ON trust_charge_type_archivals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_charge_type_archivals TO trust_app;
GRANT SELECT ON trust_charge_type_archivals TO trust_ai_agent;

-- The reports print each account's and type's name today.
CREATE OR REPLACE FUNCTION trust_report_income_statement(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
        -- Each type by its name today (migration 20261106000210).
        SELECT c.account, 1, 6,
               'income: ' || coalesce((SELECT n.name FROM trust_charge_type_names AS n
                                    WHERE n.charge_type_id = ct.id ORDER BY n.id DESC LIMIT 1),
                                   ct.name),
               3, sum(c.amount)
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
                    -- Each account by its name today (migration 20261106000210).
                    ELSE 'expense: ' || coalesce((SELECT n.name FROM trust_expense_account_names AS n
                                          WHERE n.expense_account_id = ea.id
                                          ORDER BY n.id DESC LIMIT 1), ea.name) END,
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
$$;

CREATE OR REPLACE FUNCTION trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
        -- Each type by its name today (migration 20261106000210).
        SELECT c.account, 1, 6,
               'income: ' || coalesce((SELECT n.name FROM trust_charge_type_names AS n
                                    WHERE n.charge_type_id = ct.id ORDER BY n.id DESC LIMIT 1),
                                   ct.name),
               3, sum(c.amount)
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
                    -- Each account by its name today (migration 20261106000210).
                    ELSE 'expense: ' || coalesce((SELECT n.name FROM trust_expense_account_names AS n
                                          WHERE n.expense_account_id = ea.id
                                          ORDER BY n.id DESC LIMIT 1), ea.name) END,
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
$$;

CREATE OR REPLACE FUNCTION trust_report_budget_vs_actual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, budget numeric, actual numeric, actual_less_budget numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
                    -- Each account by its name today (migration 20261106000210).
                    WHEN l.kind = 'expense account' THEN 'expense: ' || coalesce(
                        (SELECT n.name FROM trust_expense_account_names AS n
                         WHERE n.expense_account_id = e.id ORDER BY n.id DESC LIMIT 1), e.name)
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
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_expense_account_names)
       OR EXISTS (SELECT 1 FROM trust_charge_type_names)
       OR EXISTS (SELECT 1 FROM trust_expense_account_archivals)
       OR EXISTS (SELECT 1 FROM trust_charge_type_archivals)
    THEN
        RAISE EXCEPTION 'refusing to undo renaming and archiving: staff renamed or archived an '
            'expense account or charge type';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_income_statement(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
$$;

CREATE OR REPLACE FUNCTION trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
$$;

CREATE OR REPLACE FUNCTION trust_report_budget_vs_actual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, budget numeric, actual numeric, actual_less_budget numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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

DROP TRIGGER trust_chart_choice ON trust_recurring_charges;
DROP TRIGGER trust_chart_choice ON trust_charges;
DROP TRIGGER trust_chart_choice ON trust_recurring_bill_expense_accounts;
DROP TRIGGER trust_chart_choice ON trust_bill_splits;
DROP TRIGGER trust_chart_choice ON trust_bill_expense_accounts;
DROP TRIGGER trust_chart_name ON trust_charge_types;
DROP TRIGGER trust_chart_name ON trust_expense_accounts;
-- squawk-ignore ban-drop-table
DROP TABLE trust_charge_type_archivals;
-- squawk-ignore ban-drop-table
DROP TABLE trust_expense_account_archivals;
-- squawk-ignore ban-drop-table
DROP TABLE trust_charge_type_names;
-- squawk-ignore ban-drop-table
DROP TABLE trust_expense_account_names;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_chart_choice();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_chart_archival();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_chart_name();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_charge_type_archived(uuid);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_expense_account_archived(uuid);

CREATE OR REPLACE FUNCTION trust_expense_account_name(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT e.name FROM trust_expense_accounts AS e WHERE e.id = p_id
$$;

CREATE OR REPLACE FUNCTION trust_charge_type_name(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT t.name FROM trust_charge_types AS t WHERE t.id = p_id
$$;

-- squawk-ignore disallowed-unique-constraint,constraint-missing-not-valid
ALTER TABLE trust_expense_accounts ADD CONSTRAINT trust_expense_accounts_pmc_id_name_key
UNIQUE (pmc_id, name);
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_charge_types_name ON trust_charge_types (pmc_id, lower(name));
