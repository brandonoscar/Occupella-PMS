-- migrate:up
-- Per trust bank account, the books tie out: the cash the books say the bank holds equals
-- everything held for someone in it (owners, prepaid rent, security deposits, vendors, the
-- PMC's earned fees).
--
-- Every ledger account sits in one trust bank account, and bank_cash runs negative (-balance
-- is the cash), so the rule is: the balances of all accounts in one trust bank account sum to
-- zero. A transfer between two accounts in the same trust bank account keeps that. One that
-- moves money held in the operating account to the deposit account (or back) does too, but
-- only if the cash moves with it: a deposit kept for damages is paid to the owner AND the cash
-- goes from the deposit account to the operating one. So the check runs at COMMIT, once every
-- transfer in the transaction is in, and refuses the whole transaction if any trust bank
-- account it touched no longer ties out.
--
-- Two more kinds of money held in trust:
--   prepaid_rent    rent a tenant paid ahead of its due date. Never below zero.
--   vendor_payable  money set aside for a vendor's approved bill, not yet paid. Never below
--                   zero. Vendors are a new table; their accounts open through
--                   trust_open_vendor_account.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A database whose books already don't tie out would refuse every later posting to that trust
-- bank account. Refuse the migration instead, so someone looks first.
DO $$
DECLARE
    v_bank uuid;
BEGIN
    SELECT t.bank_account_id INTO v_bank
    FROM trust_ledger_accounts AS t
    JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
    GROUP BY t.bank_account_id
    HAVING sum(a.balance) <> 0
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust bank account % does not tie out; fix its books before migrating',
            v_bank;
    END IF;
END;
$$;

CREATE TABLE trust_vendors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id)
);

REVOKE ALL ON trust_vendors FROM PUBLIC;
-- Like the other records: the app may add and rename vendors, never delete them.
GRANT SELECT, INSERT ON trust_vendors TO trust_app;
GRANT UPDATE (display_name) ON trust_vendors TO trust_app;

ALTER TABLE trust_ledger_accounts ADD COLUMN vendor_id uuid;

-- Validating these two constraints scans trust_ledger_accounts, which is small, and every
-- existing row has vendor_id NULL; a separate NOT VALID / VALIDATE step would buy nothing.
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_ledger_accounts ADD CONSTRAINT trust_ledger_accounts_pmc_id_vendor_id_fkey
FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id);

-- The shape of each kind, now with the two new ones. Every existing row has vendor_id NULL.
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
ALTER TABLE trust_ledger_accounts DROP CONSTRAINT trust_ledger_accounts_kind_shape;

-- trust_ledger_accounts is small and only grows when an account is opened, so these indexes
-- build in moments; CONCURRENTLY can't run inside the migration's transaction.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_ledger_accounts_one_prepaid_rent
ON trust_ledger_accounts (bank_account_id, tenant_id) WHERE kind = 'prepaid_rent';
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_ledger_accounts_one_vendor_payable
ON trust_ledger_accounts (bank_account_id, vendor_id) WHERE kind = 'vendor_payable';
-- The tie-out check sums one trust bank account's ledger accounts at every commit.
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_ledger_accounts_bank_account_id ON trust_ledger_accounts (bank_account_id);

-- Opens a vendor's account: the pgledger account and its trust row together, like
-- trust_open_ledger_account. Runs as the owner, so trust_app needs no write grant of its own.
CREATE FUNCTION trust_open_vendor_account(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_vendor_id uuid
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_ledger_account_id text;
BEGIN
    SELECT a.id INTO v_ledger_account_id
    FROM pgledger_create_account('vendor_payable', 'USD') AS a;

    INSERT INTO trust_ledger_accounts (
        ledger_account_id, pmc_id, bank_account_id, kind, vendor_id
    ) VALUES (
        v_ledger_account_id, p_pmc_id, p_bank_account_id, 'vendor_payable', p_vendor_id
    );

    RETURN v_ledger_account_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_open_vendor_account(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_open_vendor_account(uuid, uuid, uuid) TO trust_app;

-- At commit: every trust bank account this transfer touched must still tie out.
CREATE FUNCTION trust_check_bank_tie_out() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_off numeric;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT t.bank_account_id
        FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id IN (NEW.from_account_id, NEW.to_account_id)
    LOOP
        SELECT sum(a.balance) INTO v_off
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.bank_account_id = v_bank;

        IF v_off <> 0 THEN
            RAISE EXCEPTION 'trust: trust bank account % does not tie out (off by %)',
                v_bank, v_off
                USING ERRCODE = 'check_violation',
                      HINT = 'Money held in one trust bank account moves to another only with '
                             'its cash: post both transfers in one transaction.';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_bank_tie_out
AFTER INSERT ON pgledger_transfers
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_bank_tie_out();

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'trust_account_tie_out cannot be rolled back: vendors are trust records';
END;
$$;
