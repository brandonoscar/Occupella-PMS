-- migrate:up
-- Autopays saved at another payments account (docs/edge-cases.md P-065; the founder, 2026-11-13:
-- stop them when the account differs). A tenant's bank account is saved at one Stripe account,
-- and only that account can charge it. A company that moved to another account (from the
-- server's to its own keys, to Stripe Connect, or to new keys of another account) had its
-- autopays charge a customer the new account doesn't know: each month's payment failed for three
-- days and was given up, and only two in a row stopped it.
--
--   trust_autopay_account_homes   the account an autopay's bank account was saved at, once:
--                                 noted when the partner reports it saved ('saved'), or for one
--                                 saved before this, at the first nightly look after it
--                                 ('first look'). Append-only.
--
-- Each night, before any autopay payment starts, an autopay noted at another account than the
-- company's partner's now stops ('account changed'), its tenant emailed to set it up again, and
-- the Dashboard lists it (src/pms/autopay_homes.py). The same account with new keys (rolled, or
-- linked through Stripe Connect) changes nothing.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_autopay_account_homes (
    autopay_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    partner_account text NOT NULL CHECK (length(partner_account) BETWEEN 1 AND 200),
    noted text NOT NULL CHECK (noted IN ('saved', 'first look')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id)
);

REVOKE ALL ON trust_autopay_account_homes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_autopay_account_homes TO trust_app;
GRANT SELECT ON trust_autopay_account_homes TO trust_ai_agent;

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON trust_autopay_account_homes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON trust_autopay_account_homes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- A stop by itself because the company's payments account changed. Every row already there is
-- one of the words before, so the constraint is added without reading the table, as the emails'
-- kinds were (migration 20261024000152).
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay_stops DROP CONSTRAINT trust_autopay_stops_why_check;
ALTER TABLE trust_autopay_stops ADD CONSTRAINT trust_autopay_stops_why_check CHECK (
    why IN (
        'tenant', 'staff', 'lease ended', 'tenant left', 'failed twice', 'replaced',
        'account changed'
    )
) NOT VALID;

-- migrate:down
SET LOCAL lock_timeout = '5s';

-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay_stops DROP CONSTRAINT trust_autopay_stops_why_check;
ALTER TABLE trust_autopay_stops ADD CONSTRAINT trust_autopay_stops_why_check CHECK (
    why IN ('tenant', 'staff', 'lease ended', 'tenant left', 'failed twice', 'replaced')
) NOT VALID;

-- squawk-ignore ban-drop-table
DROP TABLE trust_autopay_account_homes;
