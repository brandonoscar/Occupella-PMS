-- migrate:up
-- Vendor bills (founder decision, 2026-10-10: Buildium/AppFolio parity): what a vendor billed
-- for work on an owner's property, and paying it from that owner's money.
--
--   trust_bills           a vendor's bill against one owner's property account: the vendor's
--                         own reference (once per vendor, so a bill can't be entered twice),
--                         the bill and due dates, the amount and what it was for. Never edited.
--   trust_bill_approvals  the owner's approval of a bill above the approval limit.
--   trust_bill_payments   the two steps of paying a bill. 'set_aside' moves its amount from
--                         the owner's account to the vendor's (vendor_payable) in the same trust
--                         bank account, so it can't be drawn; 'paid' sends it out through that
--                         bank account's cash. Each step happens once per bill.
--
-- Management agreements gain approval_limit: the most a bill may be without the owner's
-- approval. Agreements made before this migration get 0, so every bill on them needs approval
-- until new terms say otherwise; a property with no agreement needs it for every bill.
--
-- A bill not yet set aside is still owed from the owner's money: a draw keeps it back on top of
-- the reserve, and the owner balances report shows it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A constant default fills existing rows without rewriting the table.
ALTER TABLE trust_management_agreements
ADD COLUMN approval_limit numeric NOT NULL DEFAULT 0 CHECK (approval_limit >= 0);

CREATE TABLE trust_bills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    reference text NOT NULL CHECK (reference <> ''),  -- the vendor's own number for the bill
    bill_date date NOT NULL,
    due_on date NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0),
    memo text NOT NULL CHECK (memo <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    UNIQUE (vendor_id, reference),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    CHECK (due_on >= bill_date)
);
CREATE INDEX trust_bills_ledger_account_id ON trust_bills (ledger_account_id);

CREATE TABLE trust_bill_approvals (
    bill_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    approved_on date NOT NULL,
    approved_by text NOT NULL CHECK (approved_by <> ''),  -- who approved for the owner, and how
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id)
);

CREATE TABLE trust_bill_payments (
    bill_id uuid NOT NULL,
    step text NOT NULL CHECK (step IN ('set_aside', 'paid')),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bill_id, step),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id)
);
CREATE INDEX trust_bill_payments_transfer_id ON trust_bill_payments (transfer_id);

-- Records: kept for good. A mistaken bill is answered with a vendor credit or a reversing
-- transfer, never by editing it.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bills
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bill_approvals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bill_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- A bill is paid from one owner's property: an owner_property ledger account of its PMC.
CREATE FUNCTION trust_check_bill_account() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: a bill is paid from an owner''s property account in its own '
            'PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_bill_account
BEFORE INSERT ON trust_bills
FOR EACH ROW EXECUTE FUNCTION trust_check_bill_account();

REVOKE ALL ON trust_bills, trust_bill_approvals, trust_bill_payments FROM PUBLIC;
-- The app enters bills and records the owner's approval; money moves through the functions
-- below.
GRANT SELECT, INSERT ON trust_bills, trust_bill_approvals TO trust_app;
GRANT SELECT ON trust_bill_payments TO trust_app;
GRANT SELECT ON trust_bills, trust_bill_approvals, trust_bill_payments TO trust_ai_agent;

-- Bills wait on the same lock as fees and draws, so the message names all of them.
-- CREATE OR REPLACE keeps the function's grants (none) from migration 20261010000016.
CREATE OR REPLACE FUNCTION trust_lock_owner_account(p_pmc_id uuid, p_account text)
RETURNS trust_ledger_accounts
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: move an owner''s money (fees, draws, bills) at READ COMMITTED, '
            'not %', current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT t.* INTO v_account
    FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = p_account AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account in PMC %', p_account, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_account;
END;
$$;

-- Bill p_bill_id of PMC p_pmc_id, with its owner's account locked so the steps of paying it
-- wait for each other and for fees and draws on that account; refused unless it exists.
CREATE FUNCTION trust_lock_bill(p_pmc_id uuid, p_bill_id uuid)
RETURNS trust_bills
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b WHERE b.id = p_bill_id AND b.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no bill % in PMC %', p_bill_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM trust_lock_owner_account(p_pmc_id, v_bill.ledger_account_id);
    RETURN v_bill;
END;
$$;

REVOKE ALL ON FUNCTION trust_lock_bill(uuid, uuid) FROM PUBLIC;

-- Set a bill's money aside: its amount moves from the owner's account to the vendor's account
-- in the same trust bank account. A bill over the approval limit of the agreement in force on
-- its date (0 without one) needs the owner's approval first. Safe to retry: a bill is set
-- aside once, and a retry returns that transfer. Runs as the owner.
CREATE FUNCTION trust_set_aside_bill(
    p_pmc_id uuid,
    p_bill_id uuid,
    p_event_at timestamptz
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
BEGIN
    SELECT p.transfer_id INTO v_transfer_id
    FROM trust_bill_payments AS p
    WHERE p.bill_id = p_bill_id AND p.step = 'set_aside';
    IF FOUND THEN
        RETURN v_transfer_id;  -- a retry: the bill is set aside already
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

REVOKE ALL ON FUNCTION trust_set_aside_bill(uuid, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_set_aside_bill(uuid, uuid, timestamptz) TO trust_app;

-- Pay a bill that was set aside: the money leaves the vendor's account through the trust bank
-- account's cash, no earlier than it was set aside. Safe to retry: a bill is paid once, and a
-- retry returns that transfer. Runs as the owner.
CREATE FUNCTION trust_pay_bill(
    p_pmc_id uuid,
    p_bill_id uuid,
    p_event_at timestamptz
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(p_pmc_id, p_bill_id);
    v_set_aside pgledger_transfers;
    v_cash text;
    v_transfer_id text;
BEGIN
    SELECT p.transfer_id INTO v_transfer_id
    FROM trust_bill_payments AS p
    WHERE p.bill_id = p_bill_id AND p.step = 'paid';
    IF FOUND THEN
        RETURN v_transfer_id;  -- a retry: the bill is paid already
    END IF;

    SELECT t.* INTO v_set_aside
    FROM trust_bill_payments AS p
    JOIN pgledger_transfers AS t ON t.id = p.transfer_id
    WHERE p.bill_id = p_bill_id AND p.step = 'set_aside';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: bill % is not set aside yet; set it aside, then pay it',
            v_bill.reference
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- pgledger dates a transfer with no date now.
    IF coalesce(p_event_at, now()) < v_set_aside.event_at THEN
        RAISE EXCEPTION 'trust: bill % was set aside at %; it can''t be paid before that',
            v_bill.reference, v_set_aside.event_at
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT c.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS v
    JOIN trust_ledger_accounts AS c ON c.bank_account_id = v.bank_account_id
    WHERE v.ledger_account_id = v_set_aside.to_account_id AND c.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_set_aside.to_account_id, v_cash, v_set_aside.amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', format('Bill %s paid', v_bill.reference))
    ) AS t;
    INSERT INTO trust_bill_payments (bill_id, step, pmc_id, transfer_id)
    VALUES (p_bill_id, 'paid', p_pmc_id, v_transfer_id);
    RETURN v_transfer_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_pay_bill(uuid, uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_pay_bill(uuid, uuid, timestamptz) TO trust_app;

-- What an owner's property still owes on bills at the end of day p_day (UTC): bills dated on or
-- before it whose money was not set aside by then.
CREATE FUNCTION trust_unpaid_bills(p_account text, p_day date)
RETURNS numeric
LANGUAGE sql
STABLE
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
$$;

REVOKE ALL ON FUNCTION trust_unpaid_bills(text, date) FROM PUBLIC;

-- A draw keeps back unpaid bills on top of the reserve. CREATE OR REPLACE keeps the grants of
-- migration 20261010000016.
CREATE OR REPLACE FUNCTION trust_draw_owner(
    p_pmc_id uuid,
    p_account text,
    p_request_key text,
    p_amount numeric,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    -- pgledger dates a transfer with no date now; the reserve and bills are that day's.
    v_day date := (coalesce(p_event_at, now()) AT TIME ZONE 'UTC')::date;
    v_taken trust_owner_draws;
    v_reserve numeric;
    v_bills numeric;
    v_available numeric;
    v_cash text;
    v_transfer_id text;
BEGIN
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: an owner draw needs a request key, so a retry can''t pay twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a draw pays a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT d.* INTO v_taken
    FROM trust_owner_draws AS d
    WHERE d.pmc_id = p_pmc_id AND d.request_key = p_request_key;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> p_account
            OR v_taken.amount <> coalesce(p_amount, v_taken.amount) THEN
            RAISE EXCEPTION 'trust: request key % already paid % from %',
                p_request_key, v_taken.amount, v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new draw; resend a key only to retry.';
        END IF;
        RETURN v_taken.amount;  -- a retry: the original draw
    END IF;

    v_reserve := coalesce((trust_agreement_on(p_account, v_day)).reserve, 0);
    v_bills := trust_unpaid_bills(p_account, v_day);
    SELECT a.balance - v_reserve - v_bills INTO v_available
    FROM pgledger_accounts AS a WHERE a.id = p_account;
    IF coalesce(p_amount, v_available) > v_available OR v_available <= 0 THEN
        RAISE EXCEPTION 'trust: % is available to draw from % (its balance less a reserve of % '
            'and unpaid bills of %); % is too much', greatest(v_available, 0), p_account,
            v_reserve, v_bills, coalesce(p_amount, v_available)
            USING ERRCODE = 'check_violation';
    END IF;

    -- Money reaches an owner's account only through its trust bank account's cash, so a
    -- balance to draw means that cash account exists.
    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_account.bank_account_id AND t.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(p_account, v_cash, coalesce(p_amount, v_available))::transfer_request],
        p_event_at,
        jsonb_build_object('memo', 'Owner draw')
    ) AS t;

    INSERT INTO trust_owner_draws (pmc_id, request_key, ledger_account_id, amount, transfer_id)
    VALUES (p_pmc_id, p_request_key, p_account, coalesce(p_amount, v_available), v_transfer_id);
    RETURN coalesce(p_amount, v_available);
END;
$$;

-- The owner balances report gains a column: unpaid bills, kept back like the reserve, so what
-- it shows as available is what a draw that day may pay. A new column changes the result
-- type, which CREATE OR REPLACE can't do, so it is dropped and made again with its grants.
-- Nothing calls it yet but the tests (Phase 0 has no API), and both steps run in this
-- migration's one transaction, so no caller ever finds it missing.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_owner_balances(uuid, date);

-- Report: what each owner's property holds at the end of a day (UTC), the reserve its agreement
-- keeps back on that day, its unpaid bills, and what is available to pay the owner; then the
-- totals. Owners and properties sort by byte (COLLATE "C"). An unknown PMC or a missing date is
-- refused.
CREATE FUNCTION trust_report_owner_balances(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
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

REVOKE ALL ON FUNCTION trust_report_owner_balances(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_owner_balances(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'vendor_bills cannot be rolled back: bills and their payments are records';
END;
$$;
