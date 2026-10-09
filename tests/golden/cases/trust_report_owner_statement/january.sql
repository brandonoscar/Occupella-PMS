-- Owner 1's January statement: two properties, a December balance carried in, fees, a repair,
-- money moved between the two properties, and an owner draw. The Jan 20 fee is posted after the
-- Jan 30 draw, so the statement has to sort by date, not by posting order. Owner 2's rent and
-- the Feb 1 rent stay out. Worked by hand:
--   Property 1: 1000.00 opening + 1500.00 - 150.00 - 240.00 - 100.00 = 2010.00
--   Property 2:    0.00 opening + 1250.00 - 125.00 + 100.00 - 500.00 =  725.00
--   all: opening 1000.00, in 2850.00, out -1115.00, closing 2735.00
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
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2'),
('00000000-0000-4000-8000-000000000033', '00000000-0000-4000-8000-000000000001', 'Property 3');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
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
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000032', NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000022',
    '00000000-0000-4000-8000-000000000033', NULL
);

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000051'
);

-- Each account by a short name, for the postings below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       CASE t.kind
           WHEN 'bank_cash' THEN 'cash'
           WHEN 'owner_property' THEN pr.display_name
           WHEN 'vendor_payable' THEN 'vendor'
           ELSE 'fees'
       END AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- One statement per posting, so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'cash'),
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    1000.00, '2025-12-15 15:00:00+00', '{"memo": "December rent"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'cash'),
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    1500.00, '2026-01-03 15:00:00+00', '{"memo": "January rent"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'cash'),
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    1250.00, '2026-01-04 16:00:00+00', '{"memo": "January rent"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    (SELECT id FROM golden_account WHERE name = 'fees'),
    150.00, '2026-01-05 09:00:00+00', '{"memo": "Management fee"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    (SELECT id FROM golden_account WHERE name = 'vendor'),
    240.00, '2026-01-12 10:00:00+00', '{"memo": "Plumbing repair"}'
);

-- No memo: the line shows only the other side.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    100.00, '2026-01-25 11:00:00+00'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    (SELECT id FROM golden_account WHERE name = 'cash'),
    500.00, '2026-01-30 14:00:00+00', '{"memo": "Owner draw"}'
);

-- Posted last, dated Jan 20.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    (SELECT id FROM golden_account WHERE name = 'fees'),
    125.00, '2026-01-20 09:00:00+00', '{"memo": "Management fee"}'
);

-- Owner 2's rent, and next month's rent for Owner 1: neither is on this statement.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'cash'),
    (SELECT id FROM golden_account WHERE name = 'Property 3'),
    900.00, '2026-01-10 12:00:00+00', '{"memo": "January rent"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'cash'),
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    1500.00, '2026-02-01 00:00:00+00', '{"memo": "February rent"}'
);

SELECT * FROM trust_report_owner_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01 00:00:00+00', '2026-02-01 00:00:00+00'
);
