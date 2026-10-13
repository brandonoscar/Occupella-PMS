-- migrate:up
-- Email to an owner when a bill waits for their approval (founder decision, 2026-10-13;
-- docs/adr/0004-email.md). The PMS writes each email here in the transaction that causes it,
-- so an email exists only if its cause does, and the app hands it to the server's sender after
-- the commit (src/pms/email.py).
--
--   trust_emails             one email: to whom, its subject and text, and what it is about. A
--                            bill's approval email is written at most once.
--   trust_email_deliveries   each attempt to deliver one: by which sender, and why it failed
--                            when it did. An email is delivered at most once.
--
-- Both hold people's addresses, so neither is copied into the audit log, and the AI role reads
-- neither. Records: kept for good.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_emails (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN ('bill_approval')),
    bill_id uuid,
    to_address text NOT NULL CHECK (to_address <> '' AND length(to_address) <= 320),
    subject text NOT NULL CHECK (subject <> '' AND length(subject) <= 200),
    body text NOT NULL CHECK (body <> '' AND length(body) <= 10000),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id),
    -- A bill's approval email is about its bill.
    CHECK ((kind = 'bill_approval') = (bill_id IS NOT NULL))
);

-- Once per bill. The table starts empty, so the index builds at once.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_emails_one_per_bill_approval ON trust_emails (bill_id)
WHERE kind = 'bill_approval';

CREATE TABLE trust_email_deliveries (
    email_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    attempted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    sender text NOT NULL CHECK (sender IN ('fake', 'smtp')),
    -- NULL: delivered. Otherwise why the sender refused it.
    error text CHECK (error IS NULL OR (error <> '' AND length(error) <= 1000)),
    PRIMARY KEY (email_id, attempted_at),
    FOREIGN KEY (pmc_id, email_id) REFERENCES trust_emails (pmc_id, id)
);

-- Delivered at most once. The table starts empty, so the index builds at once.
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_email_deliveries_once ON trust_email_deliveries (email_id)
WHERE error IS NULL;

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_emails
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_email_deliveries
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_emails FROM PUBLIC;
REVOKE ALL ON trust_email_deliveries FROM PUBLIC;
GRANT SELECT, INSERT ON trust_emails TO trust_app;
GRANT SELECT, INSERT ON trust_email_deliveries TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'owner_emails cannot be rolled back: emails are records';
END;
$$;
