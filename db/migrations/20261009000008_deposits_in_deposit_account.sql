-- migrate:up
-- Each kind of money sits in its own kind of trust bank account, for every PMC (issue #6):
--   security_deposit  tenant security deposits (tenant_deposit), and its own bank_cash;
--   operating         everything else held in trust (owner_property, pmc_income, prepaid_rent,
--                     vendor_payable), and its own bank_cash.
-- Some states require a separate deposit trust account and others allow one combined account.
-- A separate one is the stricter choice, so every PMC keeps one; a per-PMC setting would be a
-- state rule, which Phase 0 leaves out.
--
-- With the tie-out (20261009000005), the deposit account's cash then always equals the deposits
-- it holds, and none of it belongs to anyone else.
--
-- A ledger account never moves to another trust bank account (trust_ledger_accounts is
-- append-only), and from here a trust bank account never changes kind, so an account checked
-- when it opens stays in the right place.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Books that already mix the two would keep doing so. Refuse the migration, so someone looks.
DO $$
DECLARE
    v_account text;
    v_kind text;
    v_bank_kind text;
BEGIN
    SELECT t.ledger_account_id, t.kind, b.kind INTO v_account, v_kind, v_bank_kind
    FROM trust_ledger_accounts AS t
    JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
    WHERE (t.kind = 'tenant_deposit' AND b.kind <> 'security_deposit')
       OR (t.kind NOT IN ('tenant_deposit', 'bank_cash') AND b.kind <> 'operating')
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION '% account % is in the % trust bank account; move it before migrating',
            v_kind, v_account, v_bank_kind;
    END IF;
END;
$$;

-- Refuse a ledger account opened in the other kind of trust bank account.
CREATE FUNCTION trust_check_account_bank_kind() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank_kind text;
    v_belongs_in text;
BEGIN
    SELECT b.kind INTO v_bank_kind
    FROM trust_bank_accounts AS b
    WHERE b.id = NEW.bank_account_id;

    -- bank_cash is the bank side of whichever account it opens in.
    v_belongs_in := CASE NEW.kind
        WHEN 'bank_cash' THEN v_bank_kind
        WHEN 'tenant_deposit' THEN 'security_deposit'
        ELSE 'operating'
    END;

    -- An unknown trust bank account leaves v_bank_kind NULL; the foreign key refuses the row.
    IF v_bank_kind <> v_belongs_in THEN
        RAISE EXCEPTION 'trust: % accounts belong in the % trust bank account, not the % one',
            NEW.kind, v_belongs_in, v_bank_kind
            USING ERRCODE = 'check_violation',
                  HINT = 'Tenant deposits go in the security-deposit trust account; everything '
                         'else held in trust goes in the operating one.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_account_in_its_bank
BEFORE INSERT ON trust_ledger_accounts
FOR EACH ROW EXECUTE FUNCTION trust_check_account_bank_kind();

-- A trust bank account keeps its kind, so the accounts opened in it stay in the right place.
CREATE FUNCTION trust_refuse_changing_bank_kind() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.kind IS DISTINCT FROM OLD.kind THEN
        RAISE EXCEPTION 'trust: trust bank account % is %, and its kind never changes',
            OLD.id, OLD.kind
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Open a new trust bank account of the other kind.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_bank_kind_fixed
BEFORE UPDATE OF kind ON trust_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_refuse_changing_bank_kind();

-- migrate:down
-- Each function is called only by the trigger dropped just before it; no client calls them.
DROP TRIGGER trust_bank_kind_fixed ON trust_bank_accounts;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_refuse_changing_bank_kind();
DROP TRIGGER trust_account_in_its_bank ON trust_ledger_accounts;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_account_bank_kind();
