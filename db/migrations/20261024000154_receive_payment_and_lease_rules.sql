-- migrate:up
-- Receive payment and the lease money rules (ADR 0010 phase 1c, the founder's design pass parts
-- D and E).
--
--   trust_payment_notes          what staff wrote about a tenant's payment as they received it,
--                                once per payment (its first transfer). The AI never reads it.
--   trust_documents              a payment's attachment (a check's photo, a PDF): the kind
--                                'payment_attachment', about the payment's lease and its first
--                                transfer.
--   trust_lease_end_notices      each end date staff gave a lease (a move-out, a tenant's notice,
--                                an end moved earlier): the lease ends there because someone said
--                                so. Append-only, naming who.
--   trust_lease_holdovers        a fixed-term lease the nightly run rolled to month to month
--                                (trust_roll_over_leases): its term ran out with no renewal signed,
--                                no move-out recorded and no lease after it at the unit, so it
--                                goes on at the same rent. Once per lease.
--
-- A lease rolls over only when its own term ran out: an end staff gave (a notice) never rolls,
-- nor a lease entered after its end date had passed (a lease from the past), nor one its tenants
-- moved from or whose deposit was returned. A lease is looked at for a week after its term ends,
-- so nights the run missed still roll it, and never before this migration: ends staff gave before
-- it (an update of the lease's end in the audit log) are recorded as notices here.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- --- a payment's note and attachment --------------------------------------------------------

CREATE TABLE trust_payment_notes (
    transfer_id text PRIMARY KEY REFERENCES pgledger_transfers (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    note text NOT NULL CHECK (note ~ '\S' AND length(note) <= 2000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_payment_notes_recorded_by ON trust_payment_notes (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_payment_notes
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_notes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_payment_notes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_payment_notes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payment_notes TO trust_app;

ALTER TABLE trust_documents ADD COLUMN transfer_id text;
-- Replaced at once by a check that allows one more kind; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_kind_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_kind_check CHECK (
    kind IN (
        'w9', 'receipt', 'move_out_photo', 'owner_document', 'work_order_photo',
        'property_document', 'unit_document', 'tenant_document', 'lease_document',
        'vendor_document', 'company_logo', 'request_photo', 'listing_photo',
        'payment_attachment'
    )
) NOT VALID;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_transfer_check
CHECK ((kind = 'payment_attachment') = (transfer_id IS NOT NULL)) NOT VALID;
-- A payment's attachment is about its lease too, so the lease's Files tab lists it.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_documents DROP CONSTRAINT trust_documents_lease_check;
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_lease_check CHECK (
    (kind IN ('move_out_photo', 'lease_document', 'payment_attachment')) = (lease_id IS NOT NULL)
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_documents ADD CONSTRAINT trust_documents_transfer_id_fkey
FOREIGN KEY (transfer_id) REFERENCES pgledger_transfers (id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_documents_transfer_id ON trust_documents (transfer_id);

-- --- the ends staff give a lease ------------------------------------------------------------

CREATE TABLE trust_lease_end_notices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    ends_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_end_notices_lease ON trust_lease_end_notices (pmc_id, lease_id);
CREATE INDEX trust_lease_end_notices_recorded_by ON trust_lease_end_notices (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_end_notices
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_end_notices
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_end_notices
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lease_end_notices FROM PUBLIC;
GRANT SELECT, INSERT ON trust_lease_end_notices TO trust_app;
GRANT SELECT ON trust_lease_end_notices TO trust_ai_agent;

-- The ends staff gave before this migration: a lease whose end was changed after it was opened
-- (the audit log keeps the row before each update) ends where someone said it does.
INSERT INTO trust_lease_end_notices (pmc_id, lease_id, ends_on, created_at)
SELECT l.pmc_id, l.id, l.ends_on, max(a.at)
FROM trust_leases AS l
JOIN trust_audit_log AS a
    ON a.table_name = 'trust_leases' AND a.action = 'update' AND a.pmc_id = l.pmc_id
   AND (a.row_data ->> 'id')::uuid = l.id
   AND (a.before ->> 'ends_on') IS DISTINCT FROM (a.row_data ->> 'ends_on')
WHERE l.ends_on IS NOT NULL
GROUP BY l.pmc_id, l.id, l.ends_on;

-- --- leases gone month to month -------------------------------------------------------------

CREATE TABLE trust_lease_holdovers (
    lease_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The last day of the term that ran out, and the night the lease went month to month.
    term_ended_on date NOT NULL,
    rolled_on date NOT NULL CHECK (rolled_on > term_ended_on),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id)
);
CREATE INDEX trust_lease_holdovers_pmc ON trust_lease_holdovers (pmc_id, rolled_on);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_holdovers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_holdovers
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lease_holdovers FROM PUBLIC;
GRANT SELECT ON trust_lease_holdovers TO trust_app;
GRANT SELECT ON trust_lease_holdovers TO trust_ai_agent;

-- Rolls each lease of the PMC whose term ran out before the day (within the week before it) to
-- month to month at the same rent, once: its end is cleared through trust_end_lease, which
-- charges back any days of its last month credited when the term was set to end. Returns how
-- many it rolled.
CREATE FUNCTION trust_roll_over_leases(p_pmc_id uuid, p_day date) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease record;
    v_rolled integer := 0;
BEGIN
    IF p_day IS NULL THEN
        RAISE EXCEPTION 'trust: leases roll over on a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Two runs at once roll each lease once: the second waits, then finds the first's.
    PERFORM pg_advisory_xact_lock(hashtextextended('trust_lease_holdovers:' || p_pmc_id::text, 0));
    FOR v_lease IN
        SELECT l.id, l.ends_on
        FROM trust_leases AS l
        WHERE l.pmc_id = p_pmc_id
          AND l.ends_on < p_day AND l.ends_on >= p_day - 7
          -- Entered while its term still ran: a lease from the past stays as it was.
          AND l.created_at::date <= l.ends_on
          AND NOT EXISTS (SELECT 1 FROM trust_lease_holdovers AS h WHERE h.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_end_notices AS n WHERE n.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_leases AS later
                          WHERE later.unit_id = l.unit_id AND later.id <> l.id
                            AND later.starts_on > l.starts_on)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_transfers AS t WHERE t.from_lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)
        ORDER BY l.ends_on, l.id
    LOOP
        PERFORM trust_end_lease(p_pmc_id, v_lease.id, NULL);
        INSERT INTO trust_lease_holdovers (lease_id, pmc_id, term_ended_on, rolled_on)
        VALUES (v_lease.id, p_pmc_id, v_lease.ends_on, p_day);
        v_rolled := v_rolled + 1;
    END LOOP;
    RETURN v_rolled;
END;
$$;
REVOKE ALL ON FUNCTION trust_roll_over_leases(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_roll_over_leases(uuid, date) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'receive_payment_and_lease_rules cannot be rolled back: notes, notices and '
        'holdovers are records';
END;
$$;
