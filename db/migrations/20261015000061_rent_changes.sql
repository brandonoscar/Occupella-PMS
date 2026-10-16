-- migrate:up
-- A lease's rent changes from the first of a month, without a renewal (founder, 2026-10-15; the
-- law each piece follows is in docs/law.md). Until now a lease's rent stayed what it was opened
-- at, and a month-to-month increase took a renewal signed through the e-sign partner.
--
--   trust_rent_changes   a lease's new monthly rent from the first day of a month, with the
--                        day written notice was given and how it was served, and for a lease
--                        with an end date the lease term that lets the rent change during it.
--                        One per lease and month; append-only, audited, naming who recorded it.
--
-- The rules every state shares (docs/law.md): a periodic tenancy's rent changes only on written
-- notice of at least one full rental period, from the start of a period (West Virginia Code
-- 37-6-5 for ending one; Washington RCW 59.18.140 says it of rent); a lease for a term changes
-- rent during it only as its terms allow. So a change starts on the first of a month after the
-- lease's start and within it, notice given no later than the first of the month before; a lease
-- with an end date names the term that allows it; and no rent is charged yet for that month or
-- after (a later month instead). Longer notice, caps and limits a state or a city sets are the
-- company's to keep: the PMS records what was given.
--
-- The rent in force on a day (trust_rent_on): the latest change from that day or before, else
-- the lease's own rent. The rent run charges it for each month; a lease's end adjusts at it; the
-- rent roll, lease expirations, tenant directory and vacancy reports show it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_rent_changes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    effective_on date NOT NULL CHECK (extract(day FROM effective_on) = 1),
    monthly_rent numeric NOT NULL
        CHECK (monthly_rent > 0 AND monthly_rent = round(monthly_rent, 2) AND monthly_rent <= 1000000),
    notice_given_on date NOT NULL,
    served_by text NOT NULL CHECK (served_by IN ('in person', 'mail', 'posted', 'email', 'other')),
    lease_terms text CHECK (lease_terms ~ '\S' AND length(lease_terms) <= 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lease_id, effective_on),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    -- A full rental period's written notice: given by the first of the month before.
    CHECK (notice_given_on <= (effective_on - interval '1 month')::date)
);
CREATE INDEX trust_rent_changes_pmc_lease ON trust_rent_changes (pmc_id, lease_id);
CREATE INDEX trust_rent_changes_recorded_by ON trust_rent_changes (pmc_id, recorded_by);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_rent_changes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_rent_changes
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_recorded_by
BEFORE INSERT ON trust_rent_changes
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- A change starts after the lease does and within it, a lease with an end date names the term
-- that allows it, and no rent is charged yet from its month on. It waits for a rent run and a
-- lease's end under way, as they wait for it.
CREATE FUNCTION trust_check_rent_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NEW.effective_on <= v_lease.starts_on
        OR (v_lease.ends_on IS NOT NULL AND NEW.effective_on > v_lease.ends_on) THEN
        RAISE EXCEPTION 'trust: a rent change starts on the first of a month after the lease starts '
            '(%) and before it ends', v_lease.starts_on
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_lease.ends_on IS NOT NULL AND NEW.lease_terms IS NULL THEN
        RAISE EXCEPTION 'trust: a lease with an end date changes its rent during it only as its '
            'terms allow; name the term'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT c.id FROM trust_charges AS c
        WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.effective_on
    ) THEN
        RAISE EXCEPTION 'trust: rent from % is charged already; change it from a later month',
            to_char(NEW.effective_on, 'YYYY-MM-DD')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_rent_change
BEFORE INSERT ON trust_rent_changes
FOR EACH ROW EXECUTE FUNCTION trust_check_rent_change();

REVOKE ALL ON trust_rent_changes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_rent_changes TO trust_app;
GRANT SELECT ON trust_rent_changes TO trust_ai_agent;

-- The rent in force for a lease on a day: the latest change from that day or before, else the
-- lease's own.
CREATE FUNCTION trust_rent_on(p_lease_id uuid, p_day date)
RETURNS numeric
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT c.monthly_rent FROM trust_rent_changes AS c
        WHERE c.lease_id = p_lease_id AND c.effective_on <= p_day
        ORDER BY c.effective_on DESC
        LIMIT 1
    ), (SELECT l.monthly_rent FROM trust_leases AS l WHERE l.id = p_lease_id))
$$;

REVOKE ALL ON FUNCTION trust_rent_on(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_rent_on(uuid, date) TO trust_app;

-- The rent run, a lease's end and the reports, at the rent in force (each as it was, with that
-- one change).

CREATE OR REPLACE FUNCTION public.trust_charge_rent_due(p_pmc_id uuid, p_due_on date)
 RETURNS integer
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_charged integer;
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every lease in effect on the due date: its rent for the days of the month it is in effect,
    -- at the rent in force that month (migration 0061).
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', r.amount,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, trust_rent_on(l.id, p_due_on), p_due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND r.amount > 0  -- a day of a few cents' rent rounds to nothing
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every lease that starts later this month, with no rent charged in the month yet: its first
    -- month's rent, due on its first day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, l.starts_on, 'rent', r.amount,
           'Rent due ' || to_char(l.starts_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, l.monthly_rent, l.starts_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on > p_due_on AND r.amount > 0
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_end_lease(p_pmc_id uuid, p_lease_id uuid, p_ends_on date)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_old trust_leases;
    v_new trust_leases;
    v_charge record;
    v_adjustment uuid;
BEGIN
    -- Wait for a rent run of the PMC under way, so the charges it makes are seen below, and keep
    -- the next one from charging until this commits.
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || p_pmc_id, 0));
    SELECT l.* INTO v_old FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    UPDATE trust_leases SET ends_on = p_ends_on WHERE id = p_lease_id AND pmc_id = p_pmc_id
    RETURNING * INTO v_new;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Each charge the rent run makes (rent, and a recurring charge's) that, with what was added
    -- or taken off it before, stands at its month's days under the old end: added to or taken
    -- off so it stands at its month's days under the new end.
    FOR v_charge IN
        SELECT k.id, k.due_on, k.label, k.was, k.now
        FROM (
            SELECT c.id, c.due_on,
                   coalesce(rc.memo, 'Rent') AS label,
                   (CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
                   + coalesce((
                       SELECT sum(CASE WHEN a.kind = 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_lease_end_adjustments AS x
                       JOIN trust_charges AS a ON a.id = x.adjustment_charge_id
                       WHERE x.charge_id = c.id
                   ), 0) AS net,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_old.starts_on, v_old.ends_on,
                           coalesce(rc.amount, trust_rent_on(p_lease_id, c.due_on)), c.due_on
                       ) AS r
                   ), 0) AS was,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_new.starts_on, v_new.ends_on,
                           coalesce(rc.amount, trust_rent_on(p_lease_id, c.due_on)), c.due_on
                       ) AS r
                   ), 0) AS now
            FROM trust_charges AS c
            LEFT JOIN trust_recurring_charges AS rc ON rc.id = c.recurring_charge_id
            WHERE c.lease_id = p_lease_id
              AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        ) AS k
        WHERE k.net = k.was AND k.now <> k.was
        ORDER BY k.due_on, k.id
    LOOP
        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, p_lease_id, v_charge.due_on,
            CASE WHEN v_charge.now < v_charge.was THEN 'credit' ELSE 'fee' END,
            abs(v_charge.now - v_charge.was),
            v_charge.label || ' due ' || to_char(v_charge.due_on, 'YYYY-MM-DD') || CASE
                WHEN abs(v_charge.now) < abs(v_charge.was)
                    THEN ': the days after the lease ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
                WHEN p_ends_on IS NULL THEN ': the days back, the lease no longer ends'
                ELSE ': the days back, the lease now ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
            END
        )
        RETURNING id INTO v_adjustment;
        INSERT INTO trust_lease_end_adjustments (adjustment_charge_id, pmc_id, charge_id, ends_on)
        VALUES (v_adjustment, p_pmc_id, v_charge.id, p_ends_on);
    END LOOP;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_rent_roll(p_pmc_id uuid, p_as_of date)
 RETURNS TABLE(line integer, item text, property text, unit text, tenants text, detail text, monthly_rent numeric, deposits numeric, prepaid numeric, charged numeric, paid numeric, balance_due numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    -- The end of the day, UTC: the first moment that is not on the roll.
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a rent roll is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH held AS (
        -- Each tenant's deposits and prepaid rent at the end of the day.
        SELECT t.tenant_id,
               coalesce(sum(e.amount) FILTER (WHERE t.kind = 'tenant_deposit'), 0.00) AS deposits,
               coalesce(sum(e.amount) FILTER (WHERE t.kind = 'prepaid_rent'), 0.00) AS prepaid
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.pmc_id = p_pmc_id AND t.kind IN ('tenant_deposit', 'prepaid_rent')
          AND tr.event_at < v_cutoff
        GROUP BY t.tenant_id
    ),
    leases AS (
        SELECT l.id, l.unit_id, l.starts_on, l.ends_on,
               -- The rent in force on the day (migration 0061).
               coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                WHERE rc.lease_id = l.id AND rc.effective_on <= p_as_of
                                ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS monthly_rent,
               l.starts_on <= p_as_of AND p_as_of <= coalesce(l.ends_on, 'infinity'::date)
                   AS is_current,
               coalesce((
                   SELECT sum(CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END)
                   FROM trust_charges AS c
                   WHERE c.lease_id = l.id AND c.due_on <= p_as_of
               ), 0.00) AS charged,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charges AS c
                   JOIN trust_charge_payments AS cp ON cp.charge_id = c.id
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE c.lease_id = l.id AND tr.event_at < v_cutoff
               ), 0.00) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_charges AS c
                   JOIN trust_payment_reversals AS r ON r.charge_id = c.id
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE c.lease_id = l.id AND tr.event_at < v_cutoff
               ), 0.00) AS paid
        FROM trust_leases AS l
        WHERE l.pmc_id = p_pmc_id
    ),
    on_lease AS (
        SELECT lt.lease_id,
               string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C", tn.id)
                   AS names,
               sum(coalesce(h.deposits, 0.00)) AS deposits,
               sum(coalesce(h.prepaid, 0.00)) AS prepaid
        FROM trust_lease_tenants AS lt
        JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
        LEFT JOIN held AS h ON h.tenant_id = lt.tenant_id
        WHERE lt.pmc_id = p_pmc_id
        GROUP BY lt.lease_id
    ),
    current_tenants AS (
        SELECT DISTINCT lt.tenant_id
        FROM trust_lease_tenants AS lt
        JOIN leases AS l ON l.id = lt.lease_id
        WHERE l.is_current
    ),
    units AS (
        SELECT u.id, pr.display_name AS property_name, u.display_name AS unit_name,
               l.id AS lease_id
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN leases AS l ON l.unit_id = u.id AND l.is_current
        WHERE u.pmc_id = p_pmc_id
    ),
    lines AS (
        -- (section, property, unit, unit id, step) orders the roll.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::uuid AS unit_id, 1 AS step, 'PMC' AS item, NULL::text AS names,
               pmc.display_name AS detail, NULL::numeric AS rent, NULL::numeric AS deposits,
               NULL::numeric AS prepaid, NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of', NULL,
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, u.property_name, u.unit_name, u.id, 0, 'unit', o.names,
               CASE
                   WHEN l.id IS NULL THEN 'vacant'
                   WHEN l.ends_on IS NULL
                       THEN to_char(l.starts_on, 'YYYY-MM-DD') || ', month to month'
                   ELSE to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                        || to_char(l.ends_on, 'YYYY-MM-DD')
               END,
               l.monthly_rent, o.deposits, o.prepaid, l.charged, l.paid
        FROM units AS u
        LEFT JOIN leases AS l ON l.id = u.lease_id
        LEFT JOIN on_lease AS o ON o.lease_id = u.lease_id
        UNION ALL
        -- g.on_current: true for current leases, false for the rest, NULL for all of them.
        SELECT 2, NULL, NULL, NULL, g.step, g.item, NULL,
               CASE WHEN g.on_current THEN (
                   SELECT count(u.lease_id) || ' of ' || count(*) || ' units leased'
                   FROM units AS u
               ) END,
               CASE WHEN g.on_current THEN coalesce((
                   SELECT sum(l.monthly_rent) FROM leases AS l WHERE l.is_current
               ), 0.00) END,
               coalesce((
                   SELECT sum(h.deposits) FROM held AS h
                   WHERE g.on_current IS NULL OR g.on_current = EXISTS (
                       SELECT ct.tenant_id FROM current_tenants AS ct
                       WHERE ct.tenant_id = h.tenant_id
                   )
               ), 0.00),
               coalesce((
                   SELECT sum(h.prepaid) FROM held AS h
                   WHERE g.on_current IS NULL OR g.on_current = EXISTS (
                       SELECT ct.tenant_id FROM current_tenants AS ct
                       WHERE ct.tenant_id = h.tenant_id
                   )
               ), 0.00),
               coalesce((
                   SELECT sum(l.charged) FROM leases AS l
                   WHERE g.on_current IS NULL OR g.on_current = l.is_current
               ), 0.00),
               coalesce((
                   SELECT sum(l.paid) FROM leases AS l
                   WHERE g.on_current IS NULL OR g.on_current = l.is_current
               ), 0.00)
        FROM (VALUES
            (0, 'total: current leases', true),
            (1, 'not on a current lease', false),
            (2, 'total: all tenants', NULL)
        ) AS g (step, item, on_current)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.unit_id, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           l.names,
           l.detail,
           l.rent,
           l.deposits,
           l.prepaid,
           l.charged,
           l.paid,
           l.charged - l.paid
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

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
        -- Each renewal offer made by the day, and how far along it was then.
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

CREATE OR REPLACE FUNCTION public.trust_report_tenant_directory(p_pmc_id uuid, p_as_of date)
 RETURNS TABLE(line integer, item text, tenant text, property text, unit text, lease_starts date, lease_ends date, monthly_rent numeric, co_tenants text)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT t.id, t.display_name AS who, coalesce(lp.display_name, tp.display_name) AS place,
               lu.display_name AS unit_name, l.lease_id, l.starts_on, l.ends_on, l.rent,
               coalesce(l.standing, 4) AS standing
        FROM trust_tenants AS t
        JOIN trust_properties AS tp ON tp.id = t.property_id
        -- Their lease in effect on the day; or else the next one; or else the last one.
        LEFT JOIN LATERAL (
            SELECT l.id AS lease_id, l.unit_id, l.starts_on, l.ends_on,
                   coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                    WHERE rc.lease_id = l.id AND rc.effective_on <= p_as_of
                                    ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS rent,
                   CASE WHEN l.starts_on <= p_as_of
                             AND (l.ends_on IS NULL OR l.ends_on >= p_as_of) THEN 1
                        WHEN l.starts_on > p_as_of THEN 2
                        ELSE 3 END AS standing
            FROM trust_lease_tenants AS lt
            JOIN trust_leases AS l ON l.id = lt.lease_id
            WHERE lt.tenant_id = t.id
            ORDER BY standing,
                     CASE WHEN l.starts_on > p_as_of THEN l.starts_on - p_as_of
                          WHEN l.ends_on < p_as_of THEN p_as_of - l.ends_on
                          ELSE 0 END,
                     l.starts_on, l.id
            LIMIT 1
        ) AS l ON true
        LEFT JOIN trust_units AS lu ON lu.id = l.unit_id
        LEFT JOIN trust_properties AS lp ON lp.id = lu.property_id
        WHERE t.pmc_id = p_pmc_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::text AS who,
               NULL::uuid AS tenant_id, 0 AS step, 'PMC' AS what, pmc.display_name AS label,
               NULL::date AS starts_on, NULL::date AS ends_on, NULL::numeric AS rent,
               NULL::text AS others
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, x.place, x.unit_name, x.who, x.id, 0,
               (ARRAY['current lease', 'next lease', 'last lease', 'no lease'])[x.standing],
               x.who, x.starts_on, x.ends_on, x.rent,
               (SELECT string_agg(o.display_name, ', ' ORDER BY o.display_name COLLATE "C", o.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS o ON o.id = lt.tenant_id
                WHERE lt.lease_id = x.lease_id AND lt.tenant_id <> x.id)
        FROM tenants AS x
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C" NULLS LAST,
                         l.who COLLATE "C", l.tenant_id, l.step
            ))::integer,
           l.what, l.label, l.place, l.unit_name, l.starts_on, l.ends_on, l.rent, l.others
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_vacancy(p_pmc_id uuid, p_as_of date)
 RETURNS TABLE(line integer, item text, property text, unit text, vacant_since date, days_vacant integer, last_rent numeric, next_lease_starts date)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vacancy is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH units AS (
        SELECT u.id, pr.display_name AS place, u.display_name AS unit_name,
               EXISTS (
                   SELECT l.id FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.starts_on <= p_as_of
                     AND (l.ends_on IS NULL OR l.ends_on >= p_as_of)
               ) AS occupied,
               last.ends_on + 1 AS since,
               last.monthly_rent AS rent,
               (SELECT min(l.starts_on) FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on > p_as_of) AS next_start
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN LATERAL (
            SELECT l.ends_on,
                   coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                    WHERE rc.lease_id = l.id AND rc.effective_on <= l.ends_on
                                    ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS monthly_rent
            FROM trust_leases AS l
            WHERE l.unit_id = u.id AND l.ends_on < p_as_of
            ORDER BY l.ends_on DESC
            LIMIT 1
        ) AS last ON true
        WHERE u.pmc_id = p_pmc_id
    ),
    counted AS (
        SELECT count(*) AS total, count(*) FILTER (WHERE o.occupied) AS occupied FROM units AS o
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::uuid AS unit_id,
               0 AS step, 'PMC' AS what, pmc.display_name AS label, NULL::date AS since,
               NULL::integer AS days, NULL::numeric AS rent, NULL::date AS next_start
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        -- A unit never leased has no day it became vacant: unknown, not zero.
        SELECT 1, o.place, o.unit_name, o.id, 0, 'vacant', o.place, o.since,
               p_as_of - o.since + 1, o.rent, o.next_start
        FROM units AS o
        WHERE NOT o.occupied
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total',
               CASE WHEN c.total = 0 THEN 'no units'
                    ELSE c.total || ' units: ' || c.occupied || ' occupied, '
                         || (c.total - c.occupied) || ' vacant ('
                         || round(100.0 * c.occupied / c.total, 1) || '% occupied)'
               END,
               NULL, NULL, NULL, NULL
        FROM counted AS c
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C", l.unit_id, l.step
            ))::integer,
           l.what, l.label, l.unit_name, l.since, l.days, l.rent, l.next_start
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'rent changes cannot be rolled back: they are records';
END;
$$;
