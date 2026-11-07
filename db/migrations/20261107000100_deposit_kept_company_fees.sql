-- migrate:up
-- A deposit kept for a fee the company keeps goes to the company, and the owner's balance sheet
-- leaves such a fee out of what tenants owe the owner (CLAUDE.md, "Known gaps" of the chart of
-- accounts, migration 20261106000220).
--
--   trust_return_deposit              what a deposit keeps for a fee whose type the company
--                                     keeps (trust_charge_payee) goes into the company's fee
--                                     account in the trust bank account that holds the owner's
--                                     money for the lease's property, its cash with it, as a
--                                     tenant's payment does (rent.split); the owner's account
--                                     gets the rest. A lease with no such fee keeps as before.
--   trust_report_owner_balance_sheet  "owed by tenants" counts no fee the company keeps, as the
--                                     accrual income statement leaves them out.
--   trust_open_charges                a lease end's credit for a fee the company keeps comes off
--   trust_report_delinquency          that fee first, as a waiver does, never off the owner's
--   (and the balance sheet)           rent: a tenant's payment after a mid-month end paid the
--                                     company the whole fee and the owner less rent. What such
--                                     a credit can't take off a fee already paid still comes off
--                                     the rent (docs/edge-cases.md, M-153).

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

CREATE OR REPLACE FUNCTION trust_report_owner_balance_sheet(
    p_pmc_id uuid, p_owner_id uuid, p_as_of date
) RETURNS TABLE (line integer, property text, item text, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz;
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: an owner''s balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_cutoff := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each account holds in trust at the day's end.
    cash AS (
        SELECT a.account,
               coalesce((SELECT sum(e.amount)
                         FROM pgledger_entries AS e
                         JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                         WHERE e.account_id = a.account AND tr.event_at < v_cutoff), 0) AS amount
        FROM accounts AS a
    ),
    -- Owed at the day's end of what was due by it: by tenants, what is still owed of each charge
    -- as the delinquency report counts it (written out as it is there, since the AI role calls
    -- no function but the reports), and by housing authorities, what is open of their part of
    -- the rent and its adjustments (migration 0095).
    paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    waived AS (
        -- What waivers due by the day take off the charge each one names (migration 0079), and
        -- so do a lease end's credits for fees the company keeps: the company's, never the
        -- owner's rent (migration 20261107000100; who keeps a fee is its type's payee when it
        -- was charged, as trust_charge_payee has it, written out: the AI role runs this report).
        SELECT g.id, sum(g.amount) AS amount
        FROM (
            SELECT w.waived_charge_id AS id, k.amount
            FROM trust_charge_waivers AS w
            JOIN trust_charges AS k ON k.id = w.credit_charge_id
            WHERE w.pmc_id = p_pmc_id AND k.due_on <= p_as_of
            UNION ALL
            SELECT a.charge_id, k.amount
            FROM trust_lease_end_adjustments AS a
            JOIN trust_charges AS k ON k.id = a.adjustment_charge_id
            JOIN trust_charges AS src ON src.id = a.charge_id
            LEFT JOIN trust_recurring_charges AS rc ON rc.id = src.recurring_charge_id
            WHERE k.pmc_id = p_pmc_id AND k.kind = 'credit' AND k.due_on <= p_as_of
              AND coalesce((
                  SELECT py.payee FROM trust_charge_type_payees AS py
                  WHERE py.charge_type_id = coalesce(src.charge_type_id, rc.charge_type_id)
                    AND py.created_at <= src.created_at
                  ORDER BY py.id DESC
                  LIMIT 1
              ), 'owner') = 'company'
        ) AS g
        GROUP BY g.id
    ),
    settled AS (
        -- Each charge less what was paid of it and what its own waivers take (up to what was
        -- left), and what of its waivers it could not take.
        -- Each charge's place, as trust_pay_rank gives it (written out here: the AI role runs
        -- this report and calls no function but the reports).
        SELECT c.id, c.lease_id, c.due_on, c.kind, c.created_at,
               CASE
                   WHEN c.kind = 'rent' THEN 0
                   WHEN c.kind = 'fee' AND (
                       c.recurring_charge_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1 FROM trust_lease_end_adjustments AS x
                           JOIN trust_charges AS o ON o.id = x.charge_id
                           WHERE x.adjustment_charge_id = c.id
                             AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                       )
                   ) THEN 1
                   ELSE 2
               END AS pay_rank,
               c.amount - p.amount
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0)
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS spare
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN waived AS w ON w.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit'
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, rent first (migration 0093).
        SELECT s.id, s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM settled AS s
        WHERE s.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day that waive
        -- nothing, what a waiver could not take off its own charge, and payments by then toward
        -- charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of
                         AND NOT EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                                         WHERE w.credit_charge_id = c.id)
                         -- nor a lease end's credit for a fee the company keeps (above)
                         AND NOT EXISTS (
                             SELECT 1 FROM trust_lease_end_adjustments AS x
                             JOIN trust_charges AS src ON src.id = x.charge_id
                             LEFT JOIN trust_recurring_charges AS rc
                                 ON rc.id = src.recurring_charge_id
                             WHERE x.adjustment_charge_id = c.id
                               AND coalesce((
                                   SELECT py.payee FROM trust_charge_type_payees AS py
                                   WHERE py.charge_type_id
                                         = coalesce(src.charge_type_id, rc.charge_type_id)
                                     AND py.created_at <= src.created_at
                                   ORDER BY py.id DESC
                                   LIMIT 1
                               ), 'owner') = 'company'
                         )
                        THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
               + coalesce(sum(s.spare), 0)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN settled AS s ON s.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.id, o.lease_id, o.due_on,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),

    owed AS (
        SELECT u.property_id, g.due_on, g.owed AS amount, 1 AS ord
        FROM aged AS g
        JOIN trust_leases AS l ON l.id = g.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE u.property_id IN (SELECT a.property_id FROM accounts AS a)
          -- A fee the company keeps is owed to the company, never the owner (migration
          -- 20261107000100): who keeps it is its type's payee when it was charged, as
          -- trust_charge_payee has it (written out here: the AI role runs this report and calls
          -- no function but the reports).
          AND coalesce((
              SELECT py.payee
              FROM trust_charges AS src
              LEFT JOIN trust_recurring_charges AS rc ON rc.id = src.recurring_charge_id
              JOIN trust_charge_type_payees AS py
                  ON py.charge_type_id = coalesce(src.charge_type_id, rc.charge_type_id)
                 AND py.created_at <= src.created_at
              WHERE src.id = coalesce(
                  (SELECT x.charge_id FROM trust_lease_end_adjustments AS x
                   WHERE x.adjustment_charge_id = g.id),
                  g.id
              )
              ORDER BY py.id DESC
              LIMIT 1
          ), 'owner') = 'owner'
        UNION ALL
        SELECT u.property_id, ac.due_on,
               ac.amount
               + coalesce((SELECT sum(CASE x.kind WHEN 'credit' THEN -x.amount ELSE x.amount END)
                           FROM trust_assistance_charges AS x
                           WHERE x.adjusts_id = ac.id AND x.due_on <= p_as_of), 0)
               - coalesce((SELECT sum(p.amount)
                           FROM trust_assistance_payments AS p
                           JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                           WHERE p.assistance_charge_id = ac.id AND tr.event_at < v_cutoff), 0),
               2
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.kind = 'charge' AND ac.adjusts_id IS NULL
          AND ac.due_on <= p_as_of
          AND u.property_id IN (SELECT a.property_id FROM accounts AS a)
    ),
    -- Each account's part of what is owed: its share of its property on the due day
    -- (migration 0090; all of it when the property has no shares), to the cent.
    receivable AS (
        SELECT a.account, x.ord,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        WHERE x.amount > 0
    ),
    -- The account's bills dated by the day and not yet set aside by its end, with any markup
    -- added to them (migration 0083), but those the owner declined by then (migration 0041).
    payable AS (
        SELECT b.ledger_account_id AS account,
               b.amount + coalesce((SELECT m.amount FROM trust_bill_markups AS m
                                    WHERE m.bill_id = b.id), 0) AS amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_as_of)
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS bp
                          JOIN pgledger_transfers AS tr ON tr.id = bp.transfer_id
                          WHERE bp.bill_id = b.id AND bp.step = 'set_aside'
                            AND tr.event_at < v_cutoff)
    ),
    -- Each account's lines, in order: what it holds, what is owed to it, what it owes, and
    -- what is the owner's.
    items AS (
        SELECT c.account, 1 AS ord, 'cash held in trust' AS item, c.amount AS total FROM cash AS c
        UNION ALL
        SELECT a.account, 1 + k.ord, k.item,
               coalesce((SELECT sum(r.amount) FROM receivable AS r
                         WHERE r.account = a.account AND r.ord = k.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (1, 'owed by tenants'), (2, 'owed by housing authorities'))
            AS k (ord, item)
        UNION ALL
        SELECT a.account, 4, 'bills not yet set aside',
               -coalesce((SELECT sum(p.amount) FROM payable AS p WHERE p.account = a.account), 0)
        FROM accounts AS a
    ),
    rows AS (
        SELECT i.account, i.ord, i.item, i.total FROM items AS i
        UNION ALL
        SELECT i.account, 5, 'owner''s equity', sum(i.total) FROM items AS i GROUP BY i.account
    ),
    lines AS (
        -- (section, property, account, order) orders the sheet; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS ord, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'as of',
               p_as_of::text || ', end of day UTC, accrual basis', NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.ord, r.item, a.name, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.ord, 'all properties: ' || r.item, NULL, sum(r.total)
        FROM rows AS r
        GROUP BY r.ord, r.item
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.ord
           )::integer,
           -- The PMC's, owner's and day's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamp with time zone) RETURNS TABLE(lease_id uuid, charge_id uuid, open numeric)
    LANGUAGE sql STABLE ROWS 20
    SET search_path TO 'public', 'pg_temp'
    SET jit TO 'off'
    AS $$
    WITH mine AS (
        -- One lease's charges by its index, or, given no lease, every charge of the PMC: each
        -- branch runs only when its parameter says so.
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at,
               trust_pay_rank(c) AS pay_rank
        FROM trust_charges AS c
        WHERE p_lease_id IS NOT NULL AND c.lease_id = p_lease_id AND c.pmc_id = p_pmc_id
        UNION ALL
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at, trust_pay_rank(c)
        FROM trust_charges AS c
        WHERE p_lease_id IS NULL AND c.pmc_id = p_pmc_id
    ),
    charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at, c.pay_rank,
               c.amount
               - coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND t.event_at < p_at
               ), 0)
               + coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                   WHERE r.charge_id = c.id AND t.event_at < p_at
               ), 0) AS left_owing,
               EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                       WHERE w.credit_charge_id = c.id) AS waiver,
               -- A lease end's credit for a fee the company keeps (migration 20261107000100).
               c.kind = 'credit' AND EXISTS (
                   SELECT 1 FROM trust_lease_end_adjustments AS x
                   JOIN trust_charges AS o ON o.id = x.charge_id
                   WHERE x.adjustment_charge_id = c.id AND trust_charge_payee(o) = 'company'
               ) AS company_end
        FROM mine AS c
    ),
    -- What waivers due before the moment's day take off the charge each one names, and so do a
    -- lease end's credits for fees the company keeps: the company's, never the owner's rent
    -- (migration 20261107000100).
    waived AS (
        SELECT x.id, sum(x.amount) AS amount
        FROM (
            SELECT w.waived_charge_id AS id, k.amount
            FROM charged AS k
            JOIN trust_charge_waivers AS w ON w.credit_charge_id = k.id
            WHERE k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT a.charge_id, k.amount
            FROM charged AS k
            JOIN trust_lease_end_adjustments AS a ON a.adjustment_charge_id = k.id
            WHERE k.company_end AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
        ) AS x
        GROUP BY x.id
    ),
    -- Each charge after its own waivers, and what of them it could not take.
    settled AS (
        SELECT k.lease_id, k.id, k.due_on, k.created_at, k.pay_rank,
               k.left_owing - least(greatest(k.left_owing, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0) - least(greatest(k.left_owing, 0), coalesce(w.amount, 0))
                   AS spare
        FROM charged AS k
        LEFT JOIN waived AS w ON w.id = k.id
        WHERE k.kind <> 'credit'
    ),
    -- The rest come off the open charges rent first (migration 0093): credits that waive nothing,
    -- and what a waiver could not take off its own charge.
    credits AS (
        SELECT x.lease_id, sum(x.amount) AS amount
        FROM (
            SELECT k.lease_id, k.amount FROM charged AS k
            WHERE k.kind = 'credit' AND NOT k.waiver AND NOT k.company_end
              AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT s.lease_id, s.spare FROM settled AS s WHERE s.spare > 0
        ) AS x
        GROUP BY x.lease_id
    ),
    owing AS (
        SELECT s.lease_id, s.id, s.left_owing,
               coalesce(sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM settled AS s
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;

CREATE OR REPLACE FUNCTION trust_report_delinquency(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, tenants text, current_due numeric, days_1_30 numeric, days_31_60 numeric, days_61_90 numeric, over_90 numeric, total numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: delinquency is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    waived AS (
        -- What waivers due by the day take off the charge each one names (migration 0079), and
        -- so do a lease end's credits for fees the company keeps: the company's, never the
        -- owner's rent (migration 20261107000100; who keeps a fee is its type's payee when it
        -- was charged, as trust_charge_payee has it, written out: the AI role runs this report).
        SELECT g.id, sum(g.amount) AS amount
        FROM (
            SELECT w.waived_charge_id AS id, k.amount
            FROM trust_charge_waivers AS w
            JOIN trust_charges AS k ON k.id = w.credit_charge_id
            WHERE w.pmc_id = p_pmc_id AND k.due_on <= p_as_of
            UNION ALL
            SELECT a.charge_id, k.amount
            FROM trust_lease_end_adjustments AS a
            JOIN trust_charges AS k ON k.id = a.adjustment_charge_id
            JOIN trust_charges AS src ON src.id = a.charge_id
            LEFT JOIN trust_recurring_charges AS rc ON rc.id = src.recurring_charge_id
            WHERE k.pmc_id = p_pmc_id AND k.kind = 'credit' AND k.due_on <= p_as_of
              AND coalesce((
                  SELECT py.payee FROM trust_charge_type_payees AS py
                  WHERE py.charge_type_id = coalesce(src.charge_type_id, rc.charge_type_id)
                    AND py.created_at <= src.created_at
                  ORDER BY py.id DESC
                  LIMIT 1
              ), 'owner') = 'company'
        ) AS g
        GROUP BY g.id
    ),
    settled AS (
        -- Each charge less what was paid of it and what its own waivers take (up to what was
        -- left), and what of its waivers it could not take.
        -- Each charge's place, as trust_pay_rank gives it (written out here: the AI role runs
        -- this report and calls no function but the reports).
        SELECT c.id, c.lease_id, c.due_on, c.kind, c.created_at,
               CASE
                   WHEN c.kind = 'rent' THEN 0
                   WHEN c.kind = 'fee' AND (
                       c.recurring_charge_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1 FROM trust_lease_end_adjustments AS x
                           JOIN trust_charges AS o ON o.id = x.charge_id
                           WHERE x.adjustment_charge_id = c.id
                             AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                       )
                   ) THEN 1
                   ELSE 2
               END AS pay_rank,
               c.amount - p.amount
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0)
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS spare
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN waived AS w ON w.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit'
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, rent first (migration 0093).
        SELECT s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM settled AS s
        WHERE s.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day that waive
        -- nothing, what a waiver could not take off its own charge, and payments by then toward
        -- charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of
                         AND NOT EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                                         WHERE w.credit_charge_id = c.id)
                         -- nor a lease end's credit for a fee the company keeps (above)
                         AND NOT EXISTS (
                             SELECT 1 FROM trust_lease_end_adjustments AS x
                             JOIN trust_charges AS src ON src.id = x.charge_id
                             LEFT JOIN trust_recurring_charges AS rc
                                 ON rc.id = src.recurring_charge_id
                             WHERE x.adjustment_charge_id = c.id
                               AND coalesce((
                                   SELECT py.payee FROM trust_charge_type_payees AS py
                                   WHERE py.charge_type_id
                                         = coalesce(src.charge_type_id, rc.charge_type_id)
                                     AND py.created_at <= src.created_at
                                   ORDER BY py.id DESC
                                   LIMIT 1
                               ), 'owner') = 'company'
                         )
                        THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
               + coalesce(sum(s.spare), 0)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN settled AS s ON s.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.lease_id, p_as_of - o.due_on AS late,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),
    by_lease AS (
        SELECT a.lease_id,
               sum(a.owed) FILTER (WHERE a.late = 0) AS b0,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 1 AND 30) AS b1,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 31 AND 60) AS b2,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 61 AND 90) AS b3,
               sum(a.owed) FILTER (WHERE a.late > 90) AS b4,
               sum(a.owed) AS owed
        FROM aged AS a
        GROUP BY a.lease_id
        HAVING sum(a.owed) > 0
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::date AS starts_on, 1 AS step, 'PMC' AS item, pmc.display_name AS names,
               NULL::numeric AS b0, NULL::numeric AS b1, NULL::numeric AS b2,
               NULL::numeric AS b3, NULL::numeric AS b4, NULL::numeric AS owed
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, pr.display_name, u.display_name, l.starts_on, 0, 'lease',
               (SELECT string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C",
                                  tn.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
                WHERE lt.lease_id = l.id),
               coalesce(b.b0, 0), coalesce(b.b1, 0), coalesce(b.b2, 0), coalesce(b.b3, 0),
               coalesce(b.b4, 0), b.owed
        FROM by_lease AS b
        JOIN trust_leases AS l ON l.id = b.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(b.b0), 0), coalesce(sum(b.b1), 0), coalesce(sum(b.b2), 0),
               coalesce(sum(b.b3), 0), coalesce(sum(b.b4), 0), coalesce(sum(b.owed), 0)
        FROM by_lease AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.starts_on, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           -- The PMC's name and the day sit in the tenants column of the first two lines.
           l.names,
           round(l.b0, 2), round(l.b1, 2), round(l.b2, 2), round(l.b3, 2), round(l.b4, 2),
           round(l.owed, 2)
    FROM lines AS l
    ORDER BY 1;
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
                    -- This lease's part goes to its owners by their shares, a lease moved from
                    -- to the owner's account its move names.
                    FOR v_pay IN
                        SELECT s.ledger_account_id COLLATE "C" AS account, s.amount
                        FROM trust_split_by_ownership(v_property, v_day, v_piece) AS s
                        WHERE v_split AND i = cardinality(v_leases)
                        UNION ALL
                        SELECT v_accounts[i] COLLATE "C", v_piece
                        WHERE NOT (v_split AND i = cardinality(v_leases))
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

CREATE OR REPLACE FUNCTION trust_report_owner_balance_sheet(
    p_pmc_id uuid, p_owner_id uuid, p_as_of date
) RETURNS TABLE (line integer, property text, item text, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz;
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: an owner''s balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_cutoff := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each account holds in trust at the day's end.
    cash AS (
        SELECT a.account,
               coalesce((SELECT sum(e.amount)
                         FROM pgledger_entries AS e
                         JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                         WHERE e.account_id = a.account AND tr.event_at < v_cutoff), 0) AS amount
        FROM accounts AS a
    ),
    -- Owed at the day's end of what was due by it: by tenants, what is still owed of each charge
    -- as the delinquency report counts it (written out as it is there, since the AI role calls
    -- no function but the reports), and by housing authorities, what is open of their part of
    -- the rent and its adjustments (migration 0095).
    paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    waived AS (
        -- What waivers due by the day take off the charge each one names (migration 0079).
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM trust_charge_waivers AS w
        JOIN trust_charges AS k ON k.id = w.credit_charge_id
        WHERE w.pmc_id = p_pmc_id AND k.due_on <= p_as_of
        GROUP BY w.waived_charge_id
    ),
    settled AS (
        -- Each charge less what was paid of it and what its own waivers take (up to what was
        -- left), and what of its waivers it could not take.
        -- Each charge's place, as trust_pay_rank gives it (written out here: the AI role runs
        -- this report and calls no function but the reports).
        SELECT c.id, c.lease_id, c.due_on, c.kind, c.created_at,
               CASE
                   WHEN c.kind = 'rent' THEN 0
                   WHEN c.kind = 'fee' AND (
                       c.recurring_charge_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1 FROM trust_lease_end_adjustments AS x
                           JOIN trust_charges AS o ON o.id = x.charge_id
                           WHERE x.adjustment_charge_id = c.id
                             AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                       )
                   ) THEN 1
                   ELSE 2
               END AS pay_rank,
               c.amount - p.amount
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0)
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS spare
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN waived AS w ON w.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit'
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, rent first (migration 0093).
        SELECT s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM settled AS s
        WHERE s.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day that waive
        -- nothing, what a waiver could not take off its own charge, and payments by then toward
        -- charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of
                         AND NOT EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                                         WHERE w.credit_charge_id = c.id)
                        THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
               + coalesce(sum(s.spare), 0)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN settled AS s ON s.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.lease_id, o.due_on,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),

    owed AS (
        SELECT u.property_id, g.due_on, g.owed AS amount, 1 AS ord
        FROM aged AS g
        JOIN trust_leases AS l ON l.id = g.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE u.property_id IN (SELECT a.property_id FROM accounts AS a)
        UNION ALL
        SELECT u.property_id, ac.due_on,
               ac.amount
               + coalesce((SELECT sum(CASE x.kind WHEN 'credit' THEN -x.amount ELSE x.amount END)
                           FROM trust_assistance_charges AS x
                           WHERE x.adjusts_id = ac.id AND x.due_on <= p_as_of), 0)
               - coalesce((SELECT sum(p.amount)
                           FROM trust_assistance_payments AS p
                           JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                           WHERE p.assistance_charge_id = ac.id AND tr.event_at < v_cutoff), 0),
               2
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.kind = 'charge' AND ac.adjusts_id IS NULL
          AND ac.due_on <= p_as_of
          AND u.property_id IN (SELECT a.property_id FROM accounts AS a)
    ),
    -- Each account's part of what is owed: its share of its property on the due day
    -- (migration 0090; all of it when the property has no shares), to the cent.
    receivable AS (
        SELECT a.account, x.ord,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        WHERE x.amount > 0
    ),
    -- The account's bills dated by the day and not yet set aside by its end, with any markup
    -- added to them (migration 0083), but those the owner declined by then (migration 0041).
    payable AS (
        SELECT b.ledger_account_id AS account,
               b.amount + coalesce((SELECT m.amount FROM trust_bill_markups AS m
                                    WHERE m.bill_id = b.id), 0) AS amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_as_of)
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS bp
                          JOIN pgledger_transfers AS tr ON tr.id = bp.transfer_id
                          WHERE bp.bill_id = b.id AND bp.step = 'set_aside'
                            AND tr.event_at < v_cutoff)
    ),
    -- Each account's lines, in order: what it holds, what is owed to it, what it owes, and
    -- what is the owner's.
    items AS (
        SELECT c.account, 1 AS ord, 'cash held in trust' AS item, c.amount AS total FROM cash AS c
        UNION ALL
        SELECT a.account, 1 + k.ord, k.item,
               coalesce((SELECT sum(r.amount) FROM receivable AS r
                         WHERE r.account = a.account AND r.ord = k.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (1, 'owed by tenants'), (2, 'owed by housing authorities'))
            AS k (ord, item)
        UNION ALL
        SELECT a.account, 4, 'bills not yet set aside',
               -coalesce((SELECT sum(p.amount) FROM payable AS p WHERE p.account = a.account), 0)
        FROM accounts AS a
    ),
    rows AS (
        SELECT i.account, i.ord, i.item, i.total FROM items AS i
        UNION ALL
        SELECT i.account, 5, 'owner''s equity', sum(i.total) FROM items AS i GROUP BY i.account
    ),
    lines AS (
        -- (section, property, account, order) orders the sheet; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS ord, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'as of',
               p_as_of::text || ', end of day UTC, accrual basis', NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.ord, r.item, a.name, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.ord, 'all properties: ' || r.item, NULL, sum(r.total)
        FROM rows AS r
        GROUP BY r.ord, r.item
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.ord
           )::integer,
           -- The PMC's, owner's and day's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamp with time zone) RETURNS TABLE(lease_id uuid, charge_id uuid, open numeric)
    LANGUAGE sql STABLE ROWS 20
    SET search_path TO 'public', 'pg_temp'
    SET jit TO 'off'
    AS $$
    WITH mine AS (
        -- One lease's charges by its index, or, given no lease, every charge of the PMC: each
        -- branch runs only when its parameter says so.
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at,
               trust_pay_rank(c) AS pay_rank
        FROM trust_charges AS c
        WHERE p_lease_id IS NOT NULL AND c.lease_id = p_lease_id AND c.pmc_id = p_pmc_id
        UNION ALL
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at, trust_pay_rank(c)
        FROM trust_charges AS c
        WHERE p_lease_id IS NULL AND c.pmc_id = p_pmc_id
    ),
    charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at, c.pay_rank,
               c.amount
               - coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND t.event_at < p_at
               ), 0)
               + coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                   WHERE r.charge_id = c.id AND t.event_at < p_at
               ), 0) AS left_owing,
               EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                       WHERE w.credit_charge_id = c.id) AS waiver
        FROM mine AS c
    ),
    -- What waivers due before the moment's day take off the charge each one names.
    waived AS (
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM charged AS k
        JOIN trust_charge_waivers AS w ON w.credit_charge_id = k.id
        WHERE k.due_on < (p_at AT TIME ZONE 'UTC')::date
        GROUP BY w.waived_charge_id
    ),
    -- Each charge after its own waivers, and what of them it could not take.
    settled AS (
        SELECT k.lease_id, k.id, k.due_on, k.created_at, k.pay_rank,
               k.left_owing - least(greatest(k.left_owing, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0) - least(greatest(k.left_owing, 0), coalesce(w.amount, 0))
                   AS spare
        FROM charged AS k
        LEFT JOIN waived AS w ON w.id = k.id
        WHERE k.kind <> 'credit'
    ),
    -- The rest come off the open charges rent first (migration 0093): credits that waive nothing,
    -- and what a waiver could not take off its own charge.
    credits AS (
        SELECT x.lease_id, sum(x.amount) AS amount
        FROM (
            SELECT k.lease_id, k.amount FROM charged AS k
            WHERE k.kind = 'credit' AND NOT k.waiver
              AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT s.lease_id, s.spare FROM settled AS s WHERE s.spare > 0
        ) AS x
        GROUP BY x.lease_id
    ),
    owing AS (
        SELECT s.lease_id, s.id, s.left_owing,
               coalesce(sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM settled AS s
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;

CREATE OR REPLACE FUNCTION trust_report_delinquency(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, tenants text, current_due numeric, days_1_30 numeric, days_31_60 numeric, days_61_90 numeric, over_90 numeric, total numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: delinquency is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    waived AS (
        -- What waivers due by the day take off the charge each one names (migration 0079).
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM trust_charge_waivers AS w
        JOIN trust_charges AS k ON k.id = w.credit_charge_id
        WHERE w.pmc_id = p_pmc_id AND k.due_on <= p_as_of
        GROUP BY w.waived_charge_id
    ),
    settled AS (
        -- Each charge less what was paid of it and what its own waivers take (up to what was
        -- left), and what of its waivers it could not take.
        -- Each charge's place, as trust_pay_rank gives it (written out here: the AI role runs
        -- this report and calls no function but the reports).
        SELECT c.id, c.lease_id, c.due_on, c.kind, c.created_at,
               CASE
                   WHEN c.kind = 'rent' THEN 0
                   WHEN c.kind = 'fee' AND (
                       c.recurring_charge_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1 FROM trust_lease_end_adjustments AS x
                           JOIN trust_charges AS o ON o.id = x.charge_id
                           WHERE x.adjustment_charge_id = c.id
                             AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                       )
                   ) THEN 1
                   ELSE 2
               END AS pay_rank,
               c.amount - p.amount
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0)
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS spare
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN waived AS w ON w.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit'
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, rent first (migration 0093).
        SELECT s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM settled AS s
        WHERE s.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day that waive
        -- nothing, what a waiver could not take off its own charge, and payments by then toward
        -- charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of
                         AND NOT EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                                         WHERE w.credit_charge_id = c.id)
                        THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
               + coalesce(sum(s.spare), 0)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN settled AS s ON s.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.lease_id, p_as_of - o.due_on AS late,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),
    by_lease AS (
        SELECT a.lease_id,
               sum(a.owed) FILTER (WHERE a.late = 0) AS b0,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 1 AND 30) AS b1,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 31 AND 60) AS b2,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 61 AND 90) AS b3,
               sum(a.owed) FILTER (WHERE a.late > 90) AS b4,
               sum(a.owed) AS owed
        FROM aged AS a
        GROUP BY a.lease_id
        HAVING sum(a.owed) > 0
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::date AS starts_on, 1 AS step, 'PMC' AS item, pmc.display_name AS names,
               NULL::numeric AS b0, NULL::numeric AS b1, NULL::numeric AS b2,
               NULL::numeric AS b3, NULL::numeric AS b4, NULL::numeric AS owed
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, pr.display_name, u.display_name, l.starts_on, 0, 'lease',
               (SELECT string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C",
                                  tn.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
                WHERE lt.lease_id = l.id),
               coalesce(b.b0, 0), coalesce(b.b1, 0), coalesce(b.b2, 0), coalesce(b.b3, 0),
               coalesce(b.b4, 0), b.owed
        FROM by_lease AS b
        JOIN trust_leases AS l ON l.id = b.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(b.b0), 0), coalesce(sum(b.b1), 0), coalesce(sum(b.b2), 0),
               coalesce(sum(b.b3), 0), coalesce(sum(b.b4), 0), coalesce(sum(b.owed), 0)
        FROM by_lease AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.starts_on, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           -- The PMC's name and the day sit in the tenants column of the first two lines.
           l.names,
           round(l.b0, 2), round(l.b1, 2), round(l.b2, 2), round(l.b3, 2), round(l.b4, 2),
           round(l.owed, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;
