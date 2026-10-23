-- migrate:up
-- State rules from the address (the founder, 2026-10-23, lane 2's second item; ADR 0009; q142). A
-- property's place in the law, the facts rules turn on, and the rules themselves, dated and cited,
-- which one database function resolves for a property on a day. Rules are deterministic: no AI
-- decides a legal value, and a rule the PMS doesn't hold reads as unknown, never as "no rule".
-- State rules are the company's to confirm (ADR 0009): a screen shows a rule as cited information
-- with the day it was checked, says whether the company confirmed it, and warns rather than
-- refuses.
--
--   trust_property_jurisdictions  where a property's address is in the law: its state, county,
--                                 county subdivision, place (a city, town or village; none outside
--                                 one) and census tract, as the Census geocoder's geographies found
--                                 them for the address, or as staff confirmed or corrected them.
--                                 The latest row for the property's address now holds
--                                 (trust_property_jurisdiction); a new address has none until it
--                                 is found or staff say. Append-only, naming who.
--   trust_property_facts          what rules turn on: the year built, the units in the building,
--                                 whether an owner lives there and whether the owner is a person or
--                                 a company; each unknown until staff say. The latest row holds.
--                                 Append-only, naming who.
--   trust_jurisdiction_rules      a rule of a jurisdiction (federal, a state, a county, a city) for
--                                 a kind of term: its value (a number in a unit, maybe the lesser
--                                 or greater of two; "no limit", confirmed; or words, where the
--                                 law sets no number the PMS can check), the property facts it
--                                 applies to, the days it is in force, its citation and URL, the
--                                 day someone read the statute's text for it and who, and in words
--                                 the exceptions the PMS can't see. Each company holds its own
--                                 rows (every trust table carries pmc_id): those the PMS adds from
--                                 its catalog (src/pms/jurisdiction_rules.toml, each under its
--                                 key) and those its staff add. A correction is a new row
--                                 superseding the old. Append-only, naming who.
--   trust_jurisdiction_rule_confirmations
--                                 a staff member's confirmation that the company follows a rule,
--                                 once per rule. A rule staff add is confirmed by whoever added it.
--
-- trust_jurisdiction_candidates(property, rule, day) ranks the rows that could govern: of the
-- company's rows not superseded and in force that day for the property's jurisdictions, the most
-- specific level first (city, county, state, federal), then precedence, then the newest; each with
-- whether its facts match the property's (unknown when it names a fact the property has not on
-- file) and the facts it needs. trust_jurisdiction_rule gives the first that matches, unless a row
-- ranked above it can't be decided: then none, and trust_jurisdiction_rule_needs names the facts
-- to put on file. None: the PMS shows the rule as unknown. The AI role reads all four tables (it
-- calls reports alone, so not these functions).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_property_jurisdictions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The address it is for: the property's, when it was recorded.
    address_id uuid NOT NULL,
    -- FIPS codes as the Census Bureau writes them, each with its name.
    state_fips text NOT NULL CHECK (state_fips ~ '^[0-9]{2}$'),
    state_name text NOT NULL CHECK (length(state_name) BETWEEN 1 AND 100),
    county_fips text CHECK (county_fips ~ '^[0-9]{3}$'),
    county_name text CHECK (length(county_name) BETWEEN 1 AND 100),
    cousub_fips text CHECK (cousub_fips ~ '^[0-9]{5}$'),
    cousub_name text CHECK (length(cousub_name) BETWEEN 1 AND 100),
    -- An incorporated place (a city, town or village); none outside one.
    place_fips text CHECK (place_fips ~ '^[0-9]{5}$'),
    place_name text CHECK (length(place_name) BETWEEN 1 AND 100),
    tract text CHECK (tract ~ '^[0-9]{6}$'),
    -- Found by a geocoder, or said by staff (who confirmed or corrected it).
    source text NOT NULL CHECK (source IN ('census', 'fake', 'staff')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, address_id) REFERENCES trust_property_addresses (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    CHECK ((county_fips IS NULL) = (county_name IS NULL)),
    CHECK ((cousub_fips IS NULL) = (cousub_name IS NULL)),
    CHECK ((place_fips IS NULL) = (place_name IS NULL)),
    CHECK (cousub_fips IS NULL OR county_fips IS NOT NULL),
    CHECK (tract IS NULL OR county_fips IS NOT NULL)
);
CREATE INDEX trust_property_jurisdictions_address
ON trust_property_jurisdictions (pmc_id, address_id, created_at);
CREATE INDEX trust_property_jurisdictions_recorded_by
ON trust_property_jurisdictions (pmc_id, recorded_by);

CREATE TABLE trust_property_facts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    -- Each NULL: not known.
    year_built integer CHECK (year_built BETWEEN 1600 AND 2200),
    units_in_building integer CHECK (units_in_building BETWEEN 1 AND 100000),
    owner_occupied boolean,
    owner_kind text CHECK (owner_kind IN ('person', 'company')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_property_facts_property
ON trust_property_facts (pmc_id, property_id, created_at);
CREATE INDEX trust_property_facts_recorded_by ON trust_property_facts (pmc_id, recorded_by);

CREATE TABLE trust_jurisdiction_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    level text NOT NULL CHECK (level IN ('federal', 'state', 'county', 'city')),
    -- Which one: 'US'; a state's FIPS (06); a county's state and county FIPS (06037); a city's
    -- state and place FIPS (0644000).
    jurisdiction text NOT NULL,
    jurisdiction_name text NOT NULL CHECK (length(jurisdiction_name) BETWEEN 1 AND 100),
    rule text NOT NULL CHECK (rule IN (
        'deposit_cap', 'deposit_return_days', 'late_fee_cap', 'late_fee_grace_days',
        'nsf_fee_cap', 'rent_increase_notice_days', 'rent_cap'
    )),
    -- Its value: a number in a unit, maybe the lesser or greater of it and a second; or confirmed
    -- to have no limit; or words where the law sets no number the PMS can check ("reasonable").
    value_kind text NOT NULL CHECK (value_kind IN ('number', 'none', 'words')),
    value_number numeric CHECK (value_number >= 0),
    value_unit text CHECK (value_unit IN (
        'days', 'months_of_rent', 'percent_of_rent', 'percent_of_unpaid', 'dollars',
        'percent_a_year'
    )),
    combine text CHECK (combine IN ('lesser', 'greater')),
    also_number numeric CHECK (also_number >= 0),
    also_unit text CHECK (also_unit IN (
        'days', 'months_of_rent', 'percent_of_rent', 'percent_of_unpaid', 'dollars',
        'percent_a_year'
    )),
    value_words text CHECK (length(value_words) BETWEEN 1 AND 1000),
    -- The property facts it applies to; each NULL: any.
    built_before integer CHECK (built_before BETWEEN 1600 AND 2200),
    built_on_or_after integer CHECK (built_on_or_after BETWEEN 1600 AND 2200),
    units_at_least integer CHECK (units_at_least >= 1),
    units_at_most integer CHECK (units_at_most >= 1),
    owner_occupied boolean,
    owner_kind text CHECK (owner_kind IN ('person', 'company')),
    -- Among rows of one level in force together, the higher wins (a narrower exemption over the
    -- general rule).
    precedence integer NOT NULL DEFAULT 0 CHECK (precedence BETWEEN 0 AND 100),
    effective_from date NOT NULL,
    -- The first day it no longer holds; none: until a later row.
    effective_to date,
    citation text NOT NULL CHECK (length(citation) BETWEEN 1 AND 300),
    url text NOT NULL CHECK (url ~ '^https://[^[:space:]]+$' AND length(url) <= 500),
    -- The day someone read the statute's text for it, and who.
    checked_on date NOT NULL CHECK (checked_on >= DATE '2020-01-01'),
    reviewed_by text NOT NULL CHECK (length(reviewed_by) BETWEEN 1 AND 100),
    -- In words, what the law excepts that the PMS can't see ("never of a service member").
    exceptions text CHECK (length(exceptions) BETWEEN 1 AND 1000),
    -- The row this one corrects, which no longer counts.
    supersedes uuid,
    -- Added from the PMS's catalog under its key, or by the company's staff.
    source text NOT NULL CHECK (source IN ('pms', 'staff')),
    catalog_key text CHECK (catalog_key ~ '^[a-z0-9][a-z0-9_.-]{0,99}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    UNIQUE (supersedes),
    UNIQUE (pmc_id, catalog_key),
    FOREIGN KEY (pmc_id, supersedes) REFERENCES trust_jurisdiction_rules (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    CONSTRAINT trust_jurisdiction_rules_code CHECK (
        CASE level
            WHEN 'federal' THEN jurisdiction = 'US'
            WHEN 'state' THEN jurisdiction ~ '^[0-9]{2}$'
            WHEN 'county' THEN jurisdiction ~ '^[0-9]{5}$'
            ELSE jurisdiction ~ '^[0-9]{7}$'
        END
    ),
    CONSTRAINT trust_jurisdiction_rules_value CHECK (
        CASE value_kind
            WHEN 'number' THEN value_number IS NOT NULL AND value_unit IS NOT NULL
                AND value_words IS NULL
            WHEN 'none' THEN value_number IS NULL AND value_unit IS NULL AND value_words IS NULL
            ELSE value_number IS NULL AND value_unit IS NULL AND value_words IS NOT NULL
        END
    ),
    -- A second number only beside a first, both or neither.
    CONSTRAINT trust_jurisdiction_rules_second
    CHECK ((combine IS NULL) = (also_number IS NULL) AND (combine IS NULL) = (also_unit IS NULL)),
    CONSTRAINT trust_jurisdiction_rules_second_of_number
    CHECK (combine IS NULL OR value_kind = 'number'),
    -- Each kind of rule in the units it can take.
    CONSTRAINT trust_jurisdiction_rules_units CHECK (
        value_unit IS NULL OR CASE rule
            WHEN 'deposit_cap' THEN value_unit IN ('months_of_rent', 'dollars')
                AND coalesce(also_unit IN ('months_of_rent', 'dollars'), true)
            WHEN 'late_fee_cap' THEN value_unit IN ('dollars', 'percent_of_rent', 'percent_of_unpaid')
                AND coalesce(also_unit IN ('dollars', 'percent_of_rent', 'percent_of_unpaid'), true)
            WHEN 'nsf_fee_cap' THEN value_unit = 'dollars' AND also_unit IS NULL
            WHEN 'rent_cap' THEN value_unit = 'percent_a_year' AND also_unit IS NULL
            ELSE value_unit = 'days' AND also_unit IS NULL
        END
    ),
    CONSTRAINT trust_jurisdiction_rules_days
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CONSTRAINT trust_jurisdiction_rules_built
    CHECK (built_before IS NULL OR built_on_or_after IS NULL OR built_before > built_on_or_after),
    CONSTRAINT trust_jurisdiction_rules_unit_count
    CHECK (units_at_most IS NULL OR units_at_least IS NULL OR units_at_most >= units_at_least),
    CONSTRAINT trust_jurisdiction_rules_catalog CHECK ((source = 'pms') = (catalog_key IS NOT NULL)),
    CONSTRAINT trust_jurisdiction_rules_staff CHECK (source = 'staff' OR recorded_by IS NULL)
);
CREATE INDEX trust_jurisdiction_rules_lookup
ON trust_jurisdiction_rules (pmc_id, rule, level, jurisdiction);
CREATE INDEX trust_jurisdiction_rules_recorded_by ON trust_jurisdiction_rules (pmc_id, recorded_by);

CREATE TABLE trust_jurisdiction_rule_confirmations (
    rule_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    confirmed_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, rule_id) REFERENCES trust_jurisdiction_rules (pmc_id, id),
    FOREIGN KEY (pmc_id, confirmed_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_jurisdiction_rule_confirmations_by
ON trust_jurisdiction_rule_confirmations (pmc_id, confirmed_by);

-- A row names who added it (staff's) or nobody (the PMS's catalog), and a correction is of a rule
-- of the same company, jurisdiction and kind.
CREATE FUNCTION trust_check_jurisdiction_rule() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.supersedes IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trust_jurisdiction_rules AS r
        WHERE r.id = NEW.supersedes AND r.pmc_id = NEW.pmc_id AND r.level = NEW.level
          AND r.jurisdiction = NEW.jurisdiction AND r.rule = NEW.rule
    ) THEN
        RAISE EXCEPTION 'trust: a correction is of a rule of the same jurisdiction and kind'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    NEW.recorded_by := CASE WHEN NEW.source = 'staff'
        THEN nullif(current_setting('trust.staff_id', true), '')::uuid END;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_jurisdiction_rule() FROM PUBLIC;

-- Where a property is in the law now: the latest row for its address now, none when that address
-- has none yet (an older address's no longer counts).
CREATE FUNCTION trust_property_jurisdiction(p_property_id uuid)
RETURNS SETOF trust_property_jurisdictions
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT j.* FROM trust_property_address(p_property_id) AS a
    JOIN trust_property_jurisdictions AS j ON j.address_id = a.id
    ORDER BY j.created_at DESC, j.id DESC
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION trust_property_jurisdiction(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_property_jurisdiction(uuid) TO trust_app;

-- The rows that could govern a kind of rule for a property on a day, in order: of the property's
-- company's rows for its jurisdictions, in force that day and not superseded, the most specific
-- level first, then the highest precedence, then the newest. Each with whether the property's
-- latest facts match it (NULL: it names a fact the property has not on file) and the facts it
-- names that are not on file.
CREATE FUNCTION trust_jurisdiction_candidates(p_property_id uuid, p_rule text, p_day date)
RETURNS TABLE (rank bigint, rule_id uuid, matches boolean, needs text[])
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    WITH place AS (
        SELECT j.pmc_id, j.state_fips,
               j.state_fips || j.county_fips AS county,
               j.state_fips || j.place_fips AS city
        FROM trust_property_jurisdiction(p_property_id) AS j
    ),
    facts AS (
        SELECT f.* FROM trust_property_facts AS f
        WHERE f.property_id = p_property_id
        ORDER BY f.created_at DESC, f.id DESC
        LIMIT 1
    )
    SELECT row_number() OVER (
               ORDER BY CASE r.level WHEN 'city' THEN 0 WHEN 'county' THEN 1
                                     WHEN 'state' THEN 2 ELSE 3 END,
                        r.precedence DESC, r.created_at DESC, r.id DESC
           ),
           r.id,
           (r.built_before IS NULL OR f.year_built < r.built_before)
           AND (r.built_on_or_after IS NULL OR f.year_built >= r.built_on_or_after)
           AND (r.units_at_least IS NULL OR f.units_in_building >= r.units_at_least)
           AND (r.units_at_most IS NULL OR f.units_in_building <= r.units_at_most)
           AND (r.owner_occupied IS NULL OR f.owner_occupied = r.owner_occupied)
           AND (r.owner_kind IS NULL OR f.owner_kind = r.owner_kind),
           array_remove(ARRAY[
               CASE WHEN (r.built_before IS NOT NULL OR r.built_on_or_after IS NOT NULL)
                         AND f.year_built IS NULL THEN 'year_built' END,
               CASE WHEN (r.units_at_least IS NOT NULL OR r.units_at_most IS NOT NULL)
                         AND f.units_in_building IS NULL THEN 'units_in_building' END,
               CASE WHEN r.owner_occupied IS NOT NULL AND f.owner_occupied IS NULL
                    THEN 'owner_occupied' END,
               CASE WHEN r.owner_kind IS NOT NULL AND f.owner_kind IS NULL THEN 'owner_kind' END
           ], NULL)
    FROM trust_jurisdiction_rules AS r
    JOIN place AS p ON p.pmc_id = r.pmc_id
    LEFT JOIN facts AS f ON true
    WHERE r.rule = p_rule
      AND (
          (r.level = 'federal' AND r.jurisdiction = 'US')
          OR (r.level = 'state' AND r.jurisdiction = p.state_fips)
          OR (r.level = 'county' AND r.jurisdiction = p.county)
          OR (r.level = 'city' AND r.jurisdiction = p.city)
      )
      AND r.effective_from <= p_day AND (r.effective_to IS NULL OR p_day < r.effective_to)
      AND NOT EXISTS (SELECT 1 FROM trust_jurisdiction_rules AS s WHERE s.supersedes = r.id)
$$;
REVOKE ALL ON FUNCTION trust_jurisdiction_candidates(uuid, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_jurisdiction_candidates(uuid, text, date) TO trust_app;

-- The rule of a kind in force for a property on a day: the first candidate that doesn't fail to
-- match, if it matches. None: no rule is known (none held, or a fact it turns on not on file).
CREATE FUNCTION trust_jurisdiction_rule(p_property_id uuid, p_rule text, p_day date)
RETURNS SETOF trust_jurisdiction_rules
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT r.* FROM trust_jurisdiction_rules AS r
    JOIN (
        SELECT c.rule_id, c.matches
        FROM trust_jurisdiction_candidates(p_property_id, p_rule, p_day) AS c
        WHERE c.matches IS NOT false
        ORDER BY c.rank
        LIMIT 1
    ) AS first ON first.rule_id = r.id
    WHERE first.matches
$$;
REVOKE ALL ON FUNCTION trust_jurisdiction_rule(uuid, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_jurisdiction_rule(uuid, text, date) TO trust_app;

-- The facts to put on file before a rule of a kind can be known for a property on a day: those
-- the first candidate that doesn't fail to match turns on. Empty when the rule is known, or when
-- no row could govern.
CREATE FUNCTION trust_jurisdiction_rule_needs(p_property_id uuid, p_rule text, p_day date)
RETURNS text[]
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT CASE WHEN c.matches IS NULL THEN c.needs ELSE '{}'::text[] END
        FROM trust_jurisdiction_candidates(p_property_id, p_rule, p_day) AS c
        WHERE c.matches IS NOT false
        ORDER BY c.rank
        LIMIT 1
    ), '{}'::text[])
$$;
REVOKE ALL ON FUNCTION trust_jurisdiction_rule_needs(uuid, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_jurisdiction_rule_needs(uuid, text, date) TO trust_app;

CREATE TRIGGER trust_check_jurisdiction_rule BEFORE INSERT ON trust_jurisdiction_rules
FOR EACH ROW EXECUTE FUNCTION trust_check_jurisdiction_rule();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_property_jurisdictions
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_property_facts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_property_jurisdictions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_property_facts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_jurisdiction_rules
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_jurisdiction_rule_confirmations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_property_jurisdictions
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_property_facts
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_jurisdiction_rules
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE
ON trust_jurisdiction_rule_confirmations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_property_jurisdictions, trust_property_facts, trust_jurisdiction_rules,
trust_jurisdiction_rule_confirmations FROM PUBLIC;
GRANT SELECT, INSERT ON trust_property_jurisdictions, trust_property_facts,
trust_jurisdiction_rules, trust_jurisdiction_rule_confirmations TO trust_app;
GRANT SELECT ON trust_property_jurisdictions, trust_property_facts, trust_jurisdiction_rules,
trust_jurisdiction_rule_confirmations TO trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_jurisdiction_rule_needs(uuid, text, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_jurisdiction_rule(uuid, text, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_jurisdiction_candidates(uuid, text, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_property_jurisdiction(uuid);
-- squawk-ignore ban-drop-table
DROP TABLE trust_jurisdiction_rule_confirmations;
-- squawk-ignore ban-drop-table
DROP TABLE trust_jurisdiction_rules;
-- squawk-ignore ban-drop-table
DROP TABLE trust_property_facts;
-- squawk-ignore ban-drop-table
DROP TABLE trust_property_jurisdictions;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_jurisdiction_rule();
