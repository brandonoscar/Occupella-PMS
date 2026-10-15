-- migrate:up
-- Journal entries (founder, 2026-10-15, the second gap against Buildium: every correction
-- needed a write path of its own). A staff member proposes an adjusting entry; another, who
-- holds the approve role, posts it. Posting goes through the ledger like any other, so every
-- trust rule still holds: no account but a bank's cash below zero, each trust bank account ties
-- out at commit, nothing dated in an approved period, every transfer inside one PMC.
--
--   trust_journal_entries           an entry: its PMC, the day it is dated (UTC), what it is
--                                   for, and who proposed it.
--   trust_journal_entry_lines       its lines: money from one ledger account of the PMC to
--                                   another, in cents, 1 to 20 of them.
--   trust_journal_entry_decisions   what became of it, once: posted (its transfers, by a staff
--                                   member who holds approve and did not propose it) or
--                                   declined (with why, by any staff member).
--   trust_propose_journal_entry     writes an entry and its lines, for the staff member the
--                                   request names.
--   trust_post_journal_entry        posts it, once; the same call again returns its transfers.
--   trust_decline_journal_entry     declines it, once, before it is posted.
--
-- The app writes these only through the three functions. Records: kept for good, append-only.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_journal_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    event_on date NOT NULL,
    memo text NOT NULL CHECK (memo ~ '\S' AND length(memo) <= 500),
    proposed_by uuid NOT NULL,
    proposed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, proposed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_journal_entry_lines (
    entry_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- squawk-ignore prefer-bigint-over-int
    line integer NOT NULL CHECK (line BETWEEN 1 AND 20),
    from_account text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    to_account text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    amount numeric NOT NULL
        CHECK (amount > 0 AND amount = round(amount, 2) AND amount <= 10000000),
    PRIMARY KEY (entry_id, line),
    CHECK (from_account <> to_account),
    FOREIGN KEY (pmc_id, entry_id) REFERENCES trust_journal_entries (pmc_id, id)
);

CREATE TABLE trust_journal_entry_decisions (
    entry_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    decision text NOT NULL CHECK (decision IN ('posted', 'declined')),
    decided_by uuid NOT NULL,
    transfer_ids text[] CHECK (cardinality(transfer_ids) > 0),
    reason text CHECK (reason ~ '\S' AND length(reason) <= 1000),
    decided_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((decision = 'posted') = (transfer_ids IS NOT NULL)),
    CHECK ((decision = 'declined') = (reason IS NOT NULL)),
    FOREIGN KEY (pmc_id, entry_id) REFERENCES trust_journal_entries (pmc_id, id),
    FOREIGN KEY (pmc_id, decided_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_journal_entries
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_journal_entry_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_journal_entry_decisions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_journal_entries
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_journal_entry_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_journal_entry_decisions
FOR EACH ROW EXECUTE FUNCTION trust_audit();
-- Posting moves money: it takes the approve role (staff roles, migration 0034).
CREATE TRIGGER trust_approve BEFORE INSERT ON trust_journal_entry_decisions
FOR EACH ROW WHEN (NEW.decision = 'posted')
EXECUTE FUNCTION trust_check_staff_role('approve', 'post a journal entry');

REVOKE ALL ON trust_journal_entries, trust_journal_entry_lines, trust_journal_entry_decisions
FROM PUBLIC;
GRANT SELECT ON trust_journal_entries, trust_journal_entry_lines, trust_journal_entry_decisions
TO trust_app, trust_ai_agent;

CREATE FUNCTION trust_propose_journal_entry(
    p_pmc_id uuid, p_event_on date, p_memo text, p_lines transfer_request[]
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_entry uuid;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who proposes the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_event_on IS NULL THEN
        RAISE EXCEPTION 'trust: a journal entry is dated a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(cardinality(p_lines), 0) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'trust: a journal entry has 1 to 20 lines, not %',
            coalesce(cardinality(p_lines), 0)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(p_lines) AS l
        WHERE NOT EXISTS (
                  SELECT 1 FROM trust_ledger_accounts AS a
                  WHERE a.ledger_account_id = l.from_account_id AND a.pmc_id = p_pmc_id)
           OR NOT EXISTS (
                  SELECT 1 FROM trust_ledger_accounts AS a
                  WHERE a.ledger_account_id = l.to_account_id AND a.pmc_id = p_pmc_id)
    ) THEN
        RAISE EXCEPTION 'trust: a journal entry moves money between ledger accounts of its own '
            'PMC only'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_journal_entries (pmc_id, event_on, memo, proposed_by)
    VALUES (p_pmc_id, p_event_on, p_memo, v_staff)
    RETURNING id INTO v_entry;
    INSERT INTO trust_journal_entry_lines (
        entry_id, pmc_id, line, from_account, to_account, amount
    )
    SELECT v_entry, p_pmc_id, l.n, l.from_account_id, l.to_account_id, l.amount
    FROM unnest(p_lines) WITH ORDINALITY AS l (from_account_id, to_account_id, amount, n);
    RETURN v_entry;
END;
$$;

-- The entry, locked: posts and declines of one entry wait for each other.
CREATE FUNCTION trust_lock_journal_entry(p_pmc_id uuid, p_entry_id uuid)
RETURNS trust_journal_entries
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_entry trust_journal_entries;
BEGIN
    SELECT e.* INTO v_entry FROM trust_journal_entries AS e
    WHERE e.id = p_entry_id AND e.pmc_id = p_pmc_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no journal entry % in PMC %', p_entry_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_entry;
END;
$$;

CREATE FUNCTION trust_post_journal_entry(p_pmc_id uuid, p_entry_id uuid) RETURNS text[]
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_entry trust_journal_entries;
    v_decided trust_journal_entry_decisions;
    v_transfers text[];
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who posts the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_entry := trust_lock_journal_entry(p_pmc_id, p_entry_id);
    SELECT d.* INTO v_decided FROM trust_journal_entry_decisions AS d
    WHERE d.entry_id = p_entry_id;
    IF FOUND THEN
        IF v_decided.decision = 'declined' THEN
            RAISE EXCEPTION 'trust: journal entry % was declined; it is never posted', p_entry_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN v_decided.transfer_ids;  -- a retry: what it posted
    END IF;
    IF v_staff = v_entry.proposed_by THEN
        RAISE EXCEPTION 'trust: the staff member who proposed a journal entry does not post it'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Another staff member who holds the approve role posts it.';
    END IF;

    SELECT array_agg(t.id ORDER BY t.ordinality) INTO v_transfers
    FROM pgledger_create_transfers(
        ARRAY(
            SELECT (l.from_account, l.to_account, l.amount)::transfer_request
            FROM trust_journal_entry_lines AS l
            WHERE l.entry_id = p_entry_id
            ORDER BY l.line
        ),
        v_entry.event_on::timestamp AT TIME ZONE 'UTC',
        jsonb_build_object(
            'memo', 'Journal entry: ' || v_entry.memo, 'journal_entry_id', p_entry_id
        )
    ) WITH ORDINALITY AS t;

    INSERT INTO trust_journal_entry_decisions (
        entry_id, pmc_id, decision, decided_by, transfer_ids
    ) VALUES (p_entry_id, p_pmc_id, 'posted', v_staff, v_transfers);
    RETURN v_transfers;
END;
$$;

CREATE FUNCTION trust_decline_journal_entry(p_pmc_id uuid, p_entry_id uuid, p_reason text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who declines the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM trust_lock_journal_entry(p_pmc_id, p_entry_id);
    IF EXISTS (
        SELECT 1 FROM trust_journal_entry_decisions AS d WHERE d.entry_id = p_entry_id
    ) THEN
        RAISE EXCEPTION 'trust: journal entry % was decided already', p_entry_id
            USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO trust_journal_entry_decisions (entry_id, pmc_id, decision, decided_by, reason)
    VALUES (p_entry_id, p_pmc_id, 'declined', v_staff, p_reason);
END;
$$;

REVOKE ALL ON FUNCTION trust_lock_journal_entry(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[])
FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_post_journal_entry(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_decline_journal_entry(uuid, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[])
TO trust_app;
GRANT EXECUTE ON FUNCTION trust_post_journal_entry(uuid, uuid) TO trust_app;
GRANT EXECUTE ON FUNCTION trust_decline_journal_entry(uuid, uuid, text) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'journal entries cannot be rolled back: they are ledger records';
END;
$$;
