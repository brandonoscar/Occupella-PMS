-- migrate:up
-- A bill's life in plain steps (ADR 0010, the design pass's part A): no "set aside" step for staff.
--
--   Held       a bill not paid yet holds its amount (and markup) from its owner's property account
--              without moving money: trust_bill_holds lays each account's balance over its open
--              bills dated by today, the one due first first, and says what is short of each and
--              what the owner must send to cover it. Draws already hold these bills back
--              (trust_unpaid_bills), so a draw never pays held money.
--   Paid       a payment of the whole bill or a part of it, once per request key
--              (trust_pay_bill_part; trust_pay_bill pays what is left). The first one takes the
--              whole bill and its markup from the owner's account, as setting it aside did, and
--              each pays its part from the trust bank account's cash; trust_bill_payments keeps
--              one 'paid' row per part, so each is held to the disburse role, the signers and the
--              payout method as before, and each counts toward the vendor's year (1099-NEC).
--   Void       a bill entered by mistake, voided by a named staff member with why, while nothing
--              was taken for it: a row of trust_bill_declines marked void, so everything that
--              leaves out a bill its owner declined (draws, the reports, archiving) leaves it out.
--   Check      a check's number with how a payout went (trust_payout_methods.check_number), so a
--              bank line with that number and amount is matched to it when a statement comes in.
--   Asked      an email asking a bill's owner to send what their account is short of it
--              ('bill_funds_needed', once per bill a day).
--
-- A bill set aside before this migration stays as it was: its money left the owner's account then,
-- it is paid as before, and it can't be voided (a decline couldn't either).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- --- bills paid in parts -------------------------------------------------------------------------

-- A bill's payments are numbered from 1; a bill never comes near an int's limit of parts.
-- squawk-ignore prefer-bigint-over-int
ALTER TABLE trust_bill_payments ADD COLUMN part integer NOT NULL DEFAULT 1;
ALTER TABLE trust_bill_payments ADD COLUMN request_key text;
ALTER TABLE trust_bill_payments ADD CONSTRAINT trust_bill_payments_part_check
CHECK (part >= 1 AND (step = 'paid' OR part = 1)) NOT VALID;
-- The checks below validate columns just added, which every row holds as their default (1, null,
-- false), on tables of a few rows a bill; each reads its table once, under this migration's
-- lock_timeout.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_payments VALIDATE CONSTRAINT trust_bill_payments_part_check;
ALTER TABLE trust_bill_payments ADD CONSTRAINT trust_bill_payments_request_key_check
CHECK (request_key IS NULL OR (step = 'paid' AND request_key ~ '\S' AND length(request_key) <= 200))
NOT VALID;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_payments VALIDATE CONSTRAINT trust_bill_payments_request_key_check;
-- One set-aside per bill, and its payments numbered from 1.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_bill_payments DROP CONSTRAINT trust_bill_payments_pkey;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_bill_payments_pkey ON trust_bill_payments (bill_id, step, part);
ALTER TABLE trust_bill_payments ADD CONSTRAINT trust_bill_payments_pkey
PRIMARY KEY USING INDEX trust_bill_payments_pkey;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_bill_payments_request_key ON trust_bill_payments (pmc_id, request_key)
WHERE request_key IS NOT NULL;

-- --- a check's number ------------------------------------------------------------------------------

ALTER TABLE trust_payout_methods ADD COLUMN check_number text;
ALTER TABLE trust_payout_methods ADD CONSTRAINT trust_payout_methods_check_number_check
CHECK (check_number IS NULL OR (method = 'check' AND check_number ~ '^[0-9]{1,15}$')) NOT VALID;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_payout_methods VALIDATE CONSTRAINT trust_payout_methods_check_number_check;

-- --- voids ---------------------------------------------------------------------------------------

ALTER TABLE trust_bill_declines ADD COLUMN void boolean NOT NULL DEFAULT false;
-- The staff member who recorded it (a decline staff record for the owner, or a void).
ALTER TABLE trust_bill_declines ADD COLUMN recorded_by uuid;
ALTER TABLE trust_bill_declines ADD CONSTRAINT trust_bill_declines_pmc_id_recorded_by_fkey
FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id) NOT VALID;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_declines VALIDATE CONSTRAINT trust_bill_declines_pmc_id_recorded_by_fkey;
-- A void is staff's, never the owner's in the portal, and names who.
ALTER TABLE trust_bill_declines ADD CONSTRAINT trust_bill_declines_void_check
CHECK (NOT void OR (owner_id IS NULL AND recorded_by IS NOT NULL)) NOT VALID;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_declines VALIDATE CONSTRAINT trust_bill_declines_void_check;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_bill_declines_recorded_by ON trust_bill_declines (pmc_id, recorded_by);
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_bill_declines
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- One decision per bill: the owner's approval or decline, or staff's void, which may follow an
-- approval (a bill entered by mistake) but nothing taken for the bill.
CREATE OR REPLACE FUNCTION trust_check_bill_decision() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills;
    v_owner uuid;
    -- Only a decline row says whether it is a void; an approval never is.
    v_void boolean := coalesce((to_jsonb(NEW) ->> 'void')::boolean, false);
    v_was trust_bill_declines;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b
    WHERE b.id = NEW.bill_id AND b.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the key to trust_bills refuses it
    END IF;
    PERFORM trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    SELECT d.* INTO v_was FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id;
    IF FOUND AND v_was.void THEN
        RAISE EXCEPTION 'trust: bill % is void already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF FOUND AND v_void THEN
        RAISE EXCEPTION 'trust: bill % was declined by its owner; it is void already',
            v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF NOT v_void AND (
        FOUND OR EXISTS (SELECT a.bill_id FROM trust_bill_approvals AS a
                         WHERE a.bill_id = NEW.bill_id)
    ) THEN
        RAISE EXCEPTION 'trust: bill % has its owner''s decision already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_bill_declines' AND EXISTS (
        SELECT p.bill_id FROM trust_bill_payments AS p
        WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside'
    ) THEN
        IF v_void THEN
            RAISE EXCEPTION 'trust: money was taken from the owner''s account for bill % '
                'already; it can''t be voided', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
        RAISE EXCEPTION 'trust: money was taken from the owner''s account for bill % '
            'already; it can''t be declined', v_bill.reference
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

CREATE OR REPLACE FUNCTION trust_fill_bill_markup() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    v_percent numeric;
BEGIN
    IF EXISTS (SELECT 1 FROM trust_bill_payments AS p
               WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside') THEN
        RAISE EXCEPTION 'trust: money was taken for bill % already; a markup is added before',
            v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id) THEN
        RAISE EXCEPTION 'trust: bill % was declined or voided; it takes no markup',
            v_bill.reference
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

CREATE OR REPLACE FUNCTION trust_set_aside_bill(
    p_pmc_id uuid, p_bill_id uuid, p_event_at timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
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
    IF FOUND AND v_declined.void THEN
        RAISE EXCEPTION 'trust: bill % is void (%); it is not paid',
            v_bill.reference, v_declined.reason
            USING ERRCODE = 'check_violation';
    ELSIF FOUND THEN
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

-- Pays an amount of a bill: the first payment takes the whole bill (and its markup) from the
-- owner's account into the vendor's, as setting it aside did; each pays its part from the vendor's
-- account through the trust bank account's cash. The caller holds the bill's lock.
CREATE FUNCTION trust_pay_bill_amount(
    p_bill trust_bills, p_amount numeric, p_request_key text, p_event_at timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_set_aside pgledger_transfers;
    v_paid numeric;
    v_left numeric;
    v_part integer;
    v_cash text;
    v_transfer_id text;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 OR p_amount <> round(p_amount, 2) THEN
        RAISE EXCEPTION 'trust: a bill payment is a positive amount in cents, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT o.bank_account_id INTO v_bank
    FROM trust_ledger_accounts AS o WHERE o.ledger_account_id = p_bill.ledger_account_id;
    IF NOT EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS t
        WHERE t.bank_account_id = v_bank AND t.kind = 'vendor_payable'
          AND t.vendor_id = p_bill.vendor_id
    ) THEN
        PERFORM trust_open_vendor_account(p_bill.pmc_id, v_bank, p_bill.vendor_id);
    END IF;
    -- A first payment posts twice (the owner's account to the vendor's, then the vendor's to the
    -- cash). pgledger locks the accounts of one posting in id order; locking every account this
    -- payment may move here, in that same order, keeps a payment from deadlocking with a rent
    -- payment or a draw that locks some of them in one posting of its own.
    PERFORM 1
    FROM pgledger_accounts AS a
    WHERE a.id IN (
        SELECT l.ledger_account_id
        FROM trust_ledger_accounts AS l
        WHERE l.bank_account_id = v_bank
          AND (l.ledger_account_id = p_bill.ledger_account_id
               OR l.kind IN ('bank_cash', 'pmc_income')
               OR (l.kind = 'vendor_payable' AND l.vendor_id = p_bill.vendor_id))
    )
    ORDER BY a.id
    FOR UPDATE;
    PERFORM trust_set_aside_bill(p_bill.pmc_id, p_bill.id, p_event_at);

    SELECT t.* INTO v_set_aside
    FROM trust_bill_payments AS p
    JOIN pgledger_transfers AS t ON t.id = p.transfer_id
    WHERE p.bill_id = p_bill.id AND p.step = 'set_aside';
    -- pgledger dates a transfer with no date now.
    IF coalesce(p_event_at, now()) < v_set_aside.event_at THEN
        RAISE EXCEPTION 'trust: money was taken for bill % at %; it can''t be paid before that',
            p_bill.reference, v_set_aside.event_at
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(sum(t.amount), 0), coalesce(max(p.part), 0) INTO v_paid, v_part
    FROM trust_bill_payments AS p
    JOIN pgledger_transfers AS t ON t.id = p.transfer_id
    WHERE p.bill_id = p_bill.id AND p.step = 'paid';
    v_left := p_bill.amount - v_paid;
    IF v_left = 0 THEN
        RAISE EXCEPTION 'trust: bill % is paid in full already', p_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_amount > v_left THEN
        RAISE EXCEPTION 'trust: bill % has % left to pay; % is more than that',
            p_bill.reference, v_left, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT c.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS c
    WHERE c.bank_account_id = v_bank AND c.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_set_aside.to_account_id, v_cash, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', CASE WHEN p_amount = v_left
            THEN format('Bill %s paid', p_bill.reference)
            ELSE format('Bill %s paid in part', p_bill.reference) END)
    ) AS t;
    INSERT INTO trust_bill_payments (bill_id, step, part, pmc_id, transfer_id, request_key)
    VALUES (p_bill.id, 'paid', v_part + 1, p_bill.pmc_id, v_transfer_id, p_request_key);
    RETURN v_transfer_id;
END;
$$;
REVOKE ALL ON FUNCTION trust_pay_bill_amount(trust_bills, numeric, text, timestamptz) FROM PUBLIC;

-- Pays what is left of a bill, once: the same again returns its last payment.
CREATE OR REPLACE FUNCTION trust_pay_bill(
    p_pmc_id uuid, p_bill_id uuid, p_event_at timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(p_pmc_id, p_bill_id);
    v_paid numeric;
    v_transfer_id text;
BEGIN
    SELECT coalesce(sum(t.amount), 0) INTO v_paid
    FROM trust_bill_payments AS p
    JOIN pgledger_transfers AS t ON t.id = p.transfer_id
    WHERE p.bill_id = p_bill_id AND p.step = 'paid';
    IF v_paid >= v_bill.amount THEN
        SELECT p.transfer_id INTO v_transfer_id
        FROM trust_bill_payments AS p
        WHERE p.bill_id = p_bill_id AND p.step = 'paid'
        ORDER BY p.part DESC LIMIT 1;
        RETURN v_transfer_id;  -- a retry: the bill is paid already
    END IF;
    RETURN trust_pay_bill_amount(v_bill, v_bill.amount - v_paid, NULL, p_event_at);
END;
$$;

-- Pays part of a bill (or all that is left), once per request key: the same key again returns
-- that payment, and the key with another bill or amount is refused.
CREATE FUNCTION trust_pay_bill_part(
    p_pmc_id uuid, p_bill_id uuid, p_amount numeric, p_request_key text, p_event_at timestamptz
) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(p_pmc_id, p_bill_id);
    v_done trust_bill_payments;
    v_done_amount numeric;
BEGIN
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: a bill payment needs a request key, so a retry can''t pay twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.* INTO v_done
    FROM trust_bill_payments AS p
    WHERE p.pmc_id = p_pmc_id AND p.request_key = p_request_key;
    IF FOUND THEN
        SELECT t.amount INTO v_done_amount
        FROM pgledger_transfers AS t WHERE t.id = v_done.transfer_id;
        IF v_done.bill_id <> p_bill_id OR v_done_amount <> p_amount THEN
            RAISE EXCEPTION 'trust: request key % already paid % of a bill',
                p_request_key, v_done_amount
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new payment; resend a key only to retry.';
        END IF;
        RETURN v_done.transfer_id;  -- a retry: the original payment
    END IF;
    RETURN trust_pay_bill_amount(v_bill, p_amount, p_request_key, p_event_at);
END;
$$;
REVOKE ALL ON FUNCTION trust_pay_bill_part(uuid, uuid, numeric, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_pay_bill_part(uuid, uuid, numeric, text, timestamptz) TO trust_app;

-- What each open bill of a PMC dated by today (UTC) holds of its owner's property account now:
-- each account's balance (none when below zero) laid over its bills not yet paid or void, in the
-- order they are due, each bill with its markup. Short is what the balance leaves uncovered of
-- the bill; needed is what the owner must send for this bill and those due before it to be
-- covered. Money moves only when a bill is paid; a draw holds the same bills back
-- (trust_unpaid_bills), so it never pays what is held here.
CREATE FUNCTION trust_bill_holds(p_pmc_id uuid)
RETURNS TABLE (bill_id uuid, held numeric, short numeric, needed numeric)
    LANGUAGE sql STABLE
    SET search_path = public, pg_temp
AS $$
    WITH open AS (
        SELECT b.id, b.ledger_account_id, b.due_on, b.bill_date, b.reference,
               b.amount + coalesce(m.amount, 0) AS need
        FROM trust_bills AS b
        LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
        WHERE b.pmc_id = p_pmc_id
          AND b.bill_date <= (now() AT TIME ZONE 'UTC')::date
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                          WHERE p.bill_id = b.id AND p.step = 'set_aside')
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = b.id)
    ),
    running AS (
        SELECT o.id, o.need, greatest(a.balance, 0) AS balance,
               sum(o.need) OVER (
                   PARTITION BY o.ledger_account_id
                   ORDER BY o.due_on, o.bill_date, o.reference COLLATE "C", o.id
               ) AS through
        FROM open AS o
        JOIN pgledger_accounts AS a ON a.id = o.ledger_account_id
    ),
    laid AS (
        SELECT r.id, r.need, r.through, r.balance,
               least(r.need, greatest(r.balance - (r.through - r.need), 0)) AS held
        FROM running AS r
    )
    SELECT l.id, l.held, l.need - l.held, greatest(l.through - l.balance, 0)
    FROM laid AS l
$$;
REVOKE ALL ON FUNCTION trust_bill_holds(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_bill_holds(uuid) TO trust_app;

-- --- asking an owner for what a bill is short of -------------------------------------------------

-- The kinds allowed so far are read from the check itself, as migration 0151 does.
DO $$
DECLARE
    v_kinds text;
BEGIN
    SELECT string_agg(quote_literal(k.kind), ', ' ORDER BY k.n) INTO v_kinds
    FROM (
        SELECT m[1] AS kind, n
        FROM pg_constraint AS c,
             regexp_matches(pg_get_constraintdef(c.oid), '''([a-z_]+)''::text', 'g')
                 WITH ORDINALITY AS r (m, n)
        WHERE c.conrelid = 'trust_emails'::regclass AND c.conname = 'trust_emails_kind_check'
        UNION ALL
        SELECT 'bill_funds_needed', 1000
    ) AS k;
    ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_kind_check;
    EXECUTE format(
        'ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_kind_check'
        ' CHECK (kind IN (%s)) NOT VALID', v_kinds
    );
END;
$$;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_emails DROP CONSTRAINT trust_emails_check;
ALTER TABLE trust_emails ADD CONSTRAINT trust_emails_check
CHECK ((kind IN ('bill_approval', 'bill_funds_needed')) = (bill_id IS NOT NULL)) NOT VALID;
-- Once per bill a day (UTC), so a second click the same day asks nobody twice.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_funds_request
ON trust_emails (bill_id, ((created_at AT TIME ZONE 'UTC')::date))
WHERE kind = 'bill_funds_needed';

-- --- what reads "paid" as paid in full ----------------------------------------------------------

CREATE OR REPLACE FUNCTION trust_check_archival() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'UTC')::date;
    v_name text;
BEGIN
    IF trust_is_archived(
        NEW.property_id, NEW.unit_id, NEW.tenant_id, NEW.owner_id, NEW.vendor_id
    ) = NEW.archived THEN
        RAISE EXCEPTION 'trust: it is % already', CASE WHEN NEW.archived THEN 'archived' ELSE 'not archived' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.archived THEN
        RETURN NEW;
    END IF;
    IF NEW.vendor_id IS NOT NULL THEN
        SELECT v.display_name INTO v_name FROM trust_vendors AS v WHERE v.id = NEW.vendor_id;
        IF EXISTS (SELECT 1 FROM trust_bills AS bill
                   WHERE bill.vendor_id = NEW.vendor_id
                     -- Not paid in full (a bill may be paid in parts, migration 0152).
                     AND (SELECT coalesce(sum(t.amount), 0) FROM trust_bill_payments AS p
                          JOIN pgledger_transfers AS t ON t.id = p.transfer_id
                          WHERE p.bill_id = bill.id AND p.step = 'paid') < bill.amount
                     AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                                     WHERE d.bill_id = bill.id))
        THEN
            RAISE EXCEPTION 'trust: % has a bill not paid; pay or decline it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                   JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                   WHERE a.kind = 'vendor_payable' AND a.vendor_id = NEW.vendor_id
                     AND b.balance <> 0)
        THEN
            RAISE EXCEPTION 'trust: % has money set aside for them; pay it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_work_orders AS w
                   WHERE w.pmc_id = NEW.pmc_id
                     AND NOT EXISTS (SELECT 1 FROM trust_work_order_steps AS c
                                     WHERE c.work_order_id = w.id
                                       AND c.step IN ('completed', 'cancelled'))
                     AND (SELECT s.vendor_id FROM trust_work_order_steps AS s
                          WHERE s.work_order_id = w.id AND s.step = 'assigned'
                          ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                          LIMIT 1) = NEW.vendor_id)
        THEN
            RAISE EXCEPTION 'trust: % has a work order open; complete, cancel or reassign it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_recurring_bills AS r
                   WHERE r.vendor_id = NEW.vendor_id
                     AND NOT EXISTS (SELECT 1 FROM trust_recurring_bill_ends AS e
                                     WHERE e.recurring_bill_id = r.id AND e.ends_on < v_today))
           OR EXISTS (SELECT 1 FROM trust_recurring_work_orders AS r
                      WHERE r.vendor_id = NEW.vendor_id
                        AND NOT EXISTS (SELECT 1 FROM trust_recurring_work_order_ends AS e
                                        WHERE e.recurring_work_order_id = r.id
                                          AND e.ends_on < v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a recurring bill or job still running; stop it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.tenant_id IS NOT NULL THEN
        SELECT t.display_name INTO v_name FROM trust_tenants AS t WHERE t.id = NEW.tenant_id;
        IF EXISTS (SELECT 1 FROM trust_lease_tenants AS lt JOIN trust_leases AS l ON l.id = lt.lease_id
                   WHERE lt.tenant_id = NEW.tenant_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % is on a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                   JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                   WHERE a.tenant_id = NEW.tenant_id AND b.balance <> 0)
        THEN
            RAISE EXCEPTION 'trust: % has prepaid rent or a deposit held; return it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.unit_id IS NOT NULL THEN
        SELECT u.display_name INTO v_name FROM trust_units AS u WHERE u.id = NEW.unit_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l
                   WHERE l.unit_id = NEW.unit_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.property_id IS NOT NULL THEN
        SELECT p.display_name INTO v_name FROM trust_properties AS p WHERE p.id = NEW.property_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
                   WHERE u.property_id = NEW.property_id
                     AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT o.display_name INTO v_name FROM trust_owners AS o WHERE o.id = NEW.owner_id;
    END IF;
    -- A property's owners' accounts, or an owner's accounts: no money held, no bill left open.
    IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
               JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
               WHERE a.kind = 'owner_property' AND b.balance <> 0
                 AND (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id))
    THEN
        RAISE EXCEPTION 'trust: % holds % money; draw it before archiving', v_name,
            CASE WHEN NEW.owner_id IS NULL THEN 'its owners''' ELSE 'their' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bills AS bill
               JOIN trust_ledger_accounts AS a ON a.ledger_account_id = bill.ledger_account_id
               WHERE (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id)
                 AND (SELECT coalesce(sum(t.amount), 0) FROM trust_bill_payments AS p
                      JOIN pgledger_transfers AS t ON t.id = p.transfer_id
                      WHERE p.bill_id = bill.id AND p.step = 'paid') < bill.amount
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                                 WHERE d.bill_id = bill.id))
    THEN
        RAISE EXCEPTION 'trust: % has a bill not paid; pay or decline it before archiving', v_name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_unpaid_bills(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, vendor text, reference text, property text, bill_date date, due_on date, days_past_due integer, bucket text, amount numeric, set_aside numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
        SELECT p.bill_id, p.step, t.amount
        FROM trust_bill_payments AS p
        JOIN pgledger_transfers AS t ON t.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND t.event_at < v_cutoff
    ),
    -- What was paid of each bill by the day's end: a bill may be paid in parts (migration 0152).
    paid AS (
        SELECT s.bill_id, sum(s.amount) AS amount FROM steps AS s
        WHERE s.step = 'paid' GROUP BY s.bill_id
    ),
    unpaid AS (
        SELECT v.display_name AS vendor_name, b.reference AS ref, pr.display_name AS place,
               b.bill_date AS billed, b.due_on AS due, greatest(p_as_of - b.due_on, 0) AS late,
               b.amount - coalesce(pd.amount, 0) AS owed,
               CASE WHEN EXISTS (
                   SELECT s.bill_id FROM steps AS s
                   WHERE s.bill_id = b.id AND s.step = 'set_aside'
               ) THEN b.amount - coalesce(pd.amount, 0) ELSE 0.00 END AS held
        FROM trust_bills AS b
        JOIN trust_vendors AS v ON v.id = b.vendor_id
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN paid AS pd ON pd.bill_id = b.id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND coalesce(pd.amount, 0) < b.amount
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

CREATE OR REPLACE FUNCTION trust_report_vendor_directory(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, vendor text, w9_on_file text, open_work_orders integer, unpaid_bills integer, unpaid numeric, paid_this_year numeric, last_paid_on date)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
    v_year_start timestamptz := date_trunc('year', p_as_of::timestamp) AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a vendor directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- Each payment of a bill by the end of the day (a bill may be paid in parts), and when
        -- the bank paid it.
        SELECT b.vendor_id, tr.amount, tr.event_at
        FROM trust_bills AS b
        JOIN trust_bill_payments AS p ON p.bill_id = b.id AND p.step = 'paid'
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE b.pmc_id = p_pmc_id AND tr.event_at < v_cutoff
    ),
    unpaid AS (
        -- Entered by the day, not paid in full by its end and not declined by then: what is left
        -- of each.
        SELECT x.vendor_id, x.amount
        FROM (
            SELECT b.vendor_id, b.amount - coalesce((
                SELECT sum(tr.amount) FROM trust_bill_payments AS p
                JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                WHERE p.bill_id = b.id AND p.step = 'paid' AND tr.event_at < v_cutoff
            ), 0) AS amount
            FROM trust_bills AS b
            WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
              AND NOT EXISTS (
                  SELECT d.bill_id FROM trust_bill_declines AS d
                  WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
              )
        ) AS x
        WHERE x.amount > 0
    ),
    assigned AS (
        -- Each work order open at the day's end, and the vendor it was last assigned to.
        SELECT (SELECT s.vendor_id FROM trust_work_order_steps AS s
                WHERE s.work_order_id = w.id AND s.step = 'assigned' AND s.taken_at < v_cutoff
                ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                LIMIT 1) AS vendor_id
        FROM trust_work_orders AS w
        WHERE w.pmc_id = p_pmc_id AND w.opened_at < v_cutoff
          AND NOT EXISTS (
              SELECT s.work_order_id FROM trust_work_order_steps AS s
              WHERE s.work_order_id = w.id AND s.step IN ('completed', 'cancelled')
                AND s.taken_at < v_cutoff
          )
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS vendor_id, 'PMC' AS what,
               pmc.display_name AS label, NULL::text AS w9, NULL::integer AS orders,
               NULL::integer AS bills, NULL::numeric AS owed, NULL::numeric AS year_paid,
               NULL::date AS last_paid
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, v.display_name, v.id, 'vendor', v.display_name,
               -- A W-9 kept by the day's end and not removed by then.
               CASE WHEN EXISTS (
                   SELECT d.id FROM trust_documents AS d
                   WHERE d.vendor_id = v.id AND d.kind = 'w9' AND d.created_at < v_cutoff
                     AND NOT EXISTS (
                         SELECT r.document_id FROM trust_document_removals AS r
                         WHERE r.document_id = d.id AND r.created_at < v_cutoff
                     )
               ) THEN 'yes' ELSE 'no' END,
               (SELECT count(*) FROM assigned AS a WHERE a.vendor_id = v.id)::integer,
               (SELECT count(*) FROM unpaid AS u WHERE u.vendor_id = v.id)::integer,
               coalesce((SELECT sum(u.amount) FROM unpaid AS u WHERE u.vendor_id = v.id), 0.00),
               coalesce((SELECT sum(p.amount) FROM paid AS p
                         WHERE p.vendor_id = v.id AND p.event_at >= v_year_start), 0.00),
               (SELECT max((p.event_at AT TIME ZONE 'UTC')::date) FROM paid AS p
                WHERE p.vendor_id = v.id)
        FROM trust_vendors AS v
        WHERE v.pmc_id = p_pmc_id
    )
    SELECT (row_number() OVER (ORDER BY l.part, l.who COLLATE "C", l.vendor_id))::integer,
           l.what, l.label, l.w9, l.orders, l.bills, l.owed, l.year_paid, l.last_paid
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'bill_lifecycle cannot be rolled back: bill payments, voids and their emails '
        'are records';
END;
$$;
