-- A synthetic lease of Unit 1 at Property 1, January to June 2026, for Tenant A, who paid a
-- security deposit of 1000.00 and a pet deposit of 400.00 (migration 0092). At move-out the lease
-- owes June's rent (1000.00) and a carpet cleaned after the dog (150.00). Worked by hand:
--   held = 1000.00 + 400.00 = 1400.00
--   owed = 1150.00, all kept: the security deposit's 1000.00 for June's rent, then 150.00 of the
--   pet deposit for the carpet
--   returned = 1400.00 - 1150.00 = 250.00, of the pet deposit
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

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000035',
    '2026-01-01', '2026-06-30', 1000.00, ARRAY['00000000-0000-4000-8000-000000000041']::uuid[]
);

-- The deposits, into the security deposit trust account in January: the pet deposit's account
-- opened first, yet the security deposit is kept from first.
SELECT count(*) FROM trust_post_transfers(
    '00000000-0000-4000-8000-000000000001',
    'deposits-2026-01',
    ARRAY(
        SELECT (cash.ledger_account_id,
                trust_open_deposit_account(
                    '00000000-0000-4000-8000-000000000001',
                    '00000000-0000-4000-8000-000000000012',
                    '00000000-0000-4000-8000-000000000041', d.kind
                ),
                d.amount)::transfer_request
        FROM (VALUES (1, 'pet', 400.00), (2, 'security', 1000.00)) AS d (n, kind, amount)
        CROSS JOIN trust_ledger_accounts AS cash
        WHERE cash.bank_account_id = '00000000-0000-4000-8000-000000000012'
          AND cash.kind = 'bank_cash'
        ORDER BY d.n
    ),
    '2026-01-02 15:00:00+00',
    '{"memo": "Deposits"}'
);

INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT c.id, '00000000-0000-4000-8000-000000000001', l.id, c.due_on, c.kind, c.amount, c.memo
FROM trust_leases AS l
CROSS JOIN (VALUES
    ('00000000-0000-4000-8000-000000000071'::uuid, '2026-06-01'::date, 'rent', 1000.00,
     'June rent'),
    ('00000000-0000-4000-8000-000000000072'::uuid, '2026-06-30'::date, 'fee', 150.00,
     'Carpet cleaned after the dog')
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
