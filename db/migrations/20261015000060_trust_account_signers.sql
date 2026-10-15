-- migrate:up
-- Who may pay money out of each trust bank account (founder, 2026-10-15: "make sure what we are
-- doing is by law like ones that apply in all states"; docs/law.md). In every state the broker
-- whose license the company runs under controls its trust accounts and stays liable for them;
-- anyone else withdraws only with the broker's written authorization (West Virginia Code
-- 30-40-18(g); California 10 CCR 2834; Arizona A.R.S. 32-2174; Florida 61J2-14.010 has the
-- broker sign every account). Until now any staff member holding disburse could record a payout
-- from any account.
--
--   trust_account_signers   for a trust bank account and a staff member: the account's broker
--                           (an admin names them) or a signer the broker authorized, with the
--                           day of the written authorization, and how they may sign: a license
--                           number, or a fidelity bond (its amount and the day it runs out) where
--                           the state lets an unlicensed employee sign (California). A row that
--                           takes it away (granted false) ends it; the latest row for an account
--                           and a staff member holds. Append-only, audited.
--
-- Once an account has a broker on file, a payout from it recorded by a named staff member (an
-- owner draw, a bill paid, a deposit returned, the company's fees taken out, a journal entry
-- that takes cash out) needs that staff member to be its broker or a signer, and a bonded
-- signer's bond in force on the day the money moved. An account with none on file keeps the
-- roles alone, so nothing stops on upgrade; Today asks for its broker. A write that names nobody
-- (the server's command line) is not held to it, as with the roles. One broker per account, and
-- signers only while it has one, checked at commit.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_account_signers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    staff_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('broker', 'signer')),
    granted boolean NOT NULL,
    license_number text CHECK (license_number ~ '\S' AND length(license_number) <= 50),
    bond_amount numeric CHECK (bond_amount > 0 AND bond_amount = round(bond_amount, 2)),
    bond_expires_on date,
    -- The day the broker authorized it in writing, or took it away.
    authorized_on date NOT NULL,
    changed_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, changed_by) REFERENCES trust_staff (pmc_id, id),
    CHECK ((bond_amount IS NULL) = (bond_expires_on IS NULL)),
    -- A grant says how they may sign; the broker is licensed.
    CHECK (NOT granted OR license_number IS NOT NULL OR bond_amount IS NOT NULL),
    CHECK (NOT granted OR role = 'signer' OR license_number IS NOT NULL),
    -- Taking it away says nothing more.
    CHECK (granted OR (license_number IS NULL AND bond_amount IS NULL))
);
CREATE INDEX trust_account_signers_account
ON trust_account_signers (bank_account_id, staff_id, created_at);
CREATE INDEX trust_account_signers_staff ON trust_account_signers (pmc_id, staff_id);
CREATE INDEX trust_account_signers_changed_by ON trust_account_signers (pmc_id, changed_by);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_account_signers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_account_signers
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_account_signers FROM PUBLIC;
GRANT SELECT, INSERT ON trust_account_signers TO trust_app;
GRANT SELECT ON trust_account_signers TO trust_ai_agent;

-- Where a staff member stands on a trust bank account: their latest row (none: not a signer).
CREATE FUNCTION trust_signer_of(p_bank_account_id uuid, p_staff_id uuid)
RETURNS trust_account_signers
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT s.* FROM trust_account_signers AS s
    WHERE s.bank_account_id = p_bank_account_id AND s.staff_id = p_staff_id
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1
$$;

-- The account's broker on file: the staff member whose latest row grants broker.
CREATE FUNCTION trust_broker_of(p_bank_account_id uuid)
RETURNS uuid
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT l.staff_id
    FROM (
        SELECT DISTINCT ON (s.staff_id) s.staff_id, s.role, s.granted
        FROM trust_account_signers AS s
        WHERE s.bank_account_id = p_bank_account_id
        ORDER BY s.staff_id, s.created_at DESC, s.id DESC
    ) AS l
    WHERE l.role = 'broker' AND l.granted
    ORDER BY l.staff_id
    LIMIT 1
$$;

REVOKE ALL ON FUNCTION trust_signer_of(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_broker_of(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_signer_of(uuid, uuid) TO trust_app;
GRANT EXECUTE ON FUNCTION trust_broker_of(uuid) TO trust_app;

-- A change: an admin names the broker; the broker authorizes or takes away a signer. Whoever is
-- named on the write is recorded as having changed it.
CREATE FUNCTION trust_check_signer_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_by uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_was trust_account_signers;
    v_broker uuid;
BEGIN
    NEW.changed_by := v_by;
    -- Signers' changes wait for each other, account by account, and read who is on file only
    -- once the change before them committed, so two brokers named at once can't both stand.
    PERFORM pg_advisory_xact_lock(hashtextextended('signers of ' || NEW.bank_account_id, 0));
    v_was := trust_signer_of(NEW.bank_account_id, NEW.staff_id);
    v_broker := trust_broker_of(NEW.bank_account_id);
    IF NEW.granted IS NOT TRUE AND (v_was.id IS NULL OR NOT v_was.granted OR v_was.role <> NEW.role)
    THEN
        RAISE EXCEPTION 'trust: % is not the % of this trust bank account; nothing to take away',
            (SELECT s.display_name FROM trust_staff AS s WHERE s.id = NEW.staff_id), NEW.role
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.role = 'broker' THEN
        IF v_by IS NOT NULL AND NOT trust_staff_holds(NEW.pmc_id, v_by, 'admin') THEN
            RAISE EXCEPTION 'trust: % may not name a trust bank account''s broker (the admin role)',
                (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_by)
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'An admin of the PMC names the broker.';
        END IF;
        IF NEW.granted AND v_broker IS NOT NULL AND v_broker <> NEW.staff_id THEN
            RAISE EXCEPTION 'trust: this trust bank account has a broker on file; take that away '
                'first, in the same change'
                USING ERRCODE = 'unique_violation';
        END IF;
    ELSE
        IF v_by IS NOT NULL AND v_by IS DISTINCT FROM v_broker THEN
            RAISE EXCEPTION 'trust: only this trust bank account''s broker authorizes its signers'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'The broker on file signs in and authorizes them.';
        END IF;
        IF NEW.granted AND NEW.staff_id = v_broker THEN
            RAISE EXCEPTION 'trust: the broker signs this trust bank account already'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_signer_change
BEFORE INSERT ON trust_account_signers
FOR EACH ROW EXECUTE FUNCTION trust_check_signer_change();

-- At commit: signers only while the account has its broker on file. (One broker per account
-- is trust_check_signer_change's, under the account's lock.)
CREATE FUNCTION trust_check_signers_have_a_broker() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_brokers bigint;
    v_signers bigint;
BEGIN
    SELECT count(*) FILTER (WHERE l.role = 'broker'), count(*) FILTER (WHERE l.role = 'signer')
    INTO v_brokers, v_signers
    FROM (
        SELECT DISTINCT ON (s.staff_id) s.role, s.granted
        FROM trust_account_signers AS s
        WHERE s.bank_account_id = NEW.bank_account_id
        ORDER BY s.staff_id, s.created_at DESC, s.id DESC
    ) AS l
    WHERE l.granted;
    IF v_signers > 0 AND v_brokers = 0 THEN
        RAISE EXCEPTION 'trust: a trust bank account''s signers need its broker on file'
            USING ERRCODE = 'check_violation',
                  HINT = 'Name the new broker in the same change, or take the signers away.';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_signers_have_a_broker
AFTER INSERT ON trust_account_signers
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_signers_have_a_broker();

-- Refuses a payout from the account by the named staff member (if any) unless the account has
-- no broker on file, or they are its broker or a signer, a bonded signer's bond in force that day.
CREATE FUNCTION trust_require_signer(p_bank_account_id uuid, p_day date)
RETURNS void
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_signer trust_account_signers;
    v_name text;
    v_account text;
BEGIN
    IF v_staff IS NULL OR trust_broker_of(p_bank_account_id) IS NULL THEN
        RETURN;
    END IF;
    v_signer := trust_signer_of(p_bank_account_id, v_staff);
    SELECT s.display_name INTO v_name FROM trust_staff AS s WHERE s.id = v_staff;
    SELECT b.display_name INTO v_account FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id;
    IF v_signer.id IS NULL OR NOT v_signer.granted THEN
        RAISE EXCEPTION 'trust: % is not an authorized signer of %', v_name, v_account
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'The account''s broker authorizes its signers.';
    END IF;
    IF v_signer.license_number IS NULL AND v_signer.bond_expires_on < p_day THEN
        RAISE EXCEPTION 'trust: %''s bond to sign % ran out on %', v_name, v_account,
            v_signer.bond_expires_on
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'The broker records the renewed bond.';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION trust_require_signer(uuid, date) FROM PUBLIC;

-- Each payout's trust bank account and day, checked before it is recorded: one trigger function
-- for each kind of payout, so each reads only its own table's columns.

-- An owner draw or the company's fees taken out: the account paid from, on the transfer's day.
CREATE FUNCTION trust_check_draw_signer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_signer(a.bank_account_id, (t.event_at AT TIME ZONE 'UTC')::date)
    FROM trust_ledger_accounts AS a, pgledger_transfers AS t
    WHERE a.ledger_account_id = NEW.ledger_account_id AND t.id = NEW.transfer_id;
    RETURN NEW;
END;
$$;

-- A bill paid: the trust bank account whose cash the payment left.
CREATE FUNCTION trust_check_bill_payment_signer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_signer(a.bank_account_id, (t.event_at AT TIME ZONE 'UTC')::date)
    FROM pgledger_transfers AS t
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.from_account_id
    WHERE t.id = NEW.transfer_id;
    RETURN NEW;
END;
$$;

-- A deposit returned: every trust bank account holding a deposit of the lease's tenants.
CREATE FUNCTION trust_check_deposit_return_signer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT a.bank_account_id
        FROM trust_lease_tenants AS lt
        JOIN trust_ledger_accounts AS a
          ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
        WHERE lt.lease_id = NEW.lease_id
        ORDER BY a.bank_account_id
    LOOP
        PERFORM trust_require_signer(v_bank, (NEW.returned_at AT TIME ZONE 'UTC')::date);
    END LOOP;
    RETURN NEW;
END;
$$;

-- A journal entry posted: every trust bank account whose cash it takes out, on its day.
CREATE FUNCTION trust_check_journal_signer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_day date;
BEGIN
    FOR v_bank, v_day IN
        SELECT DISTINCT c.bank_account_id, e.event_on
        FROM trust_journal_entry_lines AS l
        JOIN trust_journal_entries AS e ON e.id = l.entry_id
        JOIN trust_ledger_accounts AS c
          ON c.ledger_account_id = l.to_account AND c.kind = 'bank_cash'
        WHERE l.entry_id = NEW.entry_id
        ORDER BY c.bank_account_id
    LOOP
        PERFORM trust_require_signer(v_bank, v_day);
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_signer
BEFORE INSERT ON trust_owner_draws
FOR EACH ROW EXECUTE FUNCTION trust_check_draw_signer();
CREATE TRIGGER trust_signer
BEFORE INSERT ON trust_pmc_fee_withdrawals
FOR EACH ROW EXECUTE FUNCTION trust_check_draw_signer();
CREATE TRIGGER trust_signer
BEFORE INSERT ON trust_bill_payments
FOR EACH ROW WHEN (NEW.step = 'paid') EXECUTE FUNCTION trust_check_bill_payment_signer();
CREATE TRIGGER trust_signer
BEFORE INSERT ON trust_deposit_returns
FOR EACH ROW EXECUTE FUNCTION trust_check_deposit_return_signer();
CREATE TRIGGER trust_signer
BEFORE INSERT ON trust_journal_entry_decisions
FOR EACH ROW WHEN (NEW.decision = 'posted') EXECUTE FUNCTION trust_check_journal_signer();

-- Each trust bank account's broker and signers on a day, and how each may sign: licensed, or
-- bonded (current, running out within 30 days, or run out). An account with no broker on file
-- says so. Read by staff's page and Today; it moves no money.
CREATE FUNCTION trust_report_trust_account_signers(p_pmc_id uuid, p_on date)
RETURNS TABLE (
    line integer,
    bank_account_id uuid,
    bank_account text,
    staff_id uuid,
    signer text,
    role text,
    license_number text,
    bond_amount numeric,
    bond_expires_on date,
    authorized_on date,
    standing text
)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH latest AS (
        SELECT DISTINCT ON (s.bank_account_id, s.staff_id) s.*
        FROM trust_account_signers AS s
        WHERE s.pmc_id = p_pmc_id AND s.authorized_on <= p_on
        ORDER BY s.bank_account_id, s.staff_id, s.created_at DESC, s.id DESC
    ),
    rows AS (
        SELECT b.id AS bank_id, b.display_name AS bank, l.staff_id, st.display_name AS signer,
               l.role, l.license_number, l.bond_amount, l.bond_expires_on, l.authorized_on,
               CASE
                   WHEN l.license_number IS NOT NULL THEN 'licensed'
                   WHEN l.bond_expires_on < p_on THEN 'bond ran out'
                   WHEN l.bond_expires_on - p_on <= 30 THEN
                       'bond runs out in ' || (l.bond_expires_on - p_on) || ' day'
                       || CASE WHEN l.bond_expires_on - p_on = 1 THEN '' ELSE 's' END
                   ELSE 'bonded'
               END AS standing,
               CASE l.role WHEN 'broker' THEN 0 ELSE 1 END AS rank
        FROM trust_bank_accounts AS b
        JOIN latest AS l ON l.bank_account_id = b.id AND l.granted
        JOIN trust_staff AS st ON st.id = l.staff_id
        WHERE b.pmc_id = p_pmc_id
        UNION ALL
        SELECT b.id, b.display_name, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               'no broker on file', 0
        FROM trust_bank_accounts AS b
        WHERE b.pmc_id = p_pmc_id
          AND NOT EXISTS (
              SELECT 1 FROM latest AS l
              WHERE l.bank_account_id = b.id AND l.role = 'broker' AND l.granted
          )
    )
    SELECT (row_number() OVER (
                ORDER BY r.bank COLLATE "C", r.bank_id, r.rank, r.signer COLLATE "C", r.staff_id
            ))::integer,
           r.bank_id, r.bank, r.staff_id, r.signer, r.role, r.license_number, r.bond_amount,
           r.bond_expires_on, r.authorized_on, r.standing
    FROM rows AS r
    ORDER BY 1
$$;

REVOKE ALL ON FUNCTION trust_report_trust_account_signers(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_trust_account_signers(uuid, date)
TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'trust account signers cannot be rolled back: they are records';
END;
$$;
