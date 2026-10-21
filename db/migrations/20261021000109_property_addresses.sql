-- migrate:up
-- A property's street address and where it is on a map (the open-source review's geocoding,
-- done last as agreed; src/pms/api/addresses.py, src/pms/geocoding.py):
--
--   trust_property_addresses  a property's street address, whole: a first line, maybe a second,
--                             the city, the state (a USPS code) and the ZIP code. Append-only, the
--                             latest row holding (trust_property_address), naming who recorded it.
--   trust_property_locations  where an address is: its latitude and longitude as a geocoder
--                             matched it, the address as it matched it, and which geocoder. The
--                             latest of the property's latest address holds
--                             (trust_property_location); a new address has none until it is found.
--
-- A property's address is the home's, not a person's contact details: the AI role reads both
-- tables. Only property addresses ever go to a geocoder, and only when staff ask.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_property_addresses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    line_1 text NOT NULL CHECK (line_1 = btrim(line_1) AND length(line_1) BETWEEN 1 AND 200),
    line_2 text CHECK (line_2 = btrim(line_2) AND length(line_2) BETWEEN 1 AND 200),
    city text NOT NULL CHECK (city = btrim(city) AND length(city) BETWEEN 1 AND 100),
    -- The 50 states, DC and the territories USPS gives a code.
    state text NOT NULL CHECK (state IN (
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN',
        'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH',
        'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT',
        'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'AS', 'GU', 'MP', 'PR', 'VI'
    )),
    zip text NOT NULL CHECK (zip ~ '^[0-9]{5}(-[0-9]{4})?$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_property_addresses_property
ON trust_property_addresses (pmc_id, property_id, created_at);
CREATE INDEX trust_property_addresses_recorded_by
ON trust_property_addresses (pmc_id, recorded_by);

CREATE TABLE trust_property_locations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    address_id uuid NOT NULL,
    latitude numeric NOT NULL
        CHECK (latitude BETWEEN -90 AND 90 AND latitude = round(latitude, 6)),
    longitude numeric NOT NULL
        CHECK (longitude BETWEEN -180 AND 180 AND longitude = round(longitude, 6)),
    -- The address as the geocoder matched it.
    matched text NOT NULL CHECK (length(matched) BETWEEN 1 AND 300),
    geocoder text NOT NULL CHECK (geocoder IN ('census', 'fake')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, address_id) REFERENCES trust_property_addresses (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_property_locations_address
ON trust_property_locations (pmc_id, address_id, created_at);
CREATE INDEX trust_property_locations_recorded_by
ON trust_property_locations (pmc_id, recorded_by);

-- A property's address now: its latest row.
CREATE FUNCTION trust_property_address(p_property_id uuid)
RETURNS SETOF trust_property_addresses
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT a.* FROM trust_property_addresses AS a
    WHERE a.property_id = p_property_id
    ORDER BY a.created_at DESC, a.id DESC
    LIMIT 1
$$;

-- Where a property is now: the latest location of its address now, none when that address was
-- not found yet (an older address's location no longer counts).
CREATE FUNCTION trust_property_location(p_property_id uuid)
RETURNS SETOF trust_property_locations
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT g.* FROM trust_property_address(p_property_id) AS a
    JOIN trust_property_locations AS g ON g.address_id = a.id
    ORDER BY g.created_at DESC, g.id DESC
    LIMIT 1
$$;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_property_addresses
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_property_locations
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_property_addresses
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_property_locations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_property_addresses
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_property_locations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_property_addresses FROM PUBLIC;
REVOKE ALL ON trust_property_locations FROM PUBLIC;
GRANT SELECT, INSERT ON trust_property_addresses TO trust_app;
GRANT SELECT, INSERT ON trust_property_locations TO trust_app;
GRANT SELECT ON trust_property_addresses TO trust_ai_agent;
GRANT SELECT ON trust_property_locations TO trust_ai_agent;
REVOKE ALL ON FUNCTION trust_property_address(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_property_location(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_property_address(uuid) TO trust_app;
GRANT EXECUTE ON FUNCTION trust_property_location(uuid) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'property_addresses cannot be rolled back: addresses are records';
END;
$$;
