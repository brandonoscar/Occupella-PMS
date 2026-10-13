-- migrate:up
-- Vendor payments carried over at cutover (founder decision, 2026-10-13: "do these 3",
-- product item 2).
--
--   trust_vendor_payments_carried_over
--                                  what the old system paid a vendor in a calendar year before
--                                  the cutover (the day the PMS took over), once per vendor and
--                                  year. A cutover on January 1 carries over the whole year
--                                  before it.
--   trust_report_vendor_payments   now adds it to what the PMS paid the vendor that year: the
--                                  vendor's line is the year's whole total, and a line after it
--                                  shows what of that the old system paid. It still files
--                                  nothing and applies no threshold.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_vendor_payments_carried_over (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    vendor_id uuid NOT NULL,
    -- squawk-ignore prefer-bigint-over-int
    year integer NOT NULL CHECK (year BETWEEN 1900 AND 9998),
    -- The cutover: what the old system paid in the year before this day.
    paid_before date NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vendor_id, year),
    CHECK (paid_before > make_date(year, 1, 1) AND paid_before <= make_date(year + 1, 1, 1)),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id)
);

-- Records: kept for good. A wrong amount is a matter for the old system's records.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_vendor_payments_carried_over
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_vendor_payments_carried_over
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_vendor_payments_carried_over FROM PUBLIC;
GRANT SELECT, INSERT ON trust_vendor_payments_carried_over TO trust_app;
GRANT SELECT ON trust_vendor_payments_carried_over TO trust_ai_agent;

CREATE OR REPLACE FUNCTION trust_report_vendor_payments(
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
    WITH here AS (
        SELECT b.vendor_id, count(*) AS payments, sum(tr.amount) AS total
        FROM trust_bill_payments AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND p.step = 'paid'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        GROUP BY b.vendor_id
    ),
    before AS (
        SELECT c.vendor_id, c.paid_before, c.amount
        FROM trust_vendor_payments_carried_over AS c
        WHERE c.pmc_id = p_pmc_id AND c.year = p_year
    ),
    paid AS (
        SELECT v.id AS vendor_id, v.display_name, coalesce(h.payments, 0) AS payments,
               coalesce(h.total, 0) + coalesce(c.amount, 0) AS total,
               c.paid_before, c.amount AS carried
        FROM trust_vendors AS v
        LEFT JOIN here AS h ON h.vendor_id = v.id
        LEFT JOIN before AS c ON c.vendor_id = v.id
        WHERE v.pmc_id = p_pmc_id AND (h.vendor_id IS NOT NULL OR c.vendor_id IS NOT NULL)
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS name, NULL::uuid AS id, 0 AS part, 'PMC' AS item,
               pmc.display_name AS label, NULL::bigint AS payments, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'year', p_year::text || ', paid January 1 to December 31 UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, p.display_name, p.vendor_id, 0, 'vendor', p.display_name, p.payments, p.total
        FROM paid AS p
        UNION ALL
        -- What of the vendor's total the old system paid, under the vendor's line.
        SELECT 1, p.display_name, p.vendor_id, 1, 'carried over',
               'paid in the old system before ' || to_char(p.paid_before, 'YYYY-MM-DD'),
               NULL, p.carried
        FROM paid AS p WHERE p.carried IS NOT NULL
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'total', NULL, coalesce(sum(p.payments), 0)::bigint,
               coalesce(sum(p.total), 0)
        FROM paid AS p
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.id, l.part
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

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'vendor_payments_carried_over cannot be rolled back: they are records';
END;
$$;
