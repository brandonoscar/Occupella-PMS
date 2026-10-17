-- A voided renewal offer is no renewal out (migration 0071), as of the end of October 1, 2026
-- (UTC). Worked by hand (days left = last day - Oct 1):
--   0-30   Property 1  Unit 1  Tenant 1  Oct 31  30  1500.00  none (its offer, sent Sep 20,
--                                                            was voided Sep 30)
--          Property 1  Unit 2  Tenant 2  Oct 31  30  1250.00  sent (its offer, sent Sep 20,
--                                                            is voided only on Oct 5)
--          total 2750.00, 1 of 2 with no renewal offer out
--   31-60  none: total 0.00, 0 of 0;  61-90  none: total 0.00, 0 of 0
--   Total 2750.00, 1 of 2 with no renewal offer out.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_staff (id, pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
        'Golden manager');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000111', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1'),
('00000000-0000-4000-8000-000000000112', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 2');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1'),
('00000000-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 2');

SELECT trust_open_lease('00000000-0000-4000-8000-000000000001',
                        '00000000-0000-4000-8000-000000000111', '2025-11-01', '2026-10-31',
                        1500.00, ARRAY['00000000-0000-4000-8000-000000000201'::uuid]);
SELECT trust_open_lease('00000000-0000-4000-8000-000000000001',
                        '00000000-0000-4000-8000-000000000112', '2025-11-01', '2026-10-31',
                        1250.00, ARRAY['00000000-0000-4000-8000-000000000202'::uuid]);

-- Renewal offers made Sep 15, each for the year after its lease.
INSERT INTO trust_lease_offers (
    id, pmc_id, renews_lease_id, unit_id, starts_on, ends_on, monthly_rent, document_title,
    document, offered_by, offered_at
)
SELECT o.id, '00000000-0000-4000-8000-000000000001', l.id, l.unit_id, l.ends_on + 1,
       l.ends_on + 365, l.monthly_rent, 'Golden renewal', convert_to('%PDF-1.4 golden', 'UTF8'),
       '00000000-0000-4000-8000-000000000061', '2026-09-15 09:00:00+00'
FROM (VALUES
    ('00000000-0000-4000-8000-000000000301'::uuid, '00000000-0000-4000-8000-000000000111'::uuid),
    ('00000000-0000-4000-8000-000000000302', '00000000-0000-4000-8000-000000000112')
) AS o (id, unit)
JOIN trust_leases AS l ON l.unit_id = o.unit;

INSERT INTO trust_lease_offer_signers (offer_id, position, pmc_id, display_name, email) VALUES
('00000000-0000-4000-8000-000000000301', 1, '00000000-0000-4000-8000-000000000001', 'Tenant 1',
 'tenant1@example.com'),
('00000000-0000-4000-8000-000000000302', 1, '00000000-0000-4000-8000-000000000001', 'Tenant 2',
 'tenant2@example.com');

INSERT INTO trust_signature_requests (offer_id, pmc_id, partner, partner_reference, sent_by,
                                      sent_at)
VALUES
('00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000001', 'golden',
 'golden-301', '00000000-0000-4000-8000-000000000061', '2026-09-20 09:00:00+00'),
('00000000-0000-4000-8000-000000000302', '00000000-0000-4000-8000-000000000001', 'golden',
 'golden-302', '00000000-0000-4000-8000-000000000061', '2026-09-20 09:00:00+00');

-- Voided: Unit 1's on Sep 30, Unit 2's on Oct 5, after the day the report is as of.
INSERT INTO trust_lease_offer_voids (offer_id, pmc_id, reason, recorded_at) VALUES
('00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000001',
 'The tenant is moving out', '2026-09-30 09:00:00+00'),
('00000000-0000-4000-8000-000000000302', '00000000-0000-4000-8000-000000000001',
 'Terms changed', '2026-10-05 09:00:00+00');

SELECT * FROM trust_report_lease_expirations('00000000-0000-4000-8000-000000000001', '2026-10-01');
