-- migrate:up
-- A journal entry carries the request id of the page that proposed it, and is dated no later than
-- the company's today (docs/edge-cases.md C-065, M-135).
--
-- Proposed again after its answer was lost, an entry waited twice, and an approver might post
-- both, moving the money twice. Dated next year, an entry moved balances as soon as it was posted
-- while every report as of today left it out.
--
--   trust_journal_entries.request_id   the request that proposed it, once per PMC; NULL for one
--                                      proposed before this migration, or by a caller that names
--                                      none
--
-- trust_propose_journal_entry takes the request id (last, and optional): under the request id's
-- own lock, the same request again (the same staff member, day, memo and lines) returns the entry
-- it proposed, and the same id with another entry is refused (unique_violation). It refuses a
-- day after the company's today (trust_pmc_day). trust_post_journal_entry posts an entry dated
-- after the company's today (one proposed before this migration) only once its day has come.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_journal_entries ADD COLUMN request_id uuid;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_journal_entries_request ON trust_journal_entries (pmc_id, request_id)
WHERE request_id IS NOT NULL;

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[]);

CREATE FUNCTION trust_propose_journal_entry(
    p_pmc_id uuid,
    p_event_on date,
    p_memo text,
    p_lines transfer_request[],
    p_request_id uuid DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_entry uuid;
    v_before trust_journal_entries;
    v_today date := trust_pmc_day(p_pmc_id, clock_timestamp());
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who proposes the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_event_on IS NULL THEN
        RAISE EXCEPTION 'trust: a journal entry is dated a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The same request again returns what it proposed; another entry under its id is refused.
    IF p_request_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended('journal entry request ' || p_request_id::text, 0)
        );
        SELECT e.* INTO v_before FROM trust_journal_entries AS e
        WHERE e.pmc_id = p_pmc_id AND e.request_id = p_request_id;
        IF FOUND THEN
            IF v_before.proposed_by = v_staff
               AND v_before.event_on = p_event_on
               AND v_before.memo = p_memo
               AND ARRAY(
                       SELECT (l.from_account, l.to_account, l.amount)::transfer_request
                       FROM trust_journal_entry_lines AS l
                       WHERE l.entry_id = v_before.id
                       ORDER BY l.line
                   ) = p_lines
            THEN
                RETURN v_before.id;
            END IF;
            RAISE EXCEPTION 'trust: request id % was used for a different journal entry',
                p_request_id
                USING ERRCODE = 'unique_violation';
        END IF;
    END IF;
    IF p_event_on > v_today THEN
        RAISE EXCEPTION 'trust: a journal entry is dated no later than the company''s today, %, '
            'not %', v_today, p_event_on
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

    INSERT INTO trust_journal_entries (pmc_id, event_on, memo, proposed_by, request_id)
    VALUES (p_pmc_id, p_event_on, p_memo, v_staff, p_request_id)
    RETURNING id INTO v_entry;
    INSERT INTO trust_journal_entry_lines (
        entry_id, pmc_id, line, from_account, to_account, amount
    )
    SELECT v_entry, p_pmc_id, l.n, l.from_account_id, l.to_account_id, l.amount
    FROM unnest(p_lines) WITH ORDINALITY AS l (from_account_id, to_account_id, amount, n);
    RETURN v_entry;
END;
$$;
REVOKE ALL ON FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[], uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[], uuid)
TO trust_app;

CREATE OR REPLACE FUNCTION trust_post_journal_entry(p_pmc_id uuid, p_entry_id uuid)
RETURNS text[]
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
    IF v_entry.event_on > trust_pmc_day(p_pmc_id, clock_timestamp()) THEN
        RAISE EXCEPTION 'trust: a journal entry dated % posts on its day or later, not before',
            to_char(v_entry.event_on, 'MM/DD/YYYY')
            USING ERRCODE = 'check_violation';
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

-- migrate:down
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION trust_post_journal_entry(p_pmc_id uuid, p_entry_id uuid)
RETURNS text[]
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

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[], uuid);

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
REVOKE ALL ON FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[])
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_propose_journal_entry(uuid, date, text, transfer_request[])
TO trust_app;

-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_journal_entries_request;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_journal_entries DROP COLUMN request_id;
