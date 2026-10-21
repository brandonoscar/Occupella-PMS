-- migrate:up
-- Closing a year (the founder's review q116; the founder's call on 10/21: lock it all).
--
-- Month-end already locks each trust bank account through the day its reconciliation is
-- approved: no money is dated before it, whoever posts it. A year close is one step on top of
-- that, for the whole company, once every account's December is approved:
--
--   trust_close_year    closes a year that has ended, once (the same call again returns the
--                       same close), by a named staff member who holds approve. Every trust bank
--                       account the year touched must be approved through its last day, at least
--                       one must be (a year nothing was reconciled in has nothing to close), no
--                       journal entry dated in it may wait for a decision, and no later year may
--                       be closed already.
--   trust_year_closes   the closes, append-only, naming who closed each.
--
-- From then on nothing is charged or billed with a day in a closed year (or one before it):
-- tenants' charges (rent, fees, credits, a lease end's adjustments), a housing authority's charges
-- and vendors' bills are refused, so a late fix is dated in the open year, as money already is.
-- The year as it stood stays in every report. Nothing reopens a year.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_year_closes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- A calendar year, as extract(year ...) gives it.
    -- squawk-ignore prefer-bigint-over-int
    year integer NOT NULL CHECK (year BETWEEN 2000 AND 2999),
    closed_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, year),
    FOREIGN KEY (pmc_id, closed_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_year_closes_closed_by ON trust_year_closes (pmc_id, closed_by);

-- The last day closed in a PMC: the 31st of December of its latest closed year; none open.
CREATE FUNCTION trust_closed_until(p_pmc_id uuid) RETURNS date
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT make_date(max(c.year), 12, 31) FROM trust_year_closes AS c WHERE c.pmc_id = p_pmc_id
$$;

CREATE FUNCTION trust_close_year(p_pmc_id uuid, p_year integer) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_end timestamptz;
    v_done uuid;
    v_later integer;
    v_open text;
    v_waiting bigint;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who closes the year'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    PERFORM trust_require_role(p_pmc_id, v_staff, 'approve', 'close a year');
    IF p_year IS NULL OR p_year NOT BETWEEN 2000 AND 2999 THEN
        RAISE EXCEPTION 'trust: % is not a year to close', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_end := make_timestamptz(p_year + 1, 1, 1, 0, 0, 0, 'UTC');
    IF v_end > now() THEN
        RAISE EXCEPTION 'trust: % has not ended yet', p_year
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Close a year once its last day has passed (UTC).';
    END IF;

    -- Charges and bills dated before this year started wait for this (they hold the PMC FOR
    -- SHARE), and a close waits for them. The PMC is there: its staff member holds approve.
    PERFORM 1 FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id FOR NO KEY UPDATE;

    SELECT c.id INTO v_done FROM trust_year_closes AS c
    WHERE c.pmc_id = p_pmc_id AND c.year = p_year;
    IF FOUND THEN
        RETURN v_done;
    END IF;
    SELECT max(c.year) INTO v_later FROM trust_year_closes AS c
    WHERE c.pmc_id = p_pmc_id AND c.year > p_year;
    IF v_later IS NOT NULL THEN
        RAISE EXCEPTION 'trust: % is closed already, and with it every year before', v_later
            USING ERRCODE = 'check_violation';
    END IF;

    -- Every trust bank account the year touched (a posting dated in it or before, or a cutover
    -- before its end) is approved through its last day.
    SELECT string_agg(b.display_name, ', ' ORDER BY b.display_name COLLATE "C", b.id) INTO v_open
    FROM trust_bank_accounts AS b
    WHERE b.pmc_id = p_pmc_id
      AND (b.closed_through IS NULL OR b.closed_through < v_end)
      AND (b.opened_at < v_end OR EXISTS (
          SELECT 1 FROM trust_ledger_accounts AS t
          JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
          JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
          WHERE t.bank_account_id = b.id AND tr.event_at < v_end
      ));
    IF v_open IS NOT NULL THEN
        RAISE EXCEPTION 'trust: approve month-end through % first: %',
            to_char(make_date(p_year, 12, 31), 'MM/DD/YYYY'), v_open
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_bank_accounts AS b
        WHERE b.pmc_id = p_pmc_id AND b.closed_through >= v_end
    ) THEN
        RAISE EXCEPTION 'trust: no trust bank account was reconciled through %: there is no % to close',
            to_char(make_date(p_year, 12, 31), 'MM/DD/YYYY'), p_year
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT count(*) INTO v_waiting
    FROM trust_journal_entries AS j
    WHERE j.pmc_id = p_pmc_id AND j.event_on < make_date(p_year + 1, 1, 1)
      AND NOT EXISTS (SELECT 1 FROM trust_journal_entry_decisions AS d WHERE d.entry_id = j.id);
    IF v_waiting > 0 THEN
        RAISE EXCEPTION 'trust: % journal entr% dated in % or before wait% for a decision: post or decline them first',
            v_waiting, CASE WHEN v_waiting = 1 THEN 'y' ELSE 'ies' END, p_year,
            CASE WHEN v_waiting = 1 THEN 's' ELSE '' END
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_year_closes (pmc_id, year, closed_by)
    VALUES (p_pmc_id, p_year, v_staff)
    RETURNING id INTO v_done;
    RETURN v_done;
END;
$$;

-- Nothing is charged or billed with a day in a closed year. TG_ARGV[0] names the row's day,
-- TG_ARGV[1] what the row is, in words.
CREATE FUNCTION trust_refuse_closed_year() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_day date := (to_jsonb(NEW) ->> TG_ARGV[0])::date;
    v_until date;
BEGIN
    -- The year under way is never closed: only a day before it can be.
    IF v_day IS NULL OR v_day >= date_trunc('year', now() AT TIME ZONE 'UTC')::date THEN
        RETURN NEW;
    END IF;
    PERFORM 1 FROM trust_pmcs AS p WHERE p.pmc_id = NEW.pmc_id FOR SHARE;
    v_until := trust_closed_until(NEW.pmc_id);
    IF v_day <= v_until THEN
        RAISE EXCEPTION 'trust: % is closed: a % dated % can''t be added; date it % or later',
            extract(year FROM v_until), TG_ARGV[1], to_char(v_day, 'MM/DD/YYYY'),
            to_char(v_until + 1, 'MM/DD/YYYY')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_closed_year BEFORE INSERT ON trust_charges
FOR EACH ROW EXECUTE FUNCTION trust_refuse_closed_year('due_on', 'charge');
CREATE TRIGGER trust_closed_year BEFORE INSERT ON trust_assistance_charges
FOR EACH ROW EXECUTE FUNCTION trust_refuse_closed_year('due_on', 'housing authority''s charge');
CREATE TRIGGER trust_closed_year BEFORE INSERT ON trust_bills
FOR EACH ROW EXECUTE FUNCTION trust_refuse_closed_year('bill_date', 'bill');

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_year_closes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_year_closes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_year_closes FROM PUBLIC;
-- Written only by trust_close_year.
GRANT SELECT ON trust_year_closes TO trust_app;
GRANT SELECT ON trust_year_closes TO trust_ai_agent;
REVOKE ALL ON FUNCTION trust_closed_until(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_closed_until(uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_close_year(uuid, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_close_year(uuid, integer) TO trust_app;
REVOKE ALL ON FUNCTION trust_refuse_closed_year() FROM PUBLIC;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'year_close cannot be rolled back: a closed year is a record';
END;
$$;
