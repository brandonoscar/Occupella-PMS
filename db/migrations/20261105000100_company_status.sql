-- migrate:up
-- A company's status (the One Occupella plan of 11/05, Phase 1 item 3): what whoever runs the
-- server sets for a company with `python -m pms.admin status`. Active, the default; suspended,
-- while something is put right (nobody signs in, its sessions and API keys stop answering, but its
-- nightly run goes on, so rent and late fees keep posting and its books stay whole); or closed,
-- when it has left (the same, and its nightly run stops). Nothing is deleted: a closed company's
-- books stay, and a later row makes it active again. Append-only, the latest row holding, with
-- the reason; it names no staff member, since the server's operator sets it, not the company.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_company_statuses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    status text NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
    reason text NOT NULL CHECK (reason ~ '\S' AND length(reason) <= 500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX trust_company_statuses_pmc ON trust_company_statuses (pmc_id, created_at);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_company_statuses
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_company_statuses
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_company_statuses FROM PUBLIC;
GRANT SELECT, INSERT ON trust_company_statuses TO trust_app;
GRANT SELECT ON trust_company_statuses TO trust_ai_agent;

-- The company's status: its latest row's, else active.
CREATE FUNCTION trust_pmc_status(p_pmc_id uuid) RETURNS text
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT s.status FROM trust_company_statuses AS s
         WHERE s.pmc_id = p_pmc_id
         ORDER BY s.created_at DESC, s.id DESC LIMIT 1),
        'active'
    )
$$;
REVOKE ALL ON FUNCTION trust_pmc_status(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_pmc_status(uuid) TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_company_statuses) THEN
        RAISE EXCEPTION 'refusing to undo company statuses: a company has one';
    END IF;
END;
$$;
DROP FUNCTION trust_pmc_status(uuid);
-- squawk-ignore ban-drop-table
DROP TABLE trust_company_statuses;
