-- Where each lease with housing assistance in force stands on June 15, 2026. Worked by hand
-- (due within 120 days: on or before October 13, 2026):
--   Property 1 Unit 1  Tenant 1            annual 01/01/2026, due 01/01/2027: current;
--                                          passed 12/10/2025, next 12/10/2027: current
--   Property 1 Unit 2  Tenant 2, Tenant 3  annual 09/01/2025, due 09/01/2026: 78 days, due
--                                          within 120 days; failed 06/01/2026, reinspect by
--                                          07/01/2026: failed, reinspection due
--   Property 2 Unit 1  Tenant 4            annual 03/01/2025 (an interim 11/01/2025 keeps its
--                                          due day), due 03/01/2026: overdue; passed
--                                          03/01/2024, next 03/01/2026: overdue
--   Property 2 Unit 2  Tenant 5            none on file; none on file
--   Total: 2 to certify now (overdue, none), 1 within 120 days; 2 to inspect now (overdue,
--   none), 1 within 120 days or failed.
-- Left out: Property 2 Unit 3 (its assistance ended May 1) and Property 1 Unit 3 (none).
-- Nothing printed holds a household's income or size.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

-- Units: (id, property, name).
INSERT INTO trust_units (id, pmc_id, property_id, display_name)
SELECT u.id, '00000000-0000-4000-8000-000000000001', u.property, u.name
FROM (VALUES
    ('00000000-0000-4000-8000-000000000111'::uuid, '00000000-0000-4000-8000-000000000031'::uuid,
     'Unit 1'),
    ('00000000-0000-4000-8000-000000000112', '00000000-0000-4000-8000-000000000031', 'Unit 2'),
    ('00000000-0000-4000-8000-000000000113', '00000000-0000-4000-8000-000000000031', 'Unit 3'),
    ('00000000-0000-4000-8000-000000000121', '00000000-0000-4000-8000-000000000032', 'Unit 1'),
    ('00000000-0000-4000-8000-000000000122', '00000000-0000-4000-8000-000000000032', 'Unit 2'),
    ('00000000-0000-4000-8000-000000000123', '00000000-0000-4000-8000-000000000032', 'Unit 3')
) AS u (id, property, name);

-- Tenants: (id, property, name).
INSERT INTO trust_tenants (id, pmc_id, property_id, display_name)
SELECT t.id, '00000000-0000-4000-8000-000000000001', t.property, t.name
FROM (VALUES
    ('00000000-0000-4000-8000-000000000201'::uuid, '00000000-0000-4000-8000-000000000031'::uuid,
     'Tenant 1'),
    ('00000000-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000031', 'Tenant 2'),
    ('00000000-0000-4000-8000-000000000203', '00000000-0000-4000-8000-000000000031', 'Tenant 3'),
    ('00000000-0000-4000-8000-000000000204', '00000000-0000-4000-8000-000000000032', 'Tenant 4'),
    ('00000000-0000-4000-8000-000000000205', '00000000-0000-4000-8000-000000000032', 'Tenant 5'),
    ('00000000-0000-4000-8000-000000000206', '00000000-0000-4000-8000-000000000032', 'Tenant 6'),
    ('00000000-0000-4000-8000-000000000207', '00000000-0000-4000-8000-000000000031', 'Tenant 7')
) AS t (id, property, name);

-- Leases, each month to month from January 1, 2024: (unit, tenants).
SELECT trust_open_lease('00000000-0000-4000-8000-000000000001', l.unit, '2024-01-01', NULL,
                        1400.00, l.tenants)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000111'::uuid,
     ARRAY['00000000-0000-4000-8000-000000000201'::uuid]),
    (2, '00000000-0000-4000-8000-000000000112',
     ARRAY['00000000-0000-4000-8000-000000000203'::uuid, '00000000-0000-4000-8000-000000000202']),
    (3, '00000000-0000-4000-8000-000000000121', ARRAY['00000000-0000-4000-8000-000000000204'::uuid]),
    (4, '00000000-0000-4000-8000-000000000122', ARRAY['00000000-0000-4000-8000-000000000205'::uuid]),
    (5, '00000000-0000-4000-8000-000000000123', ARRAY['00000000-0000-4000-8000-000000000206'::uuid]),
    (6, '00000000-0000-4000-8000-000000000113', ARRAY['00000000-0000-4000-8000-000000000207'::uuid])
) AS l (n, unit, tenants)
ORDER BY l.n;

INSERT INTO trust_housing_authorities (id, pmc_id, name)
VALUES ('00000000-0000-4000-8000-000000000501', '00000000-0000-4000-8000-000000000001',
        'Golden Housing Authority');

-- Assistance: (unit, from, monthly amount); Property 2 Unit 3's ends May 1, 2026.
INSERT INTO trust_rent_assistance (pmc_id, lease_id, housing_authority_id, starts_on, amount)
SELECT '00000000-0000-4000-8000-000000000001', l.id, '00000000-0000-4000-8000-000000000501',
       a.starts, a.amount
FROM (VALUES
    ('00000000-0000-4000-8000-000000000111'::uuid, '2024-01-01'::date, 600.00),
    ('00000000-0000-4000-8000-000000000112', '2024-01-01', 900.00),
    ('00000000-0000-4000-8000-000000000121', '2024-01-01', 750.00),
    ('00000000-0000-4000-8000-000000000122', '2024-01-01', 500.00),
    ('00000000-0000-4000-8000-000000000123', '2024-01-01', 400.00),
    ('00000000-0000-4000-8000-000000000123', '2026-05-01', 0.00)
) AS a (unit, starts, amount)
JOIN trust_leases AS l ON l.unit_id = a.unit;

-- Certifications: (unit, kind, effective, household, income, next annual due).
INSERT INTO trust_income_certifications (pmc_id, lease_id, kind, effective_on, household_size,
                                         annual_income, next_due_on)
SELECT '00000000-0000-4000-8000-000000000001', l.id, c.kind, c.effective, c.household,
       c.income, c.due
FROM (VALUES
    ('00000000-0000-4000-8000-000000000111'::uuid, 'move-in', '2024-01-01'::date, 2, 21000.00,
     '2025-01-01'::date),
    ('00000000-0000-4000-8000-000000000111', 'annual', '2026-01-01', 2, 22400.00, '2027-01-01'),
    ('00000000-0000-4000-8000-000000000112', 'annual', '2025-09-01', 4, 30500.00, '2026-09-01'),
    ('00000000-0000-4000-8000-000000000121', 'annual', '2025-03-01', 1, 14000.00, '2026-03-01'),
    ('00000000-0000-4000-8000-000000000121', 'interim', '2025-11-01', 1, 9000.00, '2026-03-01'),
    ('00000000-0000-4000-8000-000000000123', 'annual', '2026-01-01', 3, 19000.00, '2027-01-01')
) AS c (unit, kind, effective, household, income, due)
JOIN trust_leases AS l ON l.unit_id = c.unit;

-- Inspections: (unit, day, result, next due or reinspect by).
INSERT INTO trust_assistance_inspections (pmc_id, lease_id, housing_authority_id, inspected_on,
                                          result, due_on)
SELECT '00000000-0000-4000-8000-000000000001', l.id, '00000000-0000-4000-8000-000000000501',
       i.day, i.result, i.due
FROM (VALUES
    ('00000000-0000-4000-8000-000000000111'::uuid, '2025-12-10'::date, 'passed',
     '2027-12-10'::date),
    ('00000000-0000-4000-8000-000000000112', '2024-06-01', 'passed', '2026-06-01'),
    ('00000000-0000-4000-8000-000000000112', '2026-06-01', 'failed', '2026-07-01'),
    ('00000000-0000-4000-8000-000000000121', '2024-03-01', 'passed', '2026-03-01')
) AS i (unit, day, result, due)
JOIN trust_leases AS l ON l.unit_id = i.unit;

SELECT * FROM trust_report_assistance_compliance('00000000-0000-4000-8000-000000000001',
                                                 '2026-06-15');
