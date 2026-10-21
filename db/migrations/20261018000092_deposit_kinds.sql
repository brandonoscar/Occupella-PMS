-- migrate:up
-- A deposit's kind (the founder's review, q043; docs/law.md): a pet deposit or a key deposit held
-- apart from a tenant's security deposit, in its own ledger account, so each is shown, held and
-- returned on its own.
--
--   trust_ledger_accounts.deposit_kind   'pet' or 'key' for a tenant's deposit of that kind;
--                                        none for their security deposit (every one before this).
--   trust_open_deposit_account           a tenant's deposit account of a kind in a security-
--                                        deposit trust bank account, found or opened.
--
-- Every kind sits in the security-deposit trust bank account, as before: most states count a pet
-- deposit as part of the security deposit. A departing tenant's deposits move to the staying
-- tenant's of the same kind; the register and the return's statement name each kind.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_ledger_accounts ADD COLUMN deposit_kind text;
ALTER TABLE trust_ledger_accounts ADD CONSTRAINT trust_ledger_accounts_deposit_kind CHECK (
    deposit_kind IS NULL OR (kind = 'tenant_deposit' AND deposit_kind IN ('pet', 'key'))
) NOT VALID;  -- every row has none yet; each new one is checked

-- One deposit account of each kind per tenant and trust bank account. A migration runs in a
-- transaction, where an index can't be dropped concurrently; the lock waits at most lock_timeout.
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_ledger_accounts_one_tenant_deposit;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_ledger_accounts_one_tenant_deposit ON trust_ledger_accounts (
    bank_account_id, tenant_id, coalesce(deposit_kind, 'security')
) WHERE kind = 'tenant_deposit';

CREATE FUNCTION trust_open_deposit_account(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_tenant_id uuid,
    p_deposit_kind text
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_kind text := nullif(p_deposit_kind, 'security');
    v_ledger_account_id text;
BEGIN
    IF p_deposit_kind IS NULL OR p_deposit_kind NOT IN ('security', 'pet', 'key') THEN
        RAISE EXCEPTION 'trust: a deposit is a security, pet or key deposit, not %',
            coalesce(p_deposit_kind, 'none')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.ledger_account_id INTO v_ledger_account_id FROM trust_ledger_accounts AS t
    WHERE t.pmc_id = p_pmc_id AND t.bank_account_id = p_bank_account_id
      AND t.kind = 'tenant_deposit' AND t.tenant_id = p_tenant_id
      AND t.deposit_kind IS NOT DISTINCT FROM v_kind;
    IF FOUND THEN
        RETURN v_ledger_account_id;
    END IF;

    SELECT a.id INTO v_ledger_account_id
    FROM pgledger_create_account('tenant_deposit', 'USD', allow_negative_balance => false) AS a;
    -- The account's own checks (and its trust bank account's kind) hold as for any other.
    INSERT INTO trust_ledger_accounts (
        ledger_account_id, pmc_id, bank_account_id, kind, tenant_id, deposit_kind
    ) VALUES (
        v_ledger_account_id, p_pmc_id, p_bank_account_id, 'tenant_deposit', p_tenant_id, v_kind
    );
    RETURN v_ledger_account_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_open_deposit_account(uuid, uuid, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_open_deposit_account(uuid, uuid, uuid, text) TO trust_app;

-- A departing tenant's deposits move to the staying tenant's of the same kind. Otherwise as in
-- migration 0080.
CREATE OR REPLACE FUNCTION trust_remove_lease_tenant(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_tenant_id uuid,
    p_left_on date,
    p_deposit_to uuid
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_lease_departures;
    v_name text;
    v_still record;
    v_deposit record;
    v_to text;
    v_moved numeric := 0.00;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: remove tenants at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A lease's rent work, deposit return and departures wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));

    SELECT t.display_name INTO v_name FROM trust_lease_tenants AS lt
    JOIN trust_tenants AS t ON t.id = lt.tenant_id
    WHERE lt.lease_id = p_lease_id AND lt.tenant_id = p_tenant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: tenant % is not on lease %', p_tenant_id, p_lease_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT d.* INTO v_done FROM trust_lease_departures AS d
    WHERE d.lease_id = p_lease_id AND d.tenant_id = p_tenant_id;
    IF FOUND THEN
        IF v_done.left_on = p_left_on AND v_done.deposit_to IS NOT DISTINCT FROM p_deposit_to THEN
            RETURN v_done.moved;
        END IF;
        RAISE EXCEPTION 'trust: % left this lease on % already', v_name, v_done.left_on
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT r.lease_id FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id) THEN
        RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_left_on IS NULL OR p_left_on < v_lease.starts_on OR p_left_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: a tenant leaves a lease within its days: from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT lt.tenant_id FROM trust_lease_tenants AS lt
        WHERE lt.lease_id = p_lease_id AND lt.tenant_id <> p_tenant_id AND NOT EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id
        )
    ) THEN
        RAISE EXCEPTION 'trust: % is the last tenant on this lease: end the lease instead',
            v_name
            USING ERRCODE = 'check_violation';
    END IF;

    IF p_deposit_to IS NOT NULL THEN
        IF p_deposit_to = p_tenant_id OR NOT EXISTS (
            SELECT lt.tenant_id FROM trust_lease_tenants AS lt
            WHERE lt.lease_id = p_lease_id AND lt.tenant_id = p_deposit_to
        ) OR EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = p_lease_id AND d.tenant_id = p_deposit_to
        ) THEN
            RAISE EXCEPTION 'trust: tenant % is not staying on lease %', p_deposit_to, p_lease_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- A deposit is held per tenant: on another lease still, theirs may be that lease's.
        SELECT u.display_name AS unit INTO v_still
        FROM trust_lease_tenants AS other
        JOIN trust_leases AS l ON l.id = other.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE other.tenant_id = p_tenant_id AND l.id <> p_lease_id
          AND (l.ends_on IS NULL OR l.ends_on >= p_left_on)
          AND NOT EXISTS (
              SELECT d.tenant_id FROM trust_lease_departures AS d
              WHERE d.lease_id = l.id AND d.tenant_id = p_tenant_id
          )
        ORDER BY l.starts_on, l.id
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'trust: % is on the lease of % still, and their deposit stays with it',
                v_name, v_still.unit
                USING ERRCODE = 'check_violation';
        END IF;
        FOR v_deposit IN
            SELECT t.ledger_account_id AS account, t.bank_account_id, t.deposit_kind, a.balance
            FROM trust_ledger_accounts AS t
            JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
            WHERE t.pmc_id = p_pmc_id AND t.tenant_id = p_tenant_id
              AND t.kind = 'tenant_deposit' AND a.balance > 0
            ORDER BY t.ledger_account_id
            FOR UPDATE OF a
        LOOP
            -- Into the staying tenant's deposit of the same kind (migration 0092).
            v_to := trust_open_deposit_account(
                p_pmc_id, v_deposit.bank_account_id, p_deposit_to,
                coalesce(v_deposit.deposit_kind, 'security')
            );
            -- Within one trust bank account: its cash doesn't move.
            PERFORM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_to, v_deposit.balance)::transfer_request],
                now(),
                jsonb_build_object(
                    'memo', 'Security deposit moved as ' || v_name || ' left the lease',
                    'kind', 'deposit moved', 'lease_id', p_lease_id
                )
            );
            v_moved := v_moved + v_deposit.balance;
        END LOOP;
    END IF;

    INSERT INTO trust_lease_departures (pmc_id, lease_id, tenant_id, left_on, deposit_to, moved)
    VALUES (p_pmc_id, p_lease_id, p_tenant_id, p_left_on, p_deposit_to, v_moved);
    RETURN v_moved;
END;
$$;

-- A return keeps from each tenant's security deposit first, then their key and pet deposits.
-- Otherwise as in migration 0091.
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
        -- Each tenant's security deposit first, then their key and pet deposits (0092).
        ORDER BY tn.display_name COLLATE "C", t.tenant_id,
                 CASE t.deposit_kind WHEN 'key' THEN 1 WHEN 'pet' THEN 2 ELSE 0 END,
                 t.ledger_account_id
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

-- The register lists each deposit by its kind ("pet deposit", "key deposit"), a tenant's security
-- deposit first. Otherwise as in migration 0021.
CREATE OR REPLACE FUNCTION trust_report_security_deposits(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    bank text,
    tenant text,
    property text,
    held numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a deposit register is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.deposit_kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               tn.display_name AS tenant_name, pr.display_name AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS pr ON pr.id = tn.property_id
        WHERE t.pmc_id = p_pmc_id AND b.kind = 'security_deposit'
          AND t.kind IN ('tenant_deposit', 'bank_cash')
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS tenant_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS property_name, NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL
        UNION ALL
        -- A tenant's security deposit first, then their key and pet deposits.
        SELECT 1, b.bank_name, b.bank_account_id, 0, b.tenant_name,
               CASE b.deposit_kind WHEN 'key' THEN '1' WHEN 'pet' THEN '2' ELSE '0' END || b.id,
               CASE b.deposit_kind WHEN 'pet' THEN 'pet deposit' WHEN 'key' THEN 'key deposit'
                                   ELSE 'deposit' END,
               b.tenant_name, b.property_name, b.balance
        FROM balances AS b
        WHERE b.kind = 'tenant_deposit' AND b.balance <> 0
        UNION ALL
        SELECT 1, b.bank_name, b.bank_account_id, 1, NULL, NULL, 'deposits held', NULL, NULL,
               sum(b.balance) FILTER (WHERE b.kind = 'tenant_deposit')
        FROM balances AS b
        GROUP BY b.bank_name, b.bank_account_id
        UNION ALL
        SELECT 1, b.bank_name, b.bank_account_id, 2, NULL, NULL, 'book cash', NULL, NULL,
               -sum(b.balance) FILTER (WHERE b.kind = 'bank_cash')
        FROM balances AS b
        GROUP BY b.bank_name, b.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total held', NULL, NULL,
               coalesce(sum(b.balance) FILTER (WHERE b.kind = 'tenant_deposit'), 0)
        FROM balances AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.tenant_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the tenant column of the first two lines.
           l.label,
           l.property_name,
           round(coalesce(l.held, CASE WHEN l.section > 0 THEN 0 END), 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- The statement names each deposit by its kind: what was held and returned of a tenant's pet or
-- key deposit, beside their security deposit. Otherwise as in migration 0091.
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
    WITH kinds AS (
        -- Each line with its tenant and the kind of deposit it came from (the transfer's).
        SELECT x.*, tn.display_name || CASE a.deposit_kind
                                          WHEN 'pet' THEN ' (pet deposit)'
                                          WHEN 'key' THEN ' (key deposit)'
                                          ELSE '' END AS whose
        FROM trust_deposit_return_lines AS x
        JOIN trust_tenants AS tn ON tn.id = x.tenant_id
        JOIN pgledger_transfers AS tr ON tr.id = x.transfer_id
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = tr.from_account_id
        WHERE x.lease_id = p_lease_id
    ),
    held AS (
        -- Each tenant's deposit of each kind when it was returned: what was kept and returned.
        SELECT k.whose AS tenant, sum(k.amount) AS amount
        FROM kinds AS k
        GROUP BY k.tenant_id, k.whose
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
        SELECT 4, 0, 'returned to', k.whose, k.amount, NULL, k.line_no
        FROM kinds AS k
        WHERE k.charge_id IS NULL
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
    RAISE EXCEPTION 'deposit_kinds cannot be rolled back: deposits of a kind are held in trust';
END;
$$;
