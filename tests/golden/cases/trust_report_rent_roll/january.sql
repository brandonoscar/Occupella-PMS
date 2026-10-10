-- The rent roll at the end of January 31, 2026. Four units on two properties:
--   Unit 101  Tenants 1 and 2 share a lease; each paid a 750.00 deposit. January's rent is
--             paid; February's (due and paid on Feb 1) stays off the roll.
--   Unit 102  Tenant 3 moved out on Jan 15, owing a 150.00 cleaning fee and still holding a
--             1200.00 deposit; Tenant 4 moved in on Jan 16 and has paid 500.00 of 750.00.
--   Unit 103  vacant.
--   House     Tenant 5, month to month: a late fee and its waiver, January paid, 500.00 paid
--             ahead on February's rent, 300.00 prepaid rent held.
-- Tenant 6 holds 500.00 of prepaid rent and is on no lease yet. Worked by hand:
--   current leases: rent 4950.00, deposits 3300.00, prepaid 300.00,
--                   charged 4250.00, paid 4500.00, due -250.00
--   not on a lease: deposits 1200.00, prepaid 500.00, charged 1550.00, paid 1400.00, due 150.00
--   all tenants:    deposits 4500.00, prepaid 800.00, charged 5800.00, paid 5900.00, due -100.00
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 101'),
('00000000-0000-4000-8000-000000000042', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 102'),
('00000000-0000-4000-8000-000000000043', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 103'),
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
 '00000000-0000-4000-8000-000000000031', 'Tenant 4'),
('00000000-0000-4000-8000-000000000065', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 5'),
('00000000-0000-4000-8000-000000000066', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 6');

-- Ledger accounts: (bank, kind, owner, property, tenant).
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', a.bank, a.kind, a.owner, a.property, a.tenant
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000011'::uuid, 'bank_cash', NULL::uuid, NULL::uuid,
     NULL::uuid),
    (2, '00000000-0000-4000-8000-000000000012', 'bank_cash', NULL, NULL, NULL),
    (3, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000031', NULL),
    (4, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000032', NULL),
    (5, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000061'),
    (6, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000062'),
    (7, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000063'),
    (8, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000065'),
    (9, '00000000-0000-4000-8000-000000000011', 'prepaid_rent', NULL, NULL,
     '00000000-0000-4000-8000-000000000065'),
    (10, '00000000-0000-4000-8000-000000000011', 'prepaid_rent', NULL, NULL,
     '00000000-0000-4000-8000-000000000066')
) AS a (n, bank, kind, owner, property, tenant)
ORDER BY a.n;

-- Each account by a short name, for the postings below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       CASE t.kind
           WHEN 'bank_cash' THEN b.kind || ' cash'
           WHEN 'owner_property' THEN pr.display_name
           WHEN 'tenant_deposit' THEN tn.display_name || ' deposit'
           ELSE tn.display_name || ' prepaid'
       END AS name
FROM trust_ledger_accounts AS t
JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- Leases: (unit, first day, last day, monthly rent, tenants).
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', l.unit, l.starts_on, l.ends_on, l.rent, l.tenants
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000041'::uuid, '2026-01-01'::date, '2026-12-31'::date,
     1500.00, ARRAY['00000000-0000-4000-8000-000000000062',
                    '00000000-0000-4000-8000-000000000061']::uuid[]),
    (2, '00000000-0000-4000-8000-000000000042', '2025-01-01', '2026-01-15', 1400.00,
     ARRAY['00000000-0000-4000-8000-000000000063']::uuid[]),
    (3, '00000000-0000-4000-8000-000000000042', '2026-01-16', '2026-12-31', 1450.00,
     ARRAY['00000000-0000-4000-8000-000000000064']::uuid[]),
    (4, '00000000-0000-4000-8000-000000000044', '2025-06-01', NULL, 2000.00,
     ARRAY['00000000-0000-4000-8000-000000000065']::uuid[])
) AS l (n, unit, starts_on, ends_on, rent, tenants)
ORDER BY l.n;

-- Charges: (id, unit, the lease's first day, due, kind, amount, memo).
INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT c.id, l.pmc_id, l.id, c.due_on, c.kind, c.amount, c.memo
FROM (VALUES
    ('00000000-0000-4000-8000-000000000081'::uuid, '00000000-0000-4000-8000-000000000041'::uuid,
     '2026-01-01'::date, '2026-01-01'::date, 'rent', 1500.00, 'January rent'),
    ('00000000-0000-4000-8000-000000000082', '00000000-0000-4000-8000-000000000041',
     '2026-01-01', '2026-02-01', 'rent', 1500.00, 'February rent'),
    ('00000000-0000-4000-8000-000000000083', '00000000-0000-4000-8000-000000000042',
     '2025-01-01', '2026-01-01', 'rent', 1400.00, 'January rent'),
    ('00000000-0000-4000-8000-000000000084', '00000000-0000-4000-8000-000000000042',
     '2025-01-01', '2026-01-15', 'fee', 150.00, 'Cleaning at move-out'),
    ('00000000-0000-4000-8000-000000000085', '00000000-0000-4000-8000-000000000042',
     '2026-01-16', '2026-01-16', 'rent', 750.00, 'January rent from the 16th'),
    ('00000000-0000-4000-8000-000000000086', '00000000-0000-4000-8000-000000000044',
     '2025-06-01', '2026-01-01', 'rent', 2000.00, 'January rent'),
    ('00000000-0000-4000-8000-000000000087', '00000000-0000-4000-8000-000000000044',
     '2025-06-01', '2026-01-06', 'fee', 75.00, 'Late fee'),
    ('00000000-0000-4000-8000-000000000088', '00000000-0000-4000-8000-000000000044',
     '2025-06-01', '2026-01-10', 'credit', 75.00, 'Late fee waived'),
    ('00000000-0000-4000-8000-000000000089', '00000000-0000-4000-8000-000000000044',
     '2025-06-01', '2026-02-01', 'rent', 2000.00, 'February rent')
) AS c (id, unit, lease_starts_on, due_on, kind, amount, memo)
JOIN trust_leases AS l ON l.unit_id = c.unit AND l.starts_on = c.lease_starts_on;

-- Postings: (from, to, amount, date, memo), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 'security_deposit cash', 'Tenant 3 deposit', 1200.00,
     '2025-01-01 10:00:00+00'::timestamptz, 'Deposit'),
    (2, 'security_deposit cash', 'Tenant 5 deposit', 1800.00, '2025-06-01 10:00:00+00',
     'Deposit'),
    (3, 'security_deposit cash', 'Tenant 1 deposit', 750.00, '2025-12-20 10:00:00+00',
     'Deposit'),
    (4, 'security_deposit cash', 'Tenant 2 deposit', 750.00, '2025-12-20 10:05:00+00',
     'Deposit'),
    (5, 'operating cash', 'Property 1', 1400.00, '2026-01-02 15:00:00+00',
     'Golden Unit 102 January rent'),
    (6, 'operating cash', 'Property 1', 1500.00, '2026-01-03 15:00:00+00',
     'Golden Unit 101 January rent'),
    (7, 'operating cash', 'Property 2', 2000.00, '2026-01-05 15:00:00+00',
     'Golden House January rent'),
    (8, 'operating cash', 'Property 1', 500.00, '2026-01-16 15:00:00+00',
     'Golden Unit 102 move-in rent'),
    (9, 'operating cash', 'Tenant 5 prepaid', 300.00, '2026-01-20 15:00:00+00',
     'Prepaid rent'),
    (10, 'operating cash', 'Tenant 6 prepaid', 500.00, '2026-01-25 15:00:00+00',
     'Prepaid rent'),
    (11, 'operating cash', 'Property 2', 500.00, '2026-01-30 15:00:00+00',
     'Golden House February rent, part'),
    (12, 'operating cash', 'Property 1', 1500.00, '2026-02-01 00:00:00+00',
     'Golden Unit 101 February rent')
) AS p (n, source, target, amount, event_at, memo)
ORDER BY p.n;

-- Payments matched to the charges they pay: (charge, the payment's memo, amount).
SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', m.charge_id,
    (SELECT t.id FROM pgledger_transfers AS t
     JOIN golden_account AS a ON a.id = t.to_account_id
     WHERE t.metadata ->> 'memo' = m.memo),
    m.amount
)
FROM (VALUES
    ('00000000-0000-4000-8000-000000000083'::uuid, 'Golden Unit 102 January rent', 1400.00),
    ('00000000-0000-4000-8000-000000000081', 'Golden Unit 101 January rent', 1500.00),
    ('00000000-0000-4000-8000-000000000086', 'Golden House January rent', 2000.00),
    ('00000000-0000-4000-8000-000000000085', 'Golden Unit 102 move-in rent', 500.00),
    ('00000000-0000-4000-8000-000000000089', 'Golden House February rent, part', 500.00),
    ('00000000-0000-4000-8000-000000000082', 'Golden Unit 101 February rent', 1500.00)
) AS m (charge_id, memo, amount);

SELECT * FROM trust_report_rent_roll('00000000-0000-4000-8000-000000000001', '2026-01-31');
