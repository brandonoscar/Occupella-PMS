-- migrate:up
-- Bills (the founder's review, q062 and q065, docs/law.md):
--
--   trust_shared_bills, trust_shared_bill_parts
--                       one vendor invoice shared across properties (q062): its parts are
--                       ordinary bills, one per owner's property account, each its share, so
--                       each follows every bill rule on its own (the owner's approval limit and
--                       email, set aside from that owner's money, paid through its trust bank
--                       account's cash, held back from that owner's draws). A part's reference is
--                       the invoice's with "/1", "/2"... At commit the parts (2 to 20, each in a
--                       different account, of the invoice's vendor, with its date, due day and
--                       memo) come to the invoice. A whole bill and a shared invoice never share
--                       a vendor's reference.
--   bill_markup_percent the percent of a bill the owner's management agreement lets the company
--                       add as its markup (q065), 0 (none) unless the agreement says so.
--   trust_bill_markups  a bill staff add the markup to, at the percent of the agreement in force
--                       on the bill's date, before it is set aside; once per bill, naming who.
--   trust_bill_markup_fees
--                       the markup taken: trust_set_aside_bill moves it from the owner's account
--                       into the company's fee account with the bill's own money, so the owner's
--                       statement shows it on a line of its own. Until then draws hold it back
--                       with the bill. The income statement and the year-end package count it
--                       with the management fees (Schedule E line 11).
--
-- A markup is compensation the owner agreed to in writing and sees on their statement, never a
-- vendor's discount kept quietly (docs/law.md). The PMS records no rebate from a vendor.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_management_agreements
ADD COLUMN bill_markup_percent numeric NOT NULL DEFAULT 0
CHECK (bill_markup_percent >= 0 AND bill_markup_percent <= 100);

-- --- one invoice shared across properties -------------------------------------------------

CREATE TABLE trust_shared_bills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    reference text NOT NULL CHECK (reference ~ '\S' AND length(reference) <= 100),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (vendor_id, reference),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_shared_bills_vendor ON trust_shared_bills (pmc_id, vendor_id);
CREATE INDEX trust_shared_bills_recorded_by ON trust_shared_bills (pmc_id, recorded_by);

CREATE TABLE trust_shared_bill_parts (
    shared_bill_id uuid NOT NULL,
    -- squawk-ignore prefer-bigint-over-int
    part integer NOT NULL CHECK (part BETWEEN 1 AND 20),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bill_id uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (shared_bill_id, part),
    FOREIGN KEY (pmc_id, shared_bill_id) REFERENCES trust_shared_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id)
);
CREATE INDEX trust_shared_bill_parts_shared ON trust_shared_bill_parts (pmc_id, shared_bill_id);
CREATE INDEX trust_shared_bill_parts_bill ON trust_shared_bill_parts (pmc_id, bill_id);

-- A vendor's reference is one bill or one shared invoice, never both. Both sides take the same
-- lock, so two entries of one reference wait for each other.
CREATE FUNCTION trust_check_bill_reference() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('trust bill reference ' || NEW.vendor_id || ' ' || NEW.reference, 0)
    );
    IF TG_TABLE_NAME = 'trust_shared_bills' THEN
        IF EXISTS (SELECT 1 FROM trust_bills AS b
                   WHERE b.vendor_id = NEW.vendor_id AND b.reference = NEW.reference) THEN
            RAISE EXCEPTION 'trust: the vendor has a bill % already', NEW.reference
                USING ERRCODE = 'unique_violation';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM trust_shared_bills AS s
                  WHERE s.vendor_id = NEW.vendor_id AND s.reference = NEW.reference) THEN
        RAISE EXCEPTION 'trust: the vendor has a bill % already, shared across properties',
            NEW.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_bill_reference() FROM PUBLIC;

CREATE TRIGGER trust_bill_reference BEFORE INSERT ON trust_bills
FOR EACH ROW EXECUTE FUNCTION trust_check_bill_reference();
CREATE TRIGGER trust_bill_reference BEFORE INSERT ON trust_shared_bills
FOR EACH ROW EXECUTE FUNCTION trust_check_bill_reference();

-- At commit, a shared invoice's parts: 1 to n with no gap, at least two, each a bill of the
-- invoice's vendor under its reference and part number, in an account of its own, with one
-- date, due day and memo, together coming to the invoice.
CREATE FUNCTION trust_check_shared_bill() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_shared trust_shared_bills;
    v_parts bigint;
    v_top integer;
    v_accounts bigint;
    v_kinds bigint;
    v_total numeric;
BEGIN
    -- An invoice's row (its id) or a part's (its shared_bill_id).
    SELECT s.* INTO v_shared FROM trust_shared_bills AS s
    WHERE s.id = coalesce(to_jsonb(NEW) ->> 'shared_bill_id', to_jsonb(NEW) ->> 'id')::uuid;
    SELECT count(*), max(p.part), count(DISTINCT b.ledger_account_id),
           count(DISTINCT (b.bill_date, b.due_on, b.memo)), coalesce(sum(b.amount), 0)
    INTO v_parts, v_top, v_accounts, v_kinds, v_total
    FROM trust_shared_bill_parts AS p
    JOIN trust_bills AS b ON b.id = p.bill_id
    WHERE p.shared_bill_id = v_shared.id;
    IF v_parts < 2 OR v_top <> v_parts THEN
        RAISE EXCEPTION 'trust: bill % is shared in parts 1 to n, two at least; it has %',
            v_shared.reference, v_parts
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_accounts <> v_parts THEN
        RAISE EXCEPTION 'trust: bill %''s parts are each an owner''s property account of their own',
            v_shared.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_kinds <> 1 OR EXISTS (
        SELECT 1 FROM trust_shared_bill_parts AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        WHERE p.shared_bill_id = v_shared.id
          AND (b.vendor_id <> v_shared.vendor_id
               OR b.reference <> v_shared.reference || '/' || p.part)
    ) THEN
        RAISE EXCEPTION 'trust: bill %''s parts are the vendor''s, under its reference and '
            'part number, with one date, due day and memo', v_shared.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_total <> v_shared.amount THEN
        RAISE EXCEPTION 'trust: bill %''s parts come to %, not its %',
            v_shared.reference, v_total, v_shared.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_shared_bill() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER trust_shared_bill_whole AFTER INSERT ON trust_shared_bills
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_shared_bill();
CREATE CONSTRAINT TRIGGER trust_shared_bill_whole AFTER INSERT ON trust_shared_bill_parts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_shared_bill();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_shared_bills
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_shared_bills
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_shared_bills
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_shared_bill_parts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_shared_bill_parts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_shared_bills FROM PUBLIC;
REVOKE ALL ON trust_shared_bill_parts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_shared_bills, trust_shared_bill_parts TO trust_app;
GRANT SELECT ON trust_shared_bills, trust_shared_bill_parts TO trust_ai_agent;

-- --- markups --------------------------------------------------------------------------------

CREATE TABLE trust_bill_markups (
    bill_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Filled in from the agreement in force on the bill's date, whatever is given.
    percent numeric NOT NULL CHECK (percent > 0 AND percent <= 100),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, bill_id),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_bill_markups_recorded_by ON trust_bill_markups (pmc_id, recorded_by);

-- A markup is the agreement's percent of the bill, to the cent, added before the bill is set
-- aside and never to a declined one. Runs as the owner to take the bill's lock (its owner's
-- account), as trust_set_aside_bill does: the two wait for each other.
CREATE FUNCTION trust_fill_bill_markup() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    v_percent numeric;
BEGIN
    IF EXISTS (SELECT 1 FROM trust_bill_payments AS p
               WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside') THEN
        RAISE EXCEPTION 'trust: bill % is set aside already; a markup is added before',
            v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id) THEN
        RAISE EXCEPTION 'trust: bill % was declined; it takes no markup', v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    v_percent := coalesce(
        (trust_agreement_on(v_bill.ledger_account_id, v_bill.bill_date)).bill_markup_percent, 0
    );
    IF v_percent = 0 THEN
        RAISE EXCEPTION 'trust: the owner''s agreement in force on % sets no markup on bills',
            v_bill.bill_date
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.percent := v_percent;
    NEW.amount := round(v_bill.amount * v_percent / 100, 2);
    IF NEW.amount = 0 THEN
        RAISE EXCEPTION 'trust: a markup of % percent on bill % comes to nothing',
            trim_scale(v_percent), v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_fill_bill_markup() FROM PUBLIC;

CREATE TRIGGER trust_bill_markup BEFORE INSERT ON trust_bill_markups
FOR EACH ROW EXECUTE FUNCTION trust_fill_bill_markup();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_bill_markups
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bill_markups
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bill_markups
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_bill_markups FROM PUBLIC;
GRANT SELECT, INSERT ON trust_bill_markups TO trust_app;
GRANT SELECT ON trust_bill_markups TO trust_ai_agent;

CREATE TABLE trust_bill_markup_fees (
    bill_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bill_markups (pmc_id, bill_id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bill_markup_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bill_markup_fees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_bill_markup_fees FROM PUBLIC;
GRANT SELECT ON trust_bill_markup_fees TO trust_app, trust_ai_agent;

-- Setting a bill aside takes its markup too, into the company's fee account of the owner's trust
-- bank account, in the same transaction: both or neither.
CREATE OR REPLACE FUNCTION trust_set_aside_bill(
    p_pmc_id uuid, p_bill_id uuid, p_event_at timestamptz
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(p_pmc_id, p_bill_id);
    v_bank uuid;
    v_limit numeric;
    v_payable text;
    v_transfer_id text;
    v_declined trust_bill_declines;
    v_markup trust_bill_markups;
    v_owner trust_ledger_accounts;
BEGIN
    SELECT p.transfer_id INTO v_transfer_id
    FROM trust_bill_payments AS p
    WHERE p.bill_id = p_bill_id AND p.step = 'set_aside';
    IF FOUND THEN
        RETURN v_transfer_id;  -- a retry: the bill is set aside already
    END IF;

    SELECT d.* INTO v_declined FROM trust_bill_declines AS d WHERE d.bill_id = p_bill_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: bill % was declined by % on %; it is not paid',
            v_bill.reference, v_declined.declined_by, v_declined.declined_on
            USING ERRCODE = 'check_violation';
    END IF;

    v_limit := coalesce((
        trust_agreement_on(v_bill.ledger_account_id, v_bill.bill_date)
    ).approval_limit, 0);
    IF v_bill.amount > v_limit AND NOT EXISTS (
        SELECT a.bill_id FROM trust_bill_approvals AS a WHERE a.bill_id = p_bill_id
    ) THEN
        RAISE EXCEPTION 'trust: bill % is %, over the owner''s approval limit of %; record the '
            'owner''s approval first', v_bill.reference, v_bill.amount, v_limit
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT t.* INTO v_owner
    FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
    v_bank := v_owner.bank_account_id;
    SELECT t.ledger_account_id INTO v_payable
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_bank AND t.kind = 'vendor_payable'
      AND t.vendor_id = v_bill.vendor_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the vendor''s account (vendor_payable) in trust bank '
            'account % before setting its bill aside', v_bank
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_bill.ledger_account_id, v_payable, v_bill.amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', format('Bill %s set aside', v_bill.reference))
    ) AS t;
    INSERT INTO trust_bill_payments (bill_id, step, pmc_id, transfer_id)
    VALUES (p_bill_id, 'set_aside', p_pmc_id, v_transfer_id);

    SELECT m.* INTO v_markup FROM trust_bill_markups AS m WHERE m.bill_id = p_bill_id;
    IF FOUND THEN
        INSERT INTO trust_bill_markup_fees (bill_id, pmc_id, transfer_id)
        VALUES (
            p_bill_id, p_pmc_id,
            trust_post_fee(
                v_owner, v_markup.amount, p_event_at,
                format('Markup of %s%% on bill %s', trim_scale(v_markup.percent),
                       v_bill.reference)
            )
        );
    END IF;
    RETURN v_transfer_id;
END;
$$;

-- What a draw holds back for bills: each bill dated by the day and not set aside by then, with
-- its markup, unless its owner declined it.
CREATE OR REPLACE FUNCTION trust_unpaid_bills(p_account text, p_day date) RETURNS numeric
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(sum(b.amount + coalesce(m.amount, 0)), 0)
    FROM trust_bills AS b
    LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
    WHERE b.ledger_account_id = p_account AND b.bill_date <= p_day
      AND NOT EXISTS (
          SELECT p.bill_id
          FROM trust_bill_payments AS p
          JOIN pgledger_transfers AS t ON t.id = p.transfer_id
          WHERE p.bill_id = b.id AND p.step = 'set_aside'
            AND t.event_at < (p_day + 1)::timestamp AT TIME ZONE 'UTC'
      )
      -- A bill its owner declined holds nothing back from the day it was declined.
      AND NOT EXISTS (
          SELECT d.bill_id FROM trust_bill_declines AS d
          WHERE d.bill_id = b.id AND d.declined_on <= p_day
      )
$$;

CREATE OR REPLACE FUNCTION trust_report_owner_balances(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer,
    item text,
    owner text,
    property text,
    detail text,
    balance numeric,
    reserve numeric,
    bills numeric,
    available numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: owner balances are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, o.display_name AS owner_name,
               pr.display_name AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS held,
               coalesce(ag.reserve, 0.00) AS kept,
               -- trust_unpaid_bills (a bill with its markup), written out: a report runs with the caller's grants, and
               -- the app and the AI may call reports, not helpers.
               coalesce((
                   SELECT sum(b.amount + coalesce(m.amount, 0))
                   FROM trust_bills AS b
                   LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
                   WHERE b.ledger_account_id = t.ledger_account_id AND b.bill_date <= p_as_of
                     AND NOT EXISTS (
                         SELECT p.bill_id
                         FROM trust_bill_payments AS p
                         JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                         WHERE p.bill_id = b.id AND p.step = 'set_aside'
                           AND tr.event_at < v_cutoff
                     )
                     AND NOT EXISTS (
                         SELECT d.bill_id FROM trust_bill_declines AS d
                         WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
                     )
               ), 0.00) AS owed,
               ag.id, ag.fee_percent, ag.minimum_fee, ag.flat_fee
        FROM trust_ledger_accounts AS t
        JOIN trust_owners AS o ON o.id = t.owner_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        -- The agreement in force that day: the latest one starting on or before it.
        LEFT JOIN LATERAL (
            SELECT g.id, g.fee_percent, g.minimum_fee, g.flat_fee, g.reserve
            FROM trust_management_agreements AS g
            WHERE g.ledger_account_id = t.ledger_account_id AND g.starts_on <= p_as_of
            ORDER BY g.starts_on DESC
            LIMIT 1
        ) AS ag ON true
        WHERE t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS owner_name, NULL::text AS property_name,
               NULL::text AS account, 1 AS step, 'PMC' AS item, pmc.display_name AS detail,
               NULL::numeric AS held, NULL::numeric AS kept, NULL::numeric AS owed,
               NULL::numeric AS free
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, a.owner_name, a.property_name, a.account, 0, 'owner property',
               CASE
                   WHEN a.id IS NULL THEN 'no management agreement'
                   ELSE trim_scale(a.fee_percent)::text || '% of collected rent'
                        || ', minimum ' || to_char(a.minimum_fee, 'FM999999990.00')
                        || ', flat ' || to_char(a.flat_fee, 'FM999999990.00')
               END,
               a.held, a.kept, a.owed, greatest(a.held - a.kept - a.owed, 0.00)
        FROM accounts AS a
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(a.held), 0.00), coalesce(sum(a.kept), 0.00),
               coalesce(sum(a.owed), 0.00),
               coalesce(sum(greatest(a.held - a.kept - a.owed, 0.00)), 0.00)
        FROM accounts AS a
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.owner_name COLLATE "C", l.property_name COLLATE "C",
                        l.account, l.step
           )::integer,
           l.item,
           l.owner_name,
           l.property_name,
           l.detail,
           round(l.held, 2),
           round(l.kept, 2),
           round(l.owed, 2),
           round(l.free, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

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
        -- A bill's markup (migration 0083), with the management fees.
        SELECT tr.from_account_id, 2, -tr.amount
        FROM trust_bill_markup_fees AS f
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
    RAISE EXCEPTION 'shared_bills_and_markups cannot be rolled back: shared bills, markups and '
        'the fees taken are records';
END;
$$;
