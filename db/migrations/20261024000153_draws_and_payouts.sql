-- migrate:up
-- Owner draws and payout files (ADR 0010 phase 1b, the founder's design pass parts B and C;
-- ADR 0009: owners and vendors are paid by an ACH file the company uploads to its own bank, or
-- by printed check). The PMS still never moves money itself: it records what was paid and
-- writes the file the company takes to its bank.
--
--   trust_owner_draw_settings    the company's draw day (1 to 28), or none. Append-only, the
--                                latest row holding, naming who changed it.
--   trust_draw_runs, trust_draw_run_lines
--                                on the draw day, the nightly run prepares every owner's draw
--                                (trust_prepare_owner_draws): what each property account has
--                                available then and how its owner is paid. Once a month; it
--                                pays nothing.
--   trust_draw_run_approvals     staff approve a prepared run by paying it on Pay owners (the
--                                draws go through trust_draw_owner as before), once.
--   trust_owner_payout_methods   how each owner is paid: ACH ('EFT') or check. Append-only, the
--                                latest row holding.
--   trust_payee_bank_accounts    an owner's or vendor's bank account for ACH: its routing number
--                                (checked by its check digit), the last four of the account
--                                number, and the whole number sealed by the app with the
--                                server's key (PMS_DATA_KEY, AES-GCM) before it reaches the
--                                database. Append-only, the latest row holding. The AI never
--                                reads it (ADR 0009, rule 5).
--   trust_bank_payout_settings   for each trust bank account: what its bank asks in an ACH file
--                                (the bank's routing number and name, the company's origin and
--                                company ids and name) and the first number of its checks.
--                                Append-only, the latest row holding.
--   trust_payout_runs, trust_payout_run_items
--                                the payouts recorded together on Pay owners or Pay bills from
--                                one trust bank account that leave by a file the PMS writes: an
--                                ACH file ('EFT') or printed checks ('check'). Each item is a
--                                draw or a bill payment's transfer, once. Payouts only recorded
--                                (made outside the PMS) are no run, as before.
--   trust_payout_file_downloads  each time staff take an ACH file or a run's checks: the file
--                                holds whole account numbers, so each download names who.
--
-- Draws hold back every bill dated by the draw day and not yet paid (trust_unpaid_bills, since
-- migration 0017), whatever its due date: a bill due on or before the draw day is held, and so
-- is one due later (ADR 0010).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- An ABA routing number: nine digits whose check digit holds (3, 7, 1 weights, a multiple of 10).
CREATE FUNCTION trust_routing_number_ok(p_number text) RETURNS boolean
LANGUAGE sql IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT p_number ~ '^[0-9]{9}$'
       AND (3 * (substr(p_number, 1, 1)::int + substr(p_number, 4, 1)::int
                 + substr(p_number, 7, 1)::int)
            + 7 * (substr(p_number, 2, 1)::int + substr(p_number, 5, 1)::int
                   + substr(p_number, 8, 1)::int)
            + substr(p_number, 3, 1)::int + substr(p_number, 6, 1)::int
            + substr(p_number, 9, 1)::int) % 10 = 0
$$;
REVOKE ALL ON FUNCTION trust_routing_number_ok(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_routing_number_ok(text) TO trust_app;

-- --- the draw day ----------------------------------------------------------------------------

CREATE TABLE trust_owner_draw_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The day of the month the nightly run prepares the draws; none: no draw day.
    -- squawk-ignore prefer-bigint-over-int
    draw_day integer CHECK (draw_day BETWEEN 1 AND 28),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_owner_draw_settings_pmc ON trust_owner_draw_settings (pmc_id, created_at);
CREATE INDEX trust_owner_draw_settings_recorded_by
ON trust_owner_draw_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_owner_draw_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_draw_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_draw_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_draw_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_owner_draw_settings TO trust_app;
GRANT SELECT ON trust_owner_draw_settings TO trust_ai_agent;

-- --- how each owner is paid --------------------------------------------------------------------

CREATE TABLE trust_owner_payout_methods (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    owner_id uuid NOT NULL,
    method text NOT NULL CHECK (method IN ('check', 'EFT')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_owner_payout_methods_owner
ON trust_owner_payout_methods (pmc_id, owner_id, created_at);
CREATE INDEX trust_owner_payout_methods_recorded_by
ON trust_owner_payout_methods (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_owner_payout_methods
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_payout_methods
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_payout_methods
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_payout_methods FROM PUBLIC;
GRANT SELECT, INSERT ON trust_owner_payout_methods TO trust_app;
GRANT SELECT ON trust_owner_payout_methods TO trust_ai_agent;

-- --- an owner's or vendor's bank account -------------------------------------------------------

CREATE TABLE trust_payee_bank_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    owner_id uuid,
    vendor_id uuid,
    routing_number text NOT NULL CHECK (trust_routing_number_ok(routing_number)),
    account_type text NOT NULL CHECK (account_type IN ('checking', 'savings')),
    -- The whole account number, sealed by the app (a version byte, a nonce and AES-GCM's
    -- ciphertext); only its last four are kept in the clear, to show.
    account_sealed bytea NOT NULL CHECK (octet_length(account_sealed) BETWEEN 30 AND 200),
    account_last4 text NOT NULL CHECK (account_last4 ~ '^[0-9]{4}$'),
    holder_name text NOT NULL CHECK (btrim(holder_name) <> '' AND char_length(holder_name) <= 100),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(owner_id, vendor_id) = 1),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payee_bank_accounts_owner
ON trust_payee_bank_accounts (pmc_id, owner_id, created_at);
CREATE INDEX trust_payee_bank_accounts_vendor
ON trust_payee_bank_accounts (pmc_id, vendor_id, created_at);
CREATE INDEX trust_payee_bank_accounts_recorded_by
ON trust_payee_bank_accounts (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payee_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payee_bank_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payee_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payee_bank_accounts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payee_bank_accounts TO trust_app;

-- --- what a trust bank account's bank asks -----------------------------------------------------

CREATE TABLE trust_bank_payout_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    -- An ACH file's header: the bank it goes to (Immediate Destination) and who sends it
    -- (Immediate Origin), as the bank gives them; its batches name the company.
    bank_routing_number text CHECK (trust_routing_number_ok(bank_routing_number)),
    bank_name text CHECK (bank_name ~ '^[A-Za-z0-9 .,&''-]{1,23}$'),
    origin_id text CHECK (origin_id ~ '^[A-Za-z0-9 ]{10}$'),
    origin_name text CHECK (origin_name ~ '^[A-Za-z0-9 .,&''-]{1,23}$'),
    company_id text CHECK (company_id ~ '^[A-Za-z0-9 ]{10}$'),
    company_name text CHECK (company_name ~ '^[A-Za-z0-9 .,&''-]{1,16}$'),
    -- The number of the first check printed from this account; later ones count on from the
    -- highest number recorded.
    first_check_number bigint CHECK (first_check_number BETWEEN 1 AND 999999999999999),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- An ACH file needs every one of them, or none is kept.
    CHECK (num_nonnulls(bank_routing_number, bank_name, origin_id, origin_name, company_id,
                        company_name) IN (0, 6)),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_bank_payout_settings_account
ON trust_bank_payout_settings (pmc_id, bank_account_id, created_at);
CREATE INDEX trust_bank_payout_settings_recorded_by
ON trust_bank_payout_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_bank_payout_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_payout_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_payout_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_bank_payout_settings FROM PUBLIC;
GRANT SELECT, INSERT ON trust_bank_payout_settings TO trust_app;

-- --- payouts recorded together, and their files ----------------------------------------------

CREATE TABLE trust_payout_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('owners', 'bills')),
    method text NOT NULL CHECK (method IN ('EFT', 'check')),
    paid_on date NOT NULL,
    -- The page's request: sent again, it finds this run rather than making another. One request
    -- makes a run for each trust bank account and way paid (an ACH file and checks, say).
    request_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, request_id, bank_account_id, method),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payout_runs_account ON trust_payout_runs (pmc_id, bank_account_id, created_at);
CREATE INDEX trust_payout_runs_recorded_by ON trust_payout_runs (pmc_id, recorded_by);

CREATE TABLE trust_payout_run_items (
    run_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    transfer_id text NOT NULL UNIQUE REFERENCES pgledger_transfers (id),
    owner_id uuid,
    vendor_id uuid,
    PRIMARY KEY (run_id, transfer_id),
    CHECK (num_nonnulls(owner_id, vendor_id) = 1),
    FOREIGN KEY (pmc_id, run_id) REFERENCES trust_payout_runs (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id)
);
CREATE INDEX trust_payout_run_items_owner ON trust_payout_run_items (pmc_id, owner_id);
CREATE INDEX trust_payout_run_items_vendor ON trust_payout_run_items (pmc_id, vendor_id);

CREATE TABLE trust_payout_file_downloads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    run_id uuid NOT NULL,
    file text NOT NULL CHECK (file IN ('ach', 'checks')),
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, run_id) REFERENCES trust_payout_runs (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payout_file_downloads_run ON trust_payout_file_downloads (pmc_id, run_id);
CREATE INDEX trust_payout_file_downloads_recorded_by
ON trust_payout_file_downloads (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payout_runs
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payout_file_downloads
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payout_runs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payout_run_items
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payout_file_downloads
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payout_runs
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payout_run_items
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payout_file_downloads
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- An item is a transfer that paid out of the run's trust bank account: an owner's draw (the
-- owner's) or a bill paid (its vendor's), each in one run.
CREATE FUNCTION trust_check_payout_item() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_run trust_payout_runs;
    v_draw_owner uuid;
    v_bill_vendor uuid;
    v_bank uuid;
BEGIN
    SELECT r.* INTO v_run FROM trust_payout_runs AS r WHERE r.id = NEW.run_id;
    SELECT a.owner_id, a.bank_account_id INTO v_draw_owner, v_bank
    FROM trust_owner_draws AS d
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = d.ledger_account_id
    WHERE d.transfer_id = NEW.transfer_id;
    IF v_draw_owner IS NULL THEN
        SELECT b.vendor_id, a.bank_account_id INTO v_bill_vendor, v_bank
        FROM trust_bill_payments AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
        WHERE p.transfer_id = NEW.transfer_id AND p.step = 'paid';
    END IF;
    IF (v_run.kind = 'owners' AND v_draw_owner IS DISTINCT FROM NEW.owner_id)
       OR (v_run.kind = 'bills' AND v_bill_vendor IS DISTINCT FROM NEW.vendor_id)
       OR v_bank IS DISTINCT FROM v_run.bank_account_id
       OR v_run.pmc_id <> NEW.pmc_id THEN
        RAISE EXCEPTION 'trust: transfer % is not a % paid out of run %''s trust bank account',
            NEW.transfer_id, CASE v_run.kind WHEN 'owners' THEN 'draw' ELSE 'bill' END,
            NEW.run_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_payout_item() FROM PUBLIC;
CREATE TRIGGER trust_check_payout_item BEFORE INSERT ON trust_payout_run_items
FOR EACH ROW EXECUTE FUNCTION trust_check_payout_item();

REVOKE ALL ON trust_payout_runs, trust_payout_run_items, trust_payout_file_downloads FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payout_runs, trust_payout_run_items, trust_payout_file_downloads
TO trust_app;
GRANT SELECT ON trust_payout_runs, trust_payout_run_items TO trust_ai_agent;

-- --- prepared draws ----------------------------------------------------------------------------

CREATE TABLE trust_draw_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The month it is the draw of (its first day), and the day it was prepared.
    month date NOT NULL CHECK (extract(day FROM month) = 1),
    prepared_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, month)
);

CREATE TABLE trust_draw_run_lines (
    run_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    -- What the account had available on the day it was prepared.
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    method text NOT NULL CHECK (method IN ('check', 'EFT')),
    PRIMARY KEY (run_id, ledger_account_id),
    FOREIGN KEY (pmc_id, run_id) REFERENCES trust_draw_runs (pmc_id, id)
);
CREATE INDEX trust_draw_run_lines_account ON trust_draw_run_lines (ledger_account_id);

CREATE TABLE trust_draw_run_approvals (
    run_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    -- The Pay owners request that paid it.
    request_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, run_id) REFERENCES trust_draw_runs (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_draw_run_approvals_recorded_by
ON trust_draw_run_approvals (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_draw_run_approvals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_draw_runs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_draw_run_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_draw_run_approvals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_draw_runs
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_draw_run_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_draw_run_approvals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_draw_runs, trust_draw_run_lines, trust_draw_run_approvals FROM PUBLIC;
GRANT SELECT ON trust_draw_runs, trust_draw_run_lines TO trust_app, trust_ai_agent;
GRANT SELECT, INSERT ON trust_draw_run_approvals TO trust_app;
GRANT SELECT ON trust_draw_run_approvals TO trust_ai_agent;

-- On the company's draw day (or the first night after it, if the run missed it), the month's
-- draws prepared once: each owner's property account with money available on p_day (its
-- balance less its reserve and the bills not yet paid, as trust_draw_owner counts it) and how
-- its owner is paid (a check unless they chose ACH). Nothing is paid. Returns the run, or null
-- when there is no draw day, it hasn't come, or the month has its run already.
CREATE FUNCTION trust_prepare_owner_draws(p_pmc_id uuid, p_day date) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_draw_day integer;
    v_month date := date_trunc('month', p_day::timestamp)::date;
    v_run uuid;
BEGIN
    IF p_day IS NULL THEN
        RAISE EXCEPTION 'trust: draws are prepared on a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.draw_day INTO v_draw_day
    FROM trust_owner_draw_settings AS s
    WHERE s.pmc_id = p_pmc_id
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1;
    IF v_draw_day IS NULL OR extract(day FROM p_day) < v_draw_day THEN
        RETURN NULL;
    END IF;
    -- Two nights at once prepare one run: the second waits, then finds the first's.
    PERFORM pg_advisory_xact_lock(hashtextextended('trust_draw_runs:' || p_pmc_id::text, 0));
    IF EXISTS (SELECT 1 FROM trust_draw_runs AS r
               WHERE r.pmc_id = p_pmc_id AND r.month = v_month) THEN
        RETURN NULL;
    END IF;

    INSERT INTO trust_draw_runs (pmc_id, month, prepared_on)
    VALUES (p_pmc_id, v_month, p_day)
    RETURNING id INTO v_run;
    INSERT INTO trust_draw_run_lines (run_id, pmc_id, ledger_account_id, amount, method)
    SELECT v_run, p_pmc_id, x.ledger_account_id, x.available, x.method
    FROM (
        SELECT a.ledger_account_id,
               round(b.balance - coalesce((trust_agreement_on(a.ledger_account_id, p_day)).reserve,
                                          0)
                     - trust_unpaid_bills(a.ledger_account_id, p_day), 2) AS available,
               coalesce((SELECT m.method FROM trust_owner_payout_methods AS m
                         WHERE m.pmc_id = p_pmc_id AND m.owner_id = a.owner_id
                         ORDER BY m.created_at DESC, m.id DESC LIMIT 1), 'check') AS method
        FROM trust_ledger_accounts AS a
        JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
        WHERE a.pmc_id = p_pmc_id AND a.kind = 'owner_property'
    ) AS x
    WHERE x.available > 0;
    RETURN v_run;
END;
$$;
REVOKE ALL ON FUNCTION trust_prepare_owner_draws(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_prepare_owner_draws(uuid, date) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'draws_and_payouts cannot be rolled back: draw runs, payout runs and bank '
        'accounts are records';
END;
$$;
