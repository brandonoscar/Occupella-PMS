-- migrate:up
-- Management fees split by ownership share (the founder's review q044, on 10/21: "management
-- fees aren't split by ownership share; each owner's account keeps its own terms. this needs to
-- be split by ownership"). A property owned in shares (migration 0094) on a fee period's last
-- day takes one management fee for the whole property: its owners' accounts' collections added
-- up, under the one set of terms they share (the fee percent, minimum and flat fee of each share
-- account's agreement in force on the period's first day, which must be the same), then split by
-- the shares in force on that last day, to the cent (trust_split_by_ownership). Each owner's
-- account pays its part, once per period as before, so a minimum or a flat fee is charged once
-- for the property, not once per owner. A property without shares, or an account not in them on
-- that day, takes its own fee as before.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- What an owner's account collected over a fee period for its management fee: rent and recurring
-- fees matched to charges, and a housing authority's part of the rent, less what bounced back.
CREATE FUNCTION trust_management_fee_collected(
    p_account text, p_start timestamptz, p_end timestamptz
) RETURNS numeric
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT sum(cp.amount)
        FROM trust_charge_payments AS cp
        -- Rent, or a fee the rent run made from a recurring charge. (A recurring credit is
        -- never paid, so it has no payments here.)
        JOIN trust_charges AS c ON c.id = cp.charge_id
            AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
        WHERE t.to_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
    -- A housing authority's part of the rent it paid (migration 0095).
    + coalesce((
        SELECT sum(ap.amount)
        FROM trust_assistance_payments AS ap
        JOIN pgledger_transfers AS t ON t.id = ap.transfer_id
        WHERE t.to_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
    - coalesce((
        SELECT sum(r.amount)
        FROM trust_payment_reversals AS r
        JOIN trust_charges AS c ON c.id = r.charge_id
            AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        JOIN pgledger_transfers AS t ON t.id = r.reversal_id
        WHERE t.from_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
$$;
REVOKE ALL ON FUNCTION trust_management_fee_collected(text, timestamptz, timestamptz)
FROM PUBLIC;

CREATE OR REPLACE FUNCTION trust_post_management_fee(
    p_pmc_id uuid,
    p_account text,
    p_period_start date,
    p_period_end date,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_start timestamptz := p_period_start::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := p_period_end::timestamp AT TIME ZONE 'UTC';
    v_taken trust_management_fees;
    v_agreement trust_management_agreements;
    v_theirs trust_management_agreements;
    v_share record;
    v_shared boolean;
    v_collected numeric;
    v_whole_collected numeric := 0;
    v_whole numeric;
    v_fee numeric;
    v_memo text;
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a fee period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.* INTO v_taken
    FROM trust_management_fees AS f
    WHERE f.ledger_account_id = p_account
      AND f.period_start < p_period_end AND p_period_start < f.period_end
    LIMIT 1;
    IF FOUND THEN
        IF (v_taken.period_start, v_taken.period_end) = (p_period_start, p_period_end) THEN
            RETURN v_taken.fee;  -- a retry: the fee already taken
        END IF;
        RAISE EXCEPTION 'trust: the fee for % to % overlaps the one taken for % to %',
            p_period_start, p_period_end, v_taken.period_start, v_taken.period_end
            USING ERRCODE = 'exclusion_violation';
    END IF;

    v_agreement := trust_agreement_on(p_account, p_period_start);
    IF v_agreement.id IS NULL THEN
        RAISE EXCEPTION 'trust: no management agreement for % in force on %',
            p_account, p_period_start
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_collected := trust_management_fee_collected(p_account, v_start, v_end);
    v_memo := format('Management fee %s to %s', p_period_start, p_period_end - 1);

    -- Owned in shares on the period's last day, with this account among them: one fee for the
    -- property, under the terms its owners share, split by their shares (q044).
    v_shared := EXISTS (
        SELECT o.ledger_account_id
        FROM trust_ownership_on(v_account.property_id, p_period_end - 1) AS o
        WHERE o.ledger_account_id = p_account
    );
    IF v_shared THEN
        FOR v_share IN
            SELECT o.ledger_account_id
            FROM trust_ownership_on(v_account.property_id, p_period_end - 1) AS o
        LOOP
            v_theirs := trust_agreement_on(v_share.ledger_account_id, p_period_start);
            IF v_theirs.id IS NULL
               OR (v_theirs.fee_percent, v_theirs.minimum_fee, v_theirs.flat_fee)
                  IS DISTINCT FROM
                  (v_agreement.fee_percent, v_agreement.minimum_fee, v_agreement.flat_fee)
            THEN
                RAISE EXCEPTION 'trust: the owners of this property don''t share the same '
                    'management fee terms on %: give each owner''s account the same fee '
                    'percent, minimum and flat fee', p_period_start
                    USING ERRCODE = 'check_violation';
            END IF;
            v_whole_collected := v_whole_collected
                + trust_management_fee_collected(v_share.ledger_account_id, v_start, v_end);
        END LOOP;
        v_whole := greatest(round(v_agreement.fee_percent / 100 * v_whole_collected, 2),
                            v_agreement.minimum_fee)
                   + v_agreement.flat_fee;
        SELECT coalesce(sum(s.amount), 0) INTO v_fee
        FROM trust_split_by_ownership(v_account.property_id, p_period_end - 1, v_whole) AS s
        WHERE s.ledger_account_id = p_account;
        v_memo := format('%s, this owner''s share of the property''s %s', v_memo, v_whole);
    ELSE
        v_fee := greatest(round(v_agreement.fee_percent / 100 * v_collected, 2),
                          v_agreement.minimum_fee)
                 + v_agreement.flat_fee;
    END IF;

    INSERT INTO trust_management_fees (
        pmc_id, ledger_account_id, period_start, period_end, agreement_id, collected, fee,
        transfer_id
    ) VALUES (
        p_pmc_id, p_account, p_period_start, p_period_end, v_agreement.id, v_collected, v_fee,
        CASE WHEN v_fee > 0 THEN trust_post_fee(v_account, v_fee, p_event_at, v_memo) END
    );
    RETURN v_fee;
END;
$$;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_post_management_fee(
    p_pmc_id uuid,
    p_account text,
    p_period_start date,
    p_period_end date,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_start timestamptz := p_period_start::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := p_period_end::timestamp AT TIME ZONE 'UTC';
    v_taken trust_management_fees;
    v_agreement trust_management_agreements;
    v_collected numeric;
    v_fee numeric;
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a fee period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.* INTO v_taken
    FROM trust_management_fees AS f
    WHERE f.ledger_account_id = p_account
      AND f.period_start < p_period_end AND p_period_start < f.period_end
    LIMIT 1;
    IF FOUND THEN
        IF (v_taken.period_start, v_taken.period_end) = (p_period_start, p_period_end) THEN
            RETURN v_taken.fee;  -- a retry: the fee already taken
        END IF;
        RAISE EXCEPTION 'trust: the fee for % to % overlaps the one taken for % to %',
            p_period_start, p_period_end, v_taken.period_start, v_taken.period_end
            USING ERRCODE = 'exclusion_violation';
    END IF;

    v_agreement := trust_agreement_on(p_account, p_period_start);
    IF v_agreement.id IS NULL THEN
        RAISE EXCEPTION 'trust: no management agreement for % in force on %',
            p_account, p_period_start
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(sum(cp.amount), 0) INTO v_collected
    FROM trust_charge_payments AS cp
    -- Rent, or a fee the rent run made from a recurring charge. (A recurring credit is never
    -- paid, so it has no payments here.)
    JOIN trust_charges AS c ON c.id = cp.charge_id
        AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
    JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    -- A housing authority's part of the rent it paid (migration 0095).
    SELECT v_collected + coalesce(sum(ap.amount), 0) INTO v_collected
    FROM trust_assistance_payments AS ap
    JOIN pgledger_transfers AS t ON t.id = ap.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    SELECT v_collected - coalesce(sum(r.amount), 0) INTO v_collected
    FROM trust_payment_reversals AS r
    JOIN trust_charges AS c ON c.id = r.charge_id
        AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
    JOIN pgledger_transfers AS t ON t.id = r.reversal_id
    WHERE t.from_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;

    v_fee := greatest(round(v_agreement.fee_percent / 100 * v_collected, 2),
                      v_agreement.minimum_fee)
             + v_agreement.flat_fee;

    INSERT INTO trust_management_fees (
        pmc_id, ledger_account_id, period_start, period_end, agreement_id, collected, fee,
        transfer_id
    ) VALUES (
        p_pmc_id, p_account, p_period_start, p_period_end, v_agreement.id, v_collected, v_fee,
        CASE WHEN v_fee > 0 THEN trust_post_fee(
            v_account, v_fee, p_event_at,
            format('Management fee %s to %s', p_period_start, p_period_end - 1)
        ) END
    );
    RETURN v_fee;
END;
$$;

DROP FUNCTION trust_management_fee_collected(text, timestamptz, timestamptz);
