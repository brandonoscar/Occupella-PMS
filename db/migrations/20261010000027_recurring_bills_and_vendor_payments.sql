-- migrate:up
-- Recurring bills and vendor payments by year (founder decision, 2026-10-10: Buildium/AppFolio
-- parity, "do 1 through 3").
--
--   trust_recurring_bills          a bill that comes every month (landscaping, a service
--                                  contract): its vendor, the owner's property account it is
--                                  paid from, amount, memo, day of the month (1 to 28) and first
--                                  day. trust_recurring_bill_ends stops one from a day.
--   trust_enter_recurring_bills    enters, for every recurring bill of a PMC, each month's bill
--                                  due on or before a day: dated its day of the month, with the
--                                  vendor reference <prefix>-YYYY-MM, so each month is entered
--                                  once (safe to retry; a bill already entered under that
--                                  reference, by hand or by an earlier run, is left as it is).
--                                  The bills then follow the usual rules: the owner's approval
--                                  over the agreement's limit, set aside, paid. Runs with the
--                                  app's own grants, at any isolation level.
--   trust_report_vendor_payments   what each vendor was paid in a calendar year (UTC), through
--                                  trust_pay_bill: the payments and their total, for the year's
--                                  1099 preparation. It files nothing and applies no threshold.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_recurring_bills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    reference_prefix text NOT NULL CHECK (reference_prefix <> ''),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    memo text NOT NULL CHECK (memo <> ''),
    -- 1 to 28, so every month has the day.
    -- squawk-ignore prefer-bigint-over-int
    day_of_month integer NOT NULL CHECK (day_of_month BETWEEN 1 AND 28),
    starts_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    UNIQUE (vendor_id, reference_prefix),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id)
);

CREATE TABLE trust_recurring_bill_ends (
    recurring_bill_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ends_on date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, recurring_bill_id) REFERENCES trust_recurring_bills (pmc_id, id)
);

-- A recurring bill is paid from an owner's property account of its PMC, like any bill.
CREATE FUNCTION trust_check_recurring_bill() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: a recurring bill is paid from an owner''s property account in '
            'its own PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_recurring_bill
BEFORE INSERT ON trust_recurring_bills
FOR EACH ROW EXECUTE FUNCTION trust_check_recurring_bill();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_bills
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_recurring_bill_ends
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_recurring_bills
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_recurring_bill_ends
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_recurring_bills, trust_recurring_bill_ends FROM PUBLIC;
GRANT SELECT, INSERT ON trust_recurring_bills, trust_recurring_bill_ends TO trust_app;
GRANT SELECT ON trust_recurring_bills, trust_recurring_bill_ends TO trust_ai_agent;

-- Enter each month's bill of every recurring bill of the PMC due on or before p_through; the
-- number entered by this call.
CREATE FUNCTION trust_enter_recurring_bills(p_pmc_id uuid, p_through date) RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_entered integer;
BEGIN
    IF p_through IS NULL THEN
        RAISE EXCEPTION 'trust: enter recurring bills through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_bills (
        pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
    )
    SELECT r.pmc_id, r.vendor_id, r.ledger_account_id,
           r.reference_prefix || '-' || to_char(d.due_on, 'YYYY-MM'),
           d.due_on, d.due_on, r.amount, r.memo
    FROM trust_recurring_bills AS r
    LEFT JOIN trust_recurring_bill_ends AS e ON e.recurring_bill_id = r.id
    CROSS JOIN LATERAL generate_series(
        date_trunc('month', r.starts_on), p_through::timestamp, interval '1 month'
    ) AS m (month)
    CROSS JOIN LATERAL (SELECT (m.month::date + r.day_of_month - 1) AS due_on) AS d
    WHERE r.pmc_id = p_pmc_id
      AND d.due_on >= r.starts_on
      AND d.due_on <= p_through
      AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
    ORDER BY r.id, d.due_on
    ON CONFLICT (vendor_id, reference) DO NOTHING;
    GET DIAGNOSTICS v_entered = ROW_COUNT;
    RETURN v_entered;
END;
$$;

REVOKE ALL ON FUNCTION trust_enter_recurring_bills(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_enter_recurring_bills(uuid, date) TO trust_app;

CREATE FUNCTION trust_report_vendor_payments(
    p_pmc_id uuid,
    p_year integer
) RETURNS TABLE (
    line integer,
    item text,
    vendor text,
    payments bigint,
    paid numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: vendor payments are for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH paid AS (
        SELECT v.id AS vendor_id, v.display_name, count(*) AS payments, sum(tr.amount) AS total
        FROM trust_bill_payments AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        JOIN trust_vendors AS v ON v.id = b.vendor_id
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND p.step = 'paid'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        GROUP BY v.id, v.display_name
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS name, NULL::uuid AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::bigint AS payments, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 'year', p_year::text || ', paid January 1 to December 31 UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, p.display_name, p.vendor_id, 'vendor', p.display_name, p.payments, p.total
        FROM paid AS p
        UNION ALL
        SELECT 2, NULL, NULL, 'total', NULL, coalesce(sum(p.payments), 0)::bigint,
               coalesce(sum(p.total), 0)
        FROM paid AS p
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.id, l.item COLLATE "C"
           )::integer,
           l.item,
           -- The PMC's name and the year sit in the vendor column of the first two lines.
           l.label,
           l.payments,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_vendor_payments(uuid, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_vendor_payments(uuid, integer)
TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'recurring bills cannot be rolled back: recurring bills are records';
END;
$$;
