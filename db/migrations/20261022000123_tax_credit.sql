-- migrate:up
-- Tax credit (LIHTC) compliance (the founder's review q143, approved 2026-10-22; docs/law.md):
-- a unit set aside for households under an income limit, at a rent under a limit, both from the
-- area's income limits the company enters each year (the PMS looks nothing up).
--
--   trust_tax_credit_limits  a property's area income limits at 50% of median for households of
--                            one to eight, from a day (the latest from a day on or before holds);
--                            each other percent of median is in proportion to them.
--   trust_tax_credit_units   a unit's designation from a day: the percent of median its household's
--                            income may reach (20 to 80), its bedrooms and the utility allowance;
--                            a row with no percent ends it. The latest from a day on or before holds.
--   trust_report_tax_credit_compliance
--                            each tax credit unit on a day: its household's certification, the
--                            income test (at move-in, and the 140% rule after), the most the gross
--                            rent may be and whether the rent and the utility allowance are under
--                            it; then each property's units occupied by qualified households.
--
-- A household's income is as private as an application's (migration 0108): the report runs as
-- the owner and prints words, limits and rents, never a household's income or size, so the AI may
-- run it. An income certification (migration 0108) may now be of a tax credit unit's lease too.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_tax_credit_limits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    -- The 50% of median limits for households of one to eight, as the area's table gives them.
    limits numeric [] NOT NULL CHECK (
        cardinality(limits) = 8 AND array_position(limits, NULL) IS NULL
        AND 0 < ALL (limits) AND 10000000 > ALL (limits)
    ),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (property_id, starts_on),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_tax_credit_limits_pmc ON trust_tax_credit_limits (pmc_id, property_id);
CREATE INDEX trust_tax_credit_limits_recorded_by
ON trust_tax_credit_limits (pmc_id, recorded_by);

CREATE TABLE trust_tax_credit_units (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    unit_id uuid NOT NULL,
    starts_on date NOT NULL,
    -- squawk-ignore prefer-bigint-over-int
    income_percent integer CHECK (income_percent IN (20, 30, 40, 50, 60, 70, 80)),
    -- squawk-ignore prefer-bigint-over-int
    bedrooms integer CHECK (bedrooms BETWEEN 0 AND 6),
    utility_allowance numeric
        CHECK (utility_allowance >= 0 AND utility_allowance < 100000
               AND utility_allowance = round(utility_allowance, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- A designation names its bedrooms and allowance; an end names neither.
    CHECK ((income_percent IS NULL) = (bedrooms IS NULL)
           AND (income_percent IS NULL) = (utility_allowance IS NULL)),
    UNIQUE (unit_id, starts_on),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_tax_credit_units_pmc ON trust_tax_credit_units (pmc_id, unit_id);
CREATE INDEX trust_tax_credit_units_recorded_by ON trust_tax_credit_units (pmc_id, recorded_by);

-- A unit's tax credit designation on a day: none before its first or after an end.
CREATE FUNCTION trust_tax_credit_unit_on(p_unit_id uuid, p_day date)
RETURNS TABLE (income_percent integer, bedrooms integer, utility_allowance numeric)
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT d.income_percent, d.bedrooms, d.utility_allowance
    FROM (
        SELECT t.* FROM trust_tax_credit_units AS t
        WHERE t.unit_id = p_unit_id AND t.starts_on <= p_day
        ORDER BY t.starts_on DESC
        LIMIT 1
    ) AS d
    WHERE d.income_percent IS NOT NULL
$$;

-- The income limit on a day for a household of a size at a percent of median: the property's 50%
-- limit for its size in proportion, to the dollar. A household over eight adds 8% of the
-- four-person limit for each person over eight. None without limits on file by then.
CREATE FUNCTION trust_tax_credit_income_limit(
    p_property_id uuid, p_day date, p_percent integer, p_size integer
) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT round(
        CASE WHEN p_size <= 8 THEN l.limits[p_size]
             ELSE l.limits[8] + (p_size - 8) * l.limits[4] * 0.08 END
        * p_percent / 50
    )
    FROM trust_tax_credit_limits AS l
    WHERE l.property_id = p_property_id AND l.starts_on <= p_day AND p_size >= 1
    ORDER BY l.starts_on DESC
    LIMIT 1
$$;

-- The most a tax credit unit's gross rent (rent and utility allowance) may be in a month: 30% of
-- the income limit, over twelve, for the household its bedrooms impute (one person for none, one
-- and a half for each bedroom, the mean of the two limits around a half), down to the dollar.
CREATE FUNCTION trust_tax_credit_rent_limit(
    p_property_id uuid, p_day date, p_percent integer, p_bedrooms integer
) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT floor(
        CASE WHEN p_bedrooms = 0
             THEN trust_tax_credit_income_limit(p_property_id, p_day, p_percent, 1)
             ELSE (trust_tax_credit_income_limit(
                       p_property_id, p_day, p_percent, floor(p_bedrooms * 1.5)::integer)
                   + trust_tax_credit_income_limit(
                       p_property_id, p_day, p_percent, ceil(p_bedrooms * 1.5)::integer)) / 2
        END * 0.30 / 12
    )
$$;

-- An income certification is of a lease with housing assistance on file, or now of a lease at a
-- tax credit unit on the day it takes effect; an inspection still only of an assisted lease.
CREATE OR REPLACE FUNCTION trust_check_assistance_record() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_day date := coalesce(
        (to_jsonb(NEW) ->> 'effective_on')::date, (to_jsonb(NEW) ->> 'inspected_on')::date
    );
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.pmc_id = NEW.pmc_id AND l.id = NEW.lease_id;
    IF NOT EXISTS (SELECT 1 FROM trust_rent_assistance AS a WHERE a.lease_id = NEW.lease_id)
       AND NOT (TG_TABLE_NAME = 'trust_income_certifications'
                AND EXISTS (SELECT 1 FROM trust_tax_credit_unit_on(v_lease.unit_id, v_day))) THEN
        IF TG_TABLE_NAME = 'trust_income_certifications' THEN
            RAISE EXCEPTION 'trust: this lease has no housing assistance on file, and its unit is'
                ' not a tax credit unit on %: record one first', to_char(v_day, 'MM/DD/YYYY')
                USING ERRCODE = 'check_violation';
        END IF;
        RAISE EXCEPTION 'trust: this lease has no housing assistance on file: record its terms first'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_day < v_lease.starts_on OR v_day > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: % is outside the lease, % to %', to_char(v_day, 'MM/DD/YYYY'),
            to_char(v_lease.starts_on, 'MM/DD/YYYY'),
            coalesce(to_char(v_lease.ends_on, 'MM/DD/YYYY'), 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_assistance_inspections'
       AND v_day > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: an inspection is recorded once it happened, not before %',
            to_char(v_day, 'MM/DD/YYYY')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- Each tax credit unit on a day, by property and unit, then each property's total (under its
-- income test): the units occupied by a household that qualified at move-in, of all its tax
-- credit units.
CREATE FUNCTION trust_report_tax_credit_compliance(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer,
    item text,
    unit_id uuid,
    property text,
    unit text,
    tenants text,
    income_percent integer,
    bedrooms integer,
    certification text,
    income_test text,
    max_gross_rent numeric,
    gross_rent numeric,
    rent_test text
)
LANGUAGE plpgsql STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: tax credit compliance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    WITH units AS (
        SELECT u.id AS unit_id, u.display_name AS unit, p.id AS property_id,
               p.display_name AS property, d.income_percent, d.bedrooms, d.utility_allowance,
               (SELECT l.id FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on <= p_as_of
                  AND (l.ends_on IS NULL OR l.ends_on >= p_as_of)) AS lease_id
        FROM trust_units AS u
        JOIN trust_properties AS p ON p.id = u.property_id
        CROSS JOIN LATERAL trust_tax_credit_unit_on(u.id, p_as_of) AS d
        WHERE u.pmc_id = p_pmc_id
    ),
    rows AS (
        SELECT x.*,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = x.lease_id) AS tenants,
               first.effective_on AS first_on, first.household_size AS first_size,
               first.annual_income AS first_income,
               latest.id AS latest_id, latest.effective_on AS latest_on,
               latest.household_size AS latest_size, latest.annual_income AS latest_income,
               latest.next_due_on,
               trust_tax_credit_rent_limit(x.property_id, p_as_of, x.income_percent, x.bedrooms)
                   AS max_rent,
               trust_rent_on(x.lease_id, p_as_of) + x.utility_allowance AS gross
        FROM units AS x
        LEFT JOIN LATERAL (
            SELECT c.* FROM trust_income_certifications AS c
            WHERE c.lease_id = x.lease_id AND c.effective_on <= p_as_of
            ORDER BY (c.kind = 'move-in') DESC, c.effective_on, c.created_at, c.id
            LIMIT 1
        ) AS first ON true
        LEFT JOIN LATERAL (
            SELECT c.* FROM trust_income_certifications AS c
            WHERE c.lease_id = x.lease_id AND c.effective_on <= p_as_of
            ORDER BY c.effective_on DESC, c.created_at DESC, c.id DESC
            LIMIT 1
        ) AS latest ON true
    ),
    tested AS (
        SELECT r.*,
               CASE
                   WHEN r.lease_id IS NULL THEN 'vacant'
                   WHEN r.first_on IS NULL THEN 'no certification on file'
                   WHEN trust_tax_credit_income_limit(
                            r.property_id, r.first_on, r.income_percent, r.first_size) IS NULL
                       THEN 'no income limits on file'
                   WHEN r.first_income > trust_tax_credit_income_limit(
                            r.property_id, r.first_on, r.income_percent, r.first_size)
                       THEN 'over the limit at move-in'
                   WHEN r.latest_on > r.first_on
                        AND r.latest_income > 1.4 * trust_tax_credit_income_limit(
                            r.property_id, r.latest_on, r.income_percent, r.latest_size)
                       THEN 'over 140%: next available unit rule'
                   ELSE 'qualified'
               END AS income_word
        FROM rows AS r
    ),
    lines AS (
        SELECT 0 AS part, t.property, t.property_id, t.unit, t.unit_id, 'unit' AS item, t.tenants,
               t.income_percent, t.bedrooms,
               CASE WHEN t.lease_id IS NULL THEN NULL
                    ELSE trust_due_standing(t.next_due_on, p_as_of) END AS certification,
               t.income_word,
               t.max_rent,
               t.gross,
               CASE WHEN t.lease_id IS NULL THEN NULL
                    WHEN t.max_rent IS NULL THEN 'no income limits on file'
                    WHEN t.gross <= t.max_rent THEN 'within the limit'
                    ELSE 'over by ' || to_char(t.gross - t.max_rent, 'FM999999990.00') END
                   AS rent_word
        FROM tested AS t
        UNION ALL
        SELECT 1, t.property, t.property_id, NULL, NULL, 'qualified units', NULL, NULL, NULL, NULL,
               count(*) FILTER (WHERE t.income_word IN ('qualified',
                                                        'over 140%: next available unit rule'))
               || ' of ' || count(*), NULL, NULL, NULL
        FROM tested AS t
        GROUP BY t.property, t.property_id
    )
    SELECT (row_number() OVER (
                ORDER BY l.property COLLATE "C", l.property_id, l.part, l.unit COLLATE "C",
                         l.unit_id
            ))::integer,
           l.item, l.unit_id, l.property, l.unit, l.tenants, l.income_percent, l.bedrooms,
           l.certification, l.income_word, l.max_rent, l.gross, l.rent_word
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_tax_credit_limits
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_tax_credit_units
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_tax_credit_limits
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_tax_credit_units
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_tax_credit_limits
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_tax_credit_units
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_tax_credit_limits FROM PUBLIC;
REVOKE ALL ON trust_tax_credit_units FROM PUBLIC;
GRANT SELECT, INSERT ON trust_tax_credit_limits, trust_tax_credit_units TO trust_app;
GRANT SELECT ON trust_tax_credit_limits, trust_tax_credit_units TO trust_ai_agent;

REVOKE ALL ON FUNCTION trust_tax_credit_unit_on(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_tax_credit_income_limit(uuid, date, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_tax_credit_rent_limit(uuid, date, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_tax_credit_compliance(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_tax_credit_unit_on(uuid, date),
trust_tax_credit_income_limit(uuid, date, integer, integer),
trust_tax_credit_rent_limit(uuid, date, integer, integer) TO trust_app;
GRANT EXECUTE ON FUNCTION trust_report_tax_credit_compliance(uuid, date)
TO trust_app, trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A certification of a tax credit unit's lease would lose what allowed it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_tax_credit_units) THEN
        RAISE EXCEPTION 'trust: tax credit units are on file; this migration is not rolled back'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$;
CREATE OR REPLACE FUNCTION trust_check_assistance_record() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_day date := coalesce(
        (to_jsonb(NEW) ->> 'effective_on')::date, (to_jsonb(NEW) ->> 'inspected_on')::date
    );
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.pmc_id = NEW.pmc_id AND l.id = NEW.lease_id;
    IF NOT EXISTS (SELECT 1 FROM trust_rent_assistance AS a WHERE a.lease_id = NEW.lease_id) THEN
        RAISE EXCEPTION 'trust: this lease has no housing assistance on file: record its terms first'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_day < v_lease.starts_on OR v_day > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: % is outside the lease, % to %', to_char(v_day, 'MM/DD/YYYY'),
            to_char(v_lease.starts_on, 'MM/DD/YYYY'),
            coalesce(to_char(v_lease.ends_on, 'MM/DD/YYYY'), 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_assistance_inspections'
       AND v_day > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: an inspection is recorded once it happened, not before %',
            to_char(v_day, 'MM/DD/YYYY')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_tax_credit_compliance(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_tax_credit_rent_limit(uuid, date, integer, integer);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_tax_credit_income_limit(uuid, date, integer, integer);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_tax_credit_unit_on(uuid, date);
-- squawk-ignore ban-drop-table
DROP TABLE trust_tax_credit_units;
-- squawk-ignore ban-drop-table
DROP TABLE trust_tax_credit_limits;
