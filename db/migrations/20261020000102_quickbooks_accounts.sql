-- migrate:up
-- QuickBooks (the founder's review q117): the trust ledger's transfers export as journal entries
-- QuickBooks reads (Desktop's IIF, Online's journal import CSV; src/pms/quickbooks.py), each
-- ledger account named as the company's QuickBooks account for it.
--
--   trust_quickbooks_accounts  a ledger account's name in QuickBooks, from when staff set it
--                              (append-only, the latest row holding, naming who set it); an
--                              account with none (or whose latest row names none) goes by a
--                              name the PMS makes from its kind and holder. A colon nests it under a parent account, as QuickBooks
--                              writes a subaccount.
--
-- A name moves no money and the export writes nothing; like expense accounts, the AI role reads
-- it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A ledger account is named with its PMC, so a name keeps to its own company's accounts.
-- squawk-ignore disallowed-unique-constraint
ALTER TABLE trust_ledger_accounts ADD CONSTRAINT trust_ledger_accounts_pmc_id_ledger_account_key
UNIQUE (pmc_id, ledger_account_id);

CREATE TABLE trust_quickbooks_accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ledger_account_id text NOT NULL,
    -- QuickBooks' own rules: no tab or line break, a colon only between a parent and its
    -- subaccount, no empty part. None: back to the name the PMS makes.
    name text CHECK (
        name = btrim(name) AND char_length(name) BETWEEN 1 AND 200
        AND name !~ '[\t\r\n]' AND name !~ '(^:|:$|::|\s:|:\s)'
    ),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, ledger_account_id)
    REFERENCES trust_ledger_accounts (pmc_id, ledger_account_id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_quickbooks_accounts_account ON trust_quickbooks_accounts (
    pmc_id, ledger_account_id, id
);
CREATE INDEX trust_quickbooks_accounts_recorded_by ON trust_quickbooks_accounts (
    pmc_id, recorded_by
);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_quickbooks_accounts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_quickbooks_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_quickbooks_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_quickbooks_accounts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_quickbooks_accounts TO trust_app;
GRANT SELECT ON trust_quickbooks_accounts TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'quickbooks_accounts cannot be rolled back: who named which account is a '
        'record';
END;
$$;
