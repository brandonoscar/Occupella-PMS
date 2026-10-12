-- migrate:up
-- Bank statements, matching them to the books, and approving a reconciliation from them: the
-- month-end of the staff back office (founder, 2026-10-12: docs/adr/0003-staff-back-office.md,
-- bank statement import).
--
--   trust_bank_statements       a statement of a trust bank account as staff imported it (CSV
--                               or OFX): the days it covers, its opening and closing balance and
--                               its number of lines. A bank account's statements follow one
--                               another, day after day and balance after balance, from 0: the
--                               first one brings any opening balance in as a line, so every
--                               dollar the bank holds is on a line that can be matched.
--   trust_bank_statement_lines  its lines: the day the bank posted each, the amount (money in is
--                               positive), the bank's description and its reference.
--   trust_bank_matches          lines matched to the ledger transfers that moved the trust bank
--                               account's cash by the same total (a deposit of several checks,
--                               a payment and its bounce the bank never saw, ...), with
--   trust_bank_match_lines, trust_bank_match_transfers  what each match holds, and
--   trust_bank_unmatches        a match undone.
--
-- A line or a transfer is in at most one match not undone. trust_match_bank_lines and
-- trust_unmatch_bank_lines write matches, and never change a period an approved reconciliation
-- closed. trust_report_bank_reconciliation shows, at the end of a day, the bank balance, the
-- transfers the bank had not cleared, the lines the books lack and the book cash;
-- trust_approve_bank_reconciliation approves a period only when no line is missing from the
-- books. Every table is append-only and audited.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_bank_statements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    first_day date NOT NULL,
    last_day date NOT NULL,
    opening_balance numeric NOT NULL CHECK (opening_balance = round(opening_balance, 2)),
    closing_balance numeric NOT NULL CHECK (closing_balance = round(closing_balance, 2)),
    line_count bigint NOT NULL CHECK (line_count BETWEEN 0 AND 20000),
    source text NOT NULL CHECK (source IN ('csv', 'ofx')),
    -- The file as imported, by its SHA-256: a file is imported once.
    file_sha256 text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    imported_by uuid NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    UNIQUE (bank_account_id, first_day),
    UNIQUE (bank_account_id, file_sha256),
    CHECK (last_day >= first_day),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, imported_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_bank_statement_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    statement_id uuid NOT NULL,
    line_no bigint NOT NULL CHECK (line_no >= 1),
    posted_on date NOT NULL,
    amount numeric NOT NULL CHECK (
        amount <> 0 AND amount = round(amount, 2) AND abs(amount) < 1000000000
    ),
    description text NOT NULL CHECK (length(description) <= 500),
    bank_reference text CHECK (bank_reference ~ '\S' AND length(bank_reference) <= 255),
    UNIQUE (pmc_id, id),
    UNIQUE (statement_id, line_no),
    FOREIGN KEY (pmc_id, statement_id) REFERENCES trust_bank_statements (pmc_id, id)
);

CREATE TABLE trust_bank_matches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    matched_by uuid NOT NULL,
    matched_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, matched_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_bank_matches_bank_account_id ON trust_bank_matches (bank_account_id);

CREATE TABLE trust_bank_match_lines (
    match_id uuid NOT NULL,
    line_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    PRIMARY KEY (match_id, line_id),
    FOREIGN KEY (pmc_id, match_id) REFERENCES trust_bank_matches (pmc_id, id),
    FOREIGN KEY (pmc_id, line_id) REFERENCES trust_bank_statement_lines (pmc_id, id)
);
CREATE INDEX trust_bank_match_lines_line_id ON trust_bank_match_lines (line_id);

CREATE TABLE trust_bank_match_transfers (
    match_id uuid NOT NULL,
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    PRIMARY KEY (match_id, transfer_id),
    FOREIGN KEY (pmc_id, match_id) REFERENCES trust_bank_matches (pmc_id, id)
);
CREATE INDEX trust_bank_match_transfers_transfer_id ON trust_bank_match_transfers (transfer_id);

CREATE TABLE trust_bank_unmatches (
    match_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    unmatched_by uuid NOT NULL,
    unmatched_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, match_id) REFERENCES trust_bank_matches (pmc_id, id),
    FOREIGN KEY (pmc_id, unmatched_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_statements
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_statement_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_matches
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_match_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_match_transfers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_bank_unmatches
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- A statement follows the bank account's last one: from the next day, at its closing balance.
-- The first opens at 0. None starts in a period an approved reconciliation closed, or covers a
-- day still to come (UTC).
CREATE FUNCTION trust_check_bank_statement() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_last trust_bank_statements;
    v_closed timestamptz;
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
    SELECT b.closed_through INTO v_closed FROM trust_bank_accounts AS b
    WHERE b.id = NEW.bank_account_id;
    IF NEW.first_day < (v_closed AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; a statement starts '
            'no earlier', NEW.bank_account_id, v_closed
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.last_day > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: a statement covers days that have come, not %', NEW.last_day
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_bank_statement_follows
BEFORE INSERT ON trust_bank_statements
FOR EACH ROW EXECUTE FUNCTION trust_check_bank_statement();

-- A line is one of its statement's numbered lines, posted on a day the statement covers.
CREATE FUNCTION trust_check_bank_statement_line() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_statement trust_bank_statements;
BEGIN
    SELECT s.* INTO v_statement FROM trust_bank_statements AS s WHERE s.id = NEW.statement_id;
    IF NEW.line_no > v_statement.line_count THEN
        RAISE EXCEPTION 'trust: statement % has % lines, not line %', NEW.statement_id,
            v_statement.line_count, NEW.line_no
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.posted_on NOT BETWEEN v_statement.first_day AND v_statement.last_day THEN
        RAISE EXCEPTION 'trust: statement % covers % to %, not %', NEW.statement_id,
            v_statement.first_day, v_statement.last_day, NEW.posted_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_bank_statement_line
BEFORE INSERT ON trust_bank_statement_lines
FOR EACH ROW EXECUTE FUNCTION trust_check_bank_statement_line();

-- At commit, a statement has all its lines, and they take its opening balance to its closing.
-- No line joins later: its number would be past the count, or taken.
CREATE FUNCTION trust_check_bank_statement_total() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_statement trust_bank_statements;
    v_lines bigint;
    v_total numeric;
BEGIN
    SELECT s.* INTO v_statement FROM trust_bank_statements AS s WHERE s.id = NEW.id;
    SELECT count(*), coalesce(sum(l.amount), 0) INTO v_lines, v_total
    FROM trust_bank_statement_lines AS l WHERE l.statement_id = v_statement.id;
    IF v_lines <> v_statement.line_count
        OR v_statement.opening_balance + v_total <> v_statement.closing_balance THEN
        RAISE EXCEPTION 'trust: statement % has % lines coming to %; it says % lines from % to %',
            v_statement.id, v_lines, v_total, v_statement.line_count,
            v_statement.opening_balance, v_statement.closing_balance
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trust_bank_statement_total
AFTER INSERT ON trust_bank_statements
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_bank_statement_total();

-- The cash a transfer moved in (positive) or out of a trust bank account; 0 if none.
CREATE FUNCTION trust_bank_cash_moved(p_bank_account_id uuid, p_transfer_id text)
RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(-sum(e.amount), 0)
    FROM pgledger_entries AS e
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id
    WHERE e.transfer_id = p_transfer_id
      AND t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash'
$$;

-- The trust bank account of a PMC, its matches locked against each other; or an error.
CREATE FUNCTION trust_lock_bank_matches(p_pmc_id uuid, p_bank_account_id uuid)
RETURNS trust_bank_accounts
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank trust_bank_accounts;
BEGIN
    SELECT b.* INTO v_bank FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('bank matches of ' || p_bank_account_id, 0));
    RETURN v_bank;
END;
$$;

-- True when every line and transfer of a match falls before a closing date: matching or
-- undoing them would change a reconciliation already approved.
CREATE FUNCTION trust_bank_match_closed(
    p_closed_through timestamptz,
    p_line_ids uuid[],
    p_transfer_ids text[]
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
$$;

-- Matches statement lines of a trust bank account to ledger transfers that moved its cash by
-- the same total. Lines may be from several statements, and either side may be empty when the
-- other comes to 0 (a payment and its bounce the bank never saw). Returns the match.
CREATE FUNCTION trust_match_bank_lines(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_line_ids uuid[],
    p_transfer_ids text[],
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
    v_unmoved text;
    v_line_total numeric;
    v_cash_total numeric;
    v_id uuid;
BEGIN
    IF cardinality(v_lines) + cardinality(v_transfers) = 0 THEN
        RAISE EXCEPTION 'trust: a match holds at least one line or transfer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_lines) <> (SELECT count(DISTINCT x) FROM unnest(v_lines) AS x)
        OR cardinality(v_transfers) <> (SELECT count(DISTINCT x) FROM unnest(v_transfers) AS x)
    THEN
        RAISE EXCEPTION 'trust: a match names each line and transfer once'
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
    ) THEN
        RAISE EXCEPTION 'trust: a line or transfer named is matched already'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    SELECT coalesce(sum(l.amount), 0) INTO v_line_total
    FROM trust_bank_statement_lines AS l WHERE l.id = ANY (v_lines);
    SELECT coalesce(sum(trust_bank_cash_moved(p_bank_account_id, x)), 0) INTO v_cash_total
    FROM unnest(v_transfers) AS x;
    IF v_line_total <> v_cash_total THEN
        RAISE EXCEPTION 'trust: the lines come to % and the transfers to %; a match comes to the '
            'same on both sides', v_line_total, v_cash_total
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_bank_match_closed(v_bank.closed_through, v_lines, v_transfers) THEN
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
    RETURN v_id;
END;
$$;

-- Undoes a match, once, and safe to retry; never one a reconciliation already approved.
CREATE FUNCTION trust_unmatch_bank_lines(
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
              WHERE mt.match_id = p_match_id)
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; undoing this match '
            'would change a reconciliation already approved', v_bank.id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO trust_bank_unmatches (pmc_id, match_id, unmatched_by)
    VALUES (p_pmc_id, p_match_id, p_unmatched_by);
END;
$$;

-- A trust bank account at the end of a day (UTC), as a reconciliation sees it: the bank balance
-- its statements give, the transfers the bank had not cleared by then (deposits in transit and
-- outstanding withdrawals), the adjusted bank balance, the book cash, and the difference, which
-- is the lines the books lack. A match clears when everything in it happened by the end of the
-- day. A statement must cover the day.
CREATE FUNCTION trust_report_bank_reconciliation(
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
    totals AS (
        SELECT (SELECT coalesce(sum(ln.amount), 0) FROM lines AS ln) AS bank,
               (SELECT coalesce(sum(o.cash), 0) FROM outstanding AS o WHERE o.cash > 0)
                   AS in_transit,
               (SELECT coalesce(sum(o.cash), 0) FROM outstanding AS o WHERE o.cash < 0)
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

-- Approves the period of a trust bank account up to the end of a day (UTC) from its imported
-- statements: only when no line of them up to that day is missing from the books, so the
-- adjusted bank balance is the book cash. The period starts where the last one ended (or the
-- cutover, or the first statement), and the statement balance is the bank balance the
-- statements give. Returns the reconciliation.
CREATE FUNCTION trust_approve_bank_reconciliation(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_through date,
    p_prepared_by text,
    p_approved_by text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank trust_bank_accounts := trust_lock_bank_matches(p_pmc_id, p_bank_account_id);
    v_start timestamptz;
    v_balance numeric;
    v_difference numeric;
BEGIN
    -- Matches and statements of the account wait for this (trust_approve_reconciliation then
    -- holds postings off).
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || v_bank.id, 0));
    SELECT r.amount INTO v_balance
    FROM trust_report_bank_reconciliation(p_pmc_id, p_bank_account_id, p_through) AS r
    WHERE r.line = 4;
    SELECT r.amount INTO v_difference
    FROM trust_report_bank_reconciliation(p_pmc_id, p_bank_account_id, p_through) AS r
    WHERE r.line = 9;
    IF v_difference <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', p_bank_account_id, v_difference, p_through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = v_bank.id;
    RETURN trust_approve_reconciliation(
        p_pmc_id, p_bank_account_id, v_start, (p_through + 1)::timestamp AT TIME ZONE 'UTC',
        v_balance, p_prepared_by, p_approved_by
    );
END;
$$;

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_statements
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_statement_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_matches
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_match_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_match_transfers
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_bank_unmatches
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_bank_statements, trust_bank_statement_lines, trust_bank_matches,
trust_bank_match_lines, trust_bank_match_transfers, trust_bank_unmatches FROM PUBLIC;
-- Staff import statements with plain inserts; the triggers above keep them whole.
GRANT SELECT, INSERT ON trust_bank_statements, trust_bank_statement_lines TO trust_app;
-- Matches are written only by trust_match_bank_lines and trust_unmatch_bank_lines.
GRANT SELECT ON trust_bank_matches, trust_bank_match_lines, trust_bank_match_transfers,
trust_bank_unmatches TO trust_app;
GRANT SELECT ON trust_bank_statements, trust_bank_statement_lines, trust_bank_matches,
trust_bank_match_lines, trust_bank_match_transfers, trust_bank_unmatches TO trust_ai_agent;

REVOKE ALL ON FUNCTION trust_bank_cash_moved(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_lock_bank_matches(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_bank_match_closed(timestamptz, uuid[], text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_match_bank_lines(uuid, uuid, uuid[], text[], uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_match_bank_lines(uuid, uuid, uuid[], text[], uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_unmatch_bank_lines(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_unmatch_bank_lines(uuid, uuid, uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_approve_bank_reconciliation(uuid, uuid, date, text, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_approve_bank_reconciliation(uuid, uuid, date, text, text)
TO trust_app;
REVOKE ALL ON FUNCTION trust_report_bank_reconciliation(uuid, uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_bank_reconciliation(uuid, uuid, date)
TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'bank_statements cannot be rolled back: statements, matches and the '
        'reconciliations approved from them are records';
END;
$$;
