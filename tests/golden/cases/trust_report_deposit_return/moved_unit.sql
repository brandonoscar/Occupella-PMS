-- A synthetic tenant, Tenant A, rents Unit 1 at Property 1 (Owner 1's) from January 2026, month
-- to month, with a 2500.00 deposit, and leaves March's rent (1000.00) unpaid. On April 1 they move
-- to Unit 7 at Property 2 (Owner 2's), having agreed in writing on March 25 that their deposit
-- stays held for the new lease, April to June at 1100.00; June's rent (1100.00) is unpaid too.
-- Worked by hand:
--   held = 2500.00
--   owed = 1000.00 at Unit 1 (Owner 1's, kept first: the older lease) + 1100.00 at Unit 7
--        = 2100.00, all kept
--   returned = 2500.00 - 2100.00 = 400.00, to Tenant A
-- Manager 1, the PMC's first admin, gives themself the disburse role and returns it on Jul 5.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000035', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1'),
('00000000-0000-4000-8000-000000000036', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Unit 7');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant A');

INSERT INTO trust_staff (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001', 'Manager 1');

INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000061', 'admin', true);
SELECT set_config('trust.staff_id', '00000000-0000-4000-8000-000000000061', true);
INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000061', 'disburse',
 true);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', o.owner_id, o.property_id, NULL
)
FROM (VALUES ('00000000-0000-4000-8000-000000000021'::uuid,
              '00000000-0000-4000-8000-000000000031'::uuid),
             ('00000000-0000-4000-8000-000000000022'::uuid,
              '00000000-0000-4000-8000-000000000032'::uuid)) AS o (owner_id, property_id);
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'bank_cash', NULL, NULL, NULL
);
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'tenant_deposit', NULL, NULL, '00000000-0000-4000-8000-000000000041'
);

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000035',
    '2026-01-01', NULL, 1000.00, ARRAY['00000000-0000-4000-8000-000000000041']::uuid[]
);

-- The deposit, into the security deposit trust account in January.
SELECT count(*) FROM trust_post_transfers(
    '00000000-0000-4000-8000-000000000001',
    'deposit-2026-01',
    ARRAY(
        SELECT (cash.ledger_account_id, d.ledger_account_id, 2500.00)::transfer_request
        FROM trust_ledger_accounts AS d
        JOIN trust_ledger_accounts AS cash
          ON cash.bank_account_id = d.bank_account_id AND cash.kind = 'bank_cash'
        WHERE d.kind = 'tenant_deposit' AND d.pmc_id = '00000000-0000-4000-8000-000000000001'
    ),
    '2026-01-02 15:00:00+00',
    '{"memo": "Security deposit"}'
);

INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT '00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001', l.id,
       '2026-03-01', 'rent', 1000.00, 'March rent'
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000035';

-- The move, to Unit 7 on April 1, April to June.
SELECT trust_transfer_lease(
    '00000000-0000-4000-8000-000000000001', l.id, '00000000-0000-4000-8000-000000000036',
    '2026-04-01', '2026-06-30', 1100.00, NULL, '2026-03-25'
)
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000035';

INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT '00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001', l.id,
       '2026-06-01', 'rent', 1100.00, 'June rent'
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000036';

SELECT trust_return_deposit(
    '00000000-0000-4000-8000-000000000001', l.id, NULL, 'move-out-unit-7',
    '2026-07-05 16:00:00+00'
)
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000036';

SELECT r.* FROM trust_leases AS l
CROSS JOIN LATERAL trust_report_deposit_return('00000000-0000-4000-8000-000000000001', l.id) AS r
WHERE l.unit_id = '00000000-0000-4000-8000-000000000036';
