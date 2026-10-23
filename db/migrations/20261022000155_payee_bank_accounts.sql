-- migrate:up
-- Where a vendor or an owner is paid by bank transfer (the founder, 2026-10-23: ACH files and
-- checks, bank numbers kept encrypted). A payee's bank account is a row of
-- trust_payee_bank_accounts: the name on the account, its bank's routing number, checking or
-- savings, and its account number sealed by the app (AES-GCM, with the server's PMS_FIELD_KEY,
-- which never reaches the database; src/pms/sealed.py) beside its last four digits. The latest
-- row for a payee holds; a row marked removed takes the account away. Append-only and audited,
-- naming who; a staff member named on the write must hold disburse, as changing where money goes
-- is as much a payout as paying it. The AI role reads none of it (tests/test_privileges.py).
--
-- A routing number is nine digits whose ABA checksum holds (3, 7, 1 weights); which bank it
-- names is not checked. Nothing here moves money: the ACH file that pays into the account comes
-- later, from payouts staff already recorded.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Whether nine digits make an ABA routing number: 3 x (1st + 4th + 7th) + 7 x (2nd + 5th + 8th)
-- + (3rd + 6th + 9th) is a multiple of 10.
CREATE FUNCTION trust_aba_routing_ok(p_routing text) RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT p_routing ~ '^[0-9]{9}$' AND (
        3 * (substr(p_routing, 1, 1)::int + substr(p_routing, 4, 1)::int
             + substr(p_routing, 7, 1)::int)
        + 7 * (substr(p_routing, 2, 1)::int + substr(p_routing, 5, 1)::int
               + substr(p_routing, 8, 1)::int)
        + (substr(p_routing, 3, 1)::int + substr(p_routing, 6, 1)::int
           + substr(p_routing, 9, 1)::int)
    ) % 10 = 0
$$;
REVOKE ALL ON FUNCTION trust_aba_routing_ok(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_aba_routing_ok(text) TO trust_app;

CREATE TABLE trust_payee_bank_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid,
    owner_id uuid,
    removed boolean NOT NULL DEFAULT false,
    -- The name on the account, as an ACH entry carries it (22 characters at most).
    holder_name text CHECK (holder_name = btrim(holder_name) AND char_length(holder_name) BETWEEN 1 AND 22),
    routing_number text CHECK (trust_aba_routing_ok(routing_number)),
    account_type text CHECK (account_type IN ('checking', 'savings')),
    -- The account number (4 to 17 digits), sealed by the app: version, nonce, ciphertext.
    account_number_sealed bytea CHECK (octet_length(account_number_sealed) BETWEEN 30 AND 200),
    account_last4 text CHECK (account_last4 ~ '^[0-9]{4}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- Each row is one payee's.
    CHECK (num_nonnulls(vendor_id, owner_id) = 1),
    -- An account is whole; a removal holds none of it.
    CHECK (
        CASE WHEN removed
             THEN num_nonnulls(holder_name, routing_number, account_type, account_number_sealed,
                               account_last4) = 0
             ELSE num_nonnulls(holder_name, routing_number, account_type, account_number_sealed,
                               account_last4) = 5
        END
    )
);
CREATE INDEX trust_payee_bank_accounts_vendor
ON trust_payee_bank_accounts (pmc_id, vendor_id, created_at);
CREATE INDEX trust_payee_bank_accounts_owner
ON trust_payee_bank_accounts (pmc_id, owner_id, created_at);
CREATE INDEX trust_payee_bank_accounts_recorded_by
ON trust_payee_bank_accounts (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payee_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_payee_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'change where money is paid');
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payee_bank_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payee_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payee_bank_accounts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payee_bank_accounts TO trust_app;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_payee_bank_accounts;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_aba_routing_ok(text);
