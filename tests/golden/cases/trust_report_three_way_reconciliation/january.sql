-- A synthetic PMC's January in its operating trust account, reconciled and approved on Feb 3.
-- The bank statement shows 310.00 more than the books: Owner 2's draw, paid by a check written
-- on Jan 30, had not cleared by month end. Worked by hand:
--   book cash  = 1500.00 + 1250.00 + 1500.00 - 240.00 - 310.00 - 275.00 = 3425.00
--   ledgers    = Owner 1 1110.00 + Owner 2 815.00 + prepaid 1500.00 + fees 0 + vendor 0 = 3425.00
--   statement  = 3425.00 + 310.00 (uncleared check) = 3735.00
-- The deposit account and the Feb 1 posting stay out of it.
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
    'pmc_income', NULL, NULL, NULL
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

-- Each account by a short name, for the postings below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       CASE t.kind
           WHEN 'bank_cash' THEN b.kind || ' cash'
           WHEN 'owner_property' THEN o.display_name
           WHEN 'vendor_payable' THEN 'vendor'
           WHEN 'pmc_income' THEN 'fees'
           WHEN 'prepaid_rent' THEN 'prepaid'
           ELSE 'deposit'
       END AS name
FROM trust_ledger_accounts AS t
JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
LEFT JOIN trust_owners AS o ON o.id = t.owner_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- One statement per posting, so they post in this order.
-- rent, Property 1
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    1500.00, '2026-01-03 15:00:00+00'
);

-- Tenant 1's security deposit
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'security_deposit cash'),
    (SELECT id FROM golden_account WHERE name = 'deposit'),
    1500.00, '2026-01-03 15:05:00+00'
);

-- rent, Property 2
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'Owner 2'),
    1250.00, '2026-01-04 16:00:00+00'
);

-- management fee
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    (SELECT id FROM golden_account WHERE name = 'fees'),
    150.00, '2026-01-05 09:00:00+00'
);

-- management fee
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Owner 2'),
    (SELECT id FROM golden_account WHERE name = 'fees'),
    125.00, '2026-01-05 09:00:00+00'
);

-- February rent, paid early
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    (SELECT id FROM golden_account WHERE name = 'prepaid'),
    1500.00, '2026-01-10 12:00:00+00'
);

-- repair bill set aside
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    (SELECT id FROM golden_account WHERE name = 'vendor'),
    240.00, '2026-01-12 10:00:00+00'
);

-- repair bill paid
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'vendor'),
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    240.00, '2026-01-20 10:00:00+00'
);

-- owner draw, by a check that hasn't cleared
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Owner 2'),
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    310.00, '2026-01-30 14:00:00+00'
);

-- fees taken by the PMC
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'fees'),
    (SELECT id FROM golden_account WHERE name = 'operating cash'),
    275.00, '2026-01-31 18:00:00+00'
);

-- February rent applied: the next period
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'prepaid'),
    (SELECT id FROM golden_account WHERE name = 'Owner 1'),
    1500.00, '2026-02-01 00:00:00+00'
);

-- Approved on Feb 3. Inserted directly, so the approval time in the report is fixed;
-- trust_approve_reconciliation stamps now().
INSERT INTO trust_reconciliations (
    pmc_id, bank_account_id, period_start, period_end, statement_balance, book_balance,
    prepared_by, approved_by, approved_at
) VALUES (
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '2026-01-01 00:00:00+00', '2026-02-01 00:00:00+00', 3735.00, 3425.00,
    'Preparer 1', 'Approver 1', '2026-02-03 17:30:00+00'
);

SELECT * FROM trust_report_three_way_reconciliation(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM trust_reconciliations
     WHERE pmc_id = '00000000-0000-4000-8000-000000000001')
);
