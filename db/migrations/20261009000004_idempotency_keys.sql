-- migrate:up
-- Idempotency keys: a posting retried after a timeout must not post twice.
--
-- The app sends a key it chose with every posting. trust_post_transfers posts the transfers and
-- records the key, per PMC, with exactly what was asked for. When the same key comes back:
--   - with the same transfers, event time and metadata, it returns the original transfers and
--     writes nothing, so a retry is always safe;
--   - with anything different, it refuses: reusing a key for another posting is a bug, and
--     returning the old transfers would hide it.
-- A posting that fails (an overdraft, say) records no key, so it can be retried as it is.
--
-- Two guards: an advisory lock makes a second call with the same key wait for the first and
-- then return its result; the primary key refuses a second row for the key whatever happens.
-- Keys are history like the ledger itself: append-only, and this migration never rolls back.

-- On a live database, give up rather than queue behind a long transaction while holding locks
-- (the foreign key briefly locks trust_pmcs). LOCAL: only for this migration's transaction.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_idempotency_keys (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    idempotency_key text NOT NULL
    CHECK (idempotency_key <> '' AND length(idempotency_key) <= 200),
    -- What was asked for, as trust_post_transfers normalizes it: the transfers in order, the
    -- event time as epoch seconds (so a caller's time zone can't make a retry look different),
    -- and the metadata.
    request jsonb NOT NULL,
    transfer_ids text [] NOT NULL CHECK (cardinality(transfer_ids) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pmc_id, idempotency_key)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_idempotency_keys
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- Only trust_post_transfers writes or reads keys; the app role gets nothing on the table.
REVOKE ALL ON trust_idempotency_keys FROM PUBLIC;

CREATE FUNCTION trust_post_transfers(
    p_pmc_id uuid,
    p_idempotency_key text,
    p_transfers transfer_request [],
    p_event_at timestamptz DEFAULT NULL,
    p_metadata jsonb DEFAULT NULL
) RETURNS SETOF pgledger_transfers_view
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_request jsonb := jsonb_build_object(
        'transfers', to_jsonb(p_transfers),
        'event_at', extract(epoch FROM p_event_at),
        'metadata', p_metadata
    );
    v_existing trust_idempotency_keys;
    v_transfer_ids text[];
BEGIN
    IF coalesce(cardinality(p_transfers), 0) = 0 THEN
        RAISE EXCEPTION 'trust: a posting needs at least one transfer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The key is filed under p_pmc_id, so every account must belong to that PMC. (The transfer
    -- scope trigger already keeps each transfer inside one PMC; this pins which one.)
    IF EXISTS (
        SELECT r.from_account_id
        FROM unnest(p_transfers) AS r
        LEFT JOIN trust_ledger_accounts AS f ON f.ledger_account_id = r.from_account_id
        LEFT JOIN trust_ledger_accounts AS t ON t.ledger_account_id = r.to_account_id
        WHERE f.pmc_id IS DISTINCT FROM p_pmc_id OR t.pmc_id IS DISTINCT FROM p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: a transfer in this posting is not in PMC %', p_pmc_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    -- Same PMC and key: wait here until any earlier call with them commits or rolls back.
    -- (A hash collision with another key only means waiting a little longer.)
    PERFORM pg_advisory_xact_lock(
        hashtext('trust_post_transfers'), hashtext(p_pmc_id || '/' || p_idempotency_key)
    );

    SELECT * INTO v_existing
    FROM trust_idempotency_keys
    WHERE pmc_id = p_pmc_id AND idempotency_key = p_idempotency_key;

    IF NOT FOUND THEN
        SELECT array_agg(t.id ORDER BY t.ordinality) INTO v_transfer_ids
        FROM pgledger_create_transfers(p_transfers, p_event_at, p_metadata)
            WITH ORDINALITY AS t;

        INSERT INTO trust_idempotency_keys (pmc_id, idempotency_key, request, transfer_ids)
        VALUES (p_pmc_id, p_idempotency_key, v_request, v_transfer_ids);
    ELSIF v_existing.request IS DISTINCT FROM v_request THEN
        RAISE EXCEPTION 'trust: idempotency key % was already used for a different posting',
            p_idempotency_key
            USING ERRCODE = 'unique_violation',
                  HINT = 'Use a new key for a new posting; resend a key only to retry.';
    ELSE
        v_transfer_ids := v_existing.transfer_ids;  -- a retry: the original transfers
    END IF;

    RETURN QUERY
    SELECT v.*
    FROM unnest(v_transfer_ids) WITH ORDINALITY AS k (id, n)
    JOIN pgledger_transfers_view AS v ON v.id = k.id
    ORDER BY k.n;
END;
$$;

REVOKE ALL ON FUNCTION trust_post_transfers(
    uuid, text, transfer_request [], timestamptz, jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_post_transfers(
    uuid, text, transfer_request [], timestamptz, jsonb
) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'trust_idempotency_keys cannot be rolled back: keys are ledger history';
END;
$$;
