-- migrate:up
-- How a company writes a day like 05/01/2026 (docs/edge-cases.md F-061, the founder, 11/05: the
-- PMS is to work for companies anywhere). The US writes it month first (1 May), most of the
-- world day first (5 January). Every file the PMS reads (bank statements, payment files, the
-- switching tool's) reads its dates in the order the file itself proves (13/01/2026 can only be
-- day first), else in the company's own order, kept here: month first unless it picks day first
-- in Company setup (trust_company_profiles, the latest row holding). ISO's year-first dates read
-- either way.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_company_profiles ADD COLUMN date_order text
CHECK (date_order IN ('month_first', 'day_first'));

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-column
ALTER TABLE trust_company_profiles DROP COLUMN date_order;
