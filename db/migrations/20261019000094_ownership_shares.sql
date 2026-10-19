-- migrate:up
-- A property owned in shares (the founder's review, q044): each of its owners owns a percent of
-- it, and the money its leases bring in, and a bill split by ownership, go to their accounts by
-- those percents.
--
--   trust_ownership_shares        a property's ownership from a day, naming who recorded it.
--                                 Append-only: new shares are a new row from their day, and the
--                                 one from the latest day on or before a day holds then (the
--                                 latest recorded, if two start the same day).
--   trust_ownership_share_lines   each owner's percent, by their owner's property account at the
--                                 property: more than 0, to 4 decimals. At commit the lines come
--                                 to 100, every account in one trust bank account (a payment goes
--                                 into one bank).
--   trust_ownership_on            the shares in force on a day.
--   trust_split_by_ownership      an amount split by the shares in force on a day, to the cent:
--                                 each owner's percent of it rounded down, then the cents left one
--                                 each to the largest remainders (the account first by id on a
--                                 tie), so the parts always come to the amount.
--   trust_return_deposit          what a lease owes, kept from its deposits at move-out, goes to
--                                 its property's owners by their shares on the day it was kept.
--
-- What splits besides: a tenant's payment and prepaid rent used (src/pms/api/rent.py), and a bill
-- split by ownership, entered as a shared bill with a part per owner (src/pms/api/payables.py).
-- Each owner's management fee, draws, statement, year-end package and 1099 rents then come from
-- their own account, as they do today. A property with no shares on file names one owner's
-- account for each payment, as before. What a lease the tenants moved from still owes goes to the
-- owner's account its move named (migration 0091).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_ownership_shares (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_ownership_shares_property
ON trust_ownership_shares (pmc_id, property_id, starts_on);
CREATE INDEX trust_ownership_shares_recorded_by ON trust_ownership_shares (pmc_id, recorded_by);

CREATE TABLE trust_ownership_share_lines (
    share_id uuid NOT NULL,
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    percent numeric NOT NULL CHECK (percent > 0 AND percent <= 100 AND percent = round(percent, 4)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (share_id, ledger_account_id),
    FOREIGN KEY (pmc_id, share_id) REFERENCES trust_ownership_shares (pmc_id, id)
);
CREATE INDEX trust_ownership_share_lines_account
ON trust_ownership_share_lines (ledger_account_id);
CREATE INDEX trust_ownership_share_lines_share ON trust_ownership_share_lines (pmc_id, share_id);

-- A line names an owner's property account at the shares' property, of the same PMC.
CREATE FUNCTION trust_check_ownership_share_line() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_ownership_shares AS s
        JOIN trust_ledger_accounts AS a ON a.property_id = s.property_id
        WHERE s.id = NEW.share_id AND a.ledger_account_id = NEW.ledger_account_id
          AND a.kind = 'owner_property' AND a.pmc_id = NEW.pmc_id AND s.pmc_id = NEW.pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s account at the property', NEW.ledger_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_ownership_share_line() FROM PUBLIC;

CREATE TRIGGER trust_ownership_share_line BEFORE INSERT ON trust_ownership_share_lines
FOR EACH ROW EXECUTE FUNCTION trust_check_ownership_share_line();

-- At commit, a property's shares come to 100, every account in one trust bank account.
CREATE FUNCTION trust_check_ownership_shares() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_share uuid := coalesce(to_jsonb(NEW) ->> 'share_id', to_jsonb(NEW) ->> 'id')::uuid;
    v_total numeric;
    v_banks bigint;
BEGIN
    SELECT coalesce(sum(l.percent), 0), count(DISTINCT a.bank_account_id)
    INTO v_total, v_banks
    FROM trust_ownership_share_lines AS l
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = l.ledger_account_id
    WHERE l.share_id = v_share;
    IF v_total <> 100 THEN
        RAISE EXCEPTION 'trust: a property''s ownership shares come to 100%%, not %',
            v_total || '%'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_banks <> 1 THEN
        RAISE EXCEPTION 'trust: a property''s owners in shares have their accounts in one trust '
            'bank account'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_ownership_shares() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER trust_ownership_shares_whole AFTER INSERT ON trust_ownership_shares
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_ownership_shares();
CREATE CONSTRAINT TRIGGER trust_ownership_shares_whole AFTER INSERT ON trust_ownership_share_lines
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION trust_check_ownership_shares();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_ownership_shares
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ownership_shares
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_ownership_shares
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_ownership_share_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_ownership_share_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_ownership_shares FROM PUBLIC;
REVOKE ALL ON trust_ownership_share_lines FROM PUBLIC;
GRANT SELECT, INSERT ON trust_ownership_shares, trust_ownership_share_lines TO trust_app;
GRANT SELECT ON trust_ownership_shares, trust_ownership_share_lines TO trust_ai_agent;

-- The shares in force on a day: the latest from a day on or before it.
CREATE FUNCTION trust_ownership_on(p_property_id uuid, p_day date)
RETURNS TABLE (ledger_account_id text, percent numeric)
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT l.ledger_account_id, l.percent
    FROM trust_ownership_share_lines AS l
    WHERE l.share_id = (
        SELECT s.id FROM trust_ownership_shares AS s
        WHERE s.property_id = p_property_id AND s.starts_on <= p_day
        ORDER BY s.starts_on DESC, s.created_at DESC, s.id DESC
        LIMIT 1
    )
    ORDER BY l.ledger_account_id COLLATE "C"
$$;
REVOKE ALL ON FUNCTION trust_ownership_on(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_ownership_on(uuid, date) TO trust_app;

-- An amount split by the shares in force on a day, to the cent: each owner's percent of it
-- rounded down, then the cents left over one each to the largest remainders (the account first
-- by id on a tie). The parts come to the amount; a part of nothing is left out.
CREATE FUNCTION trust_split_by_ownership(p_property_id uuid, p_day date, p_amount numeric)
RETURNS TABLE (ledger_account_id text, amount numeric)
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    WITH exact AS (
        SELECT o.ledger_account_id, round(p_amount * 100) * o.percent / 100 AS cents
        FROM trust_ownership_on(p_property_id, p_day) AS o
        WHERE p_amount > 0
    ), ranked AS (
        SELECT e.ledger_account_id, floor(e.cents) AS whole,
               row_number() OVER (
                   ORDER BY e.cents - floor(e.cents) DESC, e.ledger_account_id COLLATE "C"
               ) AS n,
               round(p_amount * 100) - sum(floor(e.cents)) OVER () AS spare
        FROM exact AS e
    )
    SELECT r.ledger_account_id, (r.whole + CASE WHEN r.n <= r.spare THEN 1 ELSE 0 END) / 100
    FROM ranked AS r
    WHERE r.whole + CASE WHEN r.n <= r.spare THEN 1 ELSE 0 END > 0
    ORDER BY r.ledger_account_id COLLATE "C"
$$;
REVOKE ALL ON FUNCTION trust_split_by_ownership(uuid, date, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_split_by_ownership(uuid, date, numeric) TO trust_app;

-- What a lease owes, kept from its deposits, goes to its property's owners by their shares on
-- the day it was kept. Otherwise as in migration 0093.
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

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'ownership_shares cannot be rolled back: money kept and split by them stands';
END;
$$;
