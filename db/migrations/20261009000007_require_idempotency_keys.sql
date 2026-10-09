-- migrate:up
-- Every posting from the app carries an idempotency key, so every posting is safe to retry.
--
-- Until now trust_app could also call pgledger's own posting functions, which take no key: a
-- request retried after a timeout through them could post twice. From here the app posts only
-- through trust_post_transfers (20261009000004), which runs as the owner and calls pgledger
-- itself. The owner role (migrations, maintenance) still posts directly.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

REVOKE EXECUTE ON FUNCTION pgledger_create_transfer(text, text, numeric, timestamptz, jsonb)
FROM trust_app;
REVOKE EXECUTE ON FUNCTION pgledger_create_transfers(transfer_request [])
FROM trust_app;
REVOKE EXECUTE ON FUNCTION pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
FROM trust_app;

-- 20261009000002 made pgledger_create_transfers run as its owner so trust_app could post
-- without a write grant. Only the owner calls it now (trust_post_transfers runs as the owner),
-- so it runs as its caller again: a grant added to it by mistake would not come with the
-- owner's rights.
ALTER FUNCTION pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
SECURITY INVOKER;

-- migrate:down
ALTER FUNCTION pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
SECURITY DEFINER;
GRANT EXECUTE ON FUNCTION pgledger_create_transfer(text, text, numeric, timestamptz, jsonb)
TO trust_app;
GRANT EXECUTE ON FUNCTION pgledger_create_transfers(transfer_request [])
TO trust_app;
GRANT EXECUTE ON FUNCTION pgledger_create_transfers(transfer_request [], timestamptz, jsonb)
TO trust_app;
