-- A synthetic PMC's operating trust account at the end of January, from its first statement.
-- Matched: the Jan 5 deposit (both rents), the Jan 11 deposit (February's rent paid early) and
-- check 101. Worked by hand:
--   bank balance = 2750.00 + 1500.00 - 240.00 - 15.00 + 80.00         = 4075.00
--   in transit   = the late fee recorded Jan 31                         =   50.00
--   outstanding  = check 102, Owner 2's draw                            = -310.00
--   adjusted     = 4075.00 + 50.00 - 310.00                            = 3815.00
--   book cash    = 1500.00 + 1250.00 + 1500.00 - 240.00 - 310.00 + 50.00 = 3750.00
--   difference   = the lines the books lack: the service fee (-15.00), and the Jan 31 deposit
--                  (80.00), matched to rent the books record on Feb 1, so not cleared by then.
-- The deposit account stays out of it.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden deposit trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1');

INSERT INTO trust_staff (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001', 'Staff 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000031', NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000022',
    '00000000-0000-4000-8000-000000000032', NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'prepaid_rent', NULL, NULL, '00000000-0000-4000-8000-000000000041'
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'tenant_deposit', NULL, NULL, '00000000-0000-4000-8000-000000000041'
);

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000051'
);

-- Each account by a short name, and each transfer by its memo, for the steps below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       CASE t.kind
           WHEN 'bank_cash' THEN b.kind || ' cash'
           WHEN 'owner_property' THEN o.display_name
           WHEN 'vendor_payable' THEN 'vendor'
           WHEN 'prepaid_rent' THEN 'prepaid'
           ELSE 'deposit'
       END AS name
FROM trust_ledger_accounts AS t
JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
LEFT JOIN trust_owners AS o ON o.id = t.owner_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

CREATE TEMP VIEW golden_transfer AS
SELECT tr.id, tr.metadata ->> 'memo' AS memo
FROM pgledger_transfers AS tr
WHERE tr.from_account_id IN (SELECT a.id FROM golden_account AS a)
   OR tr.to_account_id IN (SELECT a.id FROM golden_account AS a);

-- One statement per posting, so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    1500.00, '2026-01-03 15:00:00+00', '{"memo": "Rent, Property 1"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'security_deposit cash'),
    (SELECT id FROM golden_account WHERE name = 'deposit'),
    1500.00, '2026-01-03 15:05:00+00', '{"memo": "Security deposit, Tenant 1"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'Owner 2'),
    1250.00, '2026-01-04 16:00:00+00', '{"memo": "Rent, Property 2"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'prepaid'),
    1500.00, '2026-01-10 12:00:00+00', '{"memo": "February rent, Tenant 1"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    (SELECT id FROM golden_account WHERE name = 'vendor'),
    240.00, '2026-01-12 10:00:00+00', '{"memo": "Repair bill set aside"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'vendor'),
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    240.00, '2026-01-20 10:00:00+00', '{"memo": "Check 101, Vendor 1"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Owner 2'),
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    310.00, '2026-01-30 14:00:00+00', '{"memo": "Check 102, Owner 2"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'Owner 2'),
    50.00, '2026-01-31 18:00:00+00', '{"memo": "Late fee, Property 2"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    80.00, '2026-02-01 09:00:00+00', '{"memo": "Pet rent, Property 1"}'
);

-- January's statement, the account's first.
INSERT INTO trust_bank_statements (
    id, pmc_id, bank_account_id, first_day, last_day, opening_balance, closing_balance,
    line_count, source, file_sha256, imported_by
) VALUES (
    '00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000011', '2026-01-01', '2026-01-31', 0, 4075.00, 5, 'csv',
    repeat('0', 63) || '1', '00000000-0000-4000-8000-000000000061'
);

INSERT INTO trust_bank_statement_lines (
    id, pmc_id, statement_id, line_no, posted_on, amount, description, bank_reference
) VALUES
('00000000-0000-4000-8000-000000000081', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000071', 1, '2026-01-05', 2750.00, 'Deposit', 'D-0105'),
('00000000-0000-4000-8000-000000000082', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000071', 2, '2026-01-11', 1500.00, 'Deposit', 'D-0111'),
('00000000-0000-4000-8000-000000000083', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000071', 3, '2026-01-23', -240.00, 'Check 101', '101'),
('00000000-0000-4000-8000-000000000084', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000071', 4, '2026-01-28', -15.00, 'Service fee', NULL),
('00000000-0000-4000-8000-000000000085', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000071', 5, '2026-01-31', 80.00, 'Deposit', 'D-0131');

-- The statement is whole now, not only at commit (the case is rolled back).
SET CONSTRAINTS ALL IMMEDIATE;

SELECT trust_match_bank_lines(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    ARRAY['00000000-0000-4000-8000-000000000081'::uuid],
    ARRAY(SELECT id FROM golden_transfer WHERE memo IN ('Rent, Property 1', 'Rent, Property 2')),
    ARRAY[]::uuid[], '00000000-0000-4000-8000-000000000061'
);

SELECT trust_match_bank_lines(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    ARRAY['00000000-0000-4000-8000-000000000082'::uuid],
    ARRAY(SELECT id FROM golden_transfer WHERE memo = 'February rent, Tenant 1'),
    ARRAY[]::uuid[], '00000000-0000-4000-8000-000000000061'
);

SELECT trust_match_bank_lines(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    ARRAY['00000000-0000-4000-8000-000000000083'::uuid],
    ARRAY(SELECT id FROM golden_transfer WHERE memo = 'Check 101, Vendor 1'),
    ARRAY[]::uuid[], '00000000-0000-4000-8000-000000000061'
);

SELECT trust_match_bank_lines(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    ARRAY['00000000-0000-4000-8000-000000000085'::uuid],
    ARRAY(SELECT id FROM golden_transfer WHERE memo = 'Pet rent, Property 1'),
    ARRAY[]::uuid[], '00000000-0000-4000-8000-000000000061'
);

SELECT * FROM trust_report_bank_reconciliation(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '2026-01-31'
);
