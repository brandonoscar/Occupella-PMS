-- migrate:up
-- Two-step sign-in for owners and tenants (the founder's scope of 2026-10-22, src/pms/api/
-- two_step.py). An invite an owner or a tenant asked for themselves, a password reset
-- (accounts.request_reset), keeps their two-step sign-in on and asks for its code before it sets
-- the password, so their email alone never gets past it. Every other invite (from staff, or from
-- the server's operator by `python -m pms.admin invite`) turns it off: someone vouched for them.
-- An invite says which it is, for good: false for every one before this.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_invites ADD COLUMN asked_for boolean NOT NULL DEFAULT false;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-column
ALTER TABLE trust_invites DROP COLUMN asked_for;
