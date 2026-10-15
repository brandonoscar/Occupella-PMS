-- Owner directory at the end of March 31, 2026. Worked by hand: Owner 1's agreement in force
-- is 2026-01-01's (the 2026-04-01 one is later), balance 1450.00; Owner 2 has no agreement
-- (terms unknown, not zero), balance 3400.00; Owner 3 holds no property account.
--
-- The scenario (the same for each of migration 0054's reports), through March 31, 2026 (UTC):
--   Owner 1 holds Property 1 (Units A, B, C) under an agreement from 2026-01-01 (10%, minimum
--   50.00, reserve 200.00, approval limit 500.00; a new one from 2026-04-01 is not yet in
--   force). Owner 2 holds Property 2 (House) with no agreement. Owner 3 holds nothing.
--   Leases: Unit A to Tenant 1 until 2026-02-28 at 1200.00, then to Tenant 3 from 2026-04-15
--   at 1300.00; Unit B to Tenants 1 and 2 for 2026 at 1500.00; House to Tenant 4 from
--   2025-06-01 at 2000.00; Unit C never leased; Tenant 5 (Property 2) on no lease.
--   February: 3000.00 in for Owner 1 and 1000.00 for Owner 2; deposits of 1500.00 (Tenant 1)
--   and 2000.00 (Tenant 4); Vendor 2's V2-201 (100.00, Property 2, approved) set aside and paid
--   on 2026-02-12. Operating cash 3900.00 and deposit cash 3500.00 at March's start.
--   March: 2000.00 in for Owner 2 (03-01), 300.00 prepaid by Tenant 1 (03-02), Owner 2's
--   contribution of 500.00 (03-05); Vendor 1's V1-100 (400.00, Property 1, work order 1) set
--   aside 03-04 and paid 03-06; a 150.00 fee from Owner 1 (03-10); Owner 1's draw of 1000.00
--   (03-12); 100.00 back out of Owner 2's account (a returned payment, 03-15); Vendor 1's
--   V1-101 (250.00, work order 2) entered 03-20 and unpaid; Vendor 2's V2-200 (700.00) entered
--   03-15 and declined 03-25; 200.00 of Tenant 4's deposit paid back (03-25); 100.00 of
--   Tenant 4's deposit kept for Owner 2, its cash moving to the operating account (03-28).
--   Work orders: 1 (Unit A, opened 03-01, Vendor 1 03-02, completed 03-06); 2 (Unit B, opened
--   03-18, Vendor 2 03-18 then Vendor 1 03-19); 3 (Property 2, opened 03-25, no step); 4
--   (House, opened 02-01, cancelled 02-03); 5 (opened 04-02, after the day).
--   W-9s: Vendor 1's kept 03-05; Vendor 2's kept 03-05 and removed 03-20; none for Vendor 3.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2'),
('00000000-0000-4000-8000-000000000023', '00000000-0000-4000-8000-000000000001', 'Owner 3');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit A'),
('00000000-0000-4000-8000-000000000042', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit B'),
('00000000-0000-4000-8000-000000000043', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit C'),
('00000000-0000-4000-8000-000000000044', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'House');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1'),
('00000000-0000-4000-8000-000000000062', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 2'),
('00000000-0000-4000-8000-000000000063', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 3'),
('00000000-0000-4000-8000-000000000064', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 4'),
('00000000-0000-4000-8000-000000000065', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 5');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1'),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001', 'Vendor 2'),
('00000000-0000-4000-8000-000000000053', '00000000-0000-4000-8000-000000000001', 'Vendor 3');

-- Ledger accounts: (bank, kind, owner, property, tenant).
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', a.bank, a.kind, a.owner, a.property, a.tenant
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000011'::uuid, 'bank_cash', NULL::uuid, NULL::uuid,
     NULL::uuid),
    (2, '00000000-0000-4000-8000-000000000011', 'pmc_income', NULL, NULL, NULL),
    (3, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000031', NULL),
    (4, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000032', NULL),
    (5, '00000000-0000-4000-8000-000000000011', 'prepaid_rent', NULL, NULL,
     '00000000-0000-4000-8000-000000000061'),
    (6, '00000000-0000-4000-8000-000000000012', 'bank_cash', NULL, NULL, NULL),
    (7, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000061'),
    (8, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000064')
) AS a (n, bank, kind, owner, property, tenant)
ORDER BY a.n;

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', v.id
)
FROM (VALUES ('00000000-0000-4000-8000-000000000051'::uuid),
             ('00000000-0000-4000-8000-000000000052'::uuid)) AS v (id);

-- Each account by a short name, for the steps below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       coalesce(pr.display_name, tn.display_name || ' ' || t.kind,
                v.display_name || ' ' || t.kind, t.kind || ' ' || b.kind) AS name
FROM trust_ledger_accounts AS t
JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

INSERT INTO trust_management_agreements (
    pmc_id, ledger_account_id, starts_on, fee_percent, minimum_fee, flat_fee, reserve,
    approval_limit
)
SELECT '00000000-0000-4000-8000-000000000001', (SELECT id FROM golden_account
                                                WHERE name = 'Property 1'),
       a.starts_on, a.fee_percent, 50.00, 0, 200.00, 500.00
FROM (VALUES (DATE '2026-01-01', 10), (DATE '2026-04-01', 8)) AS a (starts_on, fee_percent);

-- Leases: (unit, starts, ends, rent, tenants), one statement each.
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2025-01-01', '2026-02-28', 1200.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-04-15', NULL, 1300.00, ARRAY['00000000-0000-4000-8000-000000000063'::uuid]
);
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000042',
    '2026-01-01', '2026-12-31', 1500.00,
    ARRAY['00000000-0000-4000-8000-000000000061'::uuid, '00000000-0000-4000-8000-000000000062']
);
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000044',
    '2025-06-01', NULL, 2000.00, ARRAY['00000000-0000-4000-8000-000000000064'::uuid]
);

-- Work orders and their steps.
INSERT INTO trust_work_orders (id, pmc_id, property_id, unit_id, summary, opened_at) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000041',
 'Leaking faucet', '2026-03-01 10:00+00'),
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000042',
 'Broken heater', '2026-03-18 09:00+00'),
('00000000-0000-4000-8000-000000000073', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', NULL, 'Gutter cleaning', '2026-03-25 08:00+00'),
('00000000-0000-4000-8000-000000000074', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000044',
 'Paint touch-up', '2026-02-01 12:00+00'),
('00000000-0000-4000-8000-000000000075', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', NULL, 'Roof inspection', '2026-04-02 12:00+00');

INSERT INTO trust_work_order_steps (pmc_id, work_order_id, step, vendor_id, taken_at) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000071', 'assigned',
 '00000000-0000-4000-8000-000000000051', '2026-03-02 09:00+00'),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000071', 'completed',
 NULL, '2026-03-06 15:00+00'),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000072', 'assigned',
 '00000000-0000-4000-8000-000000000052', '2026-03-18 10:00+00'),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000072', 'assigned',
 '00000000-0000-4000-8000-000000000051', '2026-03-19 10:00+00'),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000074', 'cancelled',
 NULL, '2026-02-03 12:00+00');

-- February: money in for both owners, and the deposits.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', 'Golden')
)
FROM (VALUES
    (1, 'bank_cash operating', 'Property 1', 3000.00, TIMESTAMPTZ '2026-02-01 12:00+00'),
    (2, 'bank_cash operating', 'Property 2', 1000.00, TIMESTAMPTZ '2026-02-01 12:00+00'),
    (3, 'bank_cash security_deposit', 'Tenant 1 tenant_deposit', 1500.00,
     TIMESTAMPTZ '2026-02-01 12:00+00'),
    (4, 'bank_cash security_deposit', 'Tenant 4 tenant_deposit', 2000.00,
     TIMESTAMPTZ '2026-02-01 12:00+00')
) AS p (n, source, target, amount, event_at)
ORDER BY p.n;

-- Vendor 2's bill on Property 2, over its approval limit of 0, approved, set aside and paid.
INSERT INTO trust_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
) VALUES (
    '00000000-0000-4000-8000-000000000084', '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000052',
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    'V2-201', '2026-02-10', '2026-02-20', 100.00, 'Gutter repair'
);
INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000084',
 '2026-02-10', 'Owner 2');
SELECT trust_set_aside_bill('00000000-0000-4000-8000-000000000001',
                            '00000000-0000-4000-8000-000000000084', '2026-02-11 12:00+00');
SELECT trust_pay_bill('00000000-0000-4000-8000-000000000001',
                      '00000000-0000-4000-8000-000000000084', '2026-02-12 12:00+00');

-- March.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', 'Golden')
)
FROM (VALUES
    (1, 'bank_cash operating', 'Property 2', 2000.00, TIMESTAMPTZ '2026-03-01 12:00+00'),
    (2, 'bank_cash operating', 'Tenant 1 prepaid_rent', 300.00,
     TIMESTAMPTZ '2026-03-02 12:00+00')
) AS p (n, source, target, amount, event_at)
ORDER BY p.n;

SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM golden_account WHERE name = 'Property 2'), 'contribution-1', 500.00,
    '2026-03-05 12:00+00', 'Roof fund'
);

INSERT INTO trust_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo,
    work_order_id
) VALUES (
    '00000000-0000-4000-8000-000000000081', '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000051',
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    'V1-100', '2026-03-03', '2026-03-10', 400.00, 'Faucet',
    '00000000-0000-4000-8000-000000000071'
);
SELECT trust_set_aside_bill('00000000-0000-4000-8000-000000000001',
                            '00000000-0000-4000-8000-000000000081', '2026-03-04 12:00+00');
SELECT trust_pay_bill('00000000-0000-4000-8000-000000000001',
                      '00000000-0000-4000-8000-000000000081', '2026-03-06 12:00+00');

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    (SELECT id FROM golden_account WHERE name = 'pmc_income operating'),
    150.00, '2026-03-10 12:00+00', jsonb_build_object('memo', 'Golden fee')
);

SELECT trust_draw_owner(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM golden_account WHERE name = 'Property 1'), 'draw-1', 1000.00,
    '2026-03-12 12:00+00'
);

-- A payment of Owner 2's that the bank returned.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    (SELECT id FROM golden_account WHERE name = 'bank_cash operating'),
    100.00, '2026-03-15 12:00+00', jsonb_build_object('memo', 'Golden returned payment')
);

INSERT INTO trust_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo,
    work_order_id
) VALUES
('00000000-0000-4000-8000-000000000082', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000051',
 (SELECT id FROM golden_account WHERE name = 'Property 1'),
 'V1-101', '2026-03-20', '2026-04-05', 250.00, 'Heater part',
 '00000000-0000-4000-8000-000000000072'),
('00000000-0000-4000-8000-000000000083', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000052',
 (SELECT id FROM golden_account WHERE name = 'Property 2'),
 'V2-200', '2026-03-15', '2026-03-30', 700.00, 'New fence', NULL);
INSERT INTO trust_bill_declines (pmc_id, bill_id, declined_on, declined_by, reason) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000083',
 '2026-03-25', 'Owner 2', 'Not needed');

-- Part of Tenant 4's deposit paid back, and part kept for Owner 2 with its cash.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Tenant 4 tenant_deposit'),
    (SELECT id FROM golden_account WHERE name = 'bank_cash security_deposit'),
    200.00, '2026-03-25 12:00+00', jsonb_build_object('memo', 'Golden deposit back')
);
SELECT id FROM pgledger_create_transfers(
    ARRAY[
        ((SELECT id FROM golden_account WHERE name = 'Tenant 4 tenant_deposit'),
         (SELECT id FROM golden_account WHERE name = 'Property 2'), 100.00)::transfer_request,
        ((SELECT id FROM golden_account WHERE name = 'bank_cash operating'),
         (SELECT id FROM golden_account WHERE name = 'bank_cash security_deposit'),
         100.00)::transfer_request
    ],
    '2026-03-28 12:00+00', jsonb_build_object('memo', 'Golden deposit kept')
);

-- W-9s: Vendor 1's kept; Vendor 2's kept and then removed.
INSERT INTO trust_documents (
    id, pmc_id, kind, vendor_id, title, content_type, size, sha256, created_at
)
SELECT d.id, '00000000-0000-4000-8000-000000000001', 'w9', d.vendor, 'W-9',
       'application/pdf', 9, sha256('\x255044462d312e340a'::bytea), '2026-03-05 12:00+00'
FROM (VALUES
    ('00000000-0000-4000-8000-000000000091'::uuid, '00000000-0000-4000-8000-000000000051'::uuid),
    ('00000000-0000-4000-8000-000000000092', '00000000-0000-4000-8000-000000000052')
) AS d (id, vendor);
INSERT INTO trust_document_files (document_id, pmc_id, content)
SELECT d.id, '00000000-0000-4000-8000-000000000001', '\x255044462d312e340a'::bytea
FROM trust_documents AS d WHERE d.pmc_id = '00000000-0000-4000-8000-000000000001';
INSERT INTO trust_document_removals (document_id, pmc_id, created_at) VALUES
('00000000-0000-4000-8000-000000000092', '00000000-0000-4000-8000-000000000001',
 '2026-03-20 12:00+00');

SELECT * FROM trust_report_owner_directory('00000000-0000-4000-8000-000000000001', '2026-03-31');
