-- Deposit interest at the end of June 30, 2026. Property 1's deposits earn 1.5% a year from
-- 2026-01-01 and 2% from 2026-06-01; Property 2 has no terms. Worked by hand, a year of 365 days:
--   Unit 1  Tenant 1's 1200.00 since 2026-01-01, nothing paid: 151 days at 1.5% is
--           1200 x 0.015 x 151 / 365 = 7.446575, and June's 30 days at 2% 1.972603: 9.42 due.
--   Unit 2  Tenant 2's 1000.00 since 2026-01-01, credited through 2026-03-31 (3.70): from
--           2026-04-01, 61 days at 1.5% 2.506849 and 30 at 2% 1.643836: 4.15 due.
--   Unit 3  no deposit held, nothing due: not listed. Unit 4 (Property 2): no terms, not listed.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_units (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000004' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       CASE WHEN n = 4 THEN '00000000-0000-4000-8000-000000000032'::uuid
            ELSE '00000000-0000-4000-8000-000000000031'::uuid END,
       'Unit ' || n
FROM generate_series(1, 4) AS n;

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000006' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       CASE WHEN n = 4 THEN '00000000-0000-4000-8000-000000000032'::uuid
            ELSE '00000000-0000-4000-8000-000000000031'::uuid END,
       'Tenant ' || n
FROM generate_series(1, 4) AS n;

-- Ledger accounts: (bank, kind, owner, property, tenant).
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', a.bank, a.kind, a.owner, a.property, a.tenant
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000011'::uuid, 'bank_cash', NULL::uuid, NULL::uuid,
     NULL::uuid),
    (2, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000031', NULL),
    (3, '00000000-0000-4000-8000-000000000012', 'bank_cash', NULL, NULL, NULL),
    (4, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000061'),
    (5, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000062'),
    (6, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000064')
) AS a (n, bank, kind, owner, property, tenant)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', ('00000000-0000-4000-8000-00000000004' || n)::uuid,
    '2026-01-01', NULL, 1500.00, ARRAY[('00000000-0000-4000-8000-00000000006' || n)::uuid]
)
FROM generate_series(1, 4) AS n;

INSERT INTO trust_deposit_interest_terms (pmc_id, property_id, starts_on, annual_percent) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000031', '2026-01-01',
 1.5),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000031', '2026-06-01',
 2);

-- The deposits, each into its tenant's deposit account: (tenant, amount).
SELECT pgledger_create_transfer(
    (SELECT c.ledger_account_id FROM trust_ledger_accounts AS c
     WHERE c.bank_account_id = '00000000-0000-4000-8000-000000000012' AND c.kind = 'bank_cash'),
    (SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
     WHERE t.tenant_id = d.tenant AND t.kind = 'tenant_deposit'),
    d.amount, '2026-01-01 15:00:00+00'::timestamptz, jsonb_build_object('memo', 'Golden')
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000061'::uuid, 1200.00),
    (2, '00000000-0000-4000-8000-000000000062', 1000.00),
    (3, '00000000-0000-4000-8000-000000000064', 800.00)
) AS d (n, tenant, amount)
ORDER BY d.n;

-- Unit 2's interest for January to March, credited against its rent on April 2.
SELECT trust_pay_deposit_interest(
    '00000000-0000-4000-8000-000000000001', l.id, '2026-03-31', 'rent_credit', NULL,
    'golden interest', '2026-04-02 15:00:00+00'
)
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000042';

SELECT * FROM trust_report_deposit_interest('00000000-0000-4000-8000-000000000001', '2026-06-30');
