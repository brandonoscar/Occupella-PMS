-- migrate:up
-- A charge and a recurring charge carry the request id of the panel that made them
-- (docs/edge-cases.md C-025, C-026). Enter charge and Add recurring charge sent again after their
-- answer was lost (a dropped connection, a second click on a slow page) charged the tenant twice,
-- or billed them twice every month: staff's payments already carried a request id, and these
-- carried none.
--
--   trust_charges.request_id            the request that charged it, once per PMC; NULL for a
--                                        charge the database makes (the rent run, late and NSF
--                                        fees, a lease end's adjustment, a cutover's carry-over)
--   trust_recurring_charges.request_id  the request that added it, once per PMC
--
-- The API (src/pms/api/rent.py) reads the request id under its own lock first: the same request
-- again returns what it made, and the same id with another charge is refused.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_charges ADD COLUMN request_id uuid;
ALTER TABLE trust_recurring_charges ADD COLUMN request_id uuid;

-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_charges_request ON trust_charges (pmc_id, request_id)
WHERE request_id IS NOT NULL;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_recurring_charges_request ON trust_recurring_charges (pmc_id, request_id)
WHERE request_id IS NOT NULL;

-- migrate:down
SET LOCAL lock_timeout = '5s';
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_recurring_charges_request;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_charges_request;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_recurring_charges DROP COLUMN request_id;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_charges DROP COLUMN request_id;
