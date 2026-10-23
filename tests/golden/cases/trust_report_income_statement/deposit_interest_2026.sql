-- Owner 1's income statement (cash basis) for 2026 with interest added to a tenant's deposit
-- (migration 0122). Worked by hand: Owner 1 put 500.00 into Property 1 (a contribution, not
-- income); Tenant 1's deposit of 1200.00 has earned 1.5% a year since 2026-01-01. On April 2 its
-- interest for January to March, 1200 x 0.015 x 90 / 365 = 4.438356, rounds to 4.44 and moves
-- from the owner's account to the deposit: an expense of its own, on Schedule E's line 19
-- (Other). Its interest for April to December is added on 2027-01-04, after the statement's
-- days, so it is not here. No rent was charged or collected: income 0.00, net income -4.44.
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
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

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
     '00000000-0000-4000-8000-000000000061')
) AS a (n, bank, kind, owner, property, tenant)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-01-01', NULL, 1500.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);

INSERT INTO trust_deposit_interest_terms (pmc_id, property_id, starts_on, annual_percent) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000031', '2026-01-01',
 1.5);

SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001', t.ledger_account_id, 'golden-contribution', 500.00,
    '2026-01-01 12:00:00+00'
)
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

SELECT pgledger_create_transfer(
    (SELECT c.ledger_account_id FROM trust_ledger_accounts AS c
     WHERE c.bank_account_id = '00000000-0000-4000-8000-000000000012' AND c.kind = 'bank_cash'),
    (SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
     WHERE t.tenant_id = '00000000-0000-4000-8000-000000000061' AND t.kind = 'tenant_deposit'),
    1200.00, '2026-01-01 15:00:00+00'::timestamptz, jsonb_build_object('memo', 'Golden')
);

-- Interest added to the deposit from the owner's account: January to March on April 2, and
-- April to December on January 4, 2027, after the statement's days.
SELECT trust_pay_deposit_interest(
    '00000000-0000-4000-8000-000000000001', l.id, p.through, 'deposit', NULL, p.request_key,
    p.paid_at
)
FROM trust_leases AS l
CROSS JOIN (VALUES
    (1, '2026-03-31'::date, 'golden interest 1', '2026-04-02 15:00:00+00'::timestamptz),
    (2, '2026-12-31', 'golden interest 2', '2027-01-04 15:00:00+00')
) AS p (n, through, request_key, paid_at)
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'
ORDER BY p.n;

SELECT * FROM trust_report_income_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-12-31'
);
