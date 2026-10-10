-- Owner balances at the end of January 31, 2026, with vendor bills. Worked by hand:
--   Owner 1 / Property 1  reserve 200.00, bills up to 500.00 need no approval. 2000.00 in.
--                         INV-101 450.00 (Vendor 1): set aside Jan 12, paid Jan 15.
--                         INV-102 900.00 (Vendor 2): approved, set aside Jan 22, not paid.
--                         INV-103 120.00 (Vendor 1): entered, not set aside.
--                         Holds 2000.00 - 450.00 - 900.00 = 650.00, keeps 200.00, bills 120.00,
--                         available 330.00.
--   Owner 2 / Property 2  no reserve, every bill needs approval. 800.00 in.
--                         INV-201 300.00 (Vendor 2), Jan 25: no approval, not set aside.
--                         INV-202 75.00 (Vendor 1), dated Feb 3: not owed yet.
--                         Holds 800.00, keeps 0.00, bills 300.00, available 500.00.
--   Totals: held 1450.00, kept 200.00, bills 420.00, available 830.00.
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
    (2, 'pmc_income', NULL, NULL),
    (3, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid),
    (4, 'owner_property', '00000000-0000-4000-8000-000000000022',
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

-- Each owner's account by its property's name, for the steps below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(pr.display_name, t.kind) AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind <> 'vendor_payable';

INSERT INTO trust_management_agreements (
    pmc_id, ledger_account_id, starts_on, reserve, approval_limit
)
SELECT '00000000-0000-4000-8000-000000000001', a.id, '2026-01-01', g.reserve, g.approval_limit
FROM (VALUES
    ('Property 1', 200.00, 500.00),
    ('Property 2', 0.00, 0.00)
) AS g (name, reserve, approval_limit)
JOIN golden_account AS a ON a.name = g.name;

-- Money in: (to, amount, date), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', 'Golden owner contribution')
)
FROM (VALUES
    (1, 'Property 1', 2000.00, '2026-01-03 15:00:00+00'::timestamptz),
    (2, 'Property 2', 800.00, '2026-01-05 15:00:00+00')
) AS p (n, target, amount, event_at)
ORDER BY p.n;

-- Bills: (id, vendor, property, reference, bill date, amount).
INSERT INTO trust_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
)
SELECT b.id, '00000000-0000-4000-8000-000000000001', b.vendor, a.id, b.reference, b.bill_date,
       b.bill_date + 30, b.amount, 'Golden repair'
FROM (VALUES
    ('00000000-0000-4000-8000-000000000101'::uuid, '00000000-0000-4000-8000-000000000051'::uuid,
     'Property 1', 'INV-101', '2026-01-10'::date, 450.00),
    ('00000000-0000-4000-8000-000000000102', '00000000-0000-4000-8000-000000000052',
     'Property 1', 'INV-102', '2026-01-20', 900.00),
    ('00000000-0000-4000-8000-000000000103', '00000000-0000-4000-8000-000000000051',
     'Property 1', 'INV-103', '2026-01-28', 120.00),
    ('00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000052',
     'Property 2', 'INV-201', '2026-01-25', 300.00),
    ('00000000-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000051',
     'Property 2', 'INV-202', '2026-02-03', 75.00)
) AS b (id, vendor, name, reference, bill_date, amount)
JOIN golden_account AS a ON a.name = b.name;

INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000102', '2026-01-21',
 'Owner 1, by email');

-- Set aside, then paid, one statement each so they post in this order.
SELECT trust_set_aside_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101',
    '2026-01-12 10:00:00+00'
);

SELECT trust_pay_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101',
    '2026-01-15 10:00:00+00'
);

SELECT trust_set_aside_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000102',
    '2026-01-22 10:00:00+00'
);

SELECT * FROM trust_report_owner_balances('00000000-0000-4000-8000-000000000001', '2026-01-31');
