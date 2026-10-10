-- Unpaid bills at the end of April 30, 2026. Worked by hand (days past due = Apr 30 - due):
--   Vendor 1  INV-A2  Property 1  due Apr 14   16 days  1-30     200.00  set aside Apr 16
--             INV-A1  Property 1  due May 20    0 days  current  100.00
--             INV-A3  set aside Apr 1, paid Apr 10: not listed.
--             INV-A4  dated May 2: not listed.
--             total 300.00, set aside 200.00
--   Vendor 2  INV-B3  Property 2  due Dec 31  120 days  over 90  500.00
--             INV-B2  Property 1  due Jan 31   89 days  61-90    400.00
--             INV-B1  Property 1  due Mar 1    60 days  31-60    300.00
--             INV-B4  Property 1  due Apr 30    0 days  current   50.00  set aside Apr 29,
--                                                                        paid May 2: unpaid
--             total 1250.00, set aside 50.00
--   Total 1550.00, set aside 250.00.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1'),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001', 'Vendor 2');

-- Ledger accounts: (kind, owner, property), then each vendor's.
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid),
    (3, 'owner_property', '00000000-0000-4000-8000-000000000022',
     '00000000-0000-4000-8000-000000000032')
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', v.id
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000051'::uuid),
    (2, '00000000-0000-4000-8000-000000000052')
) AS v (n, id)
ORDER BY v.n;

-- Each owner's account by its property's name, and the cash account.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(pr.display_name, t.kind) AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind <> 'vendor_payable';

-- Bills up to 10000.00 need no approval.
INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on, approval_limit)
SELECT '00000000-0000-4000-8000-000000000001', a.id, '2025-01-01', 10000.00
FROM golden_account AS a
WHERE a.name IN ('Property 1', 'Property 2');

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, '2025-12-01 15:00:00+00', jsonb_build_object('memo', 'Golden owner contribution')
)
FROM (VALUES (1, 'Property 1', 5000.00), (2, 'Property 2', 600.00)) AS p (n, target, amount)
ORDER BY p.n;

-- Bills: (id, vendor, property, reference, bill date, due date, amount).
INSERT INTO trust_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
)
SELECT b.id, '00000000-0000-4000-8000-000000000001', b.vendor, a.id, b.reference, b.bill_date,
       b.due_on, b.amount, 'Golden repair'
FROM (VALUES
    ('00000000-0000-4000-8000-000000000101'::uuid, '00000000-0000-4000-8000-000000000051'::uuid,
     'Property 1', 'INV-A1', '2026-04-20'::date, '2026-05-20'::date, 100.00),
    ('00000000-0000-4000-8000-000000000102', '00000000-0000-4000-8000-000000000051',
     'Property 1', 'INV-A2', '2026-03-15', '2026-04-14', 200.00),
    ('00000000-0000-4000-8000-000000000103', '00000000-0000-4000-8000-000000000051',
     'Property 1', 'INV-A3', '2026-03-01', '2026-03-31', 150.00),
    ('00000000-0000-4000-8000-000000000104', '00000000-0000-4000-8000-000000000051',
     'Property 1', 'INV-A4', '2026-05-02', '2026-06-01', 75.00),
    ('00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000052',
     'Property 1', 'INV-B1', '2026-02-01', '2026-03-01', 300.00),
    ('00000000-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000052',
     'Property 1', 'INV-B2', '2026-01-05', '2026-01-31', 400.00),
    ('00000000-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000052',
     'Property 2', 'INV-B3', '2025-12-01', '2025-12-31', 500.00),
    ('00000000-0000-4000-8000-000000000204', '00000000-0000-4000-8000-000000000052',
     'Property 1', 'INV-B4', '2026-04-01', '2026-04-30', 50.00)
) AS b (id, vendor, name, reference, bill_date, due_on, amount)
JOIN golden_account AS a ON a.name = b.name;

-- Set aside and paid: (step, bill, date), one statement each so they post in this order.
SELECT trust_set_aside_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000103',
    '2026-04-01 10:00:00+00'
);

SELECT trust_pay_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000103',
    '2026-04-10 10:00:00+00'
);

SELECT trust_set_aside_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000102',
    '2026-04-16 10:00:00+00'
);

SELECT trust_set_aside_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000204',
    '2026-04-29 10:00:00+00'
);

SELECT trust_pay_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000204',
    '2026-05-02 10:00:00+00'
);

SELECT * FROM trust_report_unpaid_bills('00000000-0000-4000-8000-000000000001', '2026-04-30');
