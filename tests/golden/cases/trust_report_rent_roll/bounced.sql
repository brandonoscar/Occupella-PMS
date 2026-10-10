-- A check that bounces. Unit 101's January rent is paid by check on Jan 3; the check comes back
-- on Jan 8 (its reversing transfer is recorded against the match), a 35.00 returned-check fee is
-- charged, and on Jan 12 the tenant pays 1000.00 of what is owed again. Worked by hand, at the
-- end of Jan 31:
--   charged 1500.00 + 35.00 = 1535.00
--   paid    1500.00 - 1500.00 + 1000.00 = 1000.00
--   due     535.00
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 101');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000031', NULL
);

-- The two accounts by a short name, for the postings below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, t.kind AS name
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-01-01', NULL, 1500.00, ARRAY['00000000-0000-4000-8000-000000000061']::uuid[]
);

INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT c.id, l.pmc_id, l.id, c.due_on, c.kind, c.amount, c.memo
FROM (VALUES
    ('00000000-0000-4000-8000-000000000081'::uuid, '2026-01-01'::date, 'rent', 1500.00,
     'January rent'),
    ('00000000-0000-4000-8000-000000000082', '2026-01-08', 'fee', 35.00, 'Returned check fee')
) AS c (id, due_on, kind, amount, memo)
JOIN trust_leases AS l ON l.unit_id = '00000000-0000-4000-8000-000000000041';

-- Postings: (from, to, amount, date, memo), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 'bank_cash', 'owner_property', 1500.00, '2026-01-03 15:00:00+00'::timestamptz,
     'Golden check 1001'),
    (2, 'owner_property', 'bank_cash', 1500.00, '2026-01-08 10:00:00+00',
     'Golden check 1001 returned'),
    (3, 'bank_cash', 'owner_property', 1000.00, '2026-01-12 15:00:00+00',
     'Golden payment after the return')
) AS p (n, source, target, amount, event_at, memo)
ORDER BY p.n;

-- The check pays January's rent, comes back, and the next payment pays part of it again.
SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000081',
    (SELECT id FROM pgledger_transfers WHERE metadata ->> 'memo' = 'Golden check 1001'
       AND to_account_id = (SELECT id FROM golden_account WHERE name = 'owner_property')),
    1500.00
);

SELECT trust_reverse_payment(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000081',
    (SELECT id FROM pgledger_transfers WHERE metadata ->> 'memo' = 'Golden check 1001'
       AND to_account_id = (SELECT id FROM golden_account WHERE name = 'owner_property')),
    (SELECT id FROM pgledger_transfers WHERE metadata ->> 'memo' = 'Golden check 1001 returned'
       AND from_account_id = (SELECT id FROM golden_account WHERE name = 'owner_property')),
    1500.00
);

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000081',
    (SELECT id FROM pgledger_transfers
     WHERE metadata ->> 'memo' = 'Golden payment after the return'
       AND to_account_id = (SELECT id FROM golden_account WHERE name = 'owner_property')),
    1000.00
);

SELECT * FROM trust_report_rent_roll('00000000-0000-4000-8000-000000000001', '2026-01-31');
