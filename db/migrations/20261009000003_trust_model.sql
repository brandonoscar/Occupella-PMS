-- migrate:up
-- The trust model: whose money each pgledger account holds, and which bank account it sits in.
--
--   trust_pmcs             property management companies. pmc_id maps to Occupella's companies.id.
--   trust_bank_accounts    a PMC's trust bank accounts, 'operating' or 'security_deposit'.
--   trust_owners           property owners, the PMC's clients.
--   trust_properties       properties the PMC manages.
--   trust_tenants          tenants, so every security deposit has a holder.
--   trust_ledger_accounts  one row per pgledger account: its PMC, bank account and kind.
--
-- Account kinds. Money held for someone is a positive balance.
--   owner_property  one owner's money for one property. Never below zero (Cal. Reg. 2832.1).
--   tenant_deposit  one tenant's security deposit. Never below zero.
--   pmc_income      fees the PMC has earned and not yet taken out. Never below zero.
--   bank_cash       the bank side of one trust bank account. Money arrives from it and leaves
--                   to it, so it runs negative: -balance is the cash the books say the bank holds.
--
-- Rules the database enforces, whoever is connected:
--   - an account of any kind but bank_cash is refused if it would go below zero, by our own
--     trigger (pgledger's allow_negative_balance flag is set too, but nothing relies on it);
--   - a transfer is refused unless both accounts are mapped here and belong to the same PMC;
--   - trust_ledger_accounts is append-only, so an account's kind and owner never change;
--   - no foreign key cascades: deleting a PMC, owner or property never deletes trust records.
-- No bank account numbers or tax IDs are stored in Phase 0.

CREATE TABLE trust_pmcs (
    pmc_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trust_bank_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN ('operating', 'security_deposit')),
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id)
);

CREATE TABLE trust_owners (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id)
);

CREATE TABLE trust_properties (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id)
);

CREATE TABLE trust_tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    -- (pmc_id, x) keys keep every reference inside one PMC.
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id)
);

CREATE TABLE trust_ledger_accounts (
    ledger_account_id text PRIMARY KEY REFERENCES pgledger_accounts (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    kind text NOT NULL,
    owner_id uuid,
    property_id uuid,
    tenant_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    CONSTRAINT trust_ledger_accounts_kind_shape CHECK (
        (
            kind = 'owner_property'
            AND owner_id IS NOT NULL AND property_id IS NOT NULL AND tenant_id IS NULL
        )
        OR (
            kind = 'tenant_deposit'
            AND tenant_id IS NOT NULL AND owner_id IS NULL AND property_id IS NULL
        )
        OR (
            kind IN ('pmc_income', 'bank_cash')
            AND owner_id IS NULL AND property_id IS NULL AND tenant_id IS NULL
        )
    )
);

CREATE INDEX trust_ledger_accounts_pmc_id ON trust_ledger_accounts (pmc_id);

-- One ledger per holder per bank account.
CREATE UNIQUE INDEX trust_ledger_accounts_one_bank_cash
ON trust_ledger_accounts (bank_account_id) WHERE kind = 'bank_cash';
CREATE UNIQUE INDEX trust_ledger_accounts_one_pmc_income
ON trust_ledger_accounts (bank_account_id) WHERE kind = 'pmc_income';
CREATE UNIQUE INDEX trust_ledger_accounts_one_owner_property
ON trust_ledger_accounts (bank_account_id, owner_id, property_id) WHERE kind = 'owner_property';
CREATE UNIQUE INDEX trust_ledger_accounts_one_tenant_deposit
ON trust_ledger_accounts (bank_account_id, tenant_id) WHERE kind = 'tenant_deposit';

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ledger_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- The no-negative rule. pgledger updates the account row before it writes the transfer and
-- entries, all in one statement, so raising here refuses the whole transfer (or the whole batch)
-- and nothing is written. An account with no trust_ledger_accounts row is refused too.
CREATE FUNCTION trust_refuse_negative_balance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_kind text;
BEGIN
    SELECT kind INTO v_kind FROM trust_ledger_accounts WHERE ledger_account_id = NEW.id;
    IF v_kind IS DISTINCT FROM 'bank_cash' THEN
        RAISE EXCEPTION 'trust: % account % would go below zero (balance %)',
            coalesce(v_kind, 'unmapped'), NEW.id, NEW.balance
            USING ERRCODE = 'check_violation',
                  HINT = 'An account held for someone may never be overdrawn (Cal. Reg. 2832.1).';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_no_negative_balance
BEFORE INSERT OR UPDATE OF balance ON pgledger_accounts
FOR EACH ROW WHEN (NEW.balance < 0)
EXECUTE FUNCTION trust_refuse_negative_balance();

-- Every transfer stays inside one PMC, between accounts that have a trust kind.
CREATE FUNCTION trust_check_transfer_scope() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_from_pmc uuid;
    v_to_pmc uuid;
BEGIN
    SELECT pmc_id INTO v_from_pmc
    FROM trust_ledger_accounts WHERE ledger_account_id = NEW.from_account_id;
    SELECT pmc_id INTO v_to_pmc
    FROM trust_ledger_accounts WHERE ledger_account_id = NEW.to_account_id;

    IF v_from_pmc IS NULL OR v_to_pmc IS NULL THEN
        RAISE EXCEPTION 'trust: transfer % -> % touches an account with no trust kind',
            NEW.from_account_id, NEW.to_account_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_from_pmc <> v_to_pmc THEN
        RAISE EXCEPTION 'trust: transfer % -> % crosses PMCs',
            NEW.from_account_id, NEW.to_account_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_transfer_scope
BEFORE INSERT ON pgledger_transfers
FOR EACH ROW EXECUTE FUNCTION trust_check_transfer_scope();

-- The only way to open a ledger account: create the pgledger account and its trust row together.
-- Runs as the owner, so trust_app needs no grant on pgledger_accounts or trust_ledger_accounts.
CREATE FUNCTION trust_open_ledger_account(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_kind text,
    p_owner_id uuid DEFAULT NULL,
    p_property_id uuid DEFAULT NULL,
    p_tenant_id uuid DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_ledger_account_id text;
BEGIN
    SELECT a.id INTO v_ledger_account_id
    FROM pgledger_create_account(
        p_kind, 'USD', allow_negative_balance => p_kind = 'bank_cash'
    ) AS a;

    INSERT INTO trust_ledger_accounts (
        ledger_account_id, pmc_id, bank_account_id, kind, owner_id, property_id, tenant_id
    ) VALUES (
        v_ledger_account_id, p_pmc_id, p_bank_account_id, p_kind,
        p_owner_id, p_property_id, p_tenant_id
    );

    RETURN v_ledger_account_id;
END;
$$;

REVOKE ALL ON
    trust_pmcs, trust_bank_accounts, trust_owners, trust_properties, trust_tenants,
    trust_ledger_accounts
FROM PUBLIC;

-- The app may add and rename records, never delete them.
GRANT SELECT, INSERT ON
    trust_pmcs, trust_bank_accounts, trust_owners, trust_properties, trust_tenants
TO trust_app;
GRANT UPDATE (display_name) ON
    trust_pmcs, trust_bank_accounts, trust_owners, trust_properties, trust_tenants
TO trust_app;
GRANT SELECT ON trust_ledger_accounts TO trust_app;

REVOKE EXECUTE ON FUNCTION
    trust_open_ledger_account(uuid, uuid, text, uuid, uuid, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    trust_open_ledger_account(uuid, uuid, text, uuid, uuid, uuid)
TO trust_app;

-- migrate:down
-- Trust records are kept for years, so the trust model is never rolled back.
DO $$ BEGIN RAISE EXCEPTION 'trust_model cannot be rolled back'; END $$;
