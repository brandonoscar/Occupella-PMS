-- migrate:up
-- The company's starting fee types (the founder's chart of accounts pass, 2026-11-05): Housing
-- assistance rent, Resident benefit package, Pet rent, One-time pet fee, Lease initiation fee,
-- Renewal fee, Lease change fee, Admin fee, Lease break fee, Utility reimbursement, Parking and
-- storage and Deposit forfeited to owner, added to every company but for a name it has already,
-- each with its code (from 4501 on, after the company's own). The company keeps the resident
-- benefit package and the lease initiation, renewal, lease change and admin fees; the owner the
-- rest. Each is a charge type like any other: renamed, archived or given to the other payee as
-- the company likes. pms.api.setup.STARTING_CHARGE_TYPES gives a company set up later the same.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Every company's starting fee types, but a name it has already, each with its code.
DO $$
DECLARE
    v_pmc record;
    v_type record;
    v_id uuid;
BEGIN
    FOR v_pmc IN SELECT p.pmc_id FROM trust_pmcs AS p ORDER BY p.pmc_id LOOP
        FOR v_type IN
            SELECT s.n, s.name, s.payee
            FROM (VALUES
                (1, 'Housing assistance rent', 'owner'),
                (2, 'Resident benefit package', 'company'),
                (3, 'Pet rent', 'owner'),
                (4, 'One-time pet fee', 'owner'),
                (5, 'Lease initiation fee', 'company'),
                (6, 'Renewal fee', 'company'),
                (7, 'Lease change fee', 'company'),
                (8, 'Admin fee', 'company'),
                (9, 'Lease break fee', 'owner'),
                (10, 'Utility reimbursement', 'owner'),
                (11, 'Parking and storage', 'owner'),
                (12, 'Deposit forfeited to owner', 'owner')
            ) AS s (n, name, payee)
            WHERE NOT EXISTS (
                SELECT 1 FROM trust_charge_types AS t
                WHERE t.pmc_id = v_pmc.pmc_id AND lower(trust_charge_type_name(t.id)) = lower(s.name)
            )
            ORDER BY s.n
        LOOP
            INSERT INTO trust_charge_types (pmc_id, name) VALUES (v_pmc.pmc_id, v_type.name)
            RETURNING id INTO v_id;
            IF v_type.payee = 'company' THEN
                INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee)
                VALUES (v_pmc.pmc_id, v_id, 'company');
            END IF;
        END LOOP;
    END LOOP;
END;
$$;

-- migrate:down
-- The fee types added stay: each is a record now, with its code and maybe fees charged under it.
SELECT 1;
