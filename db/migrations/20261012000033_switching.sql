-- migrate:up
-- The switching tool: what a PMC moving from another system carries over besides its balances
-- (founder, 2026-10-12: docs/adr/0003-staff-back-office.md, a switching tool).
--
--   trust_cutover_items             the old system's checks and deposits the bank had not
--                                   cleared at a trust bank account's cutover: the cash each had
--                                   already moved in the old books (a check out is negative).
--                                   Entered after the opening balances, until the sign-off.
--   trust_cutover_signoffs          a staff member's sign-off that the opening balances and
--                                   those items tie out to the old system (trust_report_cutover
--                                   is what they sign). It freezes the cutover's items.
--   trust_bank_match_cutover_items  a cutover item in a bank match: 'carried' in the opening
--                                   match, which holds the first statement's balance brought
--                                   forward against the opening balances and every item, and
--                                   'cleared' once a later bank line clears it.
--
-- So the bank's balance brought forward is the old book cash less the items not yet cleared,
-- and a migrated account reconciles from its first period: trust_match_cutover_opening makes the
-- opening match once the cutover is signed off; trust_match_bank_lines (replaced) takes cutover
-- items it clears; trust_report_bank_reconciliation (replaced) counts an item carried and not
-- cleared as outstanding. A statement no longer starts before its account's cutover.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_cutover_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    issued_on date NOT NULL,
    amount numeric NOT NULL CHECK (
        amount <> 0 AND amount = round(amount, 2) AND abs(amount) < 1000000000
    ),
    description text NOT NULL CHECK (description ~ '\S' AND length(description) <= 200),
    reference text CHECK (reference ~ '\S' AND length(reference) <= 100),
    entered_by uuid NOT NULL,
    entered_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, entered_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_cutover_items_bank_account_id ON trust_cutover_items (bank_account_id);

CREATE TABLE trust_cutover_signoffs (
    bank_account_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    signed_by uuid NOT NULL,
    signed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, signed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_bank_match_cutover_items (
    match_id uuid NOT NULL,
    item_id uuid NOT NULL,
    side text NOT NULL CHECK (side IN ('carried', 'cleared')),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    PRIMARY KEY (match_id, item_id),
    FOREIGN KEY (pmc_id, match_id) REFERENCES trust_bank_matches (pmc_id, id),
    FOREIGN KEY (pmc_id, item_id) REFERENCES trust_cutover_items (pmc_id, id)
);
CREATE INDEX trust_bank_match_cutover_items_item_id ON trust_bank_match_cutover_items (item_id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_cutover_items
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_cutover_signoffs
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_match_cutover_items
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- An item, or the sign-off, comes after the account's opening balances; an item is issued no
-- later than the cutover, and none comes once the cutover is signed off. Items and the sign-off
-- of one account wait for each other.
CREATE FUNCTION trust_check_cutover_entry() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_opened date;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('cutover of ' || NEW.bank_account_id, 0));
    SELECT o.opened_on INTO v_opened FROM trust_opening_balances AS o
    WHERE o.bank_account_id = NEW.bank_account_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % has no opening balances to carry over to',
            NEW.bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT s.bank_account_id FROM trust_cutover_signoffs AS s
               WHERE s.bank_account_id = NEW.bank_account_id) THEN
        RAISE EXCEPTION 'trust: the cutover of trust bank account % is signed off already',
            NEW.bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_cutover_items' AND (to_jsonb(NEW) ->> 'issued_on')::date > v_opened
    THEN
        RAISE EXCEPTION 'trust: an item carried over was issued by the cutover, %, not %',
            v_opened, to_jsonb(NEW) ->> 'issued_on'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_cutover_entry
BEFORE INSERT ON trust_cutover_items
FOR EACH ROW EXECUTE FUNCTION trust_check_cutover_entry();
CREATE TRIGGER trust_cutover_entry
BEFORE INSERT ON trust_cutover_signoffs
FOR EACH ROW EXECUTE FUNCTION trust_check_cutover_entry();

-- A statement starts no earlier than its account's cutover or closing, and follows the last.
CREATE OR REPLACE FUNCTION trust_check_bank_statement() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_last trust_bank_statements;
    v_bank trust_bank_accounts;
BEGIN
    -- Statements of one bank account wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || NEW.bank_account_id, 0));
    SELECT s.* INTO v_last FROM trust_bank_statements AS s
    WHERE s.bank_account_id = NEW.bank_account_id
    ORDER BY s.last_day DESC LIMIT 1;
    IF FOUND THEN
        IF NEW.first_day <> v_last.last_day + 1 THEN
            RAISE EXCEPTION 'trust: the next statement of trust bank account % starts on %, '
                'the day after the last one ends', NEW.bank_account_id, v_last.last_day + 1
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.opening_balance <> v_last.closing_balance THEN
            RAISE EXCEPTION 'trust: the next statement of trust bank account % opens at %, '
                'where the last one closed, not %', NEW.bank_account_id, v_last.closing_balance,
                NEW.opening_balance
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.opening_balance <> 0 THEN
        RAISE EXCEPTION 'trust: the first statement of trust bank account % opens at 0, not %',
            NEW.bank_account_id, NEW.opening_balance
            USING ERRCODE = 'check_violation',
                  HINT = 'Bring its opening balance in as its first line.';
    END IF;
    SELECT b.* INTO v_bank FROM trust_bank_accounts AS b WHERE b.id = NEW.bank_account_id;
    IF NEW.first_day < (v_bank.closed_through AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; a statement starts '
            'no earlier', NEW.bank_account_id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.first_day < (v_bank.opened_at AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: trust bank account % opened on %; a statement starts no earlier',
            NEW.bank_account_id, (v_bank.opened_at AT TIME ZONE 'UTC')::date
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.last_day > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: a statement covers days that have come, not %', NEW.last_day
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- True when every line, transfer and cutover item of a match falls before a closing date: a
-- cutover item counts as dated at its account's cutover. Matching or undoing them would change
-- a reconciliation already approved. (It takes the cutover items too: a new signature. Only the
-- matching functions below call it.)
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_bank_match_closed(timestamptz, uuid[], text[]);
CREATE FUNCTION trust_bank_match_closed(
    p_closed_through timestamptz,
    p_line_ids uuid[],
    p_transfer_ids text[],
    p_item_ids uuid[]
) RETURNS boolean
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT p_closed_through IS NOT NULL
       AND NOT EXISTS (
           SELECT l.id FROM trust_bank_statement_lines AS l
           WHERE l.id = ANY (p_line_ids)
             AND l.posted_on >= (p_closed_through AT TIME ZONE 'UTC')::date
       )
       AND NOT EXISTS (
           SELECT tr.id FROM pgledger_transfers AS tr
           WHERE tr.id = ANY (p_transfer_ids) AND tr.event_at >= p_closed_through
       )
       AND NOT EXISTS (
           SELECT i.id FROM trust_cutover_items AS i
           JOIN trust_opening_balances AS o ON o.bank_account_id = i.bank_account_id
           WHERE i.id = ANY (p_item_ids)
             AND o.opened_on >= (p_closed_through AT TIME ZONE 'UTC')::date
       )
$$;

-- Matches statement lines of a trust bank account to ledger transfers that moved its cash and
-- to cutover items the bank has now cleared, by the same total. Lines may be from several
-- statements, and either side may be empty when the other comes to 0. A cutover item clears
-- only once the opening match carries it. Returns the match. (A new signature: the app, its only
-- caller, changes with it.)
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_match_bank_lines(uuid, uuid, uuid[], text[], uuid);
CREATE FUNCTION trust_match_bank_lines(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_line_ids uuid[],
    p_transfer_ids text[],
    p_item_ids uuid[],
    p_matched_by uuid
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank trust_bank_accounts := trust_lock_bank_matches(p_pmc_id, p_bank_account_id);
    v_lines uuid[] := coalesce(p_line_ids, '{}');
    v_transfers text[] := coalesce(p_transfer_ids, '{}');
    v_items uuid[] := coalesce(p_item_ids, '{}');
    v_unmoved text;
    v_line_total numeric;
    v_book_total numeric;
    v_id uuid;
BEGIN
    IF cardinality(v_lines) + cardinality(v_transfers) + cardinality(v_items) = 0 THEN
        RAISE EXCEPTION 'trust: a match holds at least one line, transfer or cutover item'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_lines) <> (SELECT count(DISTINCT x) FROM unnest(v_lines) AS x)
        OR cardinality(v_transfers) <> (SELECT count(DISTINCT x) FROM unnest(v_transfers) AS x)
        OR cardinality(v_items) <> (SELECT count(DISTINCT x) FROM unnest(v_items) AS x)
    THEN
        RAISE EXCEPTION 'trust: a match names each line, transfer and item once'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_lines) <> (
        SELECT count(*) FROM trust_bank_statement_lines AS l
        JOIN trust_bank_statements AS s ON s.id = l.statement_id
        WHERE l.id = ANY (v_lines) AND s.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: a line named is not on a statement of trust bank account %',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_items) <> (
        SELECT count(*) FROM trust_cutover_items AS i
        WHERE i.id = ANY (v_items) AND i.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: an item named was not carried over into trust bank account %',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT min(x) INTO v_unmoved FROM unnest(v_transfers) AS x
    WHERE trust_bank_cash_moved(p_bank_account_id, x) = 0;
    IF v_unmoved IS NOT NULL THEN
        RAISE EXCEPTION 'trust: transfer % moved no cash of trust bank account %', v_unmoved,
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT ml.line_id FROM trust_bank_match_lines AS ml
        WHERE ml.line_id = ANY (v_lines)
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = ml.match_id)
    ) OR EXISTS (
        SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
        JOIN trust_bank_matches AS m ON m.id = mt.match_id
        WHERE mt.transfer_id = ANY (v_transfers) AND m.bank_account_id = p_bank_account_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mt.match_id)
    ) OR EXISTS (
        SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
        WHERE mi.item_id = ANY (v_items) AND mi.side = 'cleared'
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mi.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: a line, transfer or item named is matched already'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    IF cardinality(v_items) <> (
        SELECT count(DISTINCT mi.item_id) FROM trust_bank_match_cutover_items AS mi
        WHERE mi.item_id = ANY (v_items) AND mi.side = 'carried'
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mi.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: an item carried over clears once the opening is matched'
            USING ERRCODE = 'check_violation',
                  HINT = 'Match the balance brought forward to the opening first.';
    END IF;
    SELECT coalesce(sum(l.amount), 0) INTO v_line_total
    FROM trust_bank_statement_lines AS l WHERE l.id = ANY (v_lines);
    SELECT coalesce(sum(trust_bank_cash_moved(p_bank_account_id, x)), 0)
           + coalesce((SELECT sum(i.amount) FROM trust_cutover_items AS i
                       WHERE i.id = ANY (v_items)), 0)
    INTO v_book_total
    FROM unnest(v_transfers) AS x;
    IF v_line_total <> v_book_total THEN
        RAISE EXCEPTION 'trust: the lines come to % and the transfers and items to %; a match '
            'comes to the same on both sides', v_line_total, v_book_total
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_bank_match_closed(v_bank.closed_through, v_lines, v_transfers, v_items) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; this match would '
            'change a reconciliation already approved', p_bank_account_id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_bank_matches (pmc_id, bank_account_id, matched_by)
    VALUES (p_pmc_id, p_bank_account_id, p_matched_by)
    RETURNING id INTO v_id;
    INSERT INTO trust_bank_match_lines (match_id, line_id, pmc_id)
    SELECT v_id, x, p_pmc_id FROM unnest(v_lines) AS x;
    INSERT INTO trust_bank_match_transfers (match_id, transfer_id, pmc_id)
    SELECT v_id, x, p_pmc_id FROM unnest(v_transfers) AS x;
    INSERT INTO trust_bank_match_cutover_items (match_id, item_id, side, pmc_id)
    SELECT v_id, x, 'cleared', p_pmc_id FROM unnest(v_items) AS x;
    RETURN v_id;
END;
$$;

-- The opening match of a migrated trust bank account, once its cutover is signed off: the
-- first statement's balance brought forward (a line) against the opening balances' transfers
-- and every item carried over, so the line is the old book cash less what the bank had not
-- cleared. Returns the match.
CREATE FUNCTION trust_match_cutover_opening(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_line_id uuid,
    p_matched_by uuid
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank trust_bank_accounts := trust_lock_bank_matches(p_pmc_id, p_bank_account_id);
    v_opening trust_opening_balances;
    v_items uuid[];
    v_line numeric;
    v_carried numeric;
    v_id uuid;
BEGIN
    SELECT o.* INTO v_opening FROM trust_opening_balances AS o
    WHERE o.bank_account_id = p_bank_account_id;
    IF NOT FOUND OR NOT EXISTS (
        SELECT s.bank_account_id FROM trust_cutover_signoffs AS s
        WHERE s.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % has no signed-off cutover to match',
            p_bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT l.amount INTO v_line FROM trust_bank_statement_lines AS l
    JOIN trust_bank_statements AS s ON s.id = l.statement_id
    WHERE l.id = p_line_id AND s.bank_account_id = p_bank_account_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: the line named is not on a statement of trust bank account %',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT coalesce(array_agg(i.id ORDER BY i.id), '{}'), coalesce(sum(i.amount), 0)
    INTO v_items, v_carried
    FROM trust_cutover_items AS i WHERE i.bank_account_id = p_bank_account_id;
    IF EXISTS (
        SELECT ml.line_id FROM trust_bank_match_lines AS ml
        WHERE ml.line_id = p_line_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = ml.match_id)
    ) OR EXISTS (
        SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
        JOIN trust_bank_matches AS m ON m.id = mt.match_id
        WHERE mt.transfer_id = ANY (v_opening.transfer_ids)
          AND m.bank_account_id = p_bank_account_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mt.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: the line or the opening is matched already'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    IF v_line <> v_opening.book_cash - v_carried THEN
        RAISE EXCEPTION 'trust: the balance brought forward is %; the opening balances (%) less '
            'the items not yet cleared (%) come to %', v_line, v_opening.book_cash, v_carried,
            v_opening.book_cash - v_carried
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_bank_match_closed(v_bank.closed_through, ARRAY[p_line_id],
                               v_opening.transfer_ids, v_items) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; this match would '
            'change a reconciliation already approved', p_bank_account_id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_bank_matches (pmc_id, bank_account_id, matched_by)
    VALUES (p_pmc_id, p_bank_account_id, p_matched_by)
    RETURNING id INTO v_id;
    INSERT INTO trust_bank_match_lines (match_id, line_id, pmc_id)
    VALUES (v_id, p_line_id, p_pmc_id);
    INSERT INTO trust_bank_match_transfers (match_id, transfer_id, pmc_id)
    SELECT v_id, x, p_pmc_id FROM unnest(v_opening.transfer_ids) AS x;
    INSERT INTO trust_bank_match_cutover_items (match_id, item_id, side, pmc_id)
    SELECT v_id, x, 'carried', p_pmc_id FROM unnest(v_items) AS x;
    RETURN v_id;
END;
$$;

-- Undoes a match, once, and safe to retry; never one a reconciliation already approved, nor an
-- opening match while a later match clears one of its items.
CREATE OR REPLACE FUNCTION trust_unmatch_bank_lines(
    p_pmc_id uuid,
    p_match_id uuid,
    p_unmatched_by uuid
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank_account uuid;
    v_bank trust_bank_accounts;
BEGIN
    SELECT m.bank_account_id INTO v_bank_account FROM trust_bank_matches AS m
    WHERE m.id = p_match_id AND m.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no bank match % in PMC %', p_match_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_bank := trust_lock_bank_matches(p_pmc_id, v_bank_account);
    IF EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u WHERE u.match_id = p_match_id)
    THEN
        RETURN;
    END IF;
    IF trust_bank_match_closed(
        v_bank.closed_through,
        ARRAY(SELECT ml.line_id FROM trust_bank_match_lines AS ml
              WHERE ml.match_id = p_match_id),
        ARRAY(SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
              WHERE mt.match_id = p_match_id),
        ARRAY(SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
              WHERE mi.match_id = p_match_id)
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; undoing this match '
            'would change a reconciliation already approved', v_bank.id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT carried.item_id FROM trust_bank_match_cutover_items AS carried
        JOIN trust_bank_match_cutover_items AS cleared
          ON cleared.item_id = carried.item_id AND cleared.side = 'cleared'
        WHERE carried.match_id = p_match_id AND carried.side = 'carried'
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = cleared.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: a later match clears an item this opening carries'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    INSERT INTO trust_bank_unmatches (pmc_id, match_id, unmatched_by)
    VALUES (p_pmc_id, p_match_id, p_unmatched_by);
END;
$$;

-- A trust bank account at the end of a day (UTC), as a reconciliation sees it: the bank balance
-- its statements give, what the bank had not cleared by then (deposits in transit and
-- outstanding withdrawals: transfers, and items carried over from before the cutover), the
-- adjusted bank balance, the book cash, and the difference, which is the lines the books lack.
-- A match clears when everything in it happened by the end of the day; a cutover item counts as
-- dated at the cutover. A statement must cover the day.
CREATE OR REPLACE FUNCTION trust_report_bank_reconciliation(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_through date
) RETURNS TABLE (line integer, item text, detail text, amount numeric)
LANGUAGE plpgsql STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_end timestamptz := (p_through + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF NOT EXISTS (
        SELECT b.id FROM trust_bank_accounts AS b
        WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT s.id FROM trust_bank_statements AS s
        WHERE s.bank_account_id = p_bank_account_id
          AND p_through BETWEEN s.first_day AND s.last_day
    ) THEN
        RAISE EXCEPTION 'trust: no statement of trust bank account % covers %',
            p_bank_account_id, p_through
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Import the statement first.';
    END IF;

    RETURN QUERY
    WITH moved AS (
        SELECT tr.id, tr.event_at, tr.metadata ->> 'memo' AS memo, -sum(e.amount) AS cash
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash'
          AND tr.event_at < v_end
        GROUP BY tr.id
        HAVING sum(e.amount) <> 0
    ),
    lines AS (
        SELECT l.id, l.posted_on, l.amount, l.description, l.bank_reference, l.line_no,
               s.first_day
        FROM trust_bank_statement_lines AS l
        JOIN trust_bank_statements AS s ON s.id = l.statement_id
        WHERE s.bank_account_id = p_bank_account_id AND l.posted_on <= p_through
    ),
    cleared AS (
        SELECT m.id FROM trust_bank_matches AS m
        WHERE m.bank_account_id = p_bank_account_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = m.id)
          AND NOT EXISTS (
              SELECT ml.line_id FROM trust_bank_match_lines AS ml
              JOIN trust_bank_statement_lines AS l ON l.id = ml.line_id
              WHERE ml.match_id = m.id AND l.posted_on > p_through
          )
          AND NOT EXISTS (
              SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
              JOIN pgledger_transfers AS tr ON tr.id = mt.transfer_id
              WHERE mt.match_id = m.id AND tr.event_at >= v_end
          )
    ),
    -- Items carried over that a cleared opening match carries and no cleared match clears.
    carried AS (
        SELECT i.id, i.issued_on, i.amount, i.description, i.reference
        FROM trust_cutover_items AS i
        WHERE i.bank_account_id = p_bank_account_id
          AND EXISTS (SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
                      JOIN cleared AS c ON c.id = mi.match_id
                      WHERE mi.item_id = i.id AND mi.side = 'carried')
          AND NOT EXISTS (SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
                          JOIN cleared AS c ON c.id = mi.match_id
                          WHERE mi.item_id = i.id AND mi.side = 'cleared')
    ),
    outstanding AS (
        SELECT mv.* FROM moved AS mv
        WHERE NOT EXISTS (
            SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
            JOIN cleared AS c ON c.id = mt.match_id
            WHERE mt.transfer_id = mv.id
        )
    ),
    missing AS (
        SELECT ln.* FROM lines AS ln
        WHERE NOT EXISTS (
            SELECT ml.line_id FROM trust_bank_match_lines AS ml
            JOIN cleared AS c ON c.id = ml.match_id
            WHERE ml.line_id = ln.id
        )
    ),
    uncleared AS (
        SELECT o.cash AS amount FROM outstanding AS o
        UNION ALL
        SELECT ci.amount FROM carried AS ci
    ),
    totals AS (
        SELECT (SELECT coalesce(sum(ln.amount), 0) FROM lines AS ln) AS bank,
               (SELECT coalesce(sum(u.amount), 0) FROM uncleared AS u WHERE u.amount > 0)
                   AS in_transit,
               (SELECT coalesce(sum(u.amount), 0) FROM uncleared AS u WHERE u.amount < 0)
                   AS outstanding,
               (SELECT coalesce(sum(mv.cash), 0) FROM moved AS mv) AS book
    ),
    heads AS (
        SELECT 1 AS n, 'PMC' AS item, p.display_name AS detail, NULL::numeric AS amount
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL
        FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id
        UNION ALL
        SELECT 3, 'at the end of', to_char(p_through, 'YYYY-MM-DD') || ' (UTC)', NULL
        UNION ALL
        SELECT 4, 'bank balance (statements)', NULL, t.bank FROM totals AS t
        UNION ALL
        SELECT 5, 'deposits in transit', NULL, t.in_transit FROM totals AS t
        UNION ALL
        SELECT 6, 'outstanding withdrawals', NULL, t.outstanding FROM totals AS t
        UNION ALL
        SELECT 7, 'adjusted bank balance', NULL, t.bank + t.in_transit + t.outstanding
        FROM totals AS t
        UNION ALL
        SELECT 8, 'trust journal (book cash)', NULL, t.book FROM totals AS t
        UNION ALL
        SELECT 9, 'difference: adjusted bank balance - trust journal', NULL,
               t.bank + t.in_transit + t.outstanding - t.book
        FROM totals AS t
    ),
    items AS (
        SELECT CASE WHEN o.cash > 0 THEN 1 ELSE 2 END AS section,
               CASE WHEN o.cash > 0 THEN 'deposit in transit' ELSE 'outstanding withdrawal' END
                   AS item,
               to_char(o.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') || ' '
                   || coalesce(o.memo, '') AS detail,
               o.cash AS amount, o.event_at::text AS happened, o.id::text AS id
        FROM outstanding AS o
        UNION ALL
        SELECT CASE WHEN ci.amount > 0 THEN 1 ELSE 2 END,
               CASE WHEN ci.amount > 0 THEN 'deposit in transit' ELSE 'outstanding withdrawal'
               END,
               to_char(ci.issued_on, 'YYYY-MM-DD') || ' ' || ci.description
                   || coalesce(' (' || ci.reference || ')', '') || ', from before the cutover',
               ci.amount, ci.issued_on::text, ci.id::text
        FROM carried AS ci
        UNION ALL
        SELECT 3, 'bank line not in the books',
               to_char(ms.posted_on, 'YYYY-MM-DD') || ' ' || ms.description
                   || coalesce(' (' || ms.bank_reference || ')', ''),
               ms.amount, ms.posted_on::text, ms.first_day::text || lpad(ms.line_no::text, 6, '0')
        FROM missing AS ms
    )
    SELECT h.n, h.item, h.detail, h.amount FROM heads AS h
    UNION ALL
    SELECT 9 + row_number() OVER (
               ORDER BY i.section, i.happened COLLATE "C", i.detail COLLATE "C", i.id COLLATE "C"
           )::integer,
           i.item, i.detail, i.amount
    FROM items AS i
    ORDER BY 1;
END;
$$;

-- A trust bank account's cutover as staff sign it: the cutover date, who entered the opening
-- balances and the old book cash; each account's opening balance and their total against it;
-- the checks and deposits the bank had not cleared, and so the bank balance to expect at the
-- cutover; and the sign-off, if given.
CREATE FUNCTION trust_report_cutover(p_pmc_id uuid, p_bank_account_id uuid)
RETURNS TABLE (line integer, item text, detail text, amount numeric)
LANGUAGE plpgsql STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_opening trust_opening_balances;
BEGIN
    SELECT o.* INTO v_opening FROM trust_opening_balances AS o
    WHERE o.bank_account_id = p_bank_account_id AND o.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % of PMC % has no opening balances',
            p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT b.key AS account, (b.value #>> '{}')::numeric AS amount,
               t.kind || ': ' || CASE t.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM jsonb_each(v_opening.balances) AS b
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.key
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
    ),
    carried AS (
        SELECT i.* FROM trust_cutover_items AS i WHERE i.bank_account_id = p_bank_account_id
    ),
    lines AS (
        SELECT 0 AS section, 1 AS n, 'PMC' AS item, p.display_name AS detail,
               NULL::numeric AS amount, NULL::text AS sort, NULL::text AS id
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL, NULL,
               NULL
        FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id
        UNION ALL
        SELECT 0, 3, 'cutover', to_char(v_opening.opened_on, 'YYYY-MM-DD')
                   || ' (opening balances at the start of the day, UTC)', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 4, 'entered by', v_opening.entered_by || ' at '
                   || to_char(v_opening.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                   || ' UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 5, 'old system''s book cash', NULL, v_opening.book_cash, NULL, NULL
        UNION ALL
        SELECT 1, 0, 'opening balance', b.holder, b.amount, b.holder, b.account
        FROM balances AS b
        UNION ALL
        SELECT 2, 0, 'opening balances, total', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b), NULL, NULL
        UNION ALL
        SELECT 2, 1, 'difference: total - book cash', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b) - v_opening.book_cash,
               NULL, NULL
        UNION ALL
        SELECT 3, 0, CASE WHEN c.amount < 0 THEN 'outstanding check' ELSE 'deposit in transit'
                     END,
               to_char(c.issued_on, 'YYYY-MM-DD') || ' ' || c.description
                   || coalesce(' (' || c.reference || ')', ''),
               c.amount, to_char(c.issued_on, 'YYYY-MM-DD') || c.description, c.id::text
        FROM carried AS c
        UNION ALL
        SELECT 4, 0, 'bank balance to expect at the cutover',
               'book cash less the items the bank had not cleared',
               v_opening.book_cash - (SELECT coalesce(sum(c.amount), 0) FROM carried AS c),
               NULL, NULL
        UNION ALL
        SELECT 4, 1, 'signed off by', coalesce(
                   (SELECT st.display_name || ' at '
                           || to_char(s.signed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                           || ' UTC'
                    FROM trust_cutover_signoffs AS s
                    JOIN trust_staff AS st ON st.id = s.signed_by
                    WHERE s.bank_account_id = p_bank_account_id),
                   'not signed off yet'), NULL, NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.n, l.sort COLLATE "C", l.id COLLATE "C"
           )::integer,
           l.item, l.detail, l.amount
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_cutover_items
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_cutover_signoffs
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_match_cutover_items
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_cutover_items, trust_cutover_signoffs, trust_bank_match_cutover_items
FROM PUBLIC;
-- Staff enter the items and sign off with plain inserts; the trigger above keeps the order.
GRANT SELECT, INSERT ON trust_cutover_items, trust_cutover_signoffs TO trust_app;
-- Written only by the matching functions.
GRANT SELECT ON trust_bank_match_cutover_items TO trust_app;
GRANT SELECT ON trust_cutover_items, trust_cutover_signoffs, trust_bank_match_cutover_items
TO trust_ai_agent;

REVOKE ALL ON FUNCTION trust_bank_match_closed(timestamptz, uuid[], text[], uuid[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_match_bank_lines(uuid, uuid, uuid[], text[], uuid[], uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_match_bank_lines(uuid, uuid, uuid[], text[], uuid[], uuid)
TO trust_app;
REVOKE ALL ON FUNCTION trust_match_cutover_opening(uuid, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_match_cutover_opening(uuid, uuid, uuid, uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_report_cutover(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_cutover(uuid, uuid) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'switching cannot be rolled back: items carried over, sign-offs and the '
        'matches made with them are records';
END;
$$;
