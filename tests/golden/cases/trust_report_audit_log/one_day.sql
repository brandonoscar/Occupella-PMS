-- The audit log of one day's writes. Its times are the clock's, so they differ on every run:
-- the time column is left out, and the period (today, give or take a day) prints as a phrase.
-- Worked by hand, in the order written:
--   Setup, by the owner role (postgres) with no staff named ("not recorded"): the PMC, its
--   operating trust account, Owner 1, Property 1, the two staff members, and the bank's cash
--   and Property 1's ledger accounts.
--   Bookkeeper 1, through trust_app: January's rent of 1500.00 into Property 1, then Unit 1.
--   Manager 1, through trust_app: renames Unit 1 to Unit 1A, then approves January's
--   reconciliation (the reconciliation, then the bank account's closing date).
--   8 + 1 + 1 + 1 + 2 = 13 changes in all.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_staff (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
 'Bookkeeper 1'),
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001', 'Manager 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000031', NULL
);

SET LOCAL ROLE trust_app;

SELECT set_config('trust.staff_id', '00000000-0000-4000-8000-000000000071', true);

SELECT count(*) FROM trust_post_transfers(
    '00000000-0000-4000-8000-000000000001',
    'rent-2026-01',
    ARRAY[(
        (SELECT ledger_account_id FROM trust_ledger_accounts
         WHERE pmc_id = '00000000-0000-4000-8000-000000000001' AND kind = 'bank_cash'),
        (SELECT ledger_account_id FROM trust_ledger_accounts
         WHERE pmc_id = '00000000-0000-4000-8000-000000000001' AND kind = 'owner_property'),
        1500.00
    )::transfer_request],
    '2026-01-05 15:00:00+00',
    '{"memo": "January rent"}'
);

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000081', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1');

SELECT set_config('trust.staff_id', '00000000-0000-4000-8000-000000000072', true);

UPDATE trust_units SET display_name = 'Unit 1A'
WHERE id = '00000000-0000-4000-8000-000000000081';

SELECT trust_approve_reconciliation(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '2026-01-01 00:00:00+00', '2026-02-01 00:00:00+00', 1500.00, 'Bookkeeper 1', 'Manager 1'
);

RESET ROLE;

SELECT line, item,
       CASE item WHEN 'period' THEN 'yesterday to tomorrow' ELSE staff END AS staff,
       db_role, action, record, detail
FROM trust_report_audit_log(
    '00000000-0000-4000-8000-000000000001',
    (now() AT TIME ZONE 'UTC')::date - 1,
    (now() AT TIME ZONE 'UTC')::date + 1
);
