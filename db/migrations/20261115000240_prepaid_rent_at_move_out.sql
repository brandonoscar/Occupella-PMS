-- migrate:up
-- Prepaid rent left over at move-out (docs/edge-cases.md M-072).
--
-- The deposit's return kept what the leases owed from the tenants' deposits while their prepaid
-- rent, their own money held in trust for rent to come, sat untouched: never used for what was
-- owed, never paid back, held for good for people no longer renting. Now trust_return_deposit
-- takes the tenants' prepaid rent first, for what the lease and the leases it carries owe (rent
-- first, migration 0093; each part for its own owners, a fee the company keeps to its fee
-- account), then keeps what is still owed from their deposits as before, and pays back what is
-- left of each, their prepaid rent out of its own trust bank account's cash. A lease whose
-- tenants hold prepaid rent but no deposit is returned too.
--
--   trust_deposit_returns              prepaid_used and prepaid_returned beside held, kept and
--                                      returned (the deposits'), each 0 on every return before
--                                      this; held may be 0 when prepaid rent was held.
--   trust_check_deposit_return_signer  prepaid rent going back is a payout from its trust bank
--                                      account, held to its signers (migration 0060).
--   trust_report_deposit_return        the statement names prepaid rent held, used and returned.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_deposit_returns ADD COLUMN prepaid_used numeric NOT NULL DEFAULT 0;
ALTER TABLE trust_deposit_returns ADD COLUMN prepaid_returned numeric NOT NULL DEFAULT 0;
-- Every return there is used and returned none (the defaults), so each holds the checks.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_deposit_returns ADD CONSTRAINT trust_deposit_returns_prepaid_check
CHECK (prepaid_used >= 0 AND prepaid_returned >= 0);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_deposit_returns DROP CONSTRAINT trust_deposit_returns_held_check;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_deposit_returns ADD CONSTRAINT trust_deposit_returns_held_check
CHECK (held >= 0 AND held + prepaid_used + prepaid_returned > 0);

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
    -- The tenants' prepaid rent, and what of it pays what the leases owe (M-072).
    v_prepaid numeric;
    v_used numeric;
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
    -- Their prepaid rent (migration 20261115000240, docs/edge-cases.md M-072): it pays what the
    -- leases owe before any deposit is kept, and what is left of it goes back with the deposits.
    SELECT coalesce(sum(a.balance), 0) INTO v_prepaid FROM pgledger_accounts AS a
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = a.id
    JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
    WHERE lt.lease_id = p_lease_id AND t.kind = 'prepaid_rent';
    IF v_held <= 0 AND v_prepaid <= 0 THEN
        RAISE EXCEPTION 'trust: no security deposit is held for lease %', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    v_used := least(
        (SELECT sum(o) FROM unnest(v_rent) AS o) + (SELECT sum(o) FROM unnest(v_fees) AS o),
        v_prepaid
    );
    v_kept := least(
        (SELECT sum(o) FROM unnest(v_rent) AS o) + (SELECT sum(o) FROM unnest(v_fees) AS o)
            - v_used,
        v_held
    );
    -- What is left to take: prepaid rent first, then deposit by deposit.
    v_keep := v_used + v_kept;

    INSERT INTO trust_deposit_returns (
        lease_id, pmc_id, request_key, returned_at, held, kept, returned, returned_by,
        prepaid_used, prepaid_returned
    ) VALUES (
        p_lease_id, p_pmc_id, p_request_key, p_event_at, v_held, v_kept, v_held - v_kept,
        nullif(current_setting('trust.staff_id', true), '')::uuid, v_used, v_prepaid - v_used
    );

    FOR v_deposit IN
        SELECT t.ledger_account_id AS account, t.tenant_id, a.balance,
               t.kind = 'prepaid_rent' AS prepaid,
               (SELECT c.ledger_account_id FROM trust_ledger_accounts AS c
                WHERE c.bank_account_id = t.bank_account_id AND c.kind = 'bank_cash') AS cash
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
        JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        WHERE lt.lease_id = p_lease_id
          AND (t.kind = 'tenant_deposit' OR (t.kind = 'prepaid_rent' AND a.balance > 0))
        -- Every tenant's prepaid rent first (M-072), then each tenant's security deposit, then
        -- their key and pet deposits (0092).
        ORDER BY t.kind <> 'prepaid_rent', tn.display_name COLLATE "C", t.tenant_id,
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
                            ARRAY[(v_deposit.account, v_fee_account, v_company)::transfer_request]
                            -- Its cash moves only from another trust bank account (prepaid rent
                            -- is held in the owner's own, as a rule).
                            || CASE WHEN v_owner_cash = v_deposit.cash
                                    THEN ARRAY[]::transfer_request[]
                                    ELSE ARRAY[(v_owner_cash, v_deposit.cash, v_company)
                                               ::transfer_request] END,
                            p_event_at,
                            jsonb_build_object('memo', CASE WHEN v_deposit.prepaid
                                                            THEN 'Prepaid rent used'
                                                            ELSE 'Security deposit kept' END)
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
                            ARRAY[(v_deposit.account, v_owner.ledger_account_id, v_pay.amount)
                                  ::transfer_request]
                            || CASE WHEN v_owner_cash = v_deposit.cash
                                    THEN ARRAY[]::transfer_request[]
                                    ELSE ARRAY[(v_owner_cash, v_deposit.cash, v_pay.amount)
                                               ::transfer_request] END,
                            p_event_at,
                            jsonb_build_object('memo', CASE WHEN v_deposit.prepaid
                                                            THEN 'Prepaid rent used'
                                                            ELSE 'Security deposit kept' END)
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
            -- Returned: out of the security-deposit trust bank account's cash, prepaid rent out
            -- of its own trust bank account's (M-072).
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_deposit.cash, v_deposit.balance - v_take)
                      ::transfer_request],
                p_event_at,
                jsonb_build_object('memo', CASE WHEN v_deposit.prepaid
                                                THEN 'Prepaid rent returned'
                                                ELSE 'Security deposit returned' END)
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

CREATE OR REPLACE FUNCTION trust_check_deposit_return_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT a.bank_account_id
        FROM trust_lease_tenants AS lt
        JOIN trust_ledger_accounts AS a
          ON a.tenant_id = lt.tenant_id
         -- Prepaid rent going back is a payout from its trust bank account too (M-072).
         AND (a.kind = 'tenant_deposit'
              OR (a.kind = 'prepaid_rent' AND NEW.prepaid_returned > 0))
        WHERE lt.lease_id = NEW.lease_id
        ORDER BY a.bank_account_id
    LOOP
        PERFORM trust_require_signer(v_bank, (NEW.returned_at AT TIME ZONE 'UTC')::date);
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_deposit_return(p_pmc_id uuid, p_lease_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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
        SELECT x.*, tn.display_name || CASE WHEN a.kind = 'prepaid_rent' THEN ' (prepaid rent)'
                                            WHEN a.deposit_kind = 'pet' THEN ' (pet deposit)'
                                            WHEN a.deposit_kind = 'key' THEN ' (key deposit)'
                                            ELSE '' END AS whose,
               a.kind = 'prepaid_rent' AS prepaid
        FROM trust_deposit_return_lines AS x
        JOIN trust_tenants AS tn ON tn.id = x.tenant_id
        JOIN pgledger_transfers AS tr ON tr.id = x.transfer_id
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = tr.from_account_id
        WHERE x.lease_id = p_lease_id
    ),
    held AS (
        -- Each tenant's deposit of each kind when it was returned: what was kept and returned.
        SELECT k.whose AS tenant, k.prepaid, sum(k.amount) AS amount
        FROM kinds AS k
        GROUP BY k.tenant_id, k.whose, k.prepaid
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
        SELECT 1, 0, CASE WHEN h.prepaid THEN 'prepaid rent held' ELSE 'deposit held' END,
               h.tenant, h.amount, h.tenant, NULL
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
        -- What the tenants' prepaid rent paid, and what of it went back (M-072), when any.
        SELECT 3, 2, 'prepaid rent used, total', NULL, v_done.prepaid_used, NULL, NULL
        WHERE v_done.prepaid_used + v_done.prepaid_returned > 0
        UNION ALL
        SELECT 4, 0, 'returned to', k.whose, k.amount, NULL, k.line_no
        FROM kinds AS k
        WHERE k.charge_id IS NULL
        UNION ALL
        SELECT 5, 0, 'returned, total', NULL, v_done.returned, NULL, NULL
        UNION ALL
        SELECT 5, 1, 'prepaid rent returned, total', NULL, v_done.prepaid_returned, NULL, NULL
        WHERE v_done.prepaid_used + v_done.prepaid_returned > 0
        UNION ALL
        SELECT 5, 2, 'returned on', to_char(v_done.returned_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')
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

CREATE OR REPLACE FUNCTION trust_check_deposit_return_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT a.bank_account_id
        FROM trust_lease_tenants AS lt
        JOIN trust_ledger_accounts AS a
          ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
        WHERE lt.lease_id = NEW.lease_id
        ORDER BY a.bank_account_id
    LOOP
        PERFORM trust_require_signer(v_bank, (NEW.returned_at AT TIME ZONE 'UTC')::date);
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_deposit_return(p_pmc_id uuid, p_lease_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
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

-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_deposit_returns DROP CONSTRAINT trust_deposit_returns_held_check;
ALTER TABLE trust_deposit_returns ADD CONSTRAINT trust_deposit_returns_held_check
CHECK (held > 0) NOT VALID;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_deposit_returns DROP CONSTRAINT trust_deposit_returns_prepaid_check;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_deposit_returns DROP COLUMN prepaid_returned;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_deposit_returns DROP COLUMN prepaid_used;
