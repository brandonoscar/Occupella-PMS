-- migrate:up
-- A lease brought over by the switching tool waits for its cutover (docs/edge-cases.md M-152).
--
-- The rent run charges no rent or monthly charge due before the cutover of the trust bank account
-- holding the lease's property's owners' money (trust_lease_cutover, migration 20261106100000,
-- M-144). But the leases come over in one step (the roster) and the opening balances in another,
-- often days later: a nightly run in between found no cutover on file and charged the month the
-- old system had charged already, and the opening balances, posted after, credited nothing back.
--
--   trust_switched_leases   each lease the switching tool brought in, once, with the company's day
--                           it came: append-only, audited, naming who.
--   trust_lease_cutover     the latest cutover on file, as before; with none on file yet, the day
--                           a switched lease came over, so the old system keeps its month until
--                           the opening balances say where the PMS's books start.
--
-- A lease staff add by hand, and every lease of a company that never switched, is charged as
-- before. Charges already written stay as they are (append-only).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_switched_leases (
    lease_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    switched_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_switched_leases_recorded_by ON trust_switched_leases (pmc_id, recorded_by);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_switched_leases
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_switched_leases
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_switched_leases
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

REVOKE ALL ON trust_switched_leases FROM PUBLIC;
GRANT SELECT, INSERT ON trust_switched_leases TO trust_app;
GRANT SELECT ON trust_switched_leases TO trust_ai_agent;

CREATE OR REPLACE FUNCTION trust_lease_cutover(p_lease_id uuid) RETURNS date
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce(
        (SELECT max(o.opened_on)
         FROM trust_leases AS l
         JOIN trust_units AS u ON u.id = l.unit_id
         JOIN trust_ledger_accounts AS a
           ON a.property_id = u.property_id AND a.kind = 'owner_property'
         JOIN trust_opening_balances AS o ON o.bank_account_id = a.bank_account_id
         WHERE l.id = p_lease_id),
        -- Brought over by the switching tool before any opening balances: the old system's
        -- books hold its money up to the day it came (M-152).
        (SELECT s.switched_on FROM trust_switched_leases AS s WHERE s.lease_id = p_lease_id)
    );
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_lease_cutover(p_lease_id uuid) RETURNS date
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT max(o.opened_on)
    FROM trust_leases AS l
    JOIN trust_units AS u ON u.id = l.unit_id
    JOIN trust_ledger_accounts AS a
      ON a.property_id = u.property_id AND a.kind = 'owner_property'
    JOIN trust_opening_balances AS o ON o.bank_account_id = a.bank_account_id
    WHERE l.id = p_lease_id;
$$;

-- squawk-ignore ban-drop-table
DROP TABLE trust_switched_leases;
