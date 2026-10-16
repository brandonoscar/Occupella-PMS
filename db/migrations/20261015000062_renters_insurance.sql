-- migrate:up
-- Renters insurance on file for each lease (founder, 2026-10-15; docs/law.md). A landlord may
-- generally require a tenant to carry renters insurance through the lease; no state requires it
-- by statute, and Oregon (ORS 90.222) forbids requiring a particular insurer or naming the
-- landlord an additional insured. So the PMS records what the lease asks and the policy the
-- tenant holds, with any insurer, and never picks one or charges for one.
--
--   trust_renters_insurance_terms   whether a lease requires renters insurance, and the least
--                                   liability coverage it asks, from a day; the latest from a day
--                                   or before holds. Recorded by staff.
--   trust_renters_insurance         a tenant's policy for a lease: the insurer (free text),
--                                   policy number, the days it runs, its liability coverage, and
--                                   whether the landlord is named an interested party. A renewal
--                                   is a new policy. Recorded by staff, or by a tenant on the
--                                   lease from the portal (who is named on the row).
--
-- trust_report_renters_insurance gives each lease in effect on a day: whether it requires
-- insurance, and of the policies started by then, the one that runs out last, standing current,
-- expires today, expires in N days (30 or fewer), expired, or none on file, and whether its
-- coverage is below what the lease asks. All append-only and audited; nothing moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_renters_insurance_terms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    required boolean NOT NULL,
    minimum_liability numeric
        CHECK (minimum_liability > 0 AND minimum_liability = round(minimum_liability, 2)),
    starts_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    CHECK (required OR minimum_liability IS NULL)
);
CREATE INDEX trust_renters_insurance_terms_lease
ON trust_renters_insurance_terms (pmc_id, lease_id, starts_on);
CREATE INDEX trust_renters_insurance_terms_recorded_by
ON trust_renters_insurance_terms (pmc_id, recorded_by);

CREATE TABLE trust_renters_insurance (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    carrier text NOT NULL CHECK (carrier ~ '\S' AND length(carrier) <= 200),
    policy_number text NOT NULL CHECK (policy_number ~ '\S' AND length(policy_number) <= 100),
    starts_on date NOT NULL,
    expires_on date NOT NULL,
    liability_limit numeric
        CHECK (liability_limit > 0 AND liability_limit = round(liability_limit, 2)),
    landlord_interested_party boolean NOT NULL DEFAULT false,
    recorded_by uuid,
    -- A tenant on the lease who recorded it from the portal.
    submitted_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (lease_id, submitted_by) REFERENCES trust_lease_tenants (lease_id, tenant_id),
    CHECK (expires_on >= starts_on),
    CHECK (recorded_by IS NULL OR submitted_by IS NULL)
);
CREATE INDEX trust_renters_insurance_lease ON trust_renters_insurance (pmc_id, lease_id);
CREATE INDEX trust_renters_insurance_recorded_by ON trust_renters_insurance (pmc_id, recorded_by);
CREATE INDEX trust_renters_insurance_submitted_by
ON trust_renters_insurance (lease_id, submitted_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_renters_insurance_terms
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_renters_insurance
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_renters_insurance_terms
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_renters_insurance
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_renters_insurance_terms
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_renters_insurance
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_renters_insurance_terms, trust_renters_insurance FROM PUBLIC;
GRANT SELECT, INSERT ON trust_renters_insurance_terms, trust_renters_insurance TO trust_app;
GRANT SELECT ON trust_renters_insurance_terms, trust_renters_insurance TO trust_ai_agent;

-- Each lease in effect on a day: whether it requires renters insurance (and the least liability
-- it asks), and the policy that counts: of those started by then, the one that runs out last
-- (then the latest recorded). Its standing: current, expires today, expires in N days (30 or
-- fewer), expired, or none on file; below the minimum when its liability coverage is less than
-- the lease asks, or unknown.
CREATE FUNCTION trust_report_renters_insurance(p_pmc_id uuid, p_on date)
RETURNS TABLE (
    line integer,
    lease_id uuid,
    property text,
    unit text,
    tenants text,
    required boolean,
    minimum_liability numeric,
    carrier text,
    policy_number text,
    starts_on date,
    expires_on date,
    liability_limit numeric,
    standing text,
    below_minimum boolean
)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    WITH leases AS (
        SELECT l.id, pr.display_name AS property, u.display_name AS unit,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS tenants,
               terms.required, terms.minimum_liability,
               p.carrier, p.policy_number, p.starts_on, p.expires_on, p.liability_limit
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN LATERAL (
            SELECT r.required, r.minimum_liability FROM trust_renters_insurance_terms AS r
            WHERE r.lease_id = l.id AND r.starts_on <= p_on
            ORDER BY r.starts_on DESC, r.created_at DESC, r.id DESC
            LIMIT 1
        ) AS terms ON true
        LEFT JOIN LATERAL (
            SELECT i.carrier, i.policy_number, i.starts_on, i.expires_on, i.liability_limit
            FROM trust_renters_insurance AS i
            WHERE i.lease_id = l.id AND i.starts_on <= p_on
            ORDER BY i.expires_on DESC, i.created_at DESC, i.id DESC
            LIMIT 1
        ) AS p ON true
        WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_on
          AND (l.ends_on IS NULL OR p_on <= l.ends_on)
    )
    SELECT (row_number() OVER (
                ORDER BY x.property COLLATE "C", x.unit COLLATE "C", x.id
            ))::integer,
           x.id, x.property, x.unit, x.tenants, coalesce(x.required, false),
           x.minimum_liability, x.carrier, x.policy_number, x.starts_on, x.expires_on,
           x.liability_limit,
           CASE
               WHEN x.expires_on IS NULL THEN 'none on file'
               WHEN x.expires_on < p_on THEN 'expired'
               WHEN x.expires_on = p_on THEN 'expires today'
               WHEN x.expires_on - p_on <= 30 THEN
                   'expires in ' || (x.expires_on - p_on) || ' day'
                   || CASE WHEN x.expires_on - p_on = 1 THEN '' ELSE 's' END
               ELSE 'current'
           END,
           x.minimum_liability IS NOT NULL AND x.expires_on IS NOT NULL
               AND coalesce(x.liability_limit, 0) < x.minimum_liability
    FROM leases AS x
    ORDER BY 1
$$;

REVOKE ALL ON FUNCTION trust_report_renters_insurance(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_renters_insurance(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'renters insurance cannot be rolled back: it is a record';
END;
$$;
