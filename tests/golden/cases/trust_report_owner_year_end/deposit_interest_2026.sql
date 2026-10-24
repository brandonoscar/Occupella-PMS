-- Owner 1's 2026 year-end package with interest added to a security deposit (migration
-- 20261023000200): a part of its own, not "other". Worked by hand, a year of 365 days:
--   Unit 1  Tenant 1's 1200.00 from 2026-01-01: January to March, 90 days at 1.5%, is
--           1200 x 0.015 x 90 / 365 = 4.438356: 4.44 added to the deposit on April 2. April to
--           June, 91 days on 1204.44: 1204.44 x 0.015 x 91 / 365 = 4.504211: 4.50 added on July 1.
--           Its interest for July to December is added on January 2, 2027: after the year.
--   Unit 2  Tenant 2's 1000.00: January to March, 3.70, credited against its rent: no money moved.
--   Unit 3  Property 2 has no terms: no interest.
-- Property 1: opening 0.00, contributions 500.00, security deposit interest -8.94,
-- other 0.00, closing 491.06. Property 2: all 0.00.
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
       CASE WHEN n = 3 THEN '00000000-0000-4000-8000-000000000032'::uuid
            ELSE '00000000-0000-4000-8000-000000000031'::uuid END,
       'Unit ' || n
FROM generate_series(1, 3) AS n;

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000006' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       CASE WHEN n = 3 THEN '00000000-0000-4000-8000-000000000032'::uuid
            ELSE '00000000-0000-4000-8000-000000000031'::uuid END,
       'Tenant ' || n
FROM generate_series(1, 3) AS n;

-- Ledger accounts: (bank, kind, owner, property, tenant).
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', a.bank, a.kind, a.owner, a.property, a.tenant
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000011'::uuid, 'bank_cash', NULL::uuid, NULL::uuid,
     NULL::uuid),
    (2, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000031', NULL),
    (3, '00000000-0000-4000-8000-000000000011', 'owner_property',
     '00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000032', NULL),
    (4, '00000000-0000-4000-8000-000000000012', 'bank_cash', NULL, NULL, NULL),
    (5, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000061'),
    (6, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000062'),
    (7, '00000000-0000-4000-8000-000000000012', 'tenant_deposit', NULL, NULL,
     '00000000-0000-4000-8000-000000000063')
) AS a (n, bank, kind, owner, property, tenant)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', ('00000000-0000-4000-8000-00000000004' || n)::uuid,
    '2026-01-01', NULL, 1500.00, ARRAY[('00000000-0000-4000-8000-00000000006' || n)::uuid]
)
FROM generate_series(1, 3) AS n;

-- Property 1's deposits earn 1.5% a year; Property 2 has no terms.
INSERT INTO trust_deposit_interest_terms (pmc_id, property_id, starts_on, annual_percent) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000031', '2026-01-01',
 1.5);

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
    (3, '00000000-0000-4000-8000-000000000063', 800.00)
) AS d (n, tenant, amount)
ORDER BY d.n;

-- Owner 1 puts 500.00 into Property 1's account, from which interest added to a deposit comes.
SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001',
    (SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
     WHERE t.property_id = '00000000-0000-4000-8000-000000000031' AND t.kind = 'owner_property'),
    'golden contribution', 500.00, '2026-01-02 15:00:00+00', 'Golden'
);

-- Unit 1's interest added to its deposit for January to March, on April 2, and for April to June
-- on July 1; Unit 2's credited against its rent for January to March; Unit 1's for July to
-- December added on January 2, 2027.
SELECT trust_pay_deposit_interest(
    '00000000-0000-4000-8000-000000000001', l.id, p.through, p.paid_as, NULL, p.request_key,
    p.paid_at
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000041'::uuid, '2026-03-31'::date, 'deposit',
     'golden interest 1', '2026-04-02 15:00:00+00'::timestamptz),
    (2, '00000000-0000-4000-8000-000000000042', '2026-03-31', 'rent_credit', 'golden interest 2',
     '2026-04-02 15:00:00+00'),
    (3, '00000000-0000-4000-8000-000000000041', '2026-06-30', 'deposit', 'golden interest 3',
     '2026-07-01 15:00:00+00'),
    (4, '00000000-0000-4000-8000-000000000041', '2026-12-31', 'deposit', 'golden interest 4',
     '2027-01-02 15:00:00+00')
) AS p (n, unit, through, paid_as, request_key, paid_at)
JOIN trust_leases AS l ON l.unit_id = p.unit
ORDER BY p.n;

SELECT * FROM trust_report_owner_year_end(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021', 2026
);
