-- migrate:up
-- Checks (the founder, 2026-10-23: ACH files and check printing). A payout staff recorded as sent
-- by check (an owner draw or a bill paid, trust_payout_methods) is printed on the company's
-- pre-printed check stock, which carries the bank's details and the MICR line; the PMS prints the
-- date, the payee, the amount in figures and in words, the memo and two stubs (src/pms/checks.py).
-- Nothing here moves money: each payout has moved in the books already, and a check is signed by
-- hand by someone the trust bank account allows (docs/law.md).
--
-- trust_check_settings: where a trust bank account's checks sit on the sheet (top, middle or
-- bottom of a voucher page) and how far to move the printing for the printer, append-only, the
-- latest row holding. trust_check_runs: a print of an account's checks on a day. trust_checks:
-- each payout printed in it, under the number on the sheet it is printed on (staff give the first
-- one); a number is used once per account, a void's too. A payout is on at most one check not
-- voided, for its whole amount, from the run's account; trust_check_voids sets a check aside (lost,
-- spoiled, never sent), with why, freeing its payout for another check under a new number. Each is
-- append-only, audited and names who; a named staff member must hold disburse. The AI role reads
-- none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_check_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    -- Where the check sits on a voucher page: a check and two stubs.
    position text NOT NULL CHECK (position IN ('top', 'middle', 'bottom')),
    -- Points (1/72 inch) to move everything printed, for a printer that prints off true.
    shift_right integer NOT NULL CHECK (shift_right BETWEEN -36 AND 36),
    shift_down integer NOT NULL CHECK (shift_down BETWEEN -36 AND 36),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_check_settings_account ON trust_check_settings (pmc_id, bank_account_id, created_at);
CREATE INDEX trust_check_settings_recorded_by ON trust_check_settings (pmc_id, recorded_by);

CREATE TABLE trust_check_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    -- The day printed on its checks (UTC), set by the database.
    dated_on date NOT NULL,
    -- How many checks it prints and what they come to.
    checks integer NOT NULL CHECK (checks BETWEEN 1 AND 500),
    total numeric NOT NULL CHECK (total > 0 AND total = round(total, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, id, bank_account_id),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_check_runs_account ON trust_check_runs (pmc_id, bank_account_id, created_at);
CREATE INDEX trust_check_runs_recorded_by ON trust_check_runs (pmc_id, recorded_by);

CREATE TABLE trust_checks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    run_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    -- The number on the sheet it is printed on.
    check_number integer NOT NULL CHECK (check_number BETWEEN 1 AND 99999999),
    transfer_id text NOT NULL REFERENCES trust_payout_methods (transfer_id),
    amount numeric NOT NULL CHECK (amount > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, bank_account_id, check_number),
    FOREIGN KEY (pmc_id, run_id, bank_account_id)
        REFERENCES trust_check_runs (pmc_id, id, bank_account_id)
);
CREATE INDEX trust_checks_run ON trust_checks (pmc_id, run_id);
CREATE INDEX trust_checks_transfer ON trust_checks (transfer_id);

CREATE TABLE trust_check_voids (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    check_id uuid PRIMARY KEY,
    reason text NOT NULL CHECK (reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, check_id) REFERENCES trust_checks (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_check_voids_recorded_by ON trust_check_voids (pmc_id, recorded_by);

-- A run is dated the day it is printed (UTC).
CREATE FUNCTION trust_check_check_run() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.created_at := clock_timestamp();
    NEW.dated_on := (NEW.created_at AT TIME ZONE 'UTC')::date;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_check_run() FROM PUBLIC;

-- A check is a payout recorded as sent by check from its run's account, for its whole amount, on
-- no other check not voided.
CREATE FUNCTION trust_check_check() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_payout record;
BEGIN
    SELECT * INTO v_payout FROM trust_payout(NEW.pmc_id, NEW.transfer_id);
    IF NOT FOUND OR v_payout.method <> 'check' THEN
        RAISE EXCEPTION 'trust: a check prints a payout recorded as sent by check'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_payout.bank_account_id <> NEW.bank_account_id THEN
        RAISE EXCEPTION 'trust: a check prints a payout from its own trust bank account'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.amount <> v_payout.amount THEN
        RAISE EXCEPTION 'trust: a check pays a payout''s whole amount, %', v_payout.amount
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('check payout ' || NEW.transfer_id, 0));
    IF EXISTS (
        SELECT 1 FROM trust_checks AS c
        WHERE c.transfer_id = NEW.transfer_id
          AND NOT EXISTS (SELECT 1 FROM trust_check_voids AS v WHERE v.check_id = c.id)
    ) THEN
        RAISE EXCEPTION 'trust: payout % is on a check already', NEW.transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_check() FROM PUBLIC;

-- At commit, a run's checks come to its count and total.
CREATE FUNCTION trust_check_check_run_whole() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run_id uuid;
    v_run trust_check_runs;
    v_count integer;
    v_sum numeric;
BEGIN
    IF TG_TABLE_NAME = 'trust_check_runs' THEN
        v_run_id := NEW.id;
    ELSE
        v_run_id := NEW.run_id;
    END IF;
    SELECT * INTO v_run FROM trust_check_runs AS r WHERE r.pmc_id = NEW.pmc_id AND r.id = v_run_id;
    SELECT count(*), coalesce(sum(c.amount), 0) INTO v_count, v_sum
    FROM trust_checks AS c WHERE c.pmc_id = v_run.pmc_id AND c.run_id = v_run.id;
    IF v_count <> v_run.checks OR v_sum <> v_run.total THEN
        RAISE EXCEPTION 'trust: check run % prints % checks of %; it says % of %',
            v_run.id, v_count, v_sum, v_run.checks, v_run.total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_check_run_whole() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_check_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_check_settings
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'change where checks print');
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_check_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_check_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_check_run BEFORE INSERT ON trust_check_runs
FOR EACH ROW EXECUTE FUNCTION trust_check_check_run();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_check_runs
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_check_runs
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'print checks');
CREATE CONSTRAINT TRIGGER trust_check_run_whole AFTER INSERT ON trust_check_runs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_check_run_whole();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_check_runs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_check_runs
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_check BEFORE INSERT ON trust_checks
FOR EACH ROW EXECUTE FUNCTION trust_check_check();
CREATE CONSTRAINT TRIGGER trust_check_run_whole AFTER INSERT ON trust_checks
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_check_run_whole();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_checks
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_checks
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_check_voids
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_check_voids
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'void a check');
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_check_voids
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_check_voids
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_check_settings, trust_check_runs, trust_checks, trust_check_voids FROM PUBLIC;
GRANT SELECT, INSERT ON trust_check_settings, trust_check_runs, trust_checks, trust_check_voids
TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_check_voids;
-- squawk-ignore ban-drop-table
DROP TABLE trust_checks;
-- squawk-ignore ban-drop-table
DROP TABLE trust_check_runs;
-- squawk-ignore ban-drop-table
DROP TABLE trust_check_settings;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_check_run_whole();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_check();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_check_run();
