-- migrate:up
-- Bank files (the founder, 2026-10-23: ACH files and check printing). A payout staff already
-- recorded as sent by EFT (an owner draw or a bill paid, trust_payout_methods) goes to the bank in
-- an ACH file: the NACHA records the bank takes to send the money, written by the app
-- (src/pms/ach.py) from rows kept here. Nothing here moves money in the books: each payout has
-- moved already, and the file tells the bank to send what the books say left.
--
-- trust_ach_settings: a trust bank account's ACH details from its bank (its routing number and
-- name, the immediate origin and the company's name and identification as the bank gives them),
-- append-only, the latest row for an account holding. trust_ach_files: a file made for an
-- account under its settings then, for an effective day (a weekday not before the day it is
-- made, UTC), its modifier (A to Z, then 0 to 9, the day's files in turn) and its count and total;
-- trust_ach_file_items: each payout in it once, with the payee's bank account it pays into (their
-- latest, not a removal) and its amount, numbered from 1. At commit a file's items come to its
-- count and total, so nothing joins a file after the transaction that made it. A payout is in at
-- most one file not voided; trust_ach_file_voids sets a file aside (the bank refused it, a day was
-- wrong), with why, freeing its payouts for another. Each is append-only, audited and names who;
-- a named staff member must hold disburse. The AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_ach_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    -- The trust bank account's bank: its routing number (the immediate destination, and the first
    -- eight digits of each trace number) and its name (23 characters at most).
    bank_routing text NOT NULL CHECK (trust_aba_routing_ok(bank_routing)),
    bank_name text NOT NULL CHECK (bank_name ~ '^[A-Za-z0-9][A-Za-z0-9 .,&''/()#-]{0,22}$'),
    -- As the bank gives them: the immediate origin (nine or ten characters, often 1 and the
    -- company's EIN) and the company identification each batch carries (ten at most).
    origin text NOT NULL CHECK (origin ~ '^[A-Za-z0-9]{9,10}$'),
    company_name text NOT NULL CHECK (company_name ~ '^[A-Za-z0-9][A-Za-z0-9 .,&''/()#-]{0,15}$'),
    company_id text NOT NULL CHECK (company_id ~ '^[A-Za-z0-9]{1,10}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_ach_settings_account ON trust_ach_settings (pmc_id, bank_account_id, created_at);
CREATE INDEX trust_ach_settings_recorded_by ON trust_ach_settings (pmc_id, recorded_by);

CREATE TABLE trust_ach_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    settings_id uuid NOT NULL,
    -- The day the payees' banks are to make the money available.
    effective_on date NOT NULL,
    -- How many payouts it carries and what they come to (each payout's whole amount).
    entries integer NOT NULL CHECK (entries BETWEEN 1 AND 9999),
    total numeric NOT NULL CHECK (total > 0 AND total = round(total, 2)),
    -- Set by the database as it is made.
    created_on date NOT NULL,
    modifier text NOT NULL CHECK (modifier ~ '^[A-Z0-9]$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, bank_account_id, created_on, modifier),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, settings_id) REFERENCES trust_ach_settings (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_ach_files_settings ON trust_ach_files (pmc_id, settings_id);
CREATE INDEX trust_ach_files_recorded_by ON trust_ach_files (pmc_id, recorded_by);

CREATE TABLE trust_ach_file_items (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    file_id uuid NOT NULL,
    -- Its place in the file (the trace number's sequence).
    sequence integer NOT NULL CHECK (sequence BETWEEN 1 AND 9999),
    transfer_id text NOT NULL REFERENCES trust_payout_methods (transfer_id),
    payee_bank_account_id uuid NOT NULL REFERENCES trust_payee_bank_accounts (id),
    amount numeric NOT NULL CHECK (amount > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (file_id, sequence),
    UNIQUE (file_id, transfer_id),
    FOREIGN KEY (pmc_id, file_id) REFERENCES trust_ach_files (pmc_id, id)
);
CREATE INDEX trust_ach_file_items_transfer ON trust_ach_file_items (transfer_id);
CREATE INDEX trust_ach_file_items_payee ON trust_ach_file_items (payee_bank_account_id);

CREATE TABLE trust_ach_file_voids (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    file_id uuid PRIMARY KEY,
    reason text NOT NULL CHECK (reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, file_id) REFERENCES trust_ach_files (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_ach_file_voids_recorded_by ON trust_ach_file_voids (pmc_id, recorded_by);

-- A payout of the PMC (an owner draw or a bill paid, with how it was sent): the trust bank
-- account it left, whom it paid (an owner for a draw, a vendor for a bill) and how much, or no row.
CREATE FUNCTION trust_payout(p_pmc_id uuid, p_transfer_id text)
RETURNS TABLE (method text, bank_account_id uuid, vendor_id uuid, owner_id uuid, amount numeric,
               paid_at timestamptz)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT m.method, a.bank_account_id, NULL::uuid, a.owner_id, t.amount, t.event_at
    FROM trust_payout_methods AS m
    JOIN trust_owner_draws AS d ON d.pmc_id = m.pmc_id AND d.transfer_id = m.transfer_id
    JOIN trust_ledger_accounts AS a
        ON a.pmc_id = d.pmc_id AND a.ledger_account_id = d.ledger_account_id
    JOIN pgledger_transfers AS t ON t.id = m.transfer_id
    WHERE m.pmc_id = p_pmc_id AND m.transfer_id = p_transfer_id
    UNION ALL
    SELECT m.method, a.bank_account_id, b.vendor_id, NULL::uuid, t.amount, t.event_at
    FROM trust_payout_methods AS m
    JOIN trust_bill_payments AS p
        ON p.pmc_id = m.pmc_id AND p.transfer_id = m.transfer_id AND p.step = 'paid'
    JOIN trust_bills AS b ON b.pmc_id = p.pmc_id AND b.id = p.bill_id
    JOIN trust_ledger_accounts AS a
        ON a.pmc_id = b.pmc_id AND a.ledger_account_id = b.ledger_account_id
    JOIN pgledger_transfers AS t ON t.id = m.transfer_id
    WHERE m.pmc_id = p_pmc_id AND m.transfer_id = p_transfer_id
$$;
REVOKE ALL ON FUNCTION trust_payout(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_payout(uuid, text) TO trust_app;

-- A file is made under its account's latest settings, for a weekday not before today (UTC), and
-- takes the day's next modifier; the account's files wait for each other.
CREATE FUNCTION trust_check_ach_file() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_latest uuid;
    v_made integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('ach files of ' || NEW.bank_account_id, 0));
    NEW.created_at := clock_timestamp();
    NEW.created_on := (NEW.created_at AT TIME ZONE 'UTC')::date;
    SELECT s.id INTO v_latest
    FROM trust_ach_settings AS s
    WHERE s.pmc_id = NEW.pmc_id AND s.bank_account_id = NEW.bank_account_id
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1;
    IF v_latest IS DISTINCT FROM NEW.settings_id THEN
        RAISE EXCEPTION 'trust: a bank file is made under its account''s latest ACH settings'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.effective_on < NEW.created_on THEN
        RAISE EXCEPTION 'trust: a bank file''s effective day is not before the day it is made'
            USING ERRCODE = 'check_violation';
    END IF;
    IF extract(isodow FROM NEW.effective_on) > 5 THEN
        RAISE EXCEPTION 'trust: a bank file''s effective day is a weekday, when banks settle'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO v_made
    FROM trust_ach_files AS f
    WHERE f.pmc_id = NEW.pmc_id AND f.bank_account_id = NEW.bank_account_id
      AND f.created_on = NEW.created_on;
    IF v_made >= 36 THEN
        RAISE EXCEPTION 'trust: a bank account makes at most 36 bank files a day'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.modifier := substr('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', v_made + 1, 1);
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_ach_file() FROM PUBLIC;

-- An item is an EFT payout that left its file's account, paying into the payee's latest bank
-- account, for the payout's whole amount, and in no other file not voided.
CREATE FUNCTION trust_check_ach_file_item() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_file trust_ach_files;
    v_payout record;
    v_latest uuid;
BEGIN
    SELECT * INTO v_file FROM trust_ach_files AS f
    WHERE f.pmc_id = NEW.pmc_id AND f.id = NEW.file_id;
    SELECT * INTO v_payout FROM trust_payout(NEW.pmc_id, NEW.transfer_id);
    IF NOT FOUND OR v_payout.method <> 'EFT' THEN
        RAISE EXCEPTION 'trust: a bank file carries payouts recorded as sent by EFT'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_payout.bank_account_id <> v_file.bank_account_id THEN
        RAISE EXCEPTION 'trust: a bank file carries payouts from its own trust bank account'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.amount <> v_payout.amount THEN
        RAISE EXCEPTION 'trust: a bank file pays a payout''s whole amount, %', v_payout.amount
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT b.id INTO v_latest
    FROM trust_payee_bank_accounts AS b
    WHERE b.pmc_id = NEW.pmc_id
      AND (b.vendor_id = v_payout.vendor_id OR b.owner_id = v_payout.owner_id)
      AND NOT b.removed
      AND NOT EXISTS (
          SELECT 1 FROM trust_payee_bank_accounts AS later
          WHERE later.pmc_id = b.pmc_id
            AND (later.vendor_id = v_payout.vendor_id OR later.owner_id = v_payout.owner_id)
            AND (later.created_at, later.id) > (b.created_at, b.id)
      );
    IF v_latest IS DISTINCT FROM NEW.payee_bank_account_id THEN
        RAISE EXCEPTION 'trust: a bank file pays into the payee''s bank account on file'
            USING ERRCODE = 'check_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('ach payout ' || NEW.transfer_id, 0));
    IF EXISTS (
        SELECT 1 FROM trust_ach_file_items AS i
        WHERE i.transfer_id = NEW.transfer_id AND i.file_id <> NEW.file_id
          AND NOT EXISTS (SELECT 1 FROM trust_ach_file_voids AS v WHERE v.file_id = i.file_id)
    ) THEN
        RAISE EXCEPTION 'trust: payout % is in a bank file already', NEW.transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_ach_file_item() FROM PUBLIC;

-- At commit, a file's items come to its count and total, numbered 1 to its count.
CREATE FUNCTION trust_check_ach_file_whole() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_file_id uuid;
    v_file trust_ach_files;
    v_count integer;
    v_sum numeric;
    v_last integer;
BEGIN
    IF TG_TABLE_NAME = 'trust_ach_files' THEN
        v_file_id := NEW.id;
    ELSE
        v_file_id := NEW.file_id;
    END IF;
    SELECT * INTO v_file FROM trust_ach_files AS f
    WHERE f.pmc_id = NEW.pmc_id AND f.id = v_file_id;
    SELECT count(*), coalesce(sum(i.amount), 0), coalesce(max(i.sequence), 0)
    INTO v_count, v_sum, v_last
    FROM trust_ach_file_items AS i WHERE i.file_id = v_file.id;
    IF v_count <> v_file.entries OR v_sum <> v_file.total OR v_last <> v_count THEN
        RAISE EXCEPTION 'trust: bank file % carries % payouts of %, numbered to %; it says % of %',
            v_file.id, v_count, v_sum, v_last, v_file.entries, v_file.total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_ach_file_whole() FROM PUBLIC;

-- A void waits for the file's account, as a new file does.
CREATE FUNCTION trust_check_ach_file_void() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account uuid;
BEGIN
    SELECT f.bank_account_id INTO v_account FROM trust_ach_files AS f
    WHERE f.pmc_id = NEW.pmc_id AND f.id = NEW.file_id;
    PERFORM pg_advisory_xact_lock(hashtextextended('ach files of ' || v_account, 0));
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_ach_file_void() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_ach_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_ach_settings
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'change a bank account''s ACH settings');
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ach_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_ach_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_ach_file BEFORE INSERT ON trust_ach_files
FOR EACH ROW EXECUTE FUNCTION trust_check_ach_file();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_ach_files
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_ach_files
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'make a bank file');
CREATE CONSTRAINT TRIGGER trust_ach_file_whole AFTER INSERT ON trust_ach_files
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_ach_file_whole();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ach_files
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_ach_files
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_ach_file_item BEFORE INSERT ON trust_ach_file_items
FOR EACH ROW EXECUTE FUNCTION trust_check_ach_file_item();
CREATE CONSTRAINT TRIGGER trust_ach_file_whole AFTER INSERT ON trust_ach_file_items
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_ach_file_whole();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ach_file_items
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_ach_file_items
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_check_ach_file_void BEFORE INSERT ON trust_ach_file_voids
FOR EACH ROW EXECUTE FUNCTION trust_check_ach_file_void();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_ach_file_voids
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_ach_file_voids
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'void a bank file');
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ach_file_voids
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_ach_file_voids
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_ach_settings, trust_ach_files, trust_ach_file_items, trust_ach_file_voids
FROM PUBLIC;
GRANT SELECT, INSERT ON trust_ach_settings, trust_ach_files, trust_ach_file_items,
    trust_ach_file_voids TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_ach_file_voids;
-- squawk-ignore ban-drop-table
DROP TABLE trust_ach_file_items;
-- squawk-ignore ban-drop-table
DROP TABLE trust_ach_files;
-- squawk-ignore ban-drop-table
DROP TABLE trust_ach_settings;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_ach_file_void();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_ach_file_whole();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_ach_file_item();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_ach_file();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_payout(uuid, text);
