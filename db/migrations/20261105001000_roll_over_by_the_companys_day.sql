-- migrate:up
-- A lease entered on the evening of its last day goes month to month (docs/edge-cases.md D-055).
--
-- trust_roll_over_leases leaves a lease entered after its term ran out as it was, and read the
-- day it was entered as created_at::date: UTC's day. A lease entered at 9 pm in New York on its
-- last day read as entered the next day, after its end, so it never rolled over and its rent
-- stopped. The day it was entered is now the company's own (trust_pmc_day, migration
-- 20261104000900), as every other day of its money is.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_roll_over_leases(p_pmc_id uuid, p_day date) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease record;
    v_rolled integer := 0;
BEGIN
    IF p_day IS NULL THEN
        RAISE EXCEPTION 'trust: leases roll over on a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Two runs at once roll each lease once: the second waits, then finds the first's.
    PERFORM pg_advisory_xact_lock(hashtextextended('trust_lease_holdovers:' || p_pmc_id::text, 0));
    FOR v_lease IN
        SELECT l.id, l.ends_on
        FROM trust_leases AS l
        WHERE l.pmc_id = p_pmc_id
          AND l.ends_on < p_day AND l.ends_on >= p_day - 7
          -- Entered while its term still ran, by the company's day: a lease from the past
          -- stays as it was (one entered at 9 pm in New York on its last day is no such lease).
          AND trust_pmc_day(p_pmc_id, l.created_at) <= l.ends_on
          AND NOT EXISTS (SELECT 1 FROM trust_lease_holdovers AS h WHERE h.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_end_notices AS n WHERE n.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_leases AS later
                          WHERE later.unit_id = l.unit_id AND later.id <> l.id
                            AND later.starts_on > l.starts_on)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_transfers AS t WHERE t.from_lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)
        ORDER BY l.ends_on, l.id
    LOOP
        PERFORM trust_end_lease(p_pmc_id, v_lease.id, NULL);
        INSERT INTO trust_lease_holdovers (lease_id, pmc_id, term_ended_on, rolled_on)
        VALUES (v_lease.id, p_pmc_id, v_lease.ends_on, p_day);
        v_rolled := v_rolled + 1;
    END LOOP;
    RETURN v_rolled;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_roll_over_leases(p_pmc_id uuid, p_day date) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease record;
    v_rolled integer := 0;
BEGIN
    IF p_day IS NULL THEN
        RAISE EXCEPTION 'trust: leases roll over on a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Two runs at once roll each lease once: the second waits, then finds the first's.
    PERFORM pg_advisory_xact_lock(hashtextextended('trust_lease_holdovers:' || p_pmc_id::text, 0));
    FOR v_lease IN
        SELECT l.id, l.ends_on
        FROM trust_leases AS l
        WHERE l.pmc_id = p_pmc_id
          AND l.ends_on < p_day AND l.ends_on >= p_day - 7
          -- Entered while its term still ran: a lease from the past stays as it was.
          AND l.created_at::date <= l.ends_on
          AND NOT EXISTS (SELECT 1 FROM trust_lease_holdovers AS h WHERE h.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_end_notices AS n WHERE n.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_leases AS later
                          WHERE later.unit_id = l.unit_id AND later.id <> l.id
                            AND later.starts_on > l.starts_on)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_transfers AS t WHERE t.from_lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)
        ORDER BY l.ends_on, l.id
    LOOP
        PERFORM trust_end_lease(p_pmc_id, v_lease.id, NULL);
        INSERT INTO trust_lease_holdovers (lease_id, pmc_id, term_ended_on, rolled_on)
        VALUES (v_lease.id, p_pmc_id, v_lease.ends_on, p_day);
        v_rolled := v_rolled + 1;
    END LOOP;
    RETURN v_rolled;
END;
$$;
