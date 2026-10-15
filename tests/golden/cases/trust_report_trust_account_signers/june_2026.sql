-- Who signs each trust bank account at the end of June 30, 2026. Worked by hand:
--   Deposits     Broker 1, the broker (licensed); Clerk 2, bonded for 50000.00 through
--                2026-12-31: bonded.
--   Operating A  Broker 1, the broker (licensed); Clerk 2 bonded through 2026-07-15: runs out in
--                15 days; Clerk 4 bonded through 2026-06-29: ran out. Clerk 3 was authorized
--                and taken away on 2026-06-01: not listed. Clerk 5 is authorized from
--                2026-07-01: not yet listed.
--   Operating B  no broker on file.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating', 'Operating A'),
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001', 'operating', 'Operating B'),
('00000000-0000-4000-8000-000000000013', '00000000-0000-4000-8000-000000000001', 'security_deposit', 'Deposits');

INSERT INTO trust_staff (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Broker 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Clerk 2'),
('00000000-0000-4000-8000-000000000023', '00000000-0000-4000-8000-000000000001', 'Clerk 3'),
('00000000-0000-4000-8000-000000000024', '00000000-0000-4000-8000-000000000001', 'Clerk 4'),
('00000000-0000-4000-8000-000000000025', '00000000-0000-4000-8000-000000000001', 'Clerk 5');

-- In the order they were recorded (the latest row for an account and a staff member holds).
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '00000000-0000-4000-8000-000000000021', 'broker', true, 'LIC-0001', NULL, NULL, '2026-01-01');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000013',
 '00000000-0000-4000-8000-000000000021', 'broker', true, 'LIC-0001', NULL, NULL, '2026-01-01');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '00000000-0000-4000-8000-000000000022', 'signer', true, NULL, 50000.00, '2026-07-15', '2026-01-02');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000013',
 '00000000-0000-4000-8000-000000000022', 'signer', true, NULL, 50000.00, '2026-12-31', '2026-01-02');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '00000000-0000-4000-8000-000000000023', 'signer', true, 'LIC-0003', NULL, NULL, '2026-02-01');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '00000000-0000-4000-8000-000000000023', 'signer', false, NULL, NULL, NULL, '2026-06-01');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '00000000-0000-4000-8000-000000000024', 'signer', true, NULL, 10000.00, '2026-06-29', '2026-03-01');
INSERT INTO trust_account_signers (
    pmc_id, bank_account_id, staff_id, role, granted, license_number, bond_amount,
    bond_expires_on, authorized_on
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '00000000-0000-4000-8000-000000000025', 'signer', true, 'LIC-0005', NULL, NULL, '2026-07-01');

SELECT * FROM trust_report_trust_account_signers('00000000-0000-4000-8000-000000000001', '2026-06-30');
