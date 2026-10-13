-- Leases ending in the 90 days from October 1, 2026 (end of day UTC). Worked by hand
-- (days left = last day - Oct 1):
--   0-30   Property 1  Unit 1  Tenant 1            Oct 1     0   1500.00  none (an offer
--                                                                        made Oct 2 is later)
--          Property 1  Unit 2  Tenant 2, Tenant 3  Oct 31   30   1450.50  sent (one of two
--                                                                        signers signed)
--          total 2950.50, 1 of 2 with no renewal offer out
--   31-60  Property 2  Unit 1  Tenant 4            Nov 15   45   1200.00  signed
--          total 1200.00, 0 of 1
--   61-90  Property 2  Unit 2  Tenant 5            Dec 30   90   1100.00  drafted (not sent)
--          total 1100.00, 1 of 1
--   Total 5250.50, 2 of 4 with no renewal offer out.
-- Not listed: Property 1 Unit 3 (ended Sep 30), Property 2 Unit 3 (Dec 31, 91 days away),
-- Property 1 Unit 4 (month to month).
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_staff (id, pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
        'Golden manager');

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
    ('00000000-0000-4000-8000-000000000114', '00000000-0000-4000-8000-000000000031', 'Unit 4'),
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
    ('00000000-0000-4000-8000-000000000206', '00000000-0000-4000-8000-000000000031', 'Tenant 6'),
    ('00000000-0000-4000-8000-000000000207', '00000000-0000-4000-8000-000000000032', 'Tenant 7'),
    ('00000000-0000-4000-8000-000000000208', '00000000-0000-4000-8000-000000000031', 'Tenant 8')
) AS t (id, property, name);

-- Leases: (unit, first day, last day, rent, tenants).
SELECT trust_open_lease('00000000-0000-4000-8000-000000000001', l.unit, l.starts, l.ends, l.rent,
                        l.tenants)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000111'::uuid, '2025-10-02'::date, '2026-10-01'::date,
     1500.00, ARRAY['00000000-0000-4000-8000-000000000201'::uuid]),
    (2, '00000000-0000-4000-8000-000000000112', '2025-11-01', '2026-10-31', 1450.50,
     ARRAY['00000000-0000-4000-8000-000000000203'::uuid, '00000000-0000-4000-8000-000000000202']),
    (3, '00000000-0000-4000-8000-000000000121', '2025-11-16', '2026-11-15', 1200.00,
     ARRAY['00000000-0000-4000-8000-000000000204'::uuid]),
    (4, '00000000-0000-4000-8000-000000000122', '2026-01-01', '2026-12-30', 1100.00,
     ARRAY['00000000-0000-4000-8000-000000000205'::uuid]),
    (5, '00000000-0000-4000-8000-000000000113', '2025-10-01', '2026-09-30', 900.00,
     ARRAY['00000000-0000-4000-8000-000000000206'::uuid]),
    (6, '00000000-0000-4000-8000-000000000123', '2026-01-01', '2026-12-31', 1000.00,
     ARRAY['00000000-0000-4000-8000-000000000207'::uuid]),
    (7, '00000000-0000-4000-8000-000000000114', '2026-01-01', NULL, 950.00,
     ARRAY['00000000-0000-4000-8000-000000000208'::uuid])
) AS l (n, unit, starts, ends, rent, tenants)
ORDER BY l.n;

-- Renewal offers: (offer, unit renewed, made on), each for the year after the lease.
INSERT INTO trust_lease_offers (
    id, pmc_id, renews_lease_id, unit_id, starts_on, ends_on, monthly_rent, document_title,
    document, offered_by, offered_at
)
SELECT o.id, '00000000-0000-4000-8000-000000000001', l.id, l.unit_id, l.ends_on + 1,
       l.ends_on + 365, l.monthly_rent, 'Golden renewal', convert_to('%PDF-1.4 golden', 'UTF8'),
       '00000000-0000-4000-8000-000000000061', o.made
FROM (VALUES
    ('00000000-0000-4000-8000-000000000301'::uuid, '00000000-0000-4000-8000-000000000111'::uuid,
     '2026-10-02 09:00:00+00'::timestamptz),
    ('00000000-0000-4000-8000-000000000302', '00000000-0000-4000-8000-000000000112',
     '2026-09-15 09:00:00+00'),
    ('00000000-0000-4000-8000-000000000303', '00000000-0000-4000-8000-000000000121',
     '2026-09-15 09:00:00+00'),
    ('00000000-0000-4000-8000-000000000304', '00000000-0000-4000-8000-000000000122',
     '2026-09-28 09:00:00+00')
) AS o (id, unit, made)
JOIN trust_leases AS l ON l.unit_id = o.unit;

-- Signers: (offer, position, name, email).
INSERT INTO trust_lease_offer_signers (offer_id, position, pmc_id, display_name, email)
SELECT s.offer, s.position, '00000000-0000-4000-8000-000000000001', s.name, s.email
FROM (VALUES
    ('00000000-0000-4000-8000-000000000301'::uuid, 1, 'Tenant 1', 'tenant1@example.com'),
    ('00000000-0000-4000-8000-000000000302', 1, 'Tenant 2', 'tenant2@example.com'),
    ('00000000-0000-4000-8000-000000000302', 2, 'Tenant 3', 'tenant3@example.com'),
    ('00000000-0000-4000-8000-000000000303', 1, 'Tenant 4', 'tenant4@example.com'),
    ('00000000-0000-4000-8000-000000000304', 1, 'Tenant 5', 'tenant5@example.com')
) AS s (offer, position, name, email);

-- Sent to sign: Unit 2's on Sep 20, Property 2 Unit 1's on Sep 22.
INSERT INTO trust_signature_requests (offer_id, pmc_id, partner, partner_reference, sent_by,
                                      sent_at)
VALUES
('00000000-0000-4000-8000-000000000302', '00000000-0000-4000-8000-000000000001', 'golden',
 'golden-302', '00000000-0000-4000-8000-000000000061', '2026-09-20 09:00:00+00'),
('00000000-0000-4000-8000-000000000303', '00000000-0000-4000-8000-000000000001', 'golden',
 'golden-303', '00000000-0000-4000-8000-000000000061', '2026-09-22 09:00:00+00');

-- Signed: Unit 2's first signer only; Property 2 Unit 1's only signer.
INSERT INTO trust_signatures (offer_id, position, pmc_id, signed_at, recorded_at)
VALUES
('00000000-0000-4000-8000-000000000302', 1, '00000000-0000-4000-8000-000000000001',
 '2026-09-21 12:00:00+00', '2026-09-21 12:05:00+00'),
('00000000-0000-4000-8000-000000000303', 1, '00000000-0000-4000-8000-000000000001',
 '2026-09-25 12:00:00+00', '2026-09-25 12:05:00+00');

SELECT * FROM trust_report_lease_expirations('00000000-0000-4000-8000-000000000001', '2026-10-01');
