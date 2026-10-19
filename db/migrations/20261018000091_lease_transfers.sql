-- migrate:up
-- A lease's tenants moving to another unit (the founder's review, q010 and q042; docs/law.md),
-- maybe at another property, of another owner.
--
--   trust_transfer_lease    ends the lease the day before the move and opens a lease at the other
--                           unit for the tenants staying on it, from the day of the move. Moving
--                           to another property, they become its tenants: a tenant is one
--                           record, with one portal login, wherever they rent.
--   trust_lease_transfers   the move, once per lease: from which lease to which, the day, the old
--                           lease's owner's account, the day the tenants agreed in writing that
--                           their deposits stay held for the new lease, and who recorded it.
--
-- What the tenants hold moves with them, as it is theirs and not a lease's: their deposits stay
-- held in the security-deposit trust bank account, for the new lease. What the old lease still
-- owes stays its own: it is owed to the old lease's owner, whose account the move names. The new
-- lease's payments (src/pms/api/rent.py) pay it first, through that account, matched to the old
-- lease's charges, so rent stays rent and the owner who was owed it is paid. When the new lease's
-- deposits are returned, what every lease it came from still owes is kept first, each lease's for
-- its own owner (trust_return_deposit, below); the lease moved from has no return of its own.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_lease_transfers (
    from_lease_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    to_lease_id uuid NOT NULL UNIQUE,
    -- The new lease's first day; the old lease's last is the day before.
    moved_on date NOT NULL,
    -- The old lease's owner's account: what the old lease still owes is paid into it.
    owner_account text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    -- The day the tenants agreed in writing that their deposits stay held for the new lease;
    -- none when they held no deposit.
    deposit_agreed_on date,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (from_lease_id <> to_lease_id),
    FOREIGN KEY (pmc_id, from_lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, to_lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_transfers_owner_account ON trust_lease_transfers (owner_account);
CREATE INDEX trust_lease_transfers_recorded_by ON trust_lease_transfers (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_transfers
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_transfers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_transfers
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Only trust_transfer_lease writes a move. Like a lease's departures, it moves no money, and the
-- AI role reads it.
REVOKE ALL ON trust_lease_transfers FROM PUBLIC;
GRANT SELECT ON trust_lease_transfers TO trust_app;
GRANT SELECT ON trust_lease_transfers TO trust_ai_agent;

-- The leases a lease's tenants moved from, oldest first, each with the owner's account what it
-- still owes is paid into.
CREATE FUNCTION trust_lease_moved_from(p_lease_id uuid)
RETURNS TABLE (lease_id uuid, owner_account text)
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    WITH RECURSIVE back AS (
        SELECT t.from_lease_id, t.owner_account, 1 AS step
        FROM trust_lease_transfers AS t WHERE t.to_lease_id = p_lease_id
        UNION ALL
        -- Each move opens its new lease, so the leases moved from come before it: no cycle.
        SELECT t.from_lease_id, t.owner_account, b.step + 1
        FROM trust_lease_transfers AS t JOIN back AS b ON t.to_lease_id = b.from_lease_id
    )
    SELECT b.from_lease_id, b.owner_account FROM back AS b ORDER BY b.step DESC
$$;

REVOKE ALL ON FUNCTION trust_lease_moved_from(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_lease_moved_from(uuid) TO trust_app;

CREATE FUNCTION trust_transfer_lease(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_unit_id uuid,
    p_moved_on date,
    p_ends_on date,
    p_monthly_rent numeric,
    p_owner_account text,
    p_deposit_agreed_on date
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_lease_transfers;
    v_from_property uuid;
    v_to_property uuid;
    v_owner text;
    v_owners bigint;
    v_tenants uuid[];
    v_who record;
    v_new uuid;
BEGIN
    -- As trust_open_lease and the leases' own trigger ask.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: move a lease at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_moved_on IS NULL THEN
        RAISE EXCEPTION 'trust: a move needs its day'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The lease's rent work and its deposit's return wait for this, and this for them.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Once per lease: the same move again (a retry) is the move made then.
    SELECT t.* INTO v_done FROM trust_lease_transfers AS t WHERE t.from_lease_id = p_lease_id;
    IF FOUND THEN
        IF v_done.moved_on = p_moved_on AND EXISTS (
            SELECT 1 FROM trust_leases AS l WHERE l.id = v_done.to_lease_id AND l.unit_id = p_unit_id
        ) THEN
            RETURN v_done.to_lease_id;
        END IF;
        RAISE EXCEPTION 'trust: lease %''s tenants moved already, to lease %',
            p_lease_id, v_done.to_lease_id
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id) THEN
        RAISE EXCEPTION 'trust: the deposit of lease % was returned: its tenants moved out',
            p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT u.property_id INTO v_from_property FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    SELECT u.property_id INTO v_to_property FROM trust_units AS u
    WHERE u.id = p_unit_id AND u.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no unit % in PMC %', p_unit_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_unit_id = v_lease.unit_id THEN
        RAISE EXCEPTION 'trust: a lease moves to another unit; at its own unit it is renewed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The old lease keeps the days up to the move: it started before and is in effect the day
    -- before.
    IF p_moved_on <= v_lease.starts_on
       OR (v_lease.ends_on IS NOT NULL AND v_lease.ends_on < p_moved_on - 1) THEN
        RAISE EXCEPTION 'trust: lease % is not in effect on %, the day before the move',
            p_lease_id, p_moved_on - 1
            USING ERRCODE = 'check_violation';
    END IF;
    -- A lease on a payment hold (an eviction filed) stays where it is until the hold is released.
    IF EXISTS (
        SELECT 1 FROM trust_payment_holds AS h
        LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
        WHERE h.lease_id = p_lease_id AND (r.ends_on IS NULL OR r.ends_on >= p_moved_on)
    ) THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold; it moves once the hold is released',
            p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- The tenants staying on it move. One who left it with a deposit still under their name keeps
    -- that deposit with this lease until its return, which a lease moved from never has.
    v_tenants := array(
        SELECT lt.tenant_id FROM trust_lease_tenants AS lt
        WHERE lt.lease_id = p_lease_id
          AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                          WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id)
        ORDER BY lt.tenant_id
    );
    SELECT t.display_name AS tenant INTO v_who
    FROM trust_lease_departures AS d
    JOIN trust_tenants AS t ON t.id = d.tenant_id
    JOIN trust_ledger_accounts AS a ON a.tenant_id = d.tenant_id AND a.kind = 'tenant_deposit'
    JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
    WHERE d.lease_id = p_lease_id AND d.deposit_to IS NULL AND b.balance > 0
    ORDER BY t.display_name COLLATE "C", t.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % left lease % with a deposit under their name, returned with the lease''s at move-out',
            v_who.tenant, p_lease_id
            USING ERRCODE = 'check_violation',
                  HINT = 'End the lease and return its deposits at move-out instead.';
    END IF;

    -- The old lease's owner's account: the only one of its property, or the one named.
    SELECT count(*), min(a.ledger_account_id) INTO v_owners, v_owner
    FROM trust_ledger_accounts AS a
    WHERE a.pmc_id = p_pmc_id AND a.kind = 'owner_property' AND a.property_id = v_from_property
      AND (p_owner_account IS NULL OR a.ledger_account_id = p_owner_account);
    IF v_owners <> 1 THEN
        RAISE EXCEPTION 'trust: % is not the one owner''s account of lease %''s property',
            coalesce(p_owner_account, 'no account given'), p_lease_id
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Name the owner''s account when the property has more than one.';
    END IF;

    -- Deposits stay held for the new lease only as the tenants agreed in writing.
    IF p_deposit_agreed_on IS NULL AND EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS a
        JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
        WHERE a.tenant_id = ANY (v_tenants) AND a.kind = 'tenant_deposit' AND b.balance > 0
    ) THEN
        RAISE EXCEPTION 'trust: the tenants hold a deposit: record the day they agreed in writing that it stays held for the new lease'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_deposit_agreed_on > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: the tenants agreed on a day that has come, not on %',
            p_deposit_agreed_on
            USING ERRCODE = 'check_violation';
    END IF;

    -- Moving to another property, they become its tenants, as long as no other lease of theirs
    -- runs on there.
    IF v_to_property <> v_from_property THEN
        SELECT t.display_name AS tenant, l.id AS lease INTO v_who
        FROM trust_lease_tenants AS lt
        JOIN trust_leases AS l ON l.id = lt.lease_id
        JOIN trust_tenants AS t ON t.id = lt.tenant_id
        WHERE lt.tenant_id = ANY (v_tenants) AND l.id <> p_lease_id
          AND (l.ends_on IS NULL OR l.ends_on >= p_moved_on)
        ORDER BY t.display_name COLLATE "C", l.id
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'trust: % is on lease % too; they move to another property with every lease of theirs ended',
                v_who.tenant, v_who.lease
                USING ERRCODE = 'check_violation';
        END IF;
        UPDATE trust_tenants SET property_id = v_to_property
        WHERE pmc_id = p_pmc_id AND id = ANY (v_tenants);
    END IF;

    IF v_lease.ends_on IS DISTINCT FROM p_moved_on - 1 THEN
        PERFORM trust_end_lease(p_pmc_id, p_lease_id, p_moved_on - 1);
    END IF;
    v_new := trust_open_lease(p_pmc_id, p_unit_id, p_moved_on, p_ends_on, p_monthly_rent, v_tenants);
    INSERT INTO trust_lease_transfers (
        from_lease_id, pmc_id, to_lease_id, moved_on, owner_account, deposit_agreed_on
    ) VALUES (p_lease_id, p_pmc_id, v_new, p_moved_on, v_owner, p_deposit_agreed_on);
    RETURN v_new;
END;
$$;

REVOKE ALL ON FUNCTION trust_transfer_lease(uuid, uuid, uuid, date, date, numeric, text, date)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_transfer_lease(uuid, uuid, uuid, date, date, numeric, text, date)
TO trust_app;

-- A deposit's return keeps what every lease the tenants moved from still owes, each lease's for
-- the owner its transfer names, before what the lease itself owes. A lease moved from has none
-- of its own. Otherwise as in migration 0079.
CREATE OR REPLACE FUNCTION trust_return_deposit(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_owner_account text,
    p_request_key text,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_deposit_returns;
    v_day date := (p_event_at AT TIME ZONE 'UTC')::date;
    v_owner trust_ledger_accounts;
    v_owner_cash text;
    v_moved uuid;
    v_still record;
    v_held numeric;
    v_kept numeric;
    v_keep numeric;
    v_owners bigint;
    v_line bigint := 0::bigint;
    v_deposit record;
    v_link record;
    v_charge record;
    v_take numeric;
    v_rest numeric;
    v_piece numeric;
    v_left numeric;
    v_part numeric;
    v_transfer text;
    -- The leases kept for, oldest first, the owner's account of each and what each still owes.
    v_leases uuid[] := ARRAY[]::uuid[];
    v_accounts text[] := ARRAY[]::text[];
    v_owing numeric[] := ARRAY[]::numeric[];
    i integer;
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

    SELECT t.to_lease_id INTO v_moved FROM trust_lease_transfers AS t
    WHERE t.from_lease_id = p_lease_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease %''s tenants moved to lease %, and their deposits with them',
            p_lease_id, v_moved
            USING ERRCODE = 'check_violation';
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

    -- The leases the tenants moved from, oldest first, then this one. Each moved from waits for
    -- its own rent work too, taken after this lease's as the rent page takes them.
    FOR v_link IN
        SELECT m.lease_id, m.owner_account
        FROM trust_lease_moved_from(p_lease_id) WITH ORDINALITY AS m (lease_id, owner_account, n)
        ORDER BY m.n
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || v_link.lease_id, 0));
        v_leases := v_leases || v_link.lease_id;
        v_accounts := v_accounts || v_link.owner_account;
        v_owing := v_owing || trust_lease_owed(v_link.lease_id);
    END LOOP;
    v_leases := v_leases || p_lease_id;
    v_accounts := v_accounts || v_owner.ledger_account_id;
    v_owing := v_owing || trust_lease_owed(p_lease_id);

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
    v_kept := least((SELECT sum(o) FROM unnest(v_owing) AS o), v_held);
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
        v_rest := v_take;
        -- Kept: lease by lease, oldest first, each part into that lease's owner's account, its
        -- cash moving to the owner's trust bank account.
        i := 1;
        WHILE v_rest > 0 AND i <= cardinality(v_leases) LOOP
            v_piece := least(v_rest, v_owing[i]);
            IF v_piece > 0 THEN
                SELECT t.* INTO v_owner FROM trust_ledger_accounts AS t
                WHERE t.ledger_account_id = v_accounts[i];
                SELECT t.ledger_account_id INTO v_owner_cash FROM trust_ledger_accounts AS t
                WHERE t.bank_account_id = v_owner.bank_account_id AND t.kind = 'bank_cash';
                SELECT t.id INTO v_transfer
                FROM pgledger_create_transfers(
                    ARRAY[(v_deposit.account, v_owner.ledger_account_id, v_piece)::transfer_request,
                          (v_owner_cash, v_deposit.cash, v_piece)::transfer_request],
                    p_event_at,
                    jsonb_build_object('memo', 'Security deposit kept')
                ) AS t
                WHERE t.from_account_id = v_deposit.account;
                v_left := v_piece;
                FOR v_charge IN
                    SELECT c.id, c.amount
                           - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
                                       WHERE p.charge_id = c.id), 0)
                           + coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
                                       WHERE r.charge_id = c.id), 0)
                           -- What a waiver took off it is not kept from the deposit (0079).
                           - coalesce((SELECT sum(k.amount) FROM trust_charge_waivers AS w
                                       JOIN trust_charges AS k ON k.id = w.credit_charge_id
                                       WHERE w.waived_charge_id = c.id), 0) AS open
                    FROM trust_charges AS c
                    WHERE c.lease_id = v_leases[i] AND c.kind <> 'credit'
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
                v_owing[i] := v_owing[i] - v_piece;
                v_rest := v_rest - v_piece;
            END IF;
            i := i + 1;
        END LOOP;
        v_keep := v_keep - v_take;
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

-- The itemized statement says which home each charge kept for was at, when the tenants moved
-- from it, and each lease they moved from. Otherwise as in migration 0035.
CREATE OR REPLACE FUNCTION trust_report_deposit_return(p_pmc_id uuid, p_lease_id uuid)
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
        -- Each lease the tenants moved from, oldest first.
        SELECT 0, 5, 'moved from', pr.display_name || ' / ' || u.display_name || ', '
                   || to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                   || to_char(l.ends_on, 'YYYY-MM-DD'),
               NULL, to_char(l.starts_on, 'YYYY-MM-DD'), NULL
        FROM trust_lease_moved_from(p_lease_id) AS m
        JOIN trust_leases AS l ON l.id = m.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 1, 0, 'deposit held', h.tenant, h.amount, h.tenant, NULL
        FROM held AS h
        UNION ALL
        -- Each charge once, with what all the deposits paid of it; one of a lease moved from
        -- names its home.
        SELECT 2, 0, 'kept for', to_char(c.due_on, 'YYYY-MM-DD') || ' ' || c.kind
                   || coalesce(': ' || c.memo, '')
                   || CASE WHEN c.lease_id = p_lease_id THEN ''
                           ELSE ' (' || pr.display_name || ' / ' || u.display_name || ')' END,
               sum(x.amount), NULL, min(x.line_no)
        FROM trust_deposit_return_lines AS x
        JOIN trust_charges AS c ON c.id = x.charge_id
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE x.lease_id = p_lease_id
        GROUP BY c.id, c.due_on, c.kind, c.memo, c.lease_id, pr.display_name, u.display_name
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

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'lease_transfers cannot be rolled back: a move is a record';
END;
$$;
