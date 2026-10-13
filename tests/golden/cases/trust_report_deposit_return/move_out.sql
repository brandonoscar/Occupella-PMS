-- A synthetic lease of Unit 1 at Property 1, January to June 2026, for Tenant A and Tenant B,
-- who paid deposits of 600.00 and 900.00. At move-out the lease owes June's rent (1000.00), a
-- cleaning fee (150.00) and a broken window (320.00), less a 100.00 credit. Worked by hand:
--   held = 600.00 + 900.00 = 1500.00
--   owed = 1000.00 + 150.00 + 320.00 - 100.00 = 1370.00, all kept (it is less than held)
--   kept from Tenant A's deposit first (names in order): 600.00 of June's rent; then from
--   Tenant B's: the other 400.00 of June's rent, 150.00 of cleaning and 220.00 of the window
--   (its last 100.00 is the credit's)
--   returned = 1500.00 - 1370.00 = 130.00, all to Tenant B
-- Manager 1, the PMC's first admin, gives themself the disburse role and returns it on Jul 5.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000035', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant B'),
('00000000-0000-4000-8000-000000000042', '00000000-0000-4000-8000-000000000001',
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
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000031', NULL
);
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'bank_cash', NULL, NULL, NULL
);
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'tenant_deposit', NULL, NULL, t.id
)
FROM (VALUES ('00000000-0000-4000-8000-000000000041'::uuid),
             ('00000000-0000-4000-8000-000000000042'::uuid)) AS t (id);

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000035',
    '2026-01-01', '2026-06-30', 1000.00,
    ARRAY['00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000042']::uuid[]
);

-- The deposits, into the security deposit trust account in January.
SELECT count(*) FROM trust_post_transfers(
    '00000000-0000-4000-8000-000000000001',
    'deposits-2026-01',
    ARRAY(
        SELECT (cash.ledger_account_id, d.ledger_account_id,
                CASE d.tenant_id WHEN '00000000-0000-4000-8000-000000000042' THEN 600.00
                                 ELSE 900.00 END)::transfer_request
        FROM trust_ledger_accounts AS d
        JOIN trust_ledger_accounts AS cash
          ON cash.bank_account_id = d.bank_account_id AND cash.kind = 'bank_cash'
        WHERE d.kind = 'tenant_deposit' AND d.pmc_id = '00000000-0000-4000-8000-000000000001'
        ORDER BY d.tenant_id
    ),
    '2026-01-02 15:00:00+00',
    '{"memo": "Security deposits"}'
);

-- Charges due the same day are paid in the order of their ids, which are fixed here.
INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT c.id, '00000000-0000-4000-8000-000000000001', l.id, c.due_on, c.kind, c.amount, c.memo
FROM trust_leases AS l
CROSS JOIN (VALUES
    ('00000000-0000-4000-8000-000000000071'::uuid, '2026-06-01'::date, 'rent', 1000.00,
     'June rent'),
    ('00000000-0000-4000-8000-000000000072'::uuid, '2026-06-30'::date, 'fee', 150.00,
     'Cleaning'),
    ('00000000-0000-4000-8000-000000000073'::uuid, '2026-06-30'::date, 'fee', 320.00,
     'Broken window'),
    ('00000000-0000-4000-8000-000000000074'::uuid, '2026-06-30'::date, 'credit', 100.00,
     'Blinds left in place')
) AS c (id, due_on, kind, amount, memo)
WHERE l.unit_id = '00000000-0000-4000-8000-000000000035';

SELECT trust_return_deposit(
    '00000000-0000-4000-8000-000000000001', l.id, NULL, 'move-out-unit-1',
    '2026-07-05 16:00:00+00'
)
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000035';

SELECT r.* FROM trust_leases AS l
CROSS JOIN LATERAL trust_report_deposit_return('00000000-0000-4000-8000-000000000001', l.id) AS r
WHERE l.unit_id = '00000000-0000-4000-8000-000000000035';
