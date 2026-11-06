-- migrate:up
-- Deposits the owner holds (the founder's chart of accounts pass, part 2, 2026-11-06): some owners
-- keep a lease's security, pet or key deposit themselves, outside trust. The PMS records it so
-- staff, the owner and the tenant see it, on a memo line (code 2150 in the chart), never in a trust
-- balance: no money moves, and no ledger account holds it.
--
--   trust_owner_held_deposits          a deposit the owner holds: per lease, tenant and kind, its
--                                      amount and the day it was held from, maybe a memo, naming
--                                      who recorded it; once per lease, tenant and kind, for a
--                                      tenant on the lease, from a day not after today.
--   trust_owner_held_deposit_outcomes  at move-out (the lease's last day set), what the owner
--                                      returned and kept of it, together its amount, on a day
--                                      from when it was held to today; once, naming who.
--
-- The tenant ledger lists each as a memo line that changes no balance. Both append-only, audited;
-- the AI role reads them, as it reads the deposits held in trust.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_owner_held_deposits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    deposit_kind text NOT NULL CHECK (deposit_kind IN ('security', 'pet', 'key')),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    held_since date NOT NULL,
    memo text CHECK (memo = btrim(memo) AND char_length(memo) BETWEEN 1 AND 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    UNIQUE (lease_id, tenant_id, deposit_kind),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_owner_held_deposits_lease ON trust_owner_held_deposits (pmc_id, lease_id);
CREATE INDEX trust_owner_held_deposits_tenant ON trust_owner_held_deposits (tenant_id);
CREATE INDEX trust_owner_held_deposits_recorded_by
ON trust_owner_held_deposits (pmc_id, recorded_by);

CREATE TABLE trust_owner_held_deposit_outcomes (
    held_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    -- What the owner gave back to the tenant and kept, together what they held.
    returned numeric NOT NULL CHECK (returned >= 0 AND returned = round(returned, 2)),
    kept numeric NOT NULL CHECK (kept >= 0 AND kept = round(kept, 2)),
    on_day date NOT NULL,
    memo text CHECK (memo = btrim(memo) AND char_length(memo) BETWEEN 1 AND 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, held_id) REFERENCES trust_owner_held_deposits (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_owner_held_deposit_outcomes_pmc
ON trust_owner_held_deposit_outcomes (pmc_id, held_id);
CREATE INDEX trust_owner_held_deposit_outcomes_recorded_by
ON trust_owner_held_deposit_outcomes (pmc_id, recorded_by);

-- A deposit the owner holds is recorded from a day not after the company's today.
CREATE FUNCTION trust_check_owner_held_deposit() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.held_since > trust_pmc_day(NEW.pmc_id) THEN
        RAISE EXCEPTION 'trust: a deposit the owner holds is recorded from a day not after today, '
            'not %', NEW.held_since
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_owner_held_deposit() FROM PUBLIC;

-- What the owner did with it, at move-out: the lease's last day set, together what they held, on a
-- day from when it was held to today.
CREATE FUNCTION trust_check_owner_held_outcome() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_held trust_owner_held_deposits;
    v_ends date;
BEGIN
    SELECT h.* INTO v_held FROM trust_owner_held_deposits AS h
    WHERE h.id = NEW.held_id AND h.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key refuses it
    END IF;
    SELECT l.ends_on INTO v_ends FROM trust_leases AS l WHERE l.id = v_held.lease_id;
    IF v_ends IS NULL THEN
        RAISE EXCEPTION 'trust: what the owner did with a deposit they hold is recorded at '
            'move-out, once its last day is set'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.returned + NEW.kept <> v_held.amount THEN
        RAISE EXCEPTION 'trust: what the owner returned and kept come to the % they held, not %',
            v_held.amount, NEW.returned + NEW.kept
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.on_day < v_held.held_since THEN
        RAISE EXCEPTION 'trust: % is before it was held (%)', NEW.on_day, v_held.held_since
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.on_day > trust_pmc_day(NEW.pmc_id) THEN
        RAISE EXCEPTION 'trust: what the owner did is recorded on a day not after today, not %',
            NEW.on_day
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_owner_held_outcome() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_owner_held_deposits
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_owner_held_deposit BEFORE INSERT ON trust_owner_held_deposits
FOR EACH ROW EXECUTE FUNCTION trust_check_owner_held_deposit();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_held_deposits
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_held_deposits
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_owner_held_deposit_outcomes
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_owner_held_outcome BEFORE INSERT ON trust_owner_held_deposit_outcomes
FOR EACH ROW EXECUTE FUNCTION trust_check_owner_held_outcome();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_held_deposit_outcomes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_owner_held_deposit_outcomes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_owner_held_deposits FROM PUBLIC;
GRANT SELECT, INSERT ON trust_owner_held_deposits TO trust_app;
GRANT SELECT ON trust_owner_held_deposits TO trust_ai_agent;
REVOKE ALL ON trust_owner_held_deposit_outcomes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_owner_held_deposit_outcomes TO trust_app;
GRANT SELECT ON trust_owner_held_deposit_outcomes TO trust_ai_agent;

-- The tenant ledger: each deposit the owner holds, and what became of it, as memo lines.
CREATE OR REPLACE FUNCTION trust_report_tenant_ledger(p_pmc_id uuid, p_lease_id uuid, p_as_of date) RETURNS TABLE(line integer, on_date date, item text, detail text, charged numeric, paid numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant ledger is through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT l.id FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH entries AS (
        SELECT c.due_on AS on_date, 1 AS step, c.created_at AS at, c.id::text AS id, c.kind AS item,
               c.memo AS detail,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS charged,
               0::numeric AS paid
        FROM trust_charges AS c
        WHERE c.lease_id = p_lease_id AND c.due_on <= p_as_of
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 2, tr.event_at, tr.id, 'payment',
               tr.metadata ->> 'memo', 0, sum(cp.amount)
        FROM trust_charge_payments AS cp
        JOIN trust_charges AS c ON c.id = cp.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 3, tr.event_at, tr.id, 'bounced payment',
               tr.metadata ->> 'memo', 0, -sum(r.amount)
        FROM trust_payment_reversals AS r
        JOIN trust_charges AS c ON c.id = r.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
    ),
    lines AS (
        SELECT 0 AS section, NULL::date AS on_date, 0 AS step, NULL::timestamptz AS at,
               NULL::text AS id, 'lease' AS item,
               u.display_name || ', ' || to_char(l.starts_on, 'YYYY-MM-DD')
               || CASE WHEN l.ends_on IS NULL THEN ', month to month'
                       ELSE ' to ' || to_char(l.ends_on, 'YYYY-MM-DD') END AS detail,
               NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
        WHERE l.id = p_lease_id
        UNION ALL
        SELECT 1, e.on_date, e.step, e.at, e.id, e.item, e.detail, e.charged, e.paid
        FROM entries AS e
        UNION ALL
        -- A deposit the owner holds, never in trust (migration 20261106000241): a memo line that
        -- changes no balance; and what the owner returned and kept of it at move-out.
        SELECT 1, h.held_since, 4, h.created_at, h.id::text, 'owner-held deposit',
               t.display_name || '''s ' || h.deposit_kind
               || ' deposit, held by the owner (not in trust): '
               || to_char(h.amount, 'FM999,999,990.00'),
               NULL::numeric, NULL::numeric
        FROM trust_owner_held_deposits AS h
        JOIN trust_tenants AS t ON t.id = h.tenant_id
        WHERE h.lease_id = p_lease_id AND h.held_since <= p_as_of
        UNION ALL
        SELECT 1, o.on_day, 5, o.created_at, h.id::text, 'owner-held deposit settled',
               t.display_name || '''s ' || h.deposit_kind || ' deposit the owner held: '
               || to_char(o.returned, 'FM999,999,990.00') || ' returned, '
               || to_char(o.kept, 'FM999,999,990.00') || ' kept',
               NULL::numeric, NULL::numeric
        FROM trust_owner_held_deposit_outcomes AS o
        JOIN trust_owner_held_deposits AS h ON h.id = o.held_id
        JOIN trust_tenants AS t ON t.id = h.tenant_id
        WHERE h.lease_id = p_lease_id AND o.on_day <= p_as_of
        UNION ALL
        SELECT 2, p_as_of, 0, NULL, NULL, 'balance', 'end of day UTC',
               coalesce((SELECT sum(e.charged) FROM entries AS e), 0),
               coalesce((SELECT sum(e.paid) FROM entries AS e), 0)
    )
    SELECT row_number() OVER w::integer,
           l.on_date,
           l.item,
           l.detail,
           round(l.charged, 2),
           round(l.paid, 2),
           CASE WHEN l.section > 0 THEN round(coalesce(sum(
               coalesce(l.charged, 0) - coalesce(l.paid, 0)
           ) FILTER (WHERE l.section = 1) OVER w, 0), 2) END
    FROM lines AS l
    WINDOW w AS (ORDER BY l.section, l.on_date, l.step, l.at, l.id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    ORDER BY 1;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_owner_held_deposits) THEN
        RAISE EXCEPTION 'refusing to undo deposits the owner holds: some are recorded';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_tenant_ledger(p_pmc_id uuid, p_lease_id uuid, p_as_of date) RETURNS TABLE(line integer, on_date date, item text, detail text, charged numeric, paid numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant ledger is through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT l.id FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH entries AS (
        SELECT c.due_on AS on_date, 1 AS step, c.created_at AS at, c.id::text AS id, c.kind AS item,
               c.memo AS detail,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS charged,
               0::numeric AS paid
        FROM trust_charges AS c
        WHERE c.lease_id = p_lease_id AND c.due_on <= p_as_of
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 2, tr.event_at, tr.id, 'payment',
               tr.metadata ->> 'memo', 0, sum(cp.amount)
        FROM trust_charge_payments AS cp
        JOIN trust_charges AS c ON c.id = cp.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 3, tr.event_at, tr.id, 'bounced payment',
               tr.metadata ->> 'memo', 0, -sum(r.amount)
        FROM trust_payment_reversals AS r
        JOIN trust_charges AS c ON c.id = r.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
    ),
    lines AS (
        SELECT 0 AS section, NULL::date AS on_date, 0 AS step, NULL::timestamptz AS at,
               NULL::text AS id, 'lease' AS item,
               u.display_name || ', ' || to_char(l.starts_on, 'YYYY-MM-DD')
               || CASE WHEN l.ends_on IS NULL THEN ', month to month'
                       ELSE ' to ' || to_char(l.ends_on, 'YYYY-MM-DD') END AS detail,
               NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
        WHERE l.id = p_lease_id
        UNION ALL
        SELECT 1, e.on_date, e.step, e.at, e.id, e.item, e.detail, e.charged, e.paid
        FROM entries AS e
        UNION ALL
        SELECT 2, p_as_of, 0, NULL, NULL, 'balance', 'end of day UTC',
               coalesce((SELECT sum(e.charged) FROM entries AS e), 0),
               coalesce((SELECT sum(e.paid) FROM entries AS e), 0)
    )
    SELECT row_number() OVER w::integer,
           l.on_date,
           l.item,
           l.detail,
           round(l.charged, 2),
           round(l.paid, 2),
           CASE WHEN l.section > 0 THEN round(coalesce(sum(
               coalesce(l.charged, 0) - coalesce(l.paid, 0)
           ) FILTER (WHERE l.section = 1) OVER w, 0), 2) END
    FROM lines AS l
    WINDOW w AS (ORDER BY l.section, l.on_date, l.step, l.at, l.id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    ORDER BY 1;
END;
$$;

-- squawk-ignore ban-drop-table
DROP TABLE trust_owner_held_deposit_outcomes;
-- squawk-ignore ban-drop-table
DROP TABLE trust_owner_held_deposits;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_owner_held_outcome();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_owner_held_deposit();
