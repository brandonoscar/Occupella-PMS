-- migrate:up
-- Payment files (the founder's review q035): a bank's or lockbox's file of the tenants' payments
-- it received, brought in once, each line recorded as a tenant's payment to the lease it names
-- (src/pms/api/payment_files.py). A line names its lease by its property and unit, or by the
-- payer's name; one that names no single lease in effect on its day, or looks like a payment
-- already recorded, waits for staff, who record it to a lease they pick or set it aside with why.
-- The money moves only through the rent page's own write path (rent.record_payment, through
-- trust_post_transfers); these tables say which payment each line became.
--
--   trust_payment_files          a file, by its SHA-256: brought in once per PMC, with all its
--                                lines at commit, naming who brought it in.
--   trust_payment_file_lines     what each line of it says: the day, the amount and how it names
--                                its lease; the lease it named, and why it waited for staff if
--                                it did. Never changed.
--   trust_payment_file_postings  a line recorded: the lease, and the request id of the payment
--                                it became, whose transfers carry the line's amount and day at
--                                commit. Once per line.
--   trust_payment_file_skips     a line set aside by staff, with why. Once per line, and never a
--                                line recorded: a line is recorded or set aside, not both.
-- All append-only and audited.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_files (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    file_name text NOT NULL CHECK (file_name ~ '\S' AND length(file_name) <= 200),
    file_sha256 text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    line_count bigint NOT NULL CHECK (line_count BETWEEN 1 AND 5000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (pmc_id, file_sha256),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_files_pmc ON trust_payment_files (pmc_id, created_at);
CREATE INDEX trust_payment_files_recorded_by ON trust_payment_files (pmc_id, recorded_by);

CREATE TABLE trust_payment_file_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    file_id uuid NOT NULL,
    -- The line of the file it was on (the header is line 1).
    line_no bigint NOT NULL CHECK (line_no >= 2),
    received_on date NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2) AND amount < 1000000000),
    property text CHECK (property ~ '\S' AND length(property) <= 200),
    unit text CHECK (unit ~ '\S' AND length(unit) <= 200),
    payer text CHECK (payer ~ '\S' AND length(payer) <= 200),
    reference text CHECK (reference ~ '\S' AND length(reference) <= 200),
    method text CHECK (method IN ('check', 'cashier''s check', 'money order', 'EFT', 'cash')),
    -- The one lease in effect on its day that the line names, if it names one.
    lease_id uuid,
    -- Why it waited for staff when the file came in; NULL when it was recorded then.
    note text CHECK (note ~ '\S' AND length(note) <= 500),
    UNIQUE (pmc_id, id),
    UNIQUE (file_id, line_no),
    FOREIGN KEY (pmc_id, file_id) REFERENCES trust_payment_files (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_payment_file_lines_lease ON trust_payment_file_lines (pmc_id, lease_id);

CREATE TABLE trust_payment_file_postings (
    line_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    -- The payment's request id: its transfers carry the key 'payment/<request_id>'.
    request_id uuid NOT NULL UNIQUE,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, line_id) REFERENCES trust_payment_file_lines (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_file_postings_lease ON trust_payment_file_postings (pmc_id, lease_id);
CREATE INDEX trust_payment_file_postings_recorded_by
ON trust_payment_file_postings (pmc_id, recorded_by);

CREATE TABLE trust_payment_file_skips (
    line_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    reason text NOT NULL CHECK (reason ~ '\S' AND length(reason) <= 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, line_id) REFERENCES trust_payment_file_lines (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_file_skips_recorded_by ON trust_payment_file_skips (pmc_id, recorded_by);

-- A line is one of its file's numbered lines (the header is line 1).
CREATE FUNCTION trust_check_payment_file_line() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_count bigint;
BEGIN
    SELECT f.line_count INTO v_count FROM trust_payment_files AS f WHERE f.id = NEW.file_id;
    IF NEW.line_no > v_count + 1 THEN
        RAISE EXCEPTION 'trust: payment file % has % lines after its header, not line %',
            NEW.file_id, v_count, NEW.line_no
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_payment_file_line
BEFORE INSERT ON trust_payment_file_lines
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_file_line();

-- At commit, a file has all its lines. No line joins later: its number would be past the count,
-- or taken.
CREATE FUNCTION trust_check_payment_file_lines() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lines bigint;
BEGIN
    SELECT count(*) INTO v_lines FROM trust_payment_file_lines AS l WHERE l.file_id = NEW.id;
    IF v_lines <> NEW.line_count THEN
        RAISE EXCEPTION 'trust: payment file % has % lines; it says %', NEW.id, v_lines,
            NEW.line_count
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_payment_file_lines
AFTER INSERT ON trust_payment_files
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_file_lines();

-- A line is recorded or set aside, never both. Each takes the line's lock, so a recording and a
-- setting aside of one line wait for each other.
CREATE FUNCTION trust_check_payment_file_outcome() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('payment file line ' || NEW.line_id::text, 0));
    IF EXISTS (SELECT 1 FROM trust_payment_file_postings AS p WHERE p.line_id = NEW.line_id) THEN
        RAISE EXCEPTION 'trust: payment file line % is recorded already', NEW.line_id
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_payment_file_skips AS s WHERE s.line_id = NEW.line_id) THEN
        RAISE EXCEPTION 'trust: payment file line % is set aside already', NEW.line_id
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_payment_file_outcome
BEFORE INSERT ON trust_payment_file_postings
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_file_outcome();
CREATE TRIGGER trust_payment_file_outcome
BEFORE INSERT ON trust_payment_file_skips
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_file_outcome();

-- At commit, a recorded line is a tenant's payment to its lease, of its amount, on its day.
CREATE FUNCTION trust_check_payment_file_posting() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_line trust_payment_file_lines;
BEGIN
    SELECT l.* INTO v_line FROM trust_payment_file_lines AS l WHERE l.id = NEW.line_id;
    IF NOT EXISTS (
        SELECT 1
        FROM pgledger_transfers AS t
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
        WHERE a.pmc_id = NEW.pmc_id
          AND t.event_at = v_line.received_on::timestamp AT TIME ZONE 'UTC'
          AND t.metadata ->> 'key' = 'payment/' || NEW.request_id::text
          AND t.metadata ->> 'kind' = 'tenant payment'
          AND t.metadata ->> 'lease_id' = NEW.lease_id::text
          AND t.metadata ->> 'amount' = to_char(v_line.amount, 'FM999999999990.00')
          AND t.metadata ->> 'received_on' = to_char(v_line.received_on, 'YYYY-MM-DD')
    ) THEN
        RAISE EXCEPTION 'trust: payment file line % has no payment of % on % to lease %',
            NEW.line_id, v_line.amount, v_line.received_on, NEW.lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_payment_file_posting
AFTER INSERT ON trust_payment_file_postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_payment_file_posting();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_files
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_file_postings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_file_skips
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_files
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_file_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_file_postings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_file_skips
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_files
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_file_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_file_postings
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_file_skips
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payment_files FROM PUBLIC;
REVOKE ALL ON trust_payment_file_lines FROM PUBLIC;
REVOKE ALL ON trust_payment_file_postings FROM PUBLIC;
REVOKE ALL ON trust_payment_file_skips FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_payment_file_line() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_payment_file_lines() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_payment_file_outcome() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_payment_file_posting() FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payment_files TO trust_app;
GRANT SELECT, INSERT ON trust_payment_file_lines TO trust_app;
GRANT SELECT, INSERT ON trust_payment_file_postings TO trust_app;
GRANT SELECT, INSERT ON trust_payment_file_skips TO trust_app;
GRANT SELECT ON trust_payment_files TO trust_ai_agent;
GRANT SELECT ON trust_payment_file_lines TO trust_ai_agent;
GRANT SELECT ON trust_payment_file_postings TO trust_ai_agent;
GRANT SELECT ON trust_payment_file_skips TO trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_file_skips;
-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_file_postings;
-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_file_lines;
-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_files;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_payment_file_posting();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_payment_file_outcome();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_payment_file_lines();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_payment_file_line();
