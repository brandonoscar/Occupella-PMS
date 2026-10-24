-- Unpaid bills at the end of April 30, 2026, with bills paid in parts (migration 0152, ADR 0010):
-- the first part takes the whole bill from the owner (set aside) and each pays its part. Worked
-- by hand (days past due = Apr 30 - due):
--   Vendor 1  INV-P1  due Apr 15  15 days  1-30     300.00: 100.00 paid Apr 10, 50.00 Apr 20;
--                                                   150.00 left, set aside 150.00
--             INV-P2  due May 5                     120.00: 60.00 paid Apr 12, 60.00 Apr 25:
--                                                   paid in full, not listed
--             INV-P3  due Apr 30   0 days  current   80.00: 30.00 paid Apr 28, 50.00 May 2
--                                                   (after the day); 50.00 left, set aside 50.00
--             INV-P4  due May 22   0 days  current   60.00: nothing paid; 60.00 left, none set
--                                                   aside
--             total 260.00, set aside 200.00
--   Total 260.00, set aside 200.00.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid)
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000051'
);

CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(pr.display_name, t.kind) AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind <> 'vendor_payable';

-- Bills up to 10000.00 need no approval.
INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on, approval_limit)
SELECT '00000000-0000-4000-8000-000000000001', a.id, '2025-01-01', 10000.00
FROM golden_account AS a WHERE a.name = 'Property 1';

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    1000.00, '2025-12-01 15:00:00+00', jsonb_build_object('memo', 'Golden owner contribution')
);

-- Bills: (id, reference, bill date, due date, amount).
INSERT INTO trust_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
)
SELECT b.id, '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051',
       (SELECT id FROM golden_account WHERE name = 'Property 1'), b.reference, b.bill_date,
       b.due_on, b.amount, 'Golden repair'
FROM (VALUES
    ('00000000-0000-4000-8000-000000000101'::uuid, 'INV-P1', '2026-04-01'::date,
     '2026-04-15'::date, 300.00),
    ('00000000-0000-4000-8000-000000000102', 'INV-P2', '2026-04-05', '2026-05-05', 120.00),
    ('00000000-0000-4000-8000-000000000103', 'INV-P3', '2026-04-10', '2026-04-30', 80.00),
    ('00000000-0000-4000-8000-000000000104', 'INV-P4', '2026-04-22', '2026-05-22', 60.00)
) AS b (id, reference, bill_date, due_on, amount);

-- Parts paid: (bill, amount, request key, when), one statement each so they post in this order.
SELECT trust_pay_bill_part(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101', 100.00,
    'golden-p1-1', '2026-04-10 10:00:00+00'
);

SELECT trust_pay_bill_part(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000102', 60.00,
    'golden-p2-1', '2026-04-12 10:00:00+00'
);

SELECT trust_pay_bill_part(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101', 50.00,
    'golden-p1-2', '2026-04-20 10:00:00+00'
);

SELECT trust_pay_bill_part(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000102', 60.00,
    'golden-p2-2', '2026-04-25 10:00:00+00'
);

SELECT trust_pay_bill_part(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000103', 30.00,
    'golden-p3-1', '2026-04-28 10:00:00+00'
);

SELECT trust_pay_bill(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000103',
    '2026-05-02 10:00:00+00'
);

SELECT * FROM trust_report_unpaid_bills('00000000-0000-4000-8000-000000000001', '2026-04-30');
