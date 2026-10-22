-- Tax credit compliance at the end of June 30, 2026. Property 1's 50% limits for households of
-- one to eight: from 2025-01-01 28000, 32000, 36000, 40000, ...; from 2026-04-01 30000, 34300,
-- 38600, 42850, ... Property 2 has none. Worked by hand:
--   Unit 1  60%, one bedroom, allowance 50.00. Certified at move-in 2026-01-01, two people,
--           37000.00: the 2025 limit is 32000 x 60/50 = 38400, so qualified; due 2027-01-01,
--           185 days on: current. Most gross rent (2026 limits): one and a half people at 60%,
--           (36000 + 41160) / 2 = 38580 x 30% / 12 = 964.50, down to 964; 900.00 + 50.00 = 950.00.
--   Unit 2  50%, two bedrooms, allowance 75.00. Move-in 2025-07-01, three people, 35000.00 under
--           36000; the annual one of 2026-06-01, 55000.00, is over 140% of 38600 (54040): the next
--           available unit rule. Most gross rent: three people at 50%, 38600 x 30% / 12 = 965;
--           1100.00 + 75.00 = 1175.00, over by 210.00.
--   Unit 3  60%, no bedroom: vacant; one person at 60%, 36000 x 30% / 12 = 900.
--   Unit 4  a tax credit unit until 2026-06-01: not listed.
--   Unit 5  40%, one bedroom, no allowance; leased with no certification. (24000 + 27440) / 2 =
--           25720 x 30% / 12 = 643.
--   Unit 6  Property 2, 60%: no income limits on file.
-- Property 1: Units 1 and 2 qualified (the 140% rule keeps a unit qualified) of four.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_units (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000004' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       CASE WHEN n = 6 THEN '00000000-0000-4000-8000-000000000032'::uuid
            ELSE '00000000-0000-4000-8000-000000000031'::uuid END,
       'Unit ' || n
FROM generate_series(1, 6) AS n;

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000006' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       CASE WHEN n = 6 THEN '00000000-0000-4000-8000-000000000032'::uuid
            ELSE '00000000-0000-4000-8000-000000000031'::uuid END,
       'Tenant ' || n
FROM generate_series(1, 6) AS n;

INSERT INTO trust_tax_credit_limits (pmc_id, property_id, starts_on, limits) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000031', '2025-01-01',
 ARRAY[28000, 32000, 36000, 40000, 43200, 46400, 49600, 52800]),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000031', '2026-04-01',
 ARRAY[30000, 34300, 38600, 42850, 46300, 49750, 53150, 56600]);

INSERT INTO trust_tax_credit_units
    (pmc_id, unit_id, starts_on, income_percent, bedrooms, utility_allowance)
SELECT '00000000-0000-4000-8000-000000000001', ('00000000-0000-4000-8000-00000000004' || d.n)::uuid,
       d.starts_on, d.percent, d.bedrooms, d.allowance
FROM (VALUES (1, '2026-01-01'::date, 60, 1, 50.00),
             (2, '2025-07-01', 50, 2, 75.00),
             (3, '2026-01-01', 60, 0, 40.00),
             (4, '2026-01-01', 60, 1, 0.00),
             (4, '2026-06-01', NULL, NULL, NULL),
             (5, '2026-01-01', 40, 1, 0.00),
             (6, '2026-01-01', 60, 1, 0.00)) AS d (n, starts_on, percent, bedrooms, allowance);

-- Leases: (unit, first day, rent); Unit 3 is vacant.
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', ('00000000-0000-4000-8000-00000000004' || l.n)::uuid,
    l.starts_on, NULL, l.rent, ARRAY[('00000000-0000-4000-8000-00000000006' || l.n)::uuid]
)
FROM (VALUES (1, '2026-01-01'::date, 900.00),
             (2, '2025-07-01', 1100.00),
             (4, '2026-01-01', 800.00),
             (5, '2026-02-01', 500.00),
             (6, '2026-01-01', 700.00)) AS l (n, starts_on, rent)
ORDER BY l.n;

INSERT INTO trust_income_certifications
    (pmc_id, lease_id, kind, effective_on, household_size, annual_income, next_due_on)
SELECT '00000000-0000-4000-8000-000000000001', l.id, c.kind, c.effective_on, c.size, c.income,
       c.next_due_on
FROM (VALUES (1, 'move-in', '2026-01-01'::date, 2, 37000.00, '2027-01-01'::date),
             (2, 'move-in', '2025-07-01', 3, 35000.00, '2026-07-01'),
             (2, 'annual', '2026-06-01', 3, 55000.00, '2027-06-01'),
             (6, 'move-in', '2026-01-01', 1, 20000.00, '2027-01-01'))
     AS c (n, kind, effective_on, size, income, next_due_on)
JOIN trust_leases AS l ON l.unit_id = ('00000000-0000-4000-8000-00000000004' || c.n)::uuid;

SELECT * FROM trust_report_tax_credit_compliance('00000000-0000-4000-8000-000000000001', '2026-06-30');
