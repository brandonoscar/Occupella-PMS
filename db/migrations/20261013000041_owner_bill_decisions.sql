-- migrate:up
-- An owner's decision on a bill (founder decision, 2026-10-13, product item 2: rental owners
-- approve or decline bills over their agreement's approval limit in the owner portal; no money
-- moves).
--
--   trust_bill_declines   a bill its owner declined: on which day, by whom (as recorded) and
--                         why. A declined bill is never set aside or paid, and from the day it
--                         was declined it no longer holds money back from the owner's draws
--                         (trust_unpaid_bills) nor shows as unpaid (trust_report_unpaid_bills,
--                         trust_report_owner_balances).
--   trust_bill_approvals  gains owner_id: the owner who approved it in the portal, NULL when
--                         staff recorded an approval the owner gave them.
--
-- A bill takes one decision: an approval or a decline, never both, and a decline only before it
-- is set aside. A decision taken in the portal names the owner of the bill's property account.
-- Decisions and setting a bill aside take the owner account's lock (trust_lock_bill), so they
-- wait for each other.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_bill_declines (
    bill_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    declined_on date NOT NULL,
    declined_by text NOT NULL CHECK (declined_by <> '' AND length(declined_by) <= 200),
    reason text NOT NULL CHECK (reason <> '' AND length(reason) <= 1000),
    owner_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id)
);

-- Every existing approval gets NULL: staff recorded it.
ALTER TABLE trust_bill_approvals ADD COLUMN owner_id uuid;
-- Every row has owner_id NULL, which a key doesn't check, and approvals run to one per bill, so
-- validating it scans little; a separate NOT VALID / VALIDATE step would buy nothing.
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_bill_approvals ADD CONSTRAINT trust_bill_approvals_owner_fkey
FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id);

-- One decision per bill, a decline only before the bill is set aside, and a decision taken in
-- the portal only by the owner of the bill's property account. Runs as the table owner to take
-- the owner account's lock, as trust_set_aside_bill does.
CREATE FUNCTION trust_check_bill_decision() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills;
    v_owner uuid;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b
    WHERE b.id = NEW.bill_id AND b.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the key to trust_bills refuses it
    END IF;
    PERFORM trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    IF EXISTS (SELECT a.bill_id FROM trust_bill_approvals AS a WHERE a.bill_id = NEW.bill_id)
       OR EXISTS (SELECT d.bill_id FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id)
    THEN
        RAISE EXCEPTION 'trust: bill % has its owner''s decision already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_bill_declines' AND EXISTS (
        SELECT p.bill_id FROM trust_bill_payments AS p
        WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside'
    ) THEN
        RAISE EXCEPTION 'trust: bill % is set aside already; it can''t be declined',
            v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.owner_id IS NOT NULL THEN
        SELECT t.owner_id INTO v_owner
        FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
        IF v_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'trust: only the owner of bill %''s property account decides it in '
                'the portal', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION trust_check_bill_decision() FROM PUBLIC;

CREATE TRIGGER trust_bill_decision BEFORE INSERT ON trust_bill_approvals
FOR EACH ROW EXECUTE FUNCTION trust_check_bill_decision();
CREATE TRIGGER trust_bill_decision BEFORE INSERT ON trust_bill_declines
FOR EACH ROW EXECUTE FUNCTION trust_check_bill_decision();

-- Records: kept for good.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bill_declines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bill_declines
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_bill_declines FROM PUBLIC;
GRANT SELECT, INSERT ON trust_bill_declines TO trust_app;
GRANT SELECT ON trust_bill_declines TO trust_ai_agent;

-- A declined bill is never set aside.
CREATE OR REPLACE FUNCTION trust_set_aside_bill(p_pmc_id uuid, p_bill_id uuid, p_event_at timestamp with time zone) RETURNS text
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

    SELECT t.bank_account_id INTO v_bank
    FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
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
    RETURN v_transfer_id;
END;
$$;

-- A declined bill holds nothing back from the owner's draws from the day it was declined.
CREATE OR REPLACE FUNCTION trust_unpaid_bills(p_account text, p_day date) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(sum(b.amount), 0)
    FROM trust_bills AS b
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

-- Nor does it show in what each owner has available...
CREATE OR REPLACE FUNCTION trust_report_owner_balances(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, owner text, property text, detail text, balance numeric, reserve numeric, bills numeric, available numeric)
LANGUAGE plpgsql STABLE
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
               -- trust_unpaid_bills, written out: a report runs with the caller's grants, and
               -- the app and the AI may call reports, not helpers.
               coalesce((
                   SELECT sum(b.amount)
                   FROM trust_bills AS b
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

-- ...or among the unpaid bills.
CREATE OR REPLACE FUNCTION trust_report_unpaid_bills(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, vendor text, reference text, property text, bill_date date, due_on date, days_past_due integer, bucket text, amount numeric, set_aside numeric)
LANGUAGE plpgsql STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: unpaid bills are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH steps AS (
        SELECT p.bill_id, p.step
        FROM trust_bill_payments AS p
        JOIN pgledger_transfers AS t ON t.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND t.event_at < v_cutoff
    ),
    unpaid AS (
        SELECT v.display_name AS vendor_name, b.reference AS ref, pr.display_name AS place,
               b.bill_date AS billed, b.due_on AS due, greatest(p_as_of - b.due_on, 0) AS late,
               b.amount AS owed,
               CASE WHEN EXISTS (
                   SELECT s.bill_id FROM steps AS s
                   WHERE s.bill_id = b.id AND s.step = 'set_aside'
               ) THEN b.amount ELSE 0.00 END AS held
        FROM trust_bills AS b
        JOIN trust_vendors AS v ON v.id = b.vendor_id
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (
              SELECT s.bill_id FROM steps AS s WHERE s.bill_id = b.id AND s.step = 'paid'
          )
          -- Not a bill its owner declined by the day: it won't be paid.
          AND NOT EXISTS (
              SELECT d.bill_id FROM trust_bill_declines AS d
              WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
          )
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS vendor_name, 1 AS step, NULL::date AS due,
               NULL::text AS ref, 'PMC' AS item, pmc.display_name AS place, NULL::date AS billed,
               NULL::integer AS late, NULL::numeric AS owed, NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, 2, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, u.vendor_name, 0, u.due, u.ref, 'bill', u.place, u.billed, u.late, u.owed,
               u.held
        FROM unpaid AS u
        UNION ALL
        SELECT 1, u.vendor_name, 1, NULL, NULL, 'vendor total', NULL, NULL, NULL,
               sum(u.owed), sum(u.held)
        FROM unpaid AS u GROUP BY u.vendor_name
        UNION ALL
        SELECT 2, NULL, 0, NULL, NULL, 'total', NULL, NULL, NULL,
               coalesce(sum(u.owed), 0.00), coalesce(sum(u.held), 0.00)
        FROM unpaid AS u
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.vendor_name COLLATE "C", l.step, l.due,
                        l.ref COLLATE "C"
           )::integer,
           l.item,
           l.vendor_name,
           l.ref,
           -- The PMC's name and the day sit in the property column of the first two lines.
           l.place,
           l.billed,
           l.due,
           l.late,
           CASE
               WHEN l.late IS NULL THEN NULL
               WHEN l.late = 0 THEN 'current'
               WHEN l.late <= 30 THEN '1-30'
               WHEN l.late <= 60 THEN '31-60'
               WHEN l.late <= 90 THEN '61-90'
               ELSE 'over 90'
           END,
           round(l.owed, 2),
           round(l.held, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'owner_bill_decisions cannot be rolled back: declines are records';
END;
$$;
