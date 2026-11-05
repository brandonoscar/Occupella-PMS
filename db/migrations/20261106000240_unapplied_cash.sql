-- migrate:up
-- Unapplied cash (the founder's chart of accounts pass, part 2, 2026-11-06): money that came into
-- an operating trust bank account that can't yet be matched to a tenant or a charge. It is held in
-- an account of its own (code 2250 in the chart), one per trust bank account, until staff apply it
-- to a lease or refund it.
--
--   trust_ledger_accounts.kind  'unapplied_cash', holding nobody's money but the trust's: one per
--                               trust bank account, operating only (trust_check_account_bank_kind).
--   trust_unapplied_receipts    money received: its transfer from the cash into that account (the
--                               app posts it through trust_post_transfers under a key of its own,
--                               as every receipt), who it seems to be from, how it came, the day,
--                               naming who recorded it.
--   trust_unapplied_applications what of a receipt was applied to a lease: the payment it became
--                               (the app records it as Receive payment does, from the unapplied
--                               account, under its key), naming who.
--   trust_unapplied_refunds     what of a receipt went back out through the cash
--                               (trust_refund_unapplied_cash: a payout, taking the disburse role
--                               and the account's signers, as a draw does), to whom and how.
--   A receipt is never applied or refunded past what is left of it (each takes its lock).
--
-- trust_apply_payment takes a payment from unapplied cash as from cash. A reconciliation is not
-- approved while unapplied cash is held in its trust bank account
-- (trust_approve_bank_reconciliation), and the three-way reconciliation shows it on a line of its
-- own. The tie-out sums every account of a trust bank account, so it counts unapplied cash as it
-- counts everything held. Reports name the account "unapplied_cash: received, not yet applied".
-- All append-only, audited; the AI role reads the three tables, as it reads the ledger.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The shape of each kind, now with unapplied cash, which names no holder. Every existing row
-- holds the new check as it held the old.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_ledger_accounts ADD CONSTRAINT trust_ledger_accounts_kind_shape_v3 CHECK (
    (
        kind = 'owner_property'
        AND owner_id IS NOT NULL AND property_id IS NOT NULL
        AND tenant_id IS NULL AND vendor_id IS NULL
    )
    OR (
        kind IN ('tenant_deposit', 'prepaid_rent')
        AND tenant_id IS NOT NULL
        AND owner_id IS NULL AND property_id IS NULL AND vendor_id IS NULL
    )
    OR (
        kind = 'vendor_payable'
        AND vendor_id IS NOT NULL
        AND owner_id IS NULL AND property_id IS NULL AND tenant_id IS NULL
    )
    OR (
        kind IN ('pmc_income', 'bank_cash', 'unapplied_cash')
        AND owner_id IS NULL AND property_id IS NULL AND tenant_id IS NULL AND vendor_id IS NULL
    )
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_ledger_accounts DROP CONSTRAINT trust_ledger_accounts_kind_shape_v2;
-- One unapplied cash account per trust bank account.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_ledger_accounts_one_unapplied ON trust_ledger_accounts (bank_account_id)
WHERE kind = 'unapplied_cash';

CREATE TABLE trust_unapplied_receipts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    -- The unapplied cash account it is held in, and the transfer that brought it there.
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    received_on date NOT NULL,
    -- Who it seems to be from, as the check or the bank says it, and what came with it.
    payer text NOT NULL CHECK (payer = btrim(payer) AND char_length(payer) BETWEEN 1 AND 200),
    memo text CHECK (memo = btrim(memo) AND char_length(memo) BETWEEN 1 AND 500),
    method text NOT NULL CHECK (
        method IN ('check', 'cashier''s check', 'money order', 'EFT', 'cash')
    ),
    reference text CHECK (reference = btrim(reference) AND char_length(reference) BETWEEN 1 AND 100),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_unapplied_receipts_bank ON trust_unapplied_receipts (pmc_id, bank_account_id);
CREATE INDEX trust_unapplied_receipts_account ON trust_unapplied_receipts (ledger_account_id);
CREATE INDEX trust_unapplied_receipts_recorded_by ON trust_unapplied_receipts (pmc_id, recorded_by);

CREATE TABLE trust_unapplied_applications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    -- The payment it became: the key its transfers were posted under (payment/<request id>).
    payment_key text NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, payment_key),
    FOREIGN KEY (pmc_id, receipt_id) REFERENCES trust_unapplied_receipts (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_unapplied_applications_receipt ON trust_unapplied_applications (receipt_id);
CREATE INDEX trust_unapplied_applications_lease ON trust_unapplied_applications (pmc_id, lease_id);
CREATE INDEX trust_unapplied_applications_recorded_by
ON trust_unapplied_applications (pmc_id, recorded_by);

CREATE TABLE trust_unapplied_refunds (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    request_key text NOT NULL,
    -- The unapplied cash account it left (read by the payout's signer check) and its transfer.
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    paid_to text NOT NULL CHECK (paid_to = btrim(paid_to) AND char_length(paid_to) BETWEEN 1 AND 200),
    method text NOT NULL CHECK (method IN ('check', 'EFT')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, request_key),
    FOREIGN KEY (pmc_id, receipt_id) REFERENCES trust_unapplied_receipts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_unapplied_refunds_receipt ON trust_unapplied_refunds (receipt_id);
CREATE INDEX trust_unapplied_refunds_account ON trust_unapplied_refunds (ledger_account_id);
CREATE INDEX trust_unapplied_refunds_recorded_by ON trust_unapplied_refunds (pmc_id, recorded_by);

-- What is left of a receipt to apply or refund.
CREATE FUNCTION trust_unapplied_left(p_receipt_id uuid) RETURNS numeric
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT r.amount
           - coalesce((SELECT sum(a.amount) FROM trust_unapplied_applications AS a
                       WHERE a.receipt_id = r.id), 0)
           - coalesce((SELECT sum(f.amount) FROM trust_unapplied_refunds AS f
                       WHERE f.receipt_id = r.id), 0)
    FROM trust_unapplied_receipts AS r
    WHERE r.id = p_receipt_id
$$;
REVOKE ALL ON FUNCTION trust_unapplied_left(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_unapplied_left(uuid) TO trust_app;

-- A receipt is the cash that came into the trust bank account's unapplied cash account: its
-- transfer moved that much from the account's cash into it, on its day.
CREATE FUNCTION trust_check_unapplied_receipt() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pgledger_transfers AS tr
        JOIN trust_ledger_accounts AS u ON u.ledger_account_id = tr.to_account_id
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = tr.from_account_id
        WHERE tr.id = NEW.transfer_id AND tr.amount = NEW.amount
          AND u.ledger_account_id = NEW.ledger_account_id AND u.kind = 'unapplied_cash'
          AND u.pmc_id = NEW.pmc_id AND u.bank_account_id = NEW.bank_account_id
          AND c.kind = 'bank_cash' AND c.bank_account_id = NEW.bank_account_id
          AND (tr.event_at AT TIME ZONE 'UTC')::date = NEW.received_on
    ) THEN
        RAISE EXCEPTION 'trust: unapplied cash is received from the trust bank account''s cash '
            'into its unapplied cash account, % of it on %', NEW.amount, NEW.received_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_unapplied_receipt() FROM PUBLIC;

-- An application or a refund never takes more than is left of its receipt; they wait for each
-- other on the receipt's row. An application is the payment its key posted from the receipt's
-- unapplied cash account, that much of it.
CREATE FUNCTION trust_check_unapplied_use() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_receipt trust_unapplied_receipts;
    v_left numeric;
BEGIN
    SELECT r.* INTO v_receipt FROM trust_unapplied_receipts AS r
    WHERE r.id = NEW.receipt_id AND r.pmc_id = NEW.pmc_id
    FOR NO KEY UPDATE;
    v_left := trust_unapplied_left(NEW.receipt_id);
    IF NEW.amount > v_left THEN
        RAISE EXCEPTION 'trust: only % of this unapplied cash is left; % is more',
            to_char(v_left, 'FM$999,999,990.00'), to_char(NEW.amount, 'FM$999,999,990.00')
            USING ERRCODE = 'check_violation';
    END IF;
    -- (Each table's own columns are read only in its own branch.)
    IF TG_TABLE_NAME = 'trust_unapplied_applications' THEN
        IF NEW.amount <> coalesce((
            SELECT sum(tr.amount) FROM pgledger_transfers AS tr
            WHERE tr.metadata ->> 'key' = NEW.payment_key
              AND tr.from_account_id = v_receipt.ledger_account_id
        ), 0) THEN
            RAISE EXCEPTION 'trust: an application of unapplied cash is the payment it posted '
                'from that account, all of it'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_unapplied_use() FROM PUBLIC;

-- Unapplied cash refunded: out of the unapplied cash account through the trust bank account's
-- cash, to whom it is paid, once per request key (the same key again returns the same), never
-- more than is left of the receipt. A payout: the disburse role and the account's signers, as a
-- draw takes them (the refund's own triggers).
CREATE FUNCTION trust_refund_unapplied_cash(
    p_pmc_id uuid, p_receipt_id uuid, p_amount numeric, p_paid_to text, p_method text,
    p_request_key text, p_event_at timestamptz
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_receipt trust_unapplied_receipts;
    v_done trust_unapplied_refunds;
    v_cash text;
    v_transfer text;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: refund unapplied cash at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' OR p_amount IS NULL OR p_amount <= 0
       OR p_amount <> round(p_amount, 2) THEN
        RAISE EXCEPTION 'trust: a refund of unapplied cash takes a request key and a positive '
            'amount in cents'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT r.* INTO v_receipt FROM trust_unapplied_receipts AS r
    WHERE r.id = p_receipt_id AND r.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no unapplied cash % in PMC %', p_receipt_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.* INTO v_done FROM trust_unapplied_refunds AS f
    WHERE f.pmc_id = p_pmc_id AND f.request_key = p_request_key;
    IF FOUND THEN
        IF (v_done.receipt_id, v_done.amount) IS DISTINCT FROM (p_receipt_id, p_amount) THEN
            RAISE EXCEPTION 'trust: request key % refunded other unapplied cash already',
                p_request_key
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_done.transfer_id;  -- a retry: the refund made then
    END IF;
    IF p_amount > trust_unapplied_left(p_receipt_id) THEN
        RAISE EXCEPTION 'trust: only % of this unapplied cash is left; % is more',
            to_char(trust_unapplied_left(p_receipt_id), 'FM$999,999,990.00'),
            to_char(p_amount, 'FM$999,999,990.00')
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT t.ledger_account_id INTO v_cash FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_receipt.bank_account_id AND t.kind = 'bank_cash';
    SELECT t.id INTO v_transfer
    FROM pgledger_create_transfers(
        ARRAY[(v_receipt.ledger_account_id, v_cash, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object(
            'key', 'unapplied-refund/' || p_request_key, 'kind', 'unapplied cash refunded',
            'memo', 'Unapplied cash refunded to ' || p_paid_to
        )
    ) AS t;
    INSERT INTO trust_unapplied_refunds (
        pmc_id, receipt_id, request_key, ledger_account_id, transfer_id, amount, paid_to, method
    ) VALUES (
        p_pmc_id, p_receipt_id, p_request_key, v_receipt.ledger_account_id, v_transfer, p_amount,
        p_paid_to, p_method
    );
    RETURN v_transfer;
END;
$$;
REVOKE ALL ON FUNCTION trust_refund_unapplied_cash(uuid, uuid, numeric, text, text, text, timestamptz)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
trust_refund_unapplied_cash(uuid, uuid, numeric, text, text, text, timestamptz) TO trust_app;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_unapplied_receipts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_unapplied_receipt BEFORE INSERT ON trust_unapplied_receipts
FOR EACH ROW EXECUTE FUNCTION trust_check_unapplied_receipt();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_unapplied_receipts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_unapplied_receipts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_unapplied_applications
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_unapplied_use BEFORE INSERT ON trust_unapplied_applications
FOR EACH ROW EXECUTE FUNCTION trust_check_unapplied_use();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_unapplied_applications
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_unapplied_applications
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_unapplied_refunds
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_unapplied_use BEFORE INSERT ON trust_unapplied_refunds
FOR EACH ROW EXECUTE FUNCTION trust_check_unapplied_use();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_unapplied_refunds
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'pay out money');
CREATE TRIGGER trust_signer BEFORE INSERT ON trust_unapplied_refunds
FOR EACH ROW EXECUTE FUNCTION trust_check_draw_signer();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_unapplied_refunds
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_unapplied_refunds
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_unapplied_receipts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_unapplied_receipts TO trust_app;
GRANT SELECT ON trust_unapplied_receipts TO trust_ai_agent;
REVOKE ALL ON trust_unapplied_applications FROM PUBLIC;
GRANT SELECT, INSERT ON trust_unapplied_applications TO trust_app;
GRANT SELECT ON trust_unapplied_applications TO trust_ai_agent;
REVOKE ALL ON trust_unapplied_refunds FROM PUBLIC;
GRANT SELECT ON trust_unapplied_refunds TO trust_app;
GRANT SELECT ON trust_unapplied_refunds TO trust_ai_agent;

-- The write paths and reports that read the new kind.
CREATE OR REPLACE FUNCTION trust_apply_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
    v_hold trust_payment_holds;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    -- Into the owner's account at the charge's property, or, for a fee the company keeps
    -- (trust_charge_payee, migration 20261106000220), into the company's fee account in the
    -- trust bank account that holds an owner's money for that property.
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND (
              (t.kind = 'owner_property' AND t.property_id = v_property_id)
              OR (t.kind = 'pmc_income' AND trust_charge_payee(v_charge) = 'company'
                  AND EXISTS (
                      SELECT o.ledger_account_id FROM trust_ledger_accounts AS o
                      WHERE o.pmc_id = p_pmc_id AND o.kind = 'owner_property'
                        AND o.property_id = v_property_id AND o.bank_account_id = t.bank_account_id
                  ))
          )
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              -- Cash, or unapplied cash applied to the lease (migration 20261106000240).
              f.kind IN ('bank_cash', 'unapplied_cash')
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property (or the company''s fee account for a fee the company keeps), from '
            'cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    -- What waivers took off it (credits linked to it, migration 0079) is settled too.
    SELECT v_paid + coalesce(sum(k.amount), 0) INTO v_paid
    FROM trust_charge_waivers AS w
    JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid or waived %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;

CREATE OR REPLACE FUNCTION trust_approve_bank_reconciliation(p_pmc_id uuid, p_preparation_id uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_prepared trust_reconciliation_preparations;
    v_bank trust_bank_accounts;
    v_start timestamptz;
    v_figures numeric[];
BEGIN
    SELECT p.* INTO v_prepared FROM trust_reconciliation_preparations AS p
    WHERE p.id = p_preparation_id AND p.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no reconciliation prepared as % in PMC %', p_preparation_id,
            p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who approves the reconciliation'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff = v_prepared.prepared_by THEN
        RAISE EXCEPTION 'trust: the staff member who prepared a reconciliation does not approve it'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Another staff member who holds the approve role approves it.';
    END IF;
    v_bank := trust_lock_bank_matches(p_pmc_id, v_prepared.bank_account_id);
    -- Matches and statements of the account wait for this (trust_approve_reconciliation then
    -- holds postings off).
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || v_bank.id, 0));
    IF EXISTS (
        SELECT p.id FROM trust_reconciliation_preparations AS p
        WHERE p.bank_account_id = v_bank.id AND p.created_at > v_prepared.created_at
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % was prepared again since; approve the '
            'latest', v_bank.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = v_bank.id;
    IF v_start >= (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC' THEN
        RAISE EXCEPTION 'trust: trust bank account % is approved through % already', v_bank.id,
            v_prepared.through
            USING ERRCODE = 'check_violation';
    END IF;
    -- Money received that is no one's yet can't stand in a month that is signed off: apply it
    -- to a lease or refund it first (migration 20261106000240).
    IF EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.bank_account_id = v_bank.id AND t.kind = 'unapplied_cash' AND a.balance <> 0
    ) THEN
        RAISE EXCEPTION 'trust: % of unapplied cash is held in this trust bank account: apply it '
            'to a lease or refund it before approving',
            (SELECT to_char(sum(a.balance), 'FM$999,999,990.00')
             FROM trust_ledger_accounts AS t
             JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
             WHERE t.bank_account_id = v_bank.id AND t.kind = 'unapplied_cash')
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT array_agg(r.amount ORDER BY r.line) INTO v_figures
    FROM trust_report_bank_reconciliation(p_pmc_id, v_bank.id, v_prepared.through) AS r
    WHERE r.line IN (4, 8, 9);
    -- (The bank balance through the day can't have changed: its statements are whole and
    -- append-only.)
    IF v_figures[2] <> v_prepared.book_cash THEN
        RAISE EXCEPTION 'trust: the books through % changed since they were prepared: % '
            '(prepared %)', v_prepared.through, v_figures[2], v_prepared.book_cash
            USING ERRCODE = 'check_violation',
                  HINT = 'Prepare it again.';
    END IF;
    IF v_figures[3] <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', v_bank.id, v_figures[3], v_prepared.through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    RETURN trust_approve_reconciliation(
        p_pmc_id, v_bank.id, v_start,
        (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC', v_figures[1],
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_prepared.prepared_by),
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_staff)
    );
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_three_way_reconciliation(p_pmc_id uuid, p_reconciliation_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rec trust_reconciliations;
    v_through date;
    v_unknown text;
    v_journal numeric;
    v_ledgers numeric;
    v_unapplied numeric;
    v_in_transit numeric;
    v_outstanding numeric;
    v_items integer := 0;
BEGIN
    SELECT r.* INTO v_rec FROM trust_reconciliations AS r
    WHERE r.id = p_reconciliation_id AND r.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- The books at the period's end: the account's cash, and what it holds for everyone.
    SELECT coalesce(-sum(e.amount) FILTER (WHERE t.kind = 'bank_cash'), 0),
           coalesce(sum(e.amount) FILTER (WHERE t.kind <> 'bank_cash'), 0)
    INTO v_journal, v_ledgers
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end;
    -- Of what it holds, money received that is no one's yet (migration 20261106000240).
    SELECT coalesce(sum(e.amount), 0.00) INTO v_unapplied
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = v_rec.bank_account_id AND t.kind = 'unapplied_cash'
      AND tr.event_at < v_rec.period_end;

    -- What had not cleared the bank by the period's last day, from the imported statements and
    -- their matches, as the bank reconciliation shows it.
    v_through := (v_rec.period_end AT TIME ZONE 'UTC')::date - 1;
    IF (v_through + 1)::timestamp AT TIME ZONE 'UTC' <> v_rec.period_end THEN
        v_unknown := 'not known: the period does not end at midnight (UTC)';
    ELSIF NOT EXISTS (
        SELECT s.id FROM trust_bank_statements AS s
        WHERE s.bank_account_id = v_rec.bank_account_id
          AND v_through BETWEEN s.first_day AND s.last_day
    ) THEN
        v_unknown := 'not known: no imported bank statement covers '
            || to_char(v_through, 'YYYY-MM-DD');
    ELSE
        SELECT max(b.amount) FILTER (WHERE b.item = 'deposits in transit'),
               max(b.amount) FILTER (WHERE b.item = 'outstanding withdrawals')
        INTO v_in_transit, v_outstanding
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b;
    END IF;

    RETURN QUERY
    SELECT 1, 'PMC', p.display_name, NULL::numeric
    FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
    UNION ALL
    SELECT 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL
    FROM trust_bank_accounts AS b WHERE b.id = v_rec.bank_account_id
    UNION ALL
    SELECT 3, 'period',
           to_char(v_rec.period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
           || to_char(v_rec.period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 4, 'prepared by', v_rec.prepared_by, NULL
    UNION ALL
    SELECT 5, 'approved by',
           v_rec.approved_by || ' at '
           || to_char(v_rec.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 6, 'bank statement balance', NULL, v_rec.statement_balance
    UNION ALL
    SELECT 7, 'trust journal (book cash)', NULL, v_journal
    UNION ALL
    SELECT 8, 'beneficiary ledgers', NULL, v_ledgers
    UNION ALL
    SELECT 9, 'of which unapplied cash', 'received, not yet applied to a lease or refunded',
           v_unapplied
    UNION ALL
    SELECT 10, 'difference: bank statement - trust journal', NULL,
           v_rec.statement_balance - v_journal
    UNION ALL
    SELECT 11, 'difference: trust journal - beneficiary ledgers', NULL, v_journal - v_ledgers
    UNION ALL
    SELECT 12, 'deposits in transit', v_unknown, v_in_transit
    UNION ALL
    SELECT 13, 'outstanding withdrawals', v_unknown, v_outstanding
    UNION ALL
    SELECT 14, 'adjusted bank balance', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding
    UNION ALL
    SELECT 15, 'difference: adjusted bank balance - trust journal', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding - v_journal
    ORDER BY 1;

    -- Each item that had not cleared, and each bank line the books lack, oldest first.
    IF v_unknown IS NULL THEN
        RETURN QUERY
        SELECT (15 + row_number() OVER (ORDER BY b.line))::integer, b.item, b.detail, b.amount
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b
        WHERE b.item IN (
            'deposit in transit', 'outstanding withdrawal', 'bank line not in the books'
        )
        ORDER BY 1;
        GET DIAGNOSTICS v_items = ROW_COUNT;
    END IF;

    -- Each beneficiary ledger in the account at the period's end, in byte order.
    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id, t.kind, t.owner_id, t.property_id, t.tenant_id, t.vendor_id,
               sum(e.amount) AS balance
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end
          AND t.kind <> 'bank_cash'
        GROUP BY t.ledger_account_id
    ),
    ledgers AS (
        SELECT b.kind || ': ' || CASE b.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   WHEN 'unapplied_cash' THEN 'received, not yet applied'
                   ELSE tn.display_name
               END AS holder,
               b.balance,
               b.ledger_account_id
        FROM balances AS b
        CROSS JOIN trust_pmcs AS pmc
        LEFT JOIN trust_owners AS o ON o.id = b.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = b.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = b.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
        WHERE pmc.pmc_id = p_pmc_id
    )
    SELECT (15 + v_items + row_number() OVER (
               ORDER BY ld.holder COLLATE "C", ld.ledger_account_id
           ))::integer,
           'ledger', ld.holder, ld.balance
    FROM ledgers AS ld
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_general_ledger(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, bank text, account text, posted_at text, description text, debit numeric, credit numeric, balance numeric, property text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: a general ledger runs from a day to the same day or a later '
            'one (got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'unapplied_cash' THEN 'received, not yet applied'
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The holder's name, for an entry on the other side of this account.
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'unapplied_cash' THEN 'received, not yet applied'
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               -- +1 where a credit grows the balance (money held for someone), -1 for cash.
               CASE t.kind WHEN 'bank_cash' THEN -1 ELSE 1 END AS sign,
               -- The property it is held for: an owner's property account's, or a tenant's.
               coalesce(pr.display_name, tp.display_name) AS property_name
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    entries AS (
        SELECT a.id AS account, e.amount, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < v_end
    ),
    openings AS (
        SELECT a.*,
               a.sign * coalesce(sum(x.amount) FILTER (WHERE x.event_at < v_start), 0)
                   AS opening,
               count(x.account) FILTER (WHERE x.event_at >= v_start) AS posted
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.id
        GROUP BY a.id, a.kind, a.bank_account_id, a.bank_name, a.account_name, a.holder, a.sign,
                 a.property_name
    ),
    shown AS (
        SELECT o.* FROM openings AS o
        WHERE o.opening <> 0 OR o.posted > 0 OR o.kind = 'bank_cash'
    ),
    postings AS (
        SELECT x.account, x.event_at, x.version,
               greatest(-x.amount, 0) AS dr, greatest(x.amount, 0) AS cr,
               s.opening + s.sign * sum(x.amount) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.holder || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN shown AS s ON s.id = x.account
        JOIN accounts AS c ON c.id = x.other
        WHERE x.event_at >= v_start
    ),
    -- Each account's debits and credits over the days, added up once: a closing balance used
    -- to add up the whole period's postings again for each account.
    totals AS (
        SELECT p.account, sum(p.dr) AS dr, sum(p.cr) AS cr FROM postings AS p GROUP BY p.account
    ),
    lines AS (
        -- (section, bank, bank id, cash first, account, account id, step, event_at, version)
        -- orders the report; names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS cash_first,
               NULL::text AS account_name, NULL::text AS id, 0 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS description, NULL::numeric AS dr,
               NULL::numeric AS cr, NULL::numeric AS balance, NULL::text AS property_name
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, NULL, NULL, 1, NULL, NULL, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 0, NULL, NULL, 'opening balance', s.account_name, NULL,
               NULL, NULL, s.opening, s.property_name
        FROM shown AS s
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 1, p.event_at, p.version, 'entry', s.account_name,
               p.description, p.dr, p.cr, p.running, s.property_name
        FROM postings AS p
        JOIN shown AS s ON s.id = p.account
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 2, NULL, NULL, 'closing balance', s.account_name, NULL,
               coalesce(t.dr, 0), coalesce(t.cr, 0), s.opening + s.sign * coalesce(t.cr - t.dr, 0),
               s.property_name
        FROM shown AS s
        LEFT JOIN totals AS t ON t.account = s.id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 0, NULL, NULL, 'total', NULL, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p), 0),
               NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.cash_first,
                        l.account_name COLLATE "C", l.id, l.step, l.event_at, l.version
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the period sit in the account column of the first two lines.
           l.label,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
           l.description,
           round(l.dr, 2),
           round(l.cr, 2),
           round(l.balance, 2),
           l.property_name
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_trial_balance(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, bank text, account text, debit numeric, credit numeric, property text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a trial balance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'unapplied_cash' THEN 'received, not yet applied'
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The property it is held for: an owner's property account's, or a tenant's.
               coalesce(pr.display_name, tp.display_name) AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    -- A balance on the debit side is money the bank holds (-balance of its cash account); every
    -- other balance is money held for someone, on the credit side.
    sides AS (
        SELECT b.*, greatest(-b.balance, 0) AS dr, greatest(b.balance, 0) AS cr
        FROM balances AS b
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS account_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS dr, NULL::numeric AS cr,
               NULL::text AS property_name
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 'account', s.account_name, s.dr, s.cr, s.property_name
        FROM sides AS s
        WHERE s.balance <> 0 OR s.kind = 'bank_cash'
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, 2, NULL, NULL, 'bank total', NULL,
               sum(s.dr), sum(s.cr), NULL
        FROM sides AS s
        GROUP BY s.bank_name, s.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total', NULL,
               coalesce(sum(s.dr), 0), coalesce(sum(s.cr), 0), NULL
        FROM sides AS s
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.account_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the account column of the first two lines.
           l.label,
           round(l.dr, 2),
           round(l.cr, 2),
           l.property_name
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_balance_sheet(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, section text, bank text, item text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.bank_account_id, t.kind,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS balance
        FROM trust_ledger_accounts AS t
        WHERE t.pmc_id = p_pmc_id
    ),
    -- What a trust bank account holds, by kind, in this order.
    kinds (kind, step, label) AS (
        VALUES ('owner_property', 1, 'owners'' funds'),
               ('tenant_deposit', 2, 'tenants'' security deposits'),
               ('prepaid_rent', 3, 'tenants'' prepaid rent'),
               ('vendor_payable', 4, 'set aside for vendors'' bills'),
               ('pmc_income', 5, 'the company''s fees not yet taken'),
               -- Money received that is no one's yet (migration 20261106000240).
               ('unapplied_cash', 6, 'unapplied cash: received, not yet applied')
    ),
    banks AS (
        -- Book cash is what the bank holds: the cash account's balance runs below zero as
        -- money arrives.
        SELECT b.id, b.display_name || ' (' || b.kind || ')' AS name,
               -coalesce(sum(x.balance) FILTER (WHERE x.kind = 'bank_cash'), 0.00) AS cash,
               coalesce(sum(x.balance) FILTER (WHERE x.kind <> 'bank_cash'), 0.00) AS held
        FROM trust_bank_accounts AS b
        LEFT JOIN balances AS x ON x.bank_account_id = b.id
        WHERE b.pmc_id = p_pmc_id
        GROUP BY b.id, b.display_name, b.kind
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               'PMC' AS part_name, pmc.display_name AS label, NULL::numeric AS value
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL
        UNION ALL
        SELECT 1, b.name, b.id, 0, 'assets', 'cash in the bank, by the books', b.cash
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 10 + k.step, 'held in trust', k.label, sum(x.balance)
        FROM banks AS b
        JOIN balances AS x ON x.bank_account_id = b.id
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY b.name, b.id, k.step, k.label
        UNION ALL
        SELECT 1, b.name, b.id, 20, 'held in trust', 'total held in trust', b.held
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 21, 'difference', 'cash less what is held', b.cash - b.held
        FROM banks AS b
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'all trust bank accounts', 'cash in the bank, by the books',
               coalesce((SELECT sum(b.cash) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 10 + k.step, 'all trust bank accounts', k.label, sum(x.balance)
        FROM balances AS x
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY k.step, k.label
        UNION ALL
        SELECT 2, NULL, NULL, 20, 'all trust bank accounts', 'total held in trust',
               coalesce((SELECT sum(b.held) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 21, 'all trust bank accounts', 'cash less what is held',
               coalesce((SELECT sum(b.cash - b.held) FROM banks AS b), 0.00)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.bank_name COLLATE "C", l.bank_id, l.step
            ))::integer,
           l.part_name, l.bank_name, l.label, l.value
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_cutover(p_pmc_id uuid, p_bank_account_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_opening trust_opening_balances;
BEGIN
    SELECT o.* INTO v_opening FROM trust_opening_balances AS o
    WHERE o.bank_account_id = p_bank_account_id AND o.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % of PMC % has no opening balances',
            p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT b.key AS account, (b.value #>> '{}')::numeric AS amount,
               t.kind || ': ' || CASE t.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'unapplied_cash' THEN 'received, not yet applied'
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM jsonb_each(v_opening.balances) AS b
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.key
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
    ),
    carried AS (
        SELECT i.* FROM trust_cutover_items AS i WHERE i.bank_account_id = p_bank_account_id
    ),
    lines AS (
        SELECT 0 AS section, 1 AS n, 'PMC' AS item, p.display_name AS detail,
               NULL::numeric AS amount, NULL::text AS sort, NULL::text AS id
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL, NULL,
               NULL
        FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id
        UNION ALL
        SELECT 0, 3, 'cutover', to_char(v_opening.opened_on, 'YYYY-MM-DD')
                   || ' (opening balances at the start of the day, UTC)', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 4, 'entered by', v_opening.entered_by || ' at '
                   || to_char(v_opening.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                   || ' UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 5, 'old system''s book cash', NULL, v_opening.book_cash, NULL, NULL
        UNION ALL
        SELECT 1, 0, 'opening balance', b.holder, b.amount, b.holder, b.account
        FROM balances AS b
        UNION ALL
        SELECT 2, 0, 'opening balances, total', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b), NULL, NULL
        UNION ALL
        SELECT 2, 1, 'difference: total - book cash', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b) - v_opening.book_cash,
               NULL, NULL
        UNION ALL
        SELECT 3, 0, CASE WHEN c.amount < 0 THEN 'outstanding check' ELSE 'deposit in transit'
                     END,
               to_char(c.issued_on, 'YYYY-MM-DD') || ' ' || c.description
                   || coalesce(' (' || c.reference || ')', ''),
               c.amount, to_char(c.issued_on, 'YYYY-MM-DD') || c.description, c.id::text
        FROM carried AS c
        UNION ALL
        SELECT 4, 0, 'bank balance to expect at the cutover',
               'book cash less the items the bank had not cleared',
               v_opening.book_cash - (SELECT coalesce(sum(c.amount), 0) FROM carried AS c),
               NULL, NULL
        UNION ALL
        SELECT 4, 1, 'signed off by', coalesce(
                   (SELECT st.display_name || ' at '
                           || to_char(s.signed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                           || ' UTC'
                    FROM trust_cutover_signoffs AS s
                    JOIN trust_staff AS st ON st.id = s.signed_by
                    WHERE s.bank_account_id = p_bank_account_id),
                   'not signed off yet'), NULL, NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.n, l.sort COLLATE "C", l.id COLLATE "C"
           )::integer,
           l.item, l.detail, l.amount
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_audit_log(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, at text, staff text, db_role text, action text, record text, detail text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: an audit log runs from a day to the same day or a later one '
            '(got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH holders AS (
        SELECT t.ledger_account_id AS id,
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'unapplied_cash' THEN 'received, not yet applied'
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    changes AS (
        SELECT a.id, a.at, coalesce(s.display_name, 'not recorded') AS staff, a.db_role,
               a.action, regexp_replace(a.table_name, '^(trust|pgledger)_', '') AS record,
               CASE
                   -- A transfer: how much, from and to whom, dated when, and its memo.
                   WHEN a.table_name = 'pgledger_transfers' THEN
                       (a.row_data ->> 'amount') || ' from ' || f.holder || ' to ' || t.holder
                       || ' dated ' || (a.row_data ->> 'event_at')
                       || coalesce(' - ' || (a.row_data -> 'metadata' ->> 'memo'), '')
                   -- An update: each field it changed, from what to what.
                   WHEN a.action = 'update' THEN (
                       SELECT coalesce(string_agg(
                           n.key || ': ' || CASE WHEN EXISTS (
                               SELECT att.attnum FROM pg_attribute AS att
                               WHERE att.attrelid = to_regclass(a.table_name)
                                 AND att.attname = n.key AND att.atttypid = 'bytea'::regtype
                           )
                               -- A file's bytes, as hex: its size before and after.
                               THEN coalesce('(' || (length(a.before ->> n.key) - 2) / 2
                                       || ' bytes)', 'null')
                                   || ' to ' || coalesce('(' || (length(n.value #>> '{}') - 2) / 2
                                       || ' bytes)', 'null')
                               ELSE coalesce(a.before ->> n.key, 'null') || ' to '
                                   || coalesce(n.value #>> '{}', 'null')
                           END,
                           ', ' ORDER BY n.key COLLATE "C"
                       ), 'no change')
                       FROM jsonb_each(a.row_data) AS n
                       WHERE n.value IS DISTINCT FROM a.before -> n.key
                   )
                   -- Anything else: its fields, but ids and the clock's time of writing it
                   -- (the log's own time says when).
                   ELSE (
                       SELECT string_agg(
                           n.key || ': ' || CASE WHEN EXISTS (
                               SELECT att.attnum FROM pg_attribute AS att
                               WHERE att.attrelid = to_regclass(a.table_name)
                                 AND att.attname = n.key AND att.atttypid = 'bytea'::regtype
                           )
                               -- A file's bytes, as hex ("\\x2550..."): their size.
                               THEN '(' || (length(n.value) - 2) / 2 || ' bytes)'
                               ELSE n.value
                           END,
                           ', ' ORDER BY n.key COLLATE "C"
                       )
                       FROM jsonb_each_text(a.row_data) AS n
                       WHERE n.value IS NOT NULL AND n.key <> 'id' AND n.key NOT LIKE '%\_id'
                         AND n.key NOT IN ('created_at', 'approved_at')
                   )
               END AS detail
        FROM trust_audit_log AS a
        LEFT JOIN trust_staff AS s ON s.id = a.staff_id
        LEFT JOIN holders AS f ON f.id = a.row_data ->> 'from_account_id'
        LEFT JOIN holders AS t ON t.id = a.row_data ->> 'to_account_id'
        WHERE a.pmc_id = p_pmc_id AND a.at >= v_start AND a.at < v_end
    ),
    lines AS (
        SELECT 0 AS section, NULL::bigint AS id, 'PMC' AS item, NULL::timestamptz AS at,
               pmc.display_name AS staff, NULL::text AS db_role, NULL::text AS action,
               NULL::text AS record, NULL::text AS detail
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, NULL, 'period', NULL,
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 2, c.id, 'change', c.at, c.staff, c.db_role, c.action, c.record, c.detail
        FROM changes AS c
        UNION ALL
        SELECT 3, NULL, 'total', NULL, NULL, NULL, NULL, NULL,
               (SELECT count(*) FROM changes) || ' changes'
    )
    SELECT row_number() OVER (ORDER BY l.section, l.id)::integer,
           l.item,
           to_char(l.at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'),
           -- The PMC's name and the period sit in the staff column of the first two lines.
           l.staff,
           l.db_role,
           l.action,
           l.record,
           l.detail
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_ledger_accounts WHERE kind = 'unapplied_cash') THEN
        RAISE EXCEPTION 'refusing to undo unapplied cash: a trust bank account holds an unapplied '
            'cash account';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trust_apply_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
    v_hold trust_payment_holds;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    -- Into the owner's account at the charge's property, or, for a fee the company keeps
    -- (trust_charge_payee, migration 20261106000220), into the company's fee account in the
    -- trust bank account that holds an owner's money for that property.
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND (
              (t.kind = 'owner_property' AND t.property_id = v_property_id)
              OR (t.kind = 'pmc_income' AND trust_charge_payee(v_charge) = 'company'
                  AND EXISTS (
                      SELECT o.ledger_account_id FROM trust_ledger_accounts AS o
                      WHERE o.pmc_id = p_pmc_id AND o.kind = 'owner_property'
                        AND o.property_id = v_property_id AND o.bank_account_id = t.bank_account_id
                  ))
          )
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              f.kind = 'bank_cash'
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property (or the company''s fee account for a fee the company keeps), from '
            'cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    -- What waivers took off it (credits linked to it, migration 0079) is settled too.
    SELECT v_paid + coalesce(sum(k.amount), 0) INTO v_paid
    FROM trust_charge_waivers AS w
    JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid or waived %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;

CREATE OR REPLACE FUNCTION trust_approve_bank_reconciliation(p_pmc_id uuid, p_preparation_id uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_prepared trust_reconciliation_preparations;
    v_bank trust_bank_accounts;
    v_start timestamptz;
    v_figures numeric[];
BEGIN
    SELECT p.* INTO v_prepared FROM trust_reconciliation_preparations AS p
    WHERE p.id = p_preparation_id AND p.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no reconciliation prepared as % in PMC %', p_preparation_id,
            p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who approves the reconciliation'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff = v_prepared.prepared_by THEN
        RAISE EXCEPTION 'trust: the staff member who prepared a reconciliation does not approve it'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Another staff member who holds the approve role approves it.';
    END IF;
    v_bank := trust_lock_bank_matches(p_pmc_id, v_prepared.bank_account_id);
    -- Matches and statements of the account wait for this (trust_approve_reconciliation then
    -- holds postings off).
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || v_bank.id, 0));
    IF EXISTS (
        SELECT p.id FROM trust_reconciliation_preparations AS p
        WHERE p.bank_account_id = v_bank.id AND p.created_at > v_prepared.created_at
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % was prepared again since; approve the '
            'latest', v_bank.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = v_bank.id;
    IF v_start >= (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC' THEN
        RAISE EXCEPTION 'trust: trust bank account % is approved through % already', v_bank.id,
            v_prepared.through
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT array_agg(r.amount ORDER BY r.line) INTO v_figures
    FROM trust_report_bank_reconciliation(p_pmc_id, v_bank.id, v_prepared.through) AS r
    WHERE r.line IN (4, 8, 9);
    -- (The bank balance through the day can't have changed: its statements are whole and
    -- append-only.)
    IF v_figures[2] <> v_prepared.book_cash THEN
        RAISE EXCEPTION 'trust: the books through % changed since they were prepared: % '
            '(prepared %)', v_prepared.through, v_figures[2], v_prepared.book_cash
            USING ERRCODE = 'check_violation',
                  HINT = 'Prepare it again.';
    END IF;
    IF v_figures[3] <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', v_bank.id, v_figures[3], v_prepared.through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    RETURN trust_approve_reconciliation(
        p_pmc_id, v_bank.id, v_start,
        (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC', v_figures[1],
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_prepared.prepared_by),
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_staff)
    );
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_three_way_reconciliation(p_pmc_id uuid, p_reconciliation_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rec trust_reconciliations;
    v_through date;
    v_unknown text;
    v_journal numeric;
    v_ledgers numeric;
    v_in_transit numeric;
    v_outstanding numeric;
    v_items integer := 0;
BEGIN
    SELECT r.* INTO v_rec FROM trust_reconciliations AS r
    WHERE r.id = p_reconciliation_id AND r.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- The books at the period's end: the account's cash, and what it holds for everyone.
    SELECT coalesce(-sum(e.amount) FILTER (WHERE t.kind = 'bank_cash'), 0),
           coalesce(sum(e.amount) FILTER (WHERE t.kind <> 'bank_cash'), 0)
    INTO v_journal, v_ledgers
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end;

    -- What had not cleared the bank by the period's last day, from the imported statements and
    -- their matches, as the bank reconciliation shows it.
    v_through := (v_rec.period_end AT TIME ZONE 'UTC')::date - 1;
    IF (v_through + 1)::timestamp AT TIME ZONE 'UTC' <> v_rec.period_end THEN
        v_unknown := 'not known: the period does not end at midnight (UTC)';
    ELSIF NOT EXISTS (
        SELECT s.id FROM trust_bank_statements AS s
        WHERE s.bank_account_id = v_rec.bank_account_id
          AND v_through BETWEEN s.first_day AND s.last_day
    ) THEN
        v_unknown := 'not known: no imported bank statement covers '
            || to_char(v_through, 'YYYY-MM-DD');
    ELSE
        SELECT max(b.amount) FILTER (WHERE b.item = 'deposits in transit'),
               max(b.amount) FILTER (WHERE b.item = 'outstanding withdrawals')
        INTO v_in_transit, v_outstanding
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b;
    END IF;

    RETURN QUERY
    SELECT 1, 'PMC', p.display_name, NULL::numeric
    FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
    UNION ALL
    SELECT 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL
    FROM trust_bank_accounts AS b WHERE b.id = v_rec.bank_account_id
    UNION ALL
    SELECT 3, 'period',
           to_char(v_rec.period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
           || to_char(v_rec.period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 4, 'prepared by', v_rec.prepared_by, NULL
    UNION ALL
    SELECT 5, 'approved by',
           v_rec.approved_by || ' at '
           || to_char(v_rec.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 6, 'bank statement balance', NULL, v_rec.statement_balance
    UNION ALL
    SELECT 7, 'trust journal (book cash)', NULL, v_journal
    UNION ALL
    SELECT 8, 'beneficiary ledgers', NULL, v_ledgers
    UNION ALL
    SELECT 9, 'difference: bank statement - trust journal', NULL,
           v_rec.statement_balance - v_journal
    UNION ALL
    SELECT 10, 'difference: trust journal - beneficiary ledgers', NULL, v_journal - v_ledgers
    UNION ALL
    SELECT 11, 'deposits in transit', v_unknown, v_in_transit
    UNION ALL
    SELECT 12, 'outstanding withdrawals', v_unknown, v_outstanding
    UNION ALL
    SELECT 13, 'adjusted bank balance', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding
    UNION ALL
    SELECT 14, 'difference: adjusted bank balance - trust journal', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding - v_journal
    ORDER BY 1;

    -- Each item that had not cleared, and each bank line the books lack, oldest first.
    IF v_unknown IS NULL THEN
        RETURN QUERY
        SELECT (14 + row_number() OVER (ORDER BY b.line))::integer, b.item, b.detail, b.amount
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b
        WHERE b.item IN (
            'deposit in transit', 'outstanding withdrawal', 'bank line not in the books'
        )
        ORDER BY 1;
        GET DIAGNOSTICS v_items = ROW_COUNT;
    END IF;

    -- Each beneficiary ledger in the account at the period's end, in byte order.
    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id, t.kind, t.owner_id, t.property_id, t.tenant_id, t.vendor_id,
               sum(e.amount) AS balance
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end
          AND t.kind <> 'bank_cash'
        GROUP BY t.ledger_account_id
    ),
    ledgers AS (
        SELECT b.kind || ': ' || CASE b.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               b.balance,
               b.ledger_account_id
        FROM balances AS b
        CROSS JOIN trust_pmcs AS pmc
        LEFT JOIN trust_owners AS o ON o.id = b.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = b.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = b.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
        WHERE pmc.pmc_id = p_pmc_id
    )
    SELECT (14 + v_items + row_number() OVER (
               ORDER BY ld.holder COLLATE "C", ld.ledger_account_id
           ))::integer,
           'ledger', ld.holder, ld.balance
    FROM ledgers AS ld
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_general_ledger(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, bank text, account text, posted_at text, description text, debit numeric, credit numeric, balance numeric, property text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: a general ledger runs from a day to the same day or a later '
            'one (got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The holder's name, for an entry on the other side of this account.
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               -- +1 where a credit grows the balance (money held for someone), -1 for cash.
               CASE t.kind WHEN 'bank_cash' THEN -1 ELSE 1 END AS sign,
               -- The property it is held for: an owner's property account's, or a tenant's.
               coalesce(pr.display_name, tp.display_name) AS property_name
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    entries AS (
        SELECT a.id AS account, e.amount, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < v_end
    ),
    openings AS (
        SELECT a.*,
               a.sign * coalesce(sum(x.amount) FILTER (WHERE x.event_at < v_start), 0)
                   AS opening,
               count(x.account) FILTER (WHERE x.event_at >= v_start) AS posted
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.id
        GROUP BY a.id, a.kind, a.bank_account_id, a.bank_name, a.account_name, a.holder, a.sign,
                 a.property_name
    ),
    shown AS (
        SELECT o.* FROM openings AS o
        WHERE o.opening <> 0 OR o.posted > 0 OR o.kind = 'bank_cash'
    ),
    postings AS (
        SELECT x.account, x.event_at, x.version,
               greatest(-x.amount, 0) AS dr, greatest(x.amount, 0) AS cr,
               s.opening + s.sign * sum(x.amount) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.holder || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN shown AS s ON s.id = x.account
        JOIN accounts AS c ON c.id = x.other
        WHERE x.event_at >= v_start
    ),
    -- Each account's debits and credits over the days, added up once: a closing balance used
    -- to add up the whole period's postings again for each account.
    totals AS (
        SELECT p.account, sum(p.dr) AS dr, sum(p.cr) AS cr FROM postings AS p GROUP BY p.account
    ),
    lines AS (
        -- (section, bank, bank id, cash first, account, account id, step, event_at, version)
        -- orders the report; names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS cash_first,
               NULL::text AS account_name, NULL::text AS id, 0 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS description, NULL::numeric AS dr,
               NULL::numeric AS cr, NULL::numeric AS balance, NULL::text AS property_name
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, NULL, NULL, 1, NULL, NULL, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 0, NULL, NULL, 'opening balance', s.account_name, NULL,
               NULL, NULL, s.opening, s.property_name
        FROM shown AS s
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 1, p.event_at, p.version, 'entry', s.account_name,
               p.description, p.dr, p.cr, p.running, s.property_name
        FROM postings AS p
        JOIN shown AS s ON s.id = p.account
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 2, NULL, NULL, 'closing balance', s.account_name, NULL,
               coalesce(t.dr, 0), coalesce(t.cr, 0), s.opening + s.sign * coalesce(t.cr - t.dr, 0),
               s.property_name
        FROM shown AS s
        LEFT JOIN totals AS t ON t.account = s.id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 0, NULL, NULL, 'total', NULL, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p), 0),
               NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.cash_first,
                        l.account_name COLLATE "C", l.id, l.step, l.event_at, l.version
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the period sit in the account column of the first two lines.
           l.label,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
           l.description,
           round(l.dr, 2),
           round(l.cr, 2),
           round(l.balance, 2),
           l.property_name
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_trial_balance(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, bank text, account text, debit numeric, credit numeric, property text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a trial balance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The property it is held for: an owner's property account's, or a tenant's.
               coalesce(pr.display_name, tp.display_name) AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    -- A balance on the debit side is money the bank holds (-balance of its cash account); every
    -- other balance is money held for someone, on the credit side.
    sides AS (
        SELECT b.*, greatest(-b.balance, 0) AS dr, greatest(b.balance, 0) AS cr
        FROM balances AS b
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS account_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS dr, NULL::numeric AS cr,
               NULL::text AS property_name
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 'account', s.account_name, s.dr, s.cr, s.property_name
        FROM sides AS s
        WHERE s.balance <> 0 OR s.kind = 'bank_cash'
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, 2, NULL, NULL, 'bank total', NULL,
               sum(s.dr), sum(s.cr), NULL
        FROM sides AS s
        GROUP BY s.bank_name, s.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total', NULL,
               coalesce(sum(s.dr), 0), coalesce(sum(s.cr), 0), NULL
        FROM sides AS s
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.account_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the account column of the first two lines.
           l.label,
           round(l.dr, 2),
           round(l.cr, 2),
           l.property_name
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_balance_sheet(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, section text, bank text, item text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.bank_account_id, t.kind,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS balance
        FROM trust_ledger_accounts AS t
        WHERE t.pmc_id = p_pmc_id
    ),
    -- What a trust bank account holds, by kind, in this order.
    kinds (kind, step, label) AS (
        VALUES ('owner_property', 1, 'owners'' funds'),
               ('tenant_deposit', 2, 'tenants'' security deposits'),
               ('prepaid_rent', 3, 'tenants'' prepaid rent'),
               ('vendor_payable', 4, 'set aside for vendors'' bills'),
               ('pmc_income', 5, 'the company''s fees not yet taken')
    ),
    banks AS (
        -- Book cash is what the bank holds: the cash account's balance runs below zero as
        -- money arrives.
        SELECT b.id, b.display_name || ' (' || b.kind || ')' AS name,
               -coalesce(sum(x.balance) FILTER (WHERE x.kind = 'bank_cash'), 0.00) AS cash,
               coalesce(sum(x.balance) FILTER (WHERE x.kind <> 'bank_cash'), 0.00) AS held
        FROM trust_bank_accounts AS b
        LEFT JOIN balances AS x ON x.bank_account_id = b.id
        WHERE b.pmc_id = p_pmc_id
        GROUP BY b.id, b.display_name, b.kind
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               'PMC' AS part_name, pmc.display_name AS label, NULL::numeric AS value
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL
        UNION ALL
        SELECT 1, b.name, b.id, 0, 'assets', 'cash in the bank, by the books', b.cash
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 10 + k.step, 'held in trust', k.label, sum(x.balance)
        FROM banks AS b
        JOIN balances AS x ON x.bank_account_id = b.id
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY b.name, b.id, k.step, k.label
        UNION ALL
        SELECT 1, b.name, b.id, 20, 'held in trust', 'total held in trust', b.held
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 21, 'difference', 'cash less what is held', b.cash - b.held
        FROM banks AS b
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'all trust bank accounts', 'cash in the bank, by the books',
               coalesce((SELECT sum(b.cash) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 10 + k.step, 'all trust bank accounts', k.label, sum(x.balance)
        FROM balances AS x
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY k.step, k.label
        UNION ALL
        SELECT 2, NULL, NULL, 20, 'all trust bank accounts', 'total held in trust',
               coalesce((SELECT sum(b.held) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 21, 'all trust bank accounts', 'cash less what is held',
               coalesce((SELECT sum(b.cash - b.held) FROM banks AS b), 0.00)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.bank_name COLLATE "C", l.bank_id, l.step
            ))::integer,
           l.part_name, l.bank_name, l.label, l.value
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_cutover(p_pmc_id uuid, p_bank_account_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_opening trust_opening_balances;
BEGIN
    SELECT o.* INTO v_opening FROM trust_opening_balances AS o
    WHERE o.bank_account_id = p_bank_account_id AND o.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % of PMC % has no opening balances',
            p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT b.key AS account, (b.value #>> '{}')::numeric AS amount,
               t.kind || ': ' || CASE t.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM jsonb_each(v_opening.balances) AS b
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.key
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
    ),
    carried AS (
        SELECT i.* FROM trust_cutover_items AS i WHERE i.bank_account_id = p_bank_account_id
    ),
    lines AS (
        SELECT 0 AS section, 1 AS n, 'PMC' AS item, p.display_name AS detail,
               NULL::numeric AS amount, NULL::text AS sort, NULL::text AS id
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL, NULL,
               NULL
        FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id
        UNION ALL
        SELECT 0, 3, 'cutover', to_char(v_opening.opened_on, 'YYYY-MM-DD')
                   || ' (opening balances at the start of the day, UTC)', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 4, 'entered by', v_opening.entered_by || ' at '
                   || to_char(v_opening.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                   || ' UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 5, 'old system''s book cash', NULL, v_opening.book_cash, NULL, NULL
        UNION ALL
        SELECT 1, 0, 'opening balance', b.holder, b.amount, b.holder, b.account
        FROM balances AS b
        UNION ALL
        SELECT 2, 0, 'opening balances, total', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b), NULL, NULL
        UNION ALL
        SELECT 2, 1, 'difference: total - book cash', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b) - v_opening.book_cash,
               NULL, NULL
        UNION ALL
        SELECT 3, 0, CASE WHEN c.amount < 0 THEN 'outstanding check' ELSE 'deposit in transit'
                     END,
               to_char(c.issued_on, 'YYYY-MM-DD') || ' ' || c.description
                   || coalesce(' (' || c.reference || ')', ''),
               c.amount, to_char(c.issued_on, 'YYYY-MM-DD') || c.description, c.id::text
        FROM carried AS c
        UNION ALL
        SELECT 4, 0, 'bank balance to expect at the cutover',
               'book cash less the items the bank had not cleared',
               v_opening.book_cash - (SELECT coalesce(sum(c.amount), 0) FROM carried AS c),
               NULL, NULL
        UNION ALL
        SELECT 4, 1, 'signed off by', coalesce(
                   (SELECT st.display_name || ' at '
                           || to_char(s.signed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                           || ' UTC'
                    FROM trust_cutover_signoffs AS s
                    JOIN trust_staff AS st ON st.id = s.signed_by
                    WHERE s.bank_account_id = p_bank_account_id),
                   'not signed off yet'), NULL, NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.n, l.sort COLLATE "C", l.id COLLATE "C"
           )::integer,
           l.item, l.detail, l.amount
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_audit_log(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, at text, staff text, db_role text, action text, record text, detail text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: an audit log runs from a day to the same day or a later one '
            '(got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH holders AS (
        SELECT t.ledger_account_id AS id,
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    changes AS (
        SELECT a.id, a.at, coalesce(s.display_name, 'not recorded') AS staff, a.db_role,
               a.action, regexp_replace(a.table_name, '^(trust|pgledger)_', '') AS record,
               CASE
                   -- A transfer: how much, from and to whom, dated when, and its memo.
                   WHEN a.table_name = 'pgledger_transfers' THEN
                       (a.row_data ->> 'amount') || ' from ' || f.holder || ' to ' || t.holder
                       || ' dated ' || (a.row_data ->> 'event_at')
                       || coalesce(' - ' || (a.row_data -> 'metadata' ->> 'memo'), '')
                   -- An update: each field it changed, from what to what.
                   WHEN a.action = 'update' THEN (
                       SELECT coalesce(string_agg(
                           n.key || ': ' || CASE WHEN EXISTS (
                               SELECT att.attnum FROM pg_attribute AS att
                               WHERE att.attrelid = to_regclass(a.table_name)
                                 AND att.attname = n.key AND att.atttypid = 'bytea'::regtype
                           )
                               -- A file's bytes, as hex: its size before and after.
                               THEN coalesce('(' || (length(a.before ->> n.key) - 2) / 2
                                       || ' bytes)', 'null')
                                   || ' to ' || coalesce('(' || (length(n.value #>> '{}') - 2) / 2
                                       || ' bytes)', 'null')
                               ELSE coalesce(a.before ->> n.key, 'null') || ' to '
                                   || coalesce(n.value #>> '{}', 'null')
                           END,
                           ', ' ORDER BY n.key COLLATE "C"
                       ), 'no change')
                       FROM jsonb_each(a.row_data) AS n
                       WHERE n.value IS DISTINCT FROM a.before -> n.key
                   )
                   -- Anything else: its fields, but ids and the clock's time of writing it
                   -- (the log's own time says when).
                   ELSE (
                       SELECT string_agg(
                           n.key || ': ' || CASE WHEN EXISTS (
                               SELECT att.attnum FROM pg_attribute AS att
                               WHERE att.attrelid = to_regclass(a.table_name)
                                 AND att.attname = n.key AND att.atttypid = 'bytea'::regtype
                           )
                               -- A file's bytes, as hex ("\\x2550..."): their size.
                               THEN '(' || (length(n.value) - 2) / 2 || ' bytes)'
                               ELSE n.value
                           END,
                           ', ' ORDER BY n.key COLLATE "C"
                       )
                       FROM jsonb_each_text(a.row_data) AS n
                       WHERE n.value IS NOT NULL AND n.key <> 'id' AND n.key NOT LIKE '%\_id'
                         AND n.key NOT IN ('created_at', 'approved_at')
                   )
               END AS detail
        FROM trust_audit_log AS a
        LEFT JOIN trust_staff AS s ON s.id = a.staff_id
        LEFT JOIN holders AS f ON f.id = a.row_data ->> 'from_account_id'
        LEFT JOIN holders AS t ON t.id = a.row_data ->> 'to_account_id'
        WHERE a.pmc_id = p_pmc_id AND a.at >= v_start AND a.at < v_end
    ),
    lines AS (
        SELECT 0 AS section, NULL::bigint AS id, 'PMC' AS item, NULL::timestamptz AS at,
               pmc.display_name AS staff, NULL::text AS db_role, NULL::text AS action,
               NULL::text AS record, NULL::text AS detail
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, NULL, 'period', NULL,
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 2, c.id, 'change', c.at, c.staff, c.db_role, c.action, c.record, c.detail
        FROM changes AS c
        UNION ALL
        SELECT 3, NULL, 'total', NULL, NULL, NULL, NULL, NULL,
               (SELECT count(*) FROM changes) || ' changes'
    )
    SELECT row_number() OVER (ORDER BY l.section, l.id)::integer,
           l.item,
           to_char(l.at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'),
           -- The PMC's name and the period sit in the staff column of the first two lines.
           l.staff,
           l.db_role,
           l.action,
           l.record,
           l.detail
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- squawk-ignore ban-drop-table
DROP TABLE trust_unapplied_refunds;
-- squawk-ignore ban-drop-table
DROP TABLE trust_unapplied_applications;
-- squawk-ignore ban-drop-table
DROP TABLE trust_unapplied_receipts;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_refund_unapplied_cash(uuid, uuid, numeric, text, text, text, timestamptz);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_unapplied_use();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_unapplied_receipt();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_unapplied_left(uuid);
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_ledger_accounts_one_unapplied;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_ledger_accounts ADD CONSTRAINT trust_ledger_accounts_kind_shape_v2 CHECK (
    (
        kind = 'owner_property'
        AND owner_id IS NOT NULL AND property_id IS NOT NULL
        AND tenant_id IS NULL AND vendor_id IS NULL
    )
    OR (
        kind IN ('tenant_deposit', 'prepaid_rent')
        AND tenant_id IS NOT NULL
        AND owner_id IS NULL AND property_id IS NULL AND vendor_id IS NULL
    )
    OR (
        kind = 'vendor_payable'
        AND vendor_id IS NOT NULL
        AND owner_id IS NULL AND property_id IS NULL AND tenant_id IS NULL
    )
    OR (
        kind IN ('pmc_income', 'bank_cash')
        AND owner_id IS NULL AND property_id IS NULL AND tenant_id IS NULL AND vendor_id IS NULL
    )
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_ledger_accounts DROP CONSTRAINT trust_ledger_accounts_kind_shape_v3;
