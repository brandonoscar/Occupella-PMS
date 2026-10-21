-- migrate:up
-- Draft leases (the founder's punch list, ADR 0006: "New draft lease drawer ... Fields: unit,
-- dates, rent, deposit, recurring charges"; "Draft lease page: ... Send / Resend / Void, and
-- Activate lease once it's signed"). A lease offer may now be kept as a draft: made without
-- sending it (it has no signature request until staff send it). It names the security deposit
-- the lease asks for, and the monthly fees and credits besides rent the lease will owe from its
-- first day (`trust_lease_offer_charges`, up to ten, written with the offer and never after it is
-- sent); when the offer becomes the lease, those become the lease's recurring charges. Staff may
-- void an offer that is not a lease yet (`trust_lease_offer_voids`, once, naming who did it and
-- why): a void offer is never sent, signed or made a lease, and a void and a lease being made
-- wait for each other. Offers are about applicants and tenants, so the AI role reads none of
-- it, but the lease expirations report, which tells it each lease's renewal status, leaves out
-- an offer voided by its day. Nothing here moves money: a deposit asked is not a deposit held.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The security deposit the lease asks for, or none. Offers made before ask none.
ALTER TABLE trust_lease_offers ADD COLUMN security_deposit numeric
CHECK (security_deposit > 0 AND security_deposit = round(security_deposit, 2));

CREATE TABLE trust_lease_offer_charges (
    offer_id uuid NOT NULL,
    position bigint NOT NULL CHECK (position BETWEEN 1 AND 10),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN ('fee', 'credit')),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    memo text NOT NULL CHECK (btrim(memo) <> '' AND char_length(memo) <= 200),
    PRIMARY KEY (offer_id, position),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id)
);

CREATE TABLE trust_lease_offer_voids (
    offer_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    reason text CHECK (btrim(reason) <> '' AND char_length(reason) <= 500),
    recorded_by uuid,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_offer_voids_recorded_by ON trust_lease_offer_voids (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_offer_voids
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_offer_charges
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_offer_voids
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_offer_charges
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_offer_voids
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- An offer's charges are written while it is a draft: never once it is sent.
CREATE FUNCTION trust_check_lease_offer_charge() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT r.offer_id FROM trust_signature_requests AS r WHERE r.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: a lease offer''s terms are set before it is sent'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_lease_offer_charge_terms
BEFORE INSERT ON trust_lease_offer_charges
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer_charge();

-- An offer is voided only before it is a lease.
CREATE FUNCTION trust_check_lease_offer_void() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer).
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(o.application_id, o.renews_lease_id), 0
    ))
    FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    IF EXISTS (
        SELECT c.offer_id FROM trust_lease_offer_completions AS c WHERE c.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this offer is a lease already; it is not voided'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_lease_offer_void_open
BEFORE INSERT ON trust_lease_offer_voids
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer_void();

-- A void offer is never sent, signed or made a lease.
CREATE FUNCTION trust_check_lease_offer_not_void() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer).
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(o.application_id, o.renews_lease_id), 0
    ))
    FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    IF EXISTS (
        SELECT v.offer_id FROM trust_lease_offer_voids AS v WHERE v.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this lease offer was voided; it takes nothing more'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_lease_offer_not_void
BEFORE INSERT ON trust_signature_requests
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer_not_void();
CREATE TRIGGER trust_lease_offer_not_void
BEFORE INSERT ON trust_signatures
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer_not_void();
CREATE TRIGGER trust_lease_offer_not_void
BEFORE INSERT ON trust_lease_offer_completions
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer_not_void();

-- The lease an offer becomes owes the offer's monthly charges from its first day, as recurring
-- charges the rent run then charges (migration 0037).
CREATE FUNCTION trust_open_lease_offer_charges() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on)
    SELECT c.pmc_id, NEW.lease_id, c.kind, c.amount, c.memo, o.starts_on
    FROM trust_lease_offer_charges AS c
    JOIN trust_lease_offers AS o ON o.id = c.offer_id
    WHERE c.offer_id = NEW.offer_id
    ORDER BY c.position;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trust_lease_offer_charges_open
AFTER INSERT ON trust_lease_offer_completions
FOR EACH ROW EXECUTE FUNCTION trust_open_lease_offer_charges();

-- Lease expirations (migration 0046): a renewal offer voided by the day is no renewal out.
CREATE OR REPLACE FUNCTION public.trust_report_lease_expirations(p_pmc_id uuid, p_as_of date)
 RETURNS TABLE(line integer, item text, property text, unit text, tenants text, ends_on date, days_left integer, bucket text, monthly_rent numeric, renewal text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: lease expirations are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH offers AS (
        -- Each renewal offer made by the day and not voided by then, and how far along it was.
        SELECT o.renews_lease_id AS lease_id,
               CASE
                   WHEN EXISTS (
                       SELECT c.offer_id FROM trust_lease_offer_completions AS c
                       WHERE c.offer_id = o.id AND c.completed_at < v_cutoff
                   ) THEN 4
                   WHEN EXISTS (
                       SELECT r.offer_id FROM trust_signature_requests AS r
                       WHERE r.offer_id = o.id AND r.sent_at < v_cutoff
                   ) AND NOT EXISTS (
                       SELECT s.position FROM trust_lease_offer_signers AS s
                       WHERE s.offer_id = o.id AND NOT EXISTS (
                           SELECT g.position FROM trust_signatures AS g
                           WHERE g.offer_id = o.id AND g.position = s.position
                             AND g.recorded_at < v_cutoff
                       )
                   ) THEN 3
                   WHEN EXISTS (
                       SELECT r.offer_id FROM trust_signature_requests AS r
                       WHERE r.offer_id = o.id AND r.sent_at < v_cutoff
                   ) THEN 2
                   ELSE 1
               END AS stage
        FROM trust_lease_offers AS o
        WHERE o.pmc_id = p_pmc_id AND o.renews_lease_id IS NOT NULL
          AND o.offered_at < v_cutoff
          -- An offer voided by then is no renewal out.
          AND NOT EXISTS (
              SELECT v.offer_id FROM trust_lease_offer_voids AS v
              WHERE v.offer_id = o.id AND v.recorded_at < v_cutoff
          )
    ),
    ending AS (
        SELECT l.id, pr.display_name AS place, u.display_name AS unit_name,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS names,
               l.ends_on AS last_day, l.ends_on - p_as_of AS left_days,
               CASE WHEN l.ends_on - p_as_of <= 30 THEN 1
                    WHEN l.ends_on - p_as_of <= 60 THEN 2
                    ELSE 3 END AS grp,
               coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                WHERE rc.lease_id = l.id AND rc.effective_on <= p_as_of
                                ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS rent,
               coalesce((SELECT max(f.stage) FROM offers AS f WHERE f.lease_id = l.id), 0) AS stage
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.pmc_id = p_pmc_id AND l.ends_on BETWEEN p_as_of AND p_as_of + 90
    ),
    spans (grp, label) AS (VALUES (1, '0-30'), (2, '31-60'), (3, '61-90')),
    lines AS (
        SELECT 0 AS section, 0 AS grp, 1 AS step, NULL::date AS last_day, NULL::text AS place,
               NULL::text AS unit_name, NULL::uuid AS id, 'PMC' AS item,
               pmc.display_name AS head, NULL::text AS names, NULL::integer AS left_days,
               NULL::numeric AS rent, NULL::text AS status
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 0, 2, NULL, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC; leases ending in the next '
               || '90 days', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, e.grp, 0, e.last_day, e.place, e.unit_name, e.id, 'lease', e.place, e.names,
               e.left_days, e.rent,
               (ARRAY['none', 'drafted', 'sent', 'signed', 'renewed'])[e.stage + 1]
        FROM ending AS e
        UNION ALL
        SELECT 1, g.grp, 1, NULL, NULL, NULL, NULL, 'group total', NULL, NULL, NULL,
               coalesce(sum(e.rent), 0.00),
               count(e.id) FILTER (WHERE e.stage < 2) || ' of ' || count(e.id)
               || ' with no renewal offer out'
        FROM spans AS g LEFT JOIN ending AS e ON e.grp = g.grp
        GROUP BY g.grp
        UNION ALL
        SELECT 2, 0, 0, NULL, NULL, NULL, NULL, 'total', NULL, NULL, NULL,
               coalesce(sum(e.rent), 0.00),
               count(e.id) FILTER (WHERE e.stage < 2) || ' of ' || count(e.id)
               || ' with no renewal offer out'
        FROM ending AS e
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.grp, l.step, l.last_day, l.place COLLATE "C",
                        l.unit_name COLLATE "C", l.id
           )::integer,
           l.item,
           -- The PMC's name and the day sit in the property column of the first two lines.
           l.head,
           l.unit_name,
           l.names,
           l.last_day,
           l.left_days,
           (SELECT g.label FROM spans AS g WHERE g.grp = l.grp),
           l.rent,
           l.status
    FROM lines AS l
    ORDER BY 1;
END;
$function$;


REVOKE ALL ON trust_lease_offer_charges, trust_lease_offer_voids FROM PUBLIC;
GRANT SELECT, INSERT ON trust_lease_offer_charges, trust_lease_offer_voids TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'lease_offer_drafts cannot be rolled back: voids and terms are records';
END;
$$;
