-- migrate:up
-- A renewal while the old lease still owes (docs/edge-cases.md M-073).
--
-- A renewal ends the old lease the day before and opens the new one for the same tenants, who
-- keep their deposits with it. What the old lease still owed stayed on it: the renewal's payments
-- paid only the renewal's charges, the deposit's return kept only the renewal's debt, and either
-- lease could be returned, the first taking the whole deposit. A move to another unit had solved
-- this already (trust_lease_moved_from, migration 0091); a renewal now goes the same way:
--
--   trust_lease_carried_from   the leases a lease's tenants moved from or renewed, oldest first,
--                              each with the owner's account its move names; NULL for a lease
--                              renewed at the same property, whose money is the property's
--                              owners' as the lease's own is (its one owner's account, or its
--                              owners by their shares).
--   trust_return_deposit       keeps for every lease of that chain, rent first (migration 0093),
--                              each part for its own owners, and refuses a lease that was
--                              renewed: its renewal returns the deposits.
--
-- The rent page's payments (src/pms/api/rent.py) and Move-outs read the same chain.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_lease_carried_from(p_lease_id uuid)
RETURNS TABLE(lease_id uuid, owner_account text)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH RECURSIVE back AS (
        SELECT e.from_lease, e.owner_account, 1 AS step
        FROM (
            SELECT t.from_lease_id, t.owner_account
            FROM trust_lease_transfers AS t WHERE t.to_lease_id = p_lease_id
            UNION ALL
            SELECT o.renews_lease_id, NULL::text
            FROM trust_lease_offer_completions AS c
            JOIN trust_lease_offers AS o ON o.id = c.offer_id
            WHERE c.lease_id = p_lease_id AND o.renews_lease_id IS NOT NULL
        ) AS e (from_lease, owner_account)
        UNION ALL
        -- A lease renewed before a move is the moved lease's owner's, as the move names it. Each
        -- step opens its new lease, so the leases before come first: no cycle.
        SELECT e.from_lease, coalesce(e.owner_account, b.owner_account), b.step + 1
        FROM back AS b
        CROSS JOIN LATERAL (
            SELECT t.from_lease_id, t.owner_account
            FROM trust_lease_transfers AS t WHERE t.to_lease_id = b.from_lease
            UNION ALL
            SELECT o.renews_lease_id, NULL::text
            FROM trust_lease_offer_completions AS c
            JOIN trust_lease_offers AS o ON o.id = c.offer_id
            WHERE c.lease_id = b.from_lease AND o.renews_lease_id IS NOT NULL
        ) AS e (from_lease, owner_account)
    )
    SELECT b.from_lease, b.owner_account FROM back AS b ORDER BY b.step DESC
$$;
REVOKE ALL ON FUNCTION trust_lease_carried_from(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_lease_carried_from(uuid) TO trust_app;

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
    -- The leases kept for, oldest first, the owner's account of each and what each still owes
    -- of its rent and of its other charges (migration 0093).
    v_leases uuid[] := ARRAY[]::uuid[];
    v_accounts text[] := ARRAY[]::text[];
    v_rent numeric[] := ARRAY[]::numeric[];
    v_fees numeric[] := ARRAY[]::numeric[];
    v_rent_open numeric;
    v_fees_open numeric;
    v_fees_pass boolean;
    i integer;
    -- The lease's property, and whether its owners own it in shares on the day (migration 0094).
    v_property uuid;
    v_split boolean;
    v_pay record;
    -- What of a lease's part pays fees the company keeps, and where it goes (migration
    -- 20261107000100).
    v_company numeric;
    v_cum numeric;
    v_bank uuid;
    v_fee_account text;
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
    -- A lease renewed: its renewal holds its tenants' deposits and keeps for what it still owes
    -- (docs/edge-cases.md M-073).
    SELECT c.lease_id INTO v_moved
    FROM trust_lease_offers AS o
    JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
    WHERE o.renews_lease_id = p_lease_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % was renewed as lease %, and its tenants'' deposits are '
            'returned with the renewal''s', p_lease_id, v_moved
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

    -- A property its owners own in shares on the day keeps for each of them by their share; one
    -- without is kept for its one owner's account, or the one named.
    SELECT u.property_id INTO v_property FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    v_split := EXISTS (SELECT 1 FROM trust_ownership_on(v_property, v_day));
    IF NOT v_split THEN
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
    END IF;

    -- The leases the tenants moved from or renewed (migration 20261115000230, M-073), oldest
    -- first, then this one. Each waits for its own rent work too, taken after this lease's as the
    -- rent page takes them. A lease renewed at this property is kept for as this one is: its
    -- one owner's account, or (NULL) its owners by their shares.
    FOR v_link IN
        SELECT m.lease_id, m.owner_account
        FROM trust_lease_carried_from(p_lease_id) WITH ORDINALITY AS m (lease_id, owner_account, n)
        ORDER BY m.n
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || v_link.lease_id, 0));
        v_leases := v_leases || v_link.lease_id;
        v_accounts := v_accounts || coalesce(v_link.owner_account, v_owner.ledger_account_id);
    END LOOP;
    v_leases := v_leases || p_lease_id;
    v_accounts := v_accounts || v_owner.ledger_account_id;
    -- What is open of each lease's rent (its rent and monthly charges) and of its other charges,
    -- its credits coming off its rent first (trust_open_charges).
    FOR n IN 1 .. cardinality(v_leases) LOOP
        SELECT coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) < 2), 0),
               coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) = 2), 0)
        INTO v_rent_open, v_fees_open
        FROM trust_open_charges(p_pmc_id, v_leases[n], 'infinity') AS o
        JOIN trust_charges AS c ON c.id = o.charge_id;
        v_rent := v_rent || v_rent_open;
        v_fees := v_fees || v_fees_open;
    END LOOP;

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
    v_kept := least(
        (SELECT sum(o) FROM unnest(v_rent) AS o) + (SELECT sum(o) FROM unnest(v_fees) AS o), v_held
    );
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
        -- Each tenant's security deposit first, then their key and pet deposits (0092).
        ORDER BY tn.display_name COLLATE "C", t.tenant_id,
                 CASE t.deposit_kind WHEN 'key' THEN 1 WHEN 'pet' THEN 2 ELSE 0 END,
                 t.ledger_account_id
    LOOP
        v_take := least(v_keep, v_deposit.balance);
        v_rest := v_take;
        -- Kept: the rent of every lease first, lease by lease, oldest first, then their other
        -- charges the same way (rent first, migration 0093), each part into that lease's owner's
        -- account, its cash moving to the owner's trust bank account.
        FOREACH v_fees_pass IN ARRAY ARRAY[false, true] LOOP
            i := 1;
            WHILE v_rest > 0 AND i <= cardinality(v_leases) LOOP
                v_piece := least(v_rest, CASE WHEN v_fees_pass THEN v_fees[i] ELSE v_rent[i] END);
                IF v_piece > 0 THEN
                    -- Of this lease's part, what pays fees the company keeps (trust_charge_payee,
                    -- migration 20261106000220) goes into the company's fee account in the trust
                    -- bank account that holds the owner's money for the lease's property, as a
                    -- tenant's payment does (rent.split), never into an owner's account
                    -- (migration 20261107000100): those the part reaches in the order it pays.
                    v_company := 0;
                    v_cum := 0;
                    FOR v_charge IN
                        SELECT o.open, trust_charge_payee(c) AS payee
                        FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                        JOIN trust_charges AS c ON c.id = o.charge_id
                        WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                        ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                    LOOP
                        EXIT WHEN v_cum >= v_piece;
                        CONTINUE WHEN v_charge.open <= 0;
                        v_part := least(v_piece - v_cum, v_charge.open);
                        IF v_charge.payee = 'company' THEN
                            v_company := v_company + v_part;
                        END IF;
                        v_cum := v_cum + v_part;
                    END LOOP;
                    IF v_company > 0 THEN
                        -- The trust bank account of the lease's owner's account: the one its
                        -- move names for a lease moved from, else the property's (its owners'
                        -- shares are in one trust bank account).
                        SELECT t.bank_account_id INTO v_bank
                        FROM trust_ledger_accounts AS t
                        WHERE t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
                          AND t.ledger_account_id = coalesce(
                              v_accounts[i],
                              (SELECT s.ledger_account_id
                               FROM trust_ownership_on(v_property, v_day) AS s
                               ORDER BY s.ledger_account_id COLLATE "C"
                               LIMIT 1)
                          );
                        SELECT t.ledger_account_id INTO v_fee_account
                        FROM trust_ledger_accounts AS t
                        WHERE t.pmc_id = p_pmc_id AND t.bank_account_id = v_bank
                          AND t.kind = 'pmc_income';
                        IF NOT FOUND THEN
                            v_fee_account := trust_open_ledger_account(
                                p_pmc_id, v_bank, 'pmc_income'
                            );
                        END IF;
                        SELECT t.ledger_account_id INTO v_owner_cash
                        FROM trust_ledger_accounts AS t
                        WHERE t.bank_account_id = v_bank AND t.kind = 'bank_cash';
                        SELECT t.id INTO v_transfer
                        FROM pgledger_create_transfers(
                            ARRAY[
                                (v_deposit.account, v_fee_account, v_company)::transfer_request,
                                (v_owner_cash, v_deposit.cash, v_company)::transfer_request
                            ],
                            p_event_at,
                            jsonb_build_object('memo', 'Security deposit kept')
                        ) AS t
                        WHERE t.from_account_id = v_deposit.account;
                        v_left := v_company;
                        FOR v_charge IN
                            SELECT c.id, o.open
                            FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                            JOIN trust_charges AS c ON c.id = o.charge_id
                            WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                              AND trust_charge_payee(c) = 'company'
                            ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                        LOOP
                            EXIT WHEN v_left <= 0;
                            CONTINUE WHEN v_charge.open <= 0;
                            v_part := least(v_left, v_charge.open);
                            PERFORM trust_apply_payment(
                                p_pmc_id, v_charge.id, v_transfer, v_part
                            );
                            v_line := v_line + 1;
                            INSERT INTO trust_deposit_return_lines (
                                lease_id, line_no, pmc_id, tenant_id, charge_id, amount,
                                transfer_id
                            ) VALUES (
                                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id,
                                v_part, v_transfer
                            );
                            v_left := v_left - v_part;
                        END LOOP;
                    END IF;
                    -- The rest of it goes to its owners by their shares, a lease moved from to
                    -- the owner's account its move names (a lease renewed as this one's).
                    FOR v_pay IN
                        SELECT s.ledger_account_id COLLATE "C" AS account, s.amount
                        FROM trust_split_by_ownership(v_property, v_day, v_piece - v_company) AS s
                        WHERE v_split AND v_accounts[i] IS NULL AND v_piece > v_company
                        UNION ALL
                        SELECT v_accounts[i] COLLATE "C", v_piece - v_company
                        WHERE NOT (v_split AND v_accounts[i] IS NULL) AND v_piece > v_company
                        ORDER BY account
                    LOOP
                        SELECT t.* INTO v_owner FROM trust_ledger_accounts AS t
                        WHERE t.ledger_account_id = v_pay.account;
                        SELECT t.ledger_account_id INTO v_owner_cash
                        FROM trust_ledger_accounts AS t
                        WHERE t.bank_account_id = v_owner.bank_account_id
                          AND t.kind = 'bank_cash';
                        SELECT t.id INTO v_transfer
                        FROM pgledger_create_transfers(
                            ARRAY[
                                (v_deposit.account, v_owner.ledger_account_id, v_pay.amount)
                                    ::transfer_request,
                                (v_owner_cash, v_deposit.cash, v_pay.amount)::transfer_request
                            ],
                            p_event_at,
                            jsonb_build_object('memo', 'Security deposit kept')
                        ) AS t
                        WHERE t.from_account_id = v_deposit.account;
                        v_left := v_pay.amount;
                        -- What is open of each, rent first: what a waiver or a credit took off
                        -- it is not kept from the deposit.
                        FOR v_charge IN
                            SELECT c.id, o.open
                            FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                            JOIN trust_charges AS c ON c.id = o.charge_id
                            WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                              AND trust_charge_payee(c) <> 'company'
                            ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                        LOOP
                            EXIT WHEN v_left <= 0;
                            CONTINUE WHEN v_charge.open <= 0;
                            v_part := least(v_left, v_charge.open);
                            PERFORM trust_apply_payment(
                                p_pmc_id, v_charge.id, v_transfer, v_part
                            );
                            v_line := v_line + 1;
                            INSERT INTO trust_deposit_return_lines (
                                lease_id, line_no, pmc_id, tenant_id, charge_id, amount,
                                transfer_id
                            ) VALUES (
                                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id,
                                v_part, v_transfer
                            );
                            v_left := v_left - v_part;
                        END LOOP;
                    END LOOP;
                    IF v_fees_pass THEN
                        v_fees[i] := v_fees[i] - v_piece;
                    ELSE
                        v_rent[i] := v_rent[i] - v_piece;
                    END IF;
                    v_rest := v_rest - v_piece;
                END IF;
                i := i + 1;
            END LOOP;
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

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

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
    -- The leases kept for, oldest first, the owner's account of each and what each still owes
    -- of its rent and of its other charges (migration 0093).
    v_leases uuid[] := ARRAY[]::uuid[];
    v_accounts text[] := ARRAY[]::text[];
    v_rent numeric[] := ARRAY[]::numeric[];
    v_fees numeric[] := ARRAY[]::numeric[];
    v_rent_open numeric;
    v_fees_open numeric;
    v_fees_pass boolean;
    i integer;
    -- The lease's property, and whether its owners own it in shares on the day (migration 0094).
    v_property uuid;
    v_split boolean;
    v_pay record;
    -- What of a lease's part pays fees the company keeps, and where it goes (migration
    -- 20261107000100).
    v_company numeric;
    v_cum numeric;
    v_bank uuid;
    v_fee_account text;
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

    -- A property its owners own in shares on the day keeps for each of them by their share; one
    -- without is kept for its one owner's account, or the one named.
    SELECT u.property_id INTO v_property FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    v_split := EXISTS (SELECT 1 FROM trust_ownership_on(v_property, v_day));
    IF NOT v_split THEN
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
    END LOOP;
    v_leases := v_leases || p_lease_id;
    v_accounts := v_accounts || v_owner.ledger_account_id;
    -- What is open of each lease's rent (its rent and monthly charges) and of its other charges,
    -- its credits coming off its rent first (trust_open_charges).
    FOR n IN 1 .. cardinality(v_leases) LOOP
        SELECT coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) < 2), 0),
               coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) = 2), 0)
        INTO v_rent_open, v_fees_open
        FROM trust_open_charges(p_pmc_id, v_leases[n], 'infinity') AS o
        JOIN trust_charges AS c ON c.id = o.charge_id;
        v_rent := v_rent || v_rent_open;
        v_fees := v_fees || v_fees_open;
    END LOOP;

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
    v_kept := least(
        (SELECT sum(o) FROM unnest(v_rent) AS o) + (SELECT sum(o) FROM unnest(v_fees) AS o), v_held
    );
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
        -- Each tenant's security deposit first, then their key and pet deposits (0092).
        ORDER BY tn.display_name COLLATE "C", t.tenant_id,
                 CASE t.deposit_kind WHEN 'key' THEN 1 WHEN 'pet' THEN 2 ELSE 0 END,
                 t.ledger_account_id
    LOOP
        v_take := least(v_keep, v_deposit.balance);
        v_rest := v_take;
        -- Kept: the rent of every lease first, lease by lease, oldest first, then their other
        -- charges the same way (rent first, migration 0093), each part into that lease's owner's
        -- account, its cash moving to the owner's trust bank account.
        FOREACH v_fees_pass IN ARRAY ARRAY[false, true] LOOP
            i := 1;
            WHILE v_rest > 0 AND i <= cardinality(v_leases) LOOP
                v_piece := least(v_rest, CASE WHEN v_fees_pass THEN v_fees[i] ELSE v_rent[i] END);
                IF v_piece > 0 THEN
                    -- Of this lease's part, what pays fees the company keeps (trust_charge_payee,
                    -- migration 20261106000220) goes into the company's fee account in the trust
                    -- bank account that holds the owner's money for the lease's property, as a
                    -- tenant's payment does (rent.split), never into an owner's account
                    -- (migration 20261107000100): those the part reaches in the order it pays.
                    v_company := 0;
                    v_cum := 0;
                    FOR v_charge IN
                        SELECT o.open, trust_charge_payee(c) AS payee
                        FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                        JOIN trust_charges AS c ON c.id = o.charge_id
                        WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                        ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                    LOOP
                        EXIT WHEN v_cum >= v_piece;
                        CONTINUE WHEN v_charge.open <= 0;
                        v_part := least(v_piece - v_cum, v_charge.open);
                        IF v_charge.payee = 'company' THEN
                            v_company := v_company + v_part;
                        END IF;
                        v_cum := v_cum + v_part;
                    END LOOP;
                    IF v_company > 0 THEN
                        -- The trust bank account of the lease's owner's account: the one its
                        -- move names for a lease moved from, else the property's (its owners'
                        -- shares are in one trust bank account).
                        SELECT t.bank_account_id INTO v_bank
                        FROM trust_ledger_accounts AS t
                        WHERE t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
                          AND t.ledger_account_id = coalesce(
                              v_accounts[i],
                              (SELECT s.ledger_account_id
                               FROM trust_ownership_on(v_property, v_day) AS s
                               ORDER BY s.ledger_account_id COLLATE "C"
                               LIMIT 1)
                          );
                        SELECT t.ledger_account_id INTO v_fee_account
                        FROM trust_ledger_accounts AS t
                        WHERE t.pmc_id = p_pmc_id AND t.bank_account_id = v_bank
                          AND t.kind = 'pmc_income';
                        IF NOT FOUND THEN
                            v_fee_account := trust_open_ledger_account(
                                p_pmc_id, v_bank, 'pmc_income'
                            );
                        END IF;
                        SELECT t.ledger_account_id INTO v_owner_cash
                        FROM trust_ledger_accounts AS t
                        WHERE t.bank_account_id = v_bank AND t.kind = 'bank_cash';
                        SELECT t.id INTO v_transfer
                        FROM pgledger_create_transfers(
                            ARRAY[
                                (v_deposit.account, v_fee_account, v_company)::transfer_request,
                                (v_owner_cash, v_deposit.cash, v_company)::transfer_request
                            ],
                            p_event_at,
                            jsonb_build_object('memo', 'Security deposit kept')
                        ) AS t
                        WHERE t.from_account_id = v_deposit.account;
                        v_left := v_company;
                        FOR v_charge IN
                            SELECT c.id, o.open
                            FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                            JOIN trust_charges AS c ON c.id = o.charge_id
                            WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                              AND trust_charge_payee(c) = 'company'
                            ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                        LOOP
                            EXIT WHEN v_left <= 0;
                            CONTINUE WHEN v_charge.open <= 0;
                            v_part := least(v_left, v_charge.open);
                            PERFORM trust_apply_payment(
                                p_pmc_id, v_charge.id, v_transfer, v_part
                            );
                            v_line := v_line + 1;
                            INSERT INTO trust_deposit_return_lines (
                                lease_id, line_no, pmc_id, tenant_id, charge_id, amount,
                                transfer_id
                            ) VALUES (
                                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id,
                                v_part, v_transfer
                            );
                            v_left := v_left - v_part;
                        END LOOP;
                    END IF;
                    -- The rest of it goes to its owners by their shares, a lease moved from to
                    -- the owner's account its move names.
                    FOR v_pay IN
                        SELECT s.ledger_account_id COLLATE "C" AS account, s.amount
                        FROM trust_split_by_ownership(v_property, v_day, v_piece - v_company) AS s
                        WHERE v_split AND i = cardinality(v_leases) AND v_piece > v_company
                        UNION ALL
                        SELECT v_accounts[i] COLLATE "C", v_piece - v_company
                        WHERE NOT (v_split AND i = cardinality(v_leases)) AND v_piece > v_company
                        ORDER BY account
                    LOOP
                        SELECT t.* INTO v_owner FROM trust_ledger_accounts AS t
                        WHERE t.ledger_account_id = v_pay.account;
                        SELECT t.ledger_account_id INTO v_owner_cash
                        FROM trust_ledger_accounts AS t
                        WHERE t.bank_account_id = v_owner.bank_account_id
                          AND t.kind = 'bank_cash';
                        SELECT t.id INTO v_transfer
                        FROM pgledger_create_transfers(
                            ARRAY[
                                (v_deposit.account, v_owner.ledger_account_id, v_pay.amount)
                                    ::transfer_request,
                                (v_owner_cash, v_deposit.cash, v_pay.amount)::transfer_request
                            ],
                            p_event_at,
                            jsonb_build_object('memo', 'Security deposit kept')
                        ) AS t
                        WHERE t.from_account_id = v_deposit.account;
                        v_left := v_pay.amount;
                        -- What is open of each, rent first: what a waiver or a credit took off
                        -- it is not kept from the deposit.
                        FOR v_charge IN
                            SELECT c.id, o.open
                            FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                            JOIN trust_charges AS c ON c.id = o.charge_id
                            WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                              AND trust_charge_payee(c) <> 'company'
                            ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                        LOOP
                            EXIT WHEN v_left <= 0;
                            CONTINUE WHEN v_charge.open <= 0;
                            v_part := least(v_left, v_charge.open);
                            PERFORM trust_apply_payment(
                                p_pmc_id, v_charge.id, v_transfer, v_part
                            );
                            v_line := v_line + 1;
                            INSERT INTO trust_deposit_return_lines (
                                lease_id, line_no, pmc_id, tenant_id, charge_id, amount,
                                transfer_id
                            ) VALUES (
                                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id,
                                v_part, v_transfer
                            );
                            v_left := v_left - v_part;
                        END LOOP;
                    END LOOP;
                    IF v_fees_pass THEN
                        v_fees[i] := v_fees[i] - v_piece;
                    ELSE
                        v_rent[i] := v_rent[i] - v_piece;
                    END IF;
                    v_rest := v_rest - v_piece;
                END IF;
                i := i + 1;
            END LOOP;
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

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_lease_carried_from(uuid);
