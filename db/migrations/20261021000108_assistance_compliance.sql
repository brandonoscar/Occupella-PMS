-- migrate:up
-- Keeping a housing-assistance lease in compliance (the founder's review q143; docs/law.md),
-- beside the rent split of migration 0095:
--
--   trust_income_certifications   each certification of the household's income and size: at
--                                 move-in, the annual one (24 CFR 982.516(a): at least once a
--                                 year) or an interim one between, the day it takes effect and
--                                 the day the next annual one is due. Append-only, naming who.
--   trust_assistance_inspections  each inspection of the home for its housing authority (HQS,
--                                 NSPIRE from 2027): passed, with the day the next is due (at
--                                 least every two years, 24 CFR 982.405(a)), or failed, with the
--                                 day it must pass a reinspection by. Append-only, naming who.
--   trust_report_assistance_compliance
--                                 each lease with assistance in force on a day: its latest
--                                 certification and inspection and where each stands (current,
--                                 due within 120 days, the first notice HUD Handbook 4350.3 asks
--                                 for, overdue, failed, none on file).
--
-- A household's income is as private as an application's: the AI role never reads the
-- certifications. The report runs as the owner and prints days and words, never income or
-- household size, so the AI may run it. The PMS files nothing with HUD (no 50059, no TRACS) and
-- checks no income or rent limit.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_income_certifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('move-in', 'annual', 'interim')),
    effective_on date NOT NULL,
    -- People in the household, thirty at most.
    -- squawk-ignore prefer-bigint-over-int
    household_size integer NOT NULL CHECK (household_size BETWEEN 1 AND 30),
    annual_income numeric NOT NULL
        CHECK (annual_income >= 0 AND annual_income = round(annual_income, 2)
               AND annual_income < 100000000),
    -- The day the next annual certification is due.
    next_due_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (next_due_on > effective_on),
    UNIQUE (lease_id, effective_on, kind),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_income_certifications_lease
ON trust_income_certifications (pmc_id, lease_id, effective_on);
CREATE INDEX trust_income_certifications_recorded_by
ON trust_income_certifications (pmc_id, recorded_by);

CREATE TABLE trust_assistance_inspections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    housing_authority_id uuid NOT NULL,
    inspected_on date NOT NULL,
    result text NOT NULL CHECK (result IN ('passed', 'failed')),
    -- Passed: the day the next is due. Failed: the day it must pass a reinspection by.
    due_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (due_on > inspected_on),
    UNIQUE (lease_id, inspected_on, result),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, housing_authority_id) REFERENCES trust_housing_authorities (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_assistance_inspections_lease
ON trust_assistance_inspections (pmc_id, lease_id, inspected_on);
CREATE INDEX trust_assistance_inspections_authority
ON trust_assistance_inspections (pmc_id, housing_authority_id);
CREATE INDEX trust_assistance_inspections_recorded_by
ON trust_assistance_inspections (pmc_id, recorded_by);

-- A certification or an inspection is of a lease that has housing assistance recorded, within
-- its days, and an inspection is not dated after the day it is recorded.
CREATE FUNCTION trust_check_assistance_record() RETURNS trigger
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

CREATE TRIGGER trust_check_assistance_record BEFORE INSERT ON trust_income_certifications
FOR EACH ROW EXECUTE FUNCTION trust_check_assistance_record();
CREATE TRIGGER trust_check_assistance_record BEFORE INSERT ON trust_assistance_inspections
FOR EACH ROW EXECUTE FUNCTION trust_check_assistance_record();

-- Where a due day stands on a day: overdue after it, due within the 120 days before it (the
-- first reminder HUD Handbook 4350.3 asks of owners), current before that.
CREATE FUNCTION trust_due_standing(p_due date, p_as_of date) RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE WHEN p_due IS NULL THEN 'none on file'
                WHEN p_as_of > p_due THEN 'overdue'
                WHEN p_due - p_as_of <= 120 THEN 'due within 120 days'
                ELSE 'current' END
$$;

-- Where an inspection stands on a day: a failed one waits for a reinspection by its due day.
CREATE FUNCTION trust_inspection_standing(p_result text, p_due date, p_as_of date) RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE WHEN p_result = 'failed' AND p_as_of > p_due THEN 'failed, reinspection overdue'
                WHEN p_result = 'failed' THEN 'failed, reinspection due'
                ELSE trust_due_standing(p_due, p_as_of) END
$$;

CREATE FUNCTION trust_report_assistance_compliance(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer,
    item text,
    property text,
    unit text,
    tenants text,
    authority text,
    certified text,
    recertify_by date,
    recertification text,
    inspected text,
    inspect_by date,
    inspection text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: housing assistance compliance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    WITH assisted AS (
        SELECT l.id AS lease_id, p.display_name AS property, u.display_name AS unit,
               ha.name AS authority,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS tenants
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS p ON p.id = u.property_id
        CROSS JOIN LATERAL trust_assistance_on(l.id, p_as_of) AS a
        JOIN trust_housing_authorities AS ha ON ha.id = a.housing_authority_id
        WHERE l.pmc_id = p_pmc_id AND a.amount > 0
          AND l.starts_on <= p_as_of AND (l.ends_on IS NULL OR p_as_of <= l.ends_on)
    ),
    standing AS (
        SELECT s.*, c.kind AS cert_kind, c.effective_on, c.next_due_on,
               i.inspected_on, i.result, i.due_on AS inspect_due
        FROM assisted AS s
        LEFT JOIN LATERAL (
            SELECT c.kind, c.effective_on, c.next_due_on
            FROM trust_income_certifications AS c
            WHERE c.lease_id = s.lease_id AND c.effective_on <= p_as_of
            ORDER BY c.effective_on DESC, c.created_at DESC, c.id DESC
            LIMIT 1
        ) AS c ON true
        LEFT JOIN LATERAL (
            SELECT i.inspected_on, i.result, i.due_on
            FROM trust_assistance_inspections AS i
            WHERE i.lease_id = s.lease_id AND i.inspected_on <= p_as_of
            ORDER BY i.inspected_on DESC, i.created_at DESC, i.id DESC
            LIMIT 1
        ) AS i ON true
    ),
    worded AS (
        SELECT s.*,
               trust_due_standing(s.next_due_on, p_as_of) AS recert_word,
               trust_inspection_standing(s.result, s.inspect_due, p_as_of) AS inspect_word
        FROM standing AS s
    ),
    ordered AS (
        SELECT row_number() OVER (
                   ORDER BY w.property COLLATE "C", w.unit COLLATE "C", w.tenants COLLATE "C",
                            w.lease_id
               ) AS n,
               w.*
        FROM worded AS w
    )
    SELECT 1, 'PMC'::text, pmc.display_name, NULL::text, NULL::text, NULL::text, NULL::text,
           NULL::date, NULL::text, NULL::text, NULL::date, NULL::text
    FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
    UNION ALL
    SELECT 2, 'as of',
           to_char(p_as_of, 'YYYY-MM-DD') || '; leases with housing assistance in force',
           NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
    UNION ALL
    SELECT (2 + o.n)::integer, 'assisted lease', o.property, o.unit, o.tenants, o.authority,
           CASE WHEN o.effective_on IS NULL THEN NULL
                ELSE o.cert_kind || ' ' || to_char(o.effective_on, 'MM/DD/YYYY') END,
           o.next_due_on, o.recert_word,
           CASE WHEN o.inspected_on IS NULL THEN NULL
                ELSE o.result || ' ' || to_char(o.inspected_on, 'MM/DD/YYYY') END,
           o.inspect_due, o.inspect_word
    FROM ordered AS o
    UNION ALL
    SELECT (3 + count(*))::integer, 'total', NULL, NULL, NULL, NULL, NULL, NULL,
           count(*) FILTER (WHERE w.recert_word IN ('overdue', 'none on file'))
               || ' to certify now, '
               || count(*) FILTER (WHERE w.recert_word = 'due within 120 days')
               || ' within 120 days',
           NULL, NULL,
           count(*) FILTER (WHERE w.inspect_word IN ('overdue', 'none on file',
                                                      'failed, reinspection overdue'))
               || ' to inspect now, '
               || count(*) FILTER (WHERE w.inspect_word IN ('due within 120 days',
                                                           'failed, reinspection due'))
               || ' within 120 days or failed'
    FROM worded AS w
    ORDER BY 1;
END;
$$;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_income_certifications
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_assistance_inspections
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_income_certifications
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_assistance_inspections
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_income_certifications
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_assistance_inspections
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_income_certifications FROM PUBLIC;
REVOKE ALL ON trust_assistance_inspections FROM PUBLIC;
GRANT SELECT, INSERT ON trust_income_certifications TO trust_app;
GRANT SELECT, INSERT ON trust_assistance_inspections TO trust_app;
GRANT SELECT ON trust_assistance_inspections TO trust_ai_agent;
REVOKE ALL ON FUNCTION trust_check_assistance_record() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_due_standing(date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_due_standing(date, date) TO trust_app;
REVOKE ALL ON FUNCTION trust_inspection_standing(text, date, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_inspection_standing(text, date, date) TO trust_app;
REVOKE ALL ON FUNCTION trust_report_assistance_compliance(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_assistance_compliance(uuid, date)
TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'assistance_compliance cannot be rolled back: certifications and inspections are records';
END;
$$;
