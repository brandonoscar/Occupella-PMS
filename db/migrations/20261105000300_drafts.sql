-- migrate:up
-- Drafts (ADR 0011's second piece, approved by the founder 2026-10-31): the AI proposes, a person
-- approves, the PMS does it.
--
--   trust_drafts           what software with an API key proposes: its kind, the records it names
--                          by id (never a contact detail), and why. The AI's role writes it: its
--                          one write, a plain INSERT, never a write path.
--   trust_draft_decisions  a staff member's decision on a draft, once: approved (as proposed, or
--                          as they edited it) or declined, with why.
--   trust_draft_actions    what the PMS did for an approved draft: the proposal as it was done,
--                          staff's edits in it, and what that made, by id. Written with the
--                          approval, checked at commit; the AI's role never reads it, as it reads
--                          neither the emails nor the notes staff write.
--
-- An approved draft is done by the app through the write paths staff use, as the staff member
-- who approved it and with their roles, in the decision's transaction. No kind moves money: an
-- email or a notice, a work order opened, assigned or scheduled, or a note. All append-only and
-- audited.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The key the software proposed it with; its company is the draft's.
    api_key_id uuid NOT NULL,
    kind text NOT NULL CHECK (
        kind IN (
            'email', 'notice', 'work_order_open', 'work_order_assign', 'work_order_schedule',
            'note'
        )
    ),
    -- The records it names by id and the words it proposes, as the app checked them.
    proposal jsonb NOT NULL CHECK (
        jsonb_typeof(proposal) = 'object' AND proposal ->> 'kind' = kind
        AND octet_length(proposal::text) <= 65536
    ),
    -- Why the software proposes it, shown to staff beside it.
    why text CHECK (why = btrim(why) AND char_length(why) BETWEEN 1 AND 2000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, api_key_id) REFERENCES trust_api_keys (pmc_id, id)
);
CREATE INDEX trust_drafts_key ON trust_drafts (pmc_id, api_key_id, created_at);
CREATE INDEX trust_drafts_made ON trust_drafts (pmc_id, created_at);

CREATE TABLE trust_draft_decisions (
    draft_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'declined')),
    -- Approved: whether staff changed the proposal first. Declined: why, for whoever proposed it.
    edited boolean,
    reason text CHECK (reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 2000),
    -- The staff member who decided: the request names them (the command line can't decide).
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        CASE decision
            WHEN 'approved' THEN edited IS NOT NULL AND reason IS NULL
            ELSE edited IS NULL AND reason IS NOT NULL
        END
    ),
    FOREIGN KEY (pmc_id, draft_id) REFERENCES trust_drafts (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_draft_decisions_recorded_by ON trust_draft_decisions (pmc_id, recorded_by);

CREATE TABLE trust_draft_actions (
    draft_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    -- The proposal as it was done.
    acted jsonb NOT NULL CHECK (
        jsonb_typeof(acted) = 'object' AND octet_length(acted::text) <= 65536
    ),
    -- What it made, by id: the emails, the notices, the work order, the note.
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, draft_id) REFERENCES trust_drafts (pmc_id, id)
);

-- A draft is proposed with a key that stands, of a company that is active; its time is the
-- database's. It runs as the owner: the AI's role reads no key, and needs to read none.
CREATE FUNCTION trust_check_draft() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_api_key_revocations AS r WHERE r.key_id = NEW.api_key_id) THEN
        RAISE EXCEPTION 'trust: API key % was taken back; it proposes nothing', NEW.api_key_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_pmc_status(NEW.pmc_id) <> 'active' THEN
        RAISE EXCEPTION 'trust: company % is not active; nothing is proposed to it', NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_draft() FROM PUBLIC;

-- At commit, a draft's decision and what was done agree: an approved draft has what the PMS did
-- for it, and nothing was done for a draft no one approved.
CREATE FUNCTION trust_check_draft_action() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_approved boolean;
    v_acted boolean;
BEGIN
    SELECT d.decision = 'approved' INTO v_approved
    FROM trust_draft_decisions AS d WHERE d.draft_id = NEW.draft_id;
    v_acted := EXISTS (SELECT 1 FROM trust_draft_actions AS a WHERE a.draft_id = NEW.draft_id);
    IF coalesce(v_approved, false) <> v_acted THEN
        RAISE EXCEPTION 'trust: draft % % approved but % done', NEW.draft_id,
            CASE WHEN v_approved THEN 'was' ELSE 'was not' END,
            CASE WHEN v_acted THEN 'was' ELSE 'nothing was' END
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_draft_action() FROM PUBLIC;

CREATE TRIGGER trust_check_draft BEFORE INSERT ON trust_drafts
FOR EACH ROW EXECUTE FUNCTION trust_check_draft();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_drafts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_drafts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_draft_decisions
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE CONSTRAINT TRIGGER trust_draft_done
AFTER INSERT ON trust_draft_decisions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_draft_action();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_draft_decisions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_draft_decisions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE CONSTRAINT TRIGGER trust_draft_done
AFTER INSERT ON trust_draft_actions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_draft_action();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_draft_actions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_draft_actions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- The app reads drafts and decides them; the AI's role proposes them and reads what became of
-- each, never what staff made of one.
REVOKE ALL ON trust_drafts FROM PUBLIC;
GRANT SELECT ON trust_drafts TO trust_app;
GRANT SELECT, INSERT ON trust_drafts TO trust_ai_agent;
REVOKE ALL ON trust_draft_decisions FROM PUBLIC;
GRANT SELECT, INSERT ON trust_draft_decisions TO trust_app;
GRANT SELECT ON trust_draft_decisions TO trust_ai_agent;
REVOKE ALL ON trust_draft_actions FROM PUBLIC;
GRANT SELECT, INSERT ON trust_draft_actions TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_drafts) THEN
        RAISE EXCEPTION 'refusing to undo drafts: software proposed one';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-table
DROP TABLE trust_draft_actions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_draft_decisions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_drafts;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_draft_action();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_draft();
