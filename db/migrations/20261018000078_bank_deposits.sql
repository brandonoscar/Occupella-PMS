-- migrate:up
-- Bank deposits (the founder's review, q025): the checks and money staff take to the bank
-- together, which the bank shows as one line. A deposit names its trust bank account and the day
-- it went in, and its items are the transfers that brought the money into that account's cash
-- (a payment's whole, its prepaid rent too). Each transfer goes in at most one deposit, never
-- one that took money out or moved another account's cash, and never a day before it came in.
-- A deposit moves no money: it groups what the books already hold, for a deposit slip and for
-- month-end, where its bank line matches all of it at once. Append-only, naming who recorded it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_bank_deposits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    deposited_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_bank_deposits_bank ON trust_bank_deposits (bank_account_id, deposited_on);
CREATE INDEX trust_bank_deposits_recorded_by ON trust_bank_deposits (pmc_id, recorded_by);

CREATE TABLE trust_bank_deposit_items (
    deposit_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    -- In at most one deposit.
    PRIMARY KEY (transfer_id),
    FOREIGN KEY (pmc_id, deposit_id) REFERENCES trust_bank_deposits (pmc_id, id)
);
CREATE INDEX trust_bank_deposit_items_deposit ON trust_bank_deposit_items (pmc_id, deposit_id);

-- An item brought money into its deposit's trust bank account, by the day of the deposit.
CREATE FUNCTION trust_check_bank_deposit_item() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_on date;
    v_in numeric;
    v_at timestamptz;
BEGIN
    SELECT d.bank_account_id, d.deposited_on INTO v_bank, v_on
    FROM trust_bank_deposits AS d
    WHERE d.id = NEW.deposit_id AND d.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such deposit of the PMC: the foreign key refuses it
    END IF;
    SELECT -sum(e.amount), min(tr.event_at) INTO v_in, v_at
    FROM pgledger_entries AS e
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE e.transfer_id = NEW.transfer_id AND t.pmc_id = NEW.pmc_id
      AND t.bank_account_id = v_bank AND t.kind = 'bank_cash';
    IF v_in IS NULL OR v_in <= 0 THEN
        RAISE EXCEPTION 'trust: transfer % brought no money into the deposit''s bank account',
            NEW.transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (v_at AT TIME ZONE 'UTC')::date > v_on THEN
        RAISE EXCEPTION 'trust: transfer % came in after the deposit of %', NEW.transfer_id, v_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_bank_deposit_item() FROM PUBLIC;

CREATE TRIGGER trust_bank_deposit_item_brought_money BEFORE INSERT ON trust_bank_deposit_items
FOR EACH ROW EXECUTE FUNCTION trust_check_bank_deposit_item();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_bank_deposits
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_deposits
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_deposit_items
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_deposits
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_deposit_items
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_bank_deposits FROM PUBLIC;
REVOKE ALL ON trust_bank_deposit_items FROM PUBLIC;
GRANT SELECT, INSERT ON trust_bank_deposits TO trust_app;
GRANT SELECT, INSERT ON trust_bank_deposit_items TO trust_app;
GRANT SELECT ON trust_bank_deposits TO trust_ai_agent;
GRANT SELECT ON trust_bank_deposit_items TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'bank_deposits cannot be rolled back: deposits are records';
END;
$$;
