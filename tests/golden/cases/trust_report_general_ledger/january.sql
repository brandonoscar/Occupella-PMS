-- General ledger for January 3 to 31, 2026 (the trial balance case's books). Worked by hand:
--   Opening at the start of Jan 3: deposit book cash 1500.00 and Tenant 1's deposit 1500.00
--   (posted Jan 2); every other account 0.00, so only the two book cash lines and Tenant 1's
--   deposit open the period with a line, plus each account that moves in it.
--   Operating book cash: 2000.00 (Jan 3) + 500.00 (Jan 5) + 300.00 (Jan 20) = 2800.00 debit.
--   Property 1: 2000.00 credit, then 250.00 and 160.00 debits: closes at 1590.00.
--   Property 2: 500.00; the 100.00 of Feb 1 00:00 is after the period. Vendor 1 250.00,
--   PMC fees 160.00, Tenant 1 prepaid 300.00. Deposit book cash 1500.00 + 900.00 = 2400.00.
--   Period totals: 2000 + 500 + 900 + 300 + 250 + 160 = 4110.00 each side.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1'),
('00000000-0000-4000-8000-000000000062', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 2');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1');

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
     '00000000-0000-4000-8000-000000000062')
) AS a (n, bank, kind, owner, property, tenant)
ORDER BY a.n;

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000051'
);

-- Each account by a short name, for the steps below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       coalesce(pr.display_name, tn.display_name || ' ' || t.kind,
                t.kind || ' ' || b.kind) AS name
FROM trust_ledger_accounts AS t
JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- Transfers: (from, to, amount, date), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', 'Golden')
)
FROM (VALUES
    (1, 'bank_cash security_deposit', 'Tenant 1 tenant_deposit', 1500.00,
     '2026-01-02 15:00:00+00'::timestamptz),
    (2, 'bank_cash operating', 'Property 1', 2000.00, '2026-01-03 15:00:00+00'),
    (3, 'bank_cash operating', 'Property 2', 500.00, '2026-01-05 15:00:00+00'),
    (4, 'bank_cash security_deposit', 'Tenant 2 tenant_deposit', 900.00,
     '2026-01-10 15:00:00+00'),
    (5, 'bank_cash operating', 'Tenant 1 prepaid_rent', 300.00, '2026-01-20 15:00:00+00'),
    (6, 'Property 1', 'vendor_payable operating', 250.00, '2026-01-25 15:00:00+00'),
    (7, 'Property 1', 'pmc_income operating', 160.00, '2026-01-31 18:00:00+00'),
    (8, 'bank_cash operating', 'Property 2', 100.00, '2026-02-01 00:00:00+00')
) AS p (n, source, target, amount, event_at)
ORDER BY p.n;

SELECT * FROM trust_report_general_ledger(
    '00000000-0000-4000-8000-000000000001', '2026-01-03', '2026-01-31'
);
