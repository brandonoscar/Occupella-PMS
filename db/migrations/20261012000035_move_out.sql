-- migrate:up
-- Move-out and the security deposit's return (founder, 2026-10-12:
-- docs/adr/0003-staff-back-office.md, item 5).
--
-- Once a lease has ended, trust_return_deposit settles its tenants' security deposits, once:
-- what the lease still owes (its rent and fees, including what staff charged at move-out for
-- cleaning or damage, less what was paid and less its credits) is kept from the deposits and
-- paid into the owner's account for the property, matched to those charges oldest first, its
-- cash moving with it from the security-deposit trust bank account to the owner's; the rest goes
-- back to each tenant, out of the security-deposit account's cash.
--
--   trust_deposit_returns       one per lease: when, the request key, held, kept and returned,
--                               and the staff member named
--   trust_deposit_return_lines  what moved, line by line: a tenant's deposit kept for a charge,
--                               or returned to them
--
-- trust_report_deposit_return is the itemized statement staff send the tenants. Returning a
-- deposit pays out money, so it takes the disburse role (migration 0034). A tenant still on
-- another lease keeps their deposit with it (a renewal), so such a lease's deposit is not
-- returned. No state's deadline, interest or form is built in.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_deposit_returns (
    lease_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (request_key <> ''),
    returned_at timestamptz NOT NULL,
    held numeric NOT NULL CHECK (held > 0),
    kept numeric NOT NULL CHECK (kept >= 0),
    returned numeric NOT NULL CHECK (returned >= 0),
    returned_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (kept + returned = held),
    UNIQUE (pmc_id, lease_id),
    UNIQUE (pmc_id, request_key),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, returned_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_deposit_return_lines (
    lease_id uuid NOT NULL,
    line_no bigint NOT NULL CHECK (line_no >= 1),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    tenant_id uuid NOT NULL,
    -- Kept for this charge; NULL: returned to the tenant.
    charge_id uuid,
    amount numeric NOT NULL CHECK (amount > 0),
    transfer_id text NOT NULL,
    PRIMARY KEY (lease_id, line_no),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_deposit_returns (pmc_id, lease_id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_deposit_returns
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_deposit_return_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_deposit_returns
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_deposit_return_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Paying a deposit out takes a staff member (if one is named) who holds disburse.
CREATE TRIGGER trust_disburse
BEFORE INSERT ON trust_deposit_returns
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'pay out money');

-- What a lease still owes: its rent and fees less their payments (and plus their reversals),
-- less its credits; never below 0.
CREATE FUNCTION trust_lease_owed(p_lease_id uuid) RETURNS numeric
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT greatest(
        coalesce((SELECT sum(CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
                  FROM trust_charges AS c WHERE c.lease_id = p_lease_id), 0)
        - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
                    JOIN trust_charges AS c ON c.id = p.charge_id
                    WHERE c.lease_id = p_lease_id), 0)
        + coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
                    JOIN trust_charges AS c ON c.id = r.charge_id
                    WHERE c.lease_id = p_lease_id), 0),
        0
    )
$$;

-- Settles the deposits of an ended lease, once per lease (the same request key again returns
-- what went back; another is refused): keeps what the lease owes for the owner's account
-- (p_owner_account, or the property's only owner account), matched to its open charges oldest
-- first, and returns the rest to each tenant. Returns what went back to the tenants.
CREATE FUNCTION trust_return_deposit(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_owner_account text,
    p_request_key text,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_deposit_returns;
    v_day date := (p_event_at AT TIME ZONE 'UTC')::date;
    v_owner trust_ledger_accounts;
    v_owner_cash text;
    v_still record;
    v_held numeric;
    v_kept numeric;
    v_keep numeric;
    v_owners bigint;
    v_line bigint := 0::bigint;
    v_deposit record;
    v_charge record;
    v_take numeric;
    v_left numeric;
    v_part numeric;
    v_transfer text;
BEGIN
    -- What the lease owes must count every payment that committed while this waited.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: return a deposit at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' OR p_event_at IS NULL THEN
        RAISE EXCEPTION 'trust: a deposit''s return needs a request key and the time it was paid'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The lease's rent work (src/pms/api/rent.py) waits for this, and this for it. The lease's
    -- row, as trust_apply_payment takes it, holds off a change of its days or a new hold.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT r.* INTO v_done FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id;
    IF FOUND THEN
        IF v_done.request_key <> p_request_key THEN
            RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Resend a request key only to retry.';
        END IF;
        RETURN v_done.returned;  -- a retry: what went back then
    END IF;

    IF v_lease.ends_on IS NULL OR v_lease.ends_on >= v_day THEN
        RAISE EXCEPTION 'trust: lease % has not ended by %; end it first', p_lease_id, v_day
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT t.display_name AS tenant, l.id AS lease INTO v_still
    FROM trust_lease_tenants AS mine
    JOIN trust_lease_tenants AS other ON other.tenant_id = mine.tenant_id
    JOIN trust_leases AS l ON l.id = other.lease_id
    JOIN trust_tenants AS t ON t.id = mine.tenant_id
    WHERE mine.lease_id = p_lease_id AND l.id <> p_lease_id
      AND (l.ends_on IS NULL OR l.ends_on >= v_day)
    ORDER BY t.display_name COLLATE "C", l.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % is on lease % still, and their deposit stays with it',
            v_still.tenant, v_still.lease
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT count(*), min(t.ledger_account_id) INTO v_owners, v_owner.ledger_account_id
    FROM trust_ledger_accounts AS t
    JOIN trust_units AS u ON u.property_id = t.property_id
    WHERE u.id = v_lease.unit_id AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
      AND (p_owner_account IS NULL OR t.ledger_account_id = p_owner_account);
    IF v_owners <> 1 THEN
        RAISE EXCEPTION 'trust: % is not the one owner''s account of the lease''s property',
            coalesce(p_owner_account, 'no account given')
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Name the owner''s account when the property has more than one.';
    END IF;
    SELECT t.* INTO v_owner FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = v_owner.ledger_account_id;
    SELECT t.ledger_account_id INTO v_owner_cash FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_owner.bank_account_id AND t.kind = 'bank_cash';

    -- The deposits held for the lease's tenants. One paid in meanwhile stays held (the
    -- transfers below take each account's lock); one taken out meanwhile, by the return of
    -- another lease of the same tenant, makes this refuse, as no deposit goes below zero.
    SELECT coalesce(sum(a.balance), 0) INTO v_held FROM pgledger_accounts AS a
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = a.id
    JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
    WHERE lt.lease_id = p_lease_id AND t.kind = 'tenant_deposit';
    IF v_held <= 0 THEN
        RAISE EXCEPTION 'trust: no security deposit is held for lease %', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    v_kept := least(trust_lease_owed(p_lease_id), v_held);
    v_keep := v_kept;  -- what is left to keep, deposit by deposit

    INSERT INTO trust_deposit_returns (
        lease_id, pmc_id, request_key, returned_at, held, kept, returned, returned_by
    ) VALUES (
        p_lease_id, p_pmc_id, p_request_key, p_event_at, v_held, v_kept, v_held - v_kept,
        nullif(current_setting('trust.staff_id', true), '')::uuid
    );

    FOR v_deposit IN
        SELECT t.ledger_account_id AS account, t.tenant_id, a.balance,
               (SELECT c.ledger_account_id FROM trust_ledger_accounts AS c
                WHERE c.bank_account_id = t.bank_account_id AND c.kind = 'bank_cash') AS cash
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
        JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        WHERE lt.lease_id = p_lease_id AND t.kind = 'tenant_deposit'
        ORDER BY tn.display_name COLLATE "C", t.tenant_id, t.ledger_account_id
    LOOP
        v_take := least(v_keep, v_deposit.balance);
        IF v_take > 0 THEN
            -- Kept: into the owner's account, its cash moving to the owner's trust bank account.
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_owner.ledger_account_id, v_take)::transfer_request,
                      (v_owner_cash, v_deposit.cash, v_take)::transfer_request],
                p_event_at,
                jsonb_build_object('memo', 'Security deposit kept')
            ) AS t
            WHERE t.from_account_id = v_deposit.account;
            v_left := v_take;
            FOR v_charge IN
                SELECT c.id, c.amount
                       - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
                                   WHERE p.charge_id = c.id), 0)
                       + coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
                                   WHERE r.charge_id = c.id), 0) AS open
                FROM trust_charges AS c
                WHERE c.lease_id = p_lease_id AND c.kind <> 'credit'
                ORDER BY c.due_on, c.created_at, c.id
            LOOP
                EXIT WHEN v_left <= 0;
                CONTINUE WHEN v_charge.open <= 0;
                v_part := least(v_left, v_charge.open);
                PERFORM trust_apply_payment(p_pmc_id, v_charge.id, v_transfer, v_part);
                v_line := v_line + 1;
                INSERT INTO trust_deposit_return_lines (
                    lease_id, line_no, pmc_id, tenant_id, charge_id, amount, transfer_id
                ) VALUES (
                    p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id, v_part,
                    v_transfer
                );
                v_left := v_left - v_part;
            END LOOP;
            v_keep := v_keep - v_take;
        END IF;
        IF v_deposit.balance > v_take THEN
            -- Returned: out of the security-deposit trust bank account's cash.
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_deposit.cash, v_deposit.balance - v_take)
                      ::transfer_request],
                p_event_at,
                jsonb_build_object('memo', 'Security deposit returned')
            ) AS t;
            v_line := v_line + 1;
            INSERT INTO trust_deposit_return_lines (
                lease_id, line_no, pmc_id, tenant_id, charge_id, amount, transfer_id
            ) VALUES (
                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, NULL,
                v_deposit.balance - v_take, v_transfer
            );
        END IF;
    END LOOP;
    RETURN v_held - v_kept;
END;
$$;

-- The itemized statement of a lease's deposit, as staff send it to the tenants once returned:
-- the property and unit, the tenants and the lease's days, each tenant's deposit, what was kept
-- for each charge (from all the deposits), what went back to each tenant, and who returned it
-- when.
CREATE FUNCTION trust_report_deposit_return(p_pmc_id uuid, p_lease_id uuid)
RETURNS TABLE (line integer, item text, detail text, amount numeric)
LANGUAGE plpgsql STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_done trust_deposit_returns;
BEGIN
    SELECT r.* INTO v_done FROM trust_deposit_returns AS r
    WHERE r.lease_id = p_lease_id AND r.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: the deposit of lease % of PMC % has not been returned',
            p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH held AS (
        -- Each tenant's deposit when it was returned: what was kept from it and returned.
        SELECT tn.display_name AS tenant, sum(x.amount) AS amount
        FROM trust_deposit_return_lines AS x
        JOIN trust_tenants AS tn ON tn.id = x.tenant_id
        WHERE x.lease_id = p_lease_id
        GROUP BY tn.id, tn.display_name
    ),
    lines AS (
        SELECT 0 AS section, 1 AS n, 'PMC' AS item, p.display_name AS detail,
               NULL::numeric AS amount, NULL::text AS sort, NULL::bigint AS no
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 2, 'property and unit', pr.display_name || ' / ' || u.display_name, NULL,
               NULL, NULL
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.id = p_lease_id
        UNION ALL
        SELECT 0, 3, 'tenants', string_agg(tn.display_name, '; '
                                           ORDER BY tn.display_name COLLATE "C"), NULL, NULL,
               NULL
        FROM trust_lease_tenants AS lt JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
        WHERE lt.lease_id = p_lease_id
        UNION ALL
        SELECT 0, 4, 'lease', to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                   || to_char(l.ends_on, 'YYYY-MM-DD'), NULL, NULL, NULL
        FROM trust_leases AS l WHERE l.id = p_lease_id
        UNION ALL
        SELECT 1, 0, 'deposit held', h.tenant, h.amount, h.tenant, NULL
        FROM held AS h
        UNION ALL
        -- Each charge once, with what all the deposits paid of it.
        SELECT 2, 0, 'kept for', to_char(c.due_on, 'YYYY-MM-DD') || ' ' || c.kind
                   || coalesce(': ' || c.memo, ''), sum(x.amount), NULL, min(x.line_no)
        FROM trust_deposit_return_lines AS x
        JOIN trust_charges AS c ON c.id = x.charge_id
        WHERE x.lease_id = p_lease_id
        GROUP BY c.id, c.due_on, c.kind, c.memo
        UNION ALL
        SELECT 3, 0, 'deposits held, total', NULL, v_done.held, NULL, NULL
        UNION ALL
        SELECT 3, 1, 'kept, total', NULL, v_done.kept, NULL, NULL
        UNION ALL
        SELECT 4, 0, 'returned to', tn.display_name, x.amount, NULL, x.line_no
        FROM trust_deposit_return_lines AS x
        JOIN trust_tenants AS tn ON tn.id = x.tenant_id
        WHERE x.lease_id = p_lease_id AND x.charge_id IS NULL
        UNION ALL
        SELECT 5, 0, 'returned, total', NULL, v_done.returned, NULL, NULL
        UNION ALL
        SELECT 5, 1, 'returned on', to_char(v_done.returned_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')
                   || ' (UTC), by ' || coalesce(
                       (SELECT s.display_name FROM trust_staff AS s
                        WHERE s.id = v_done.returned_by), 'staff not recorded'),
               NULL, NULL, NULL
    )
    SELECT row_number() OVER (ORDER BY l.section, l.n, l.sort COLLATE "C", l.no)::integer,
           l.item, l.detail, l.amount
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON trust_deposit_returns, trust_deposit_return_lines FROM PUBLIC;
-- Written only by trust_return_deposit.
GRANT SELECT ON trust_deposit_returns, trust_deposit_return_lines TO trust_app, trust_ai_agent;

REVOKE ALL ON FUNCTION trust_lease_owed(uuid) FROM PUBLIC;
-- The move-out page shows what a lease owes as the return counts it.
GRANT EXECUTE ON FUNCTION trust_lease_owed(uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_return_deposit(uuid, uuid, text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_return_deposit(uuid, uuid, text, text, timestamptz)
TO trust_app;
REVOKE ALL ON FUNCTION trust_report_deposit_return(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_deposit_return(uuid, uuid) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'move_out cannot be rolled back: deposits returned are records';
END;
$$;
