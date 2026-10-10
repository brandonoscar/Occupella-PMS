\restrict dbmate


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: transfer_request; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.transfer_request AS (
	from_account_id text,
	to_account_id text,
	amount numeric
);


--
-- Name: format_ulid(bytea); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.format_ulid(bytes bytea) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
  encoding   bytea = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  output     text  = '';
BEGIN

  -- Encode the timestamp
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 0) & 224) >> 5));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 0) & 31)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 1) & 248) >> 3));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 1) & 7) << 2) | ((GET_BYTE(bytes, 2) & 192) >> 6)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 2) & 62) >> 1));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 2) & 1) << 4) | ((GET_BYTE(bytes, 3) & 240) >> 4)));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 3) & 15) << 1) | ((GET_BYTE(bytes, 4) & 128) >> 7)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 4) & 124) >> 2));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 4) & 3) << 3) | ((GET_BYTE(bytes, 5) & 224) >> 5)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 5) & 31)));

  -- Encode the entropy
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 6) & 248) >> 3));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 6) & 7) << 2) | ((GET_BYTE(bytes, 7) & 192) >> 6)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 7) & 62) >> 1));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 7) & 1) << 4) | ((GET_BYTE(bytes, 8) & 240) >> 4)));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 8) & 15) << 1) | ((GET_BYTE(bytes, 9) & 128) >> 7)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 9) & 124) >> 2));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 9) & 3) << 3) | ((GET_BYTE(bytes, 10) & 224) >> 5)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 10) & 31)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 11) & 248) >> 3));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 11) & 7) << 2) | ((GET_BYTE(bytes, 12) & 192) >> 6)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 12) & 62) >> 1));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 12) & 1) << 4) | ((GET_BYTE(bytes, 13) & 240) >> 4)));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 13) & 15) << 1) | ((GET_BYTE(bytes, 14) & 128) >> 7)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 14) & 124) >> 2));
  output = output || CHR(GET_BYTE(encoding, ((GET_BYTE(bytes, 14) & 3) << 3) | ((GET_BYTE(bytes, 15) & 224) >> 5)));
  output = output || CHR(GET_BYTE(encoding, (GET_BYTE(bytes, 15) & 31)));

  RETURN output;
END
$$;


--
-- Name: parse_ulid(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.parse_ulid(ulid text) RETURNS bytea
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
DECLARE
  -- 16byte
  bytes bytea = E'\\x00000000 00000000 00000000 00000000';
  v     bytea;
  -- Allow for O(1) lookup of index values
  dec   bytea = '\x
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
    00 01 02 03 04 05 06 07 08 09 00 00 00 00 00 00
    00 0A 0B 0C 0D 0E 0F 10 11 00 12 13 00 14 15 00
    16 17 18 19 1A 00 1B 1C 1D 1E 1F 00 00 00 00 00
    00 0A 0B 0C 0D 0E 0F 10 11 00 12 13 00 14 15 00
    16 17 18 19 1A 00 1B 1C 1D 1E 1F 00 00 00 00 00
  ';
BEGIN
  IF NOT ulid ~* '^[0-7][0-9ABCDEFGHJKMNPQRSTVWXYZ]{25}$' THEN
    RAISE EXCEPTION 'Invalid ULID: %', ulid;
  END IF;

  v = ulid::bytea;

  -- 6 bytes timestamp (48 bits)
  bytes = SET_BYTE(bytes, 0, (GET_BYTE(dec, GET_BYTE(v, 0)) << 5) | GET_BYTE(dec, GET_BYTE(v, 1)));
  bytes = SET_BYTE(bytes, 1, (GET_BYTE(dec, GET_BYTE(v, 2)) << 3) | (GET_BYTE(dec, GET_BYTE(v, 3)) >> 2));
  bytes = SET_BYTE(bytes, 2, (GET_BYTE(dec, GET_BYTE(v, 3)) << 6) | (GET_BYTE(dec, GET_BYTE(v, 4)) << 1) | (GET_BYTE(dec, GET_BYTE(v, 5)) >> 4));
  bytes = SET_BYTE(bytes, 3, (GET_BYTE(dec, GET_BYTE(v, 5)) << 4) | (GET_BYTE(dec, GET_BYTE(v, 6)) >> 1));
  bytes = SET_BYTE(bytes, 4, (GET_BYTE(dec, GET_BYTE(v, 6)) << 7) | (GET_BYTE(dec, GET_BYTE(v, 7)) << 2) | (GET_BYTE(dec, GET_BYTE(v, 8)) >> 3));
  bytes = SET_BYTE(bytes, 5, (GET_BYTE(dec, GET_BYTE(v, 8)) << 5) | GET_BYTE(dec, GET_BYTE(v, 9)));

  -- 10 bytes of entropy (80 bits);
  bytes = SET_BYTE(bytes, 6, (GET_BYTE(dec, GET_BYTE(v, 10)) << 3) | (GET_BYTE(dec, GET_BYTE(v, 11)) >> 2));
  bytes = SET_BYTE(bytes, 7, (GET_BYTE(dec, GET_BYTE(v, 11)) << 6) | (GET_BYTE(dec, GET_BYTE(v, 12)) << 1) | (GET_BYTE(dec, GET_BYTE(v, 13)) >> 4));
  bytes = SET_BYTE(bytes, 8, (GET_BYTE(dec, GET_BYTE(v, 13)) << 4) | (GET_BYTE(dec, GET_BYTE(v, 14)) >> 1));
  bytes = SET_BYTE(bytes, 9, (GET_BYTE(dec, GET_BYTE(v, 14)) << 7) | (GET_BYTE(dec, GET_BYTE(v, 15)) << 2) | (GET_BYTE(dec, GET_BYTE(v, 16)) >> 3));
  bytes = SET_BYTE(bytes, 10, (GET_BYTE(dec, GET_BYTE(v, 16)) << 5) | GET_BYTE(dec, GET_BYTE(v, 17)));
  bytes = SET_BYTE(bytes, 11, (GET_BYTE(dec, GET_BYTE(v, 18)) << 3) | (GET_BYTE(dec, GET_BYTE(v, 19)) >> 2));
  bytes = SET_BYTE(bytes, 12, (GET_BYTE(dec, GET_BYTE(v, 19)) << 6) | (GET_BYTE(dec, GET_BYTE(v, 20)) << 1) | (GET_BYTE(dec, GET_BYTE(v, 21)) >> 4));
  bytes = SET_BYTE(bytes, 13, (GET_BYTE(dec, GET_BYTE(v, 21)) << 4) | (GET_BYTE(dec, GET_BYTE(v, 22)) >> 1));
  bytes = SET_BYTE(bytes, 14, (GET_BYTE(dec, GET_BYTE(v, 22)) << 7) | (GET_BYTE(dec, GET_BYTE(v, 23)) << 2) | (GET_BYTE(dec, GET_BYTE(v, 24)) >> 3));
  bytes = SET_BYTE(bytes, 15, (GET_BYTE(dec, GET_BYTE(v, 24)) << 5) | GET_BYTE(dec, GET_BYTE(v, 25)));

  RETURN bytes;
END
$_$;


--
-- Name: pgledger_generate_id(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_generate_id(prefix text) RETURNS text
    LANGUAGE sql
    AS $$
    SELECT prefix || '_' || uuid_to_ulid(pgledger_uuidv7())
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: pgledger_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pgledger_accounts (
    id text DEFAULT public.pgledger_generate_id('pgla'::text) NOT NULL,
    name text NOT NULL,
    currency text NOT NULL,
    balance numeric DEFAULT 0 NOT NULL,
    version bigint DEFAULT 0 NOT NULL,
    allow_negative_balance boolean NOT NULL,
    allow_positive_balance boolean NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: pgledger_check_account_balance_constraints(public.pgledger_accounts); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_check_account_balance_constraints(account public.pgledger_accounts) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- If account doesn't allow negative balance and balance is negative, raise an error
    IF NOT account.allow_negative_balance AND (account.balance < 0) THEN
        RAISE EXCEPTION 'Account (id=%, name=%) does not allow negative balance', account.id, account.name;
    END IF;

    -- If account doesn't allow positive balance and balance is positive, raise an error
    IF NOT account.allow_positive_balance AND (account.balance > 0) THEN
        RAISE EXCEPTION 'Account (id=%, name=%) does not allow positive balance', account.id, account.name;
    END IF;
END;
$$;


--
-- Name: pgledger_accounts_view; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.pgledger_accounts_view AS
 SELECT id,
    name,
    currency,
    balance,
    version,
    allow_negative_balance,
    allow_positive_balance,
    metadata,
    created_at,
    updated_at
   FROM public.pgledger_accounts;


--
-- Name: pgledger_create_account(text, text, boolean, boolean, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_create_account(name text, currency text, allow_negative_balance boolean DEFAULT true, allow_positive_balance boolean DEFAULT true, metadata jsonb DEFAULT NULL::jsonb) RETURNS SETOF public.pgledger_accounts_view
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    INSERT INTO pgledger_accounts (name, currency, allow_negative_balance, allow_positive_balance, metadata, created_at, updated_at)
    VALUES (name, currency, allow_negative_balance, allow_positive_balance, metadata, now(), now())
    RETURNING *;
END;
$$;


--
-- Name: pgledger_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pgledger_transfers (
    id text DEFAULT public.pgledger_generate_id('pglt'::text) NOT NULL,
    from_account_id text NOT NULL,
    to_account_id text NOT NULL,
    amount numeric NOT NULL,
    created_at timestamp with time zone NOT NULL,
    event_at timestamp with time zone NOT NULL,
    metadata jsonb,
    CONSTRAINT pgledger_transfers_check CHECK (((amount > (0)::numeric) AND (amount < 'Infinity'::numeric) AND (from_account_id <> to_account_id)))
);


--
-- Name: pgledger_transfers_view; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.pgledger_transfers_view AS
 SELECT id,
    from_account_id,
    to_account_id,
    amount,
    created_at,
    event_at,
    metadata
   FROM public.pgledger_transfers;


--
-- Name: pgledger_create_transfer(text, text, numeric, timestamp with time zone, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_create_transfer(from_account_id text, to_account_id text, amount numeric, event_at timestamp with time zone DEFAULT NULL::timestamp with time zone, metadata jsonb DEFAULT NULL::jsonb) RETURNS SETOF public.pgledger_transfers_view
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Simply call pgledger_create_transfers with a single transfer
    RETURN QUERY
    SELECT * FROM pgledger_create_transfers(
        transfer_requests => array[(from_account_id, to_account_id, amount)::TRANSFER_REQUEST],
        event_at => event_at,
        metadata => metadata
    );
END;
$$;


--
-- Name: pgledger_create_transfers(public.transfer_request[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_create_transfers(VARIADIC transfer_requests public.transfer_request[]) RETURNS SETOF public.pgledger_transfers_view
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT * FROM pgledger_create_transfers(transfer_requests);
END;
$$;


--
-- Name: pgledger_create_transfers(public.transfer_request[], timestamp with time zone, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_create_transfers(transfer_requests public.transfer_request[], event_at timestamp with time zone DEFAULT NULL::timestamp with time zone, metadata jsonb DEFAULT NULL::jsonb) RETURNS SETOF public.pgledger_transfers_view
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    transfer_request transfer_request;
    transfer_ids TEXT[] := '{}';
    transfer_id TEXT;
    from_account pgledger_accounts;
    to_account pgledger_accounts;
    from_account_id TEXT;
    to_account_id TEXT;
    all_account_ids TEXT[] := '{}';
BEGIN
    IF transfer_requests IS NULL THEN
        RAISE EXCEPTION 'Transfer requests must not be null';
    END IF;

    -- Collect all unique account IDs and sort them to prevent deadlocks
    FOREACH transfer_request IN ARRAY transfer_requests LOOP
        all_account_ids := array_append(all_account_ids, transfer_request.from_account_id);
        all_account_ids := array_append(all_account_ids, transfer_request.to_account_id);
    END LOOP;

    -- Remove duplicates and sort
    SELECT ARRAY(SELECT DISTINCT unnest FROM unnest(all_account_ids) ORDER BY unnest)
    INTO all_account_ids;

    -- Lock all accounts in order
    FOREACH from_account_id IN ARRAY all_account_ids LOOP
        PERFORM pgledger_accounts.id
        FROM pgledger_accounts
        WHERE pgledger_accounts.id = from_account_id
        FOR UPDATE;
    END LOOP;

    -- Process each transfer
    FOREACH transfer_request IN ARRAY transfer_requests LOOP
        -- Preliminary checks
        IF transfer_request.amount IS NULL
            OR NOT (transfer_request.amount > 0 AND transfer_request.amount < 'Infinity'::NUMERIC) THEN
            RAISE EXCEPTION 'Amount (%) must be a positive finite number', transfer_request.amount;
        END IF;

        IF transfer_request.from_account_id IS NULL OR transfer_request.to_account_id IS NULL THEN
            RAISE EXCEPTION 'Account ids (from=%, to=%) must not be null',
                transfer_request.from_account_id, transfer_request.to_account_id;
        END IF;

        IF transfer_request.from_account_id = transfer_request.to_account_id THEN
            RAISE EXCEPTION 'Cannot transfer to the same account (id=%)', transfer_request.from_account_id;
        END IF;

        -- Update account balances
        UPDATE pgledger_accounts
        SET balance = balance - transfer_request.amount,
            version = version + 1,
            updated_at = now()
        WHERE pgledger_accounts.id = transfer_request.from_account_id
        RETURNING * INTO from_account;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Account (id=%) does not exist', transfer_request.from_account_id;
        END IF;

        -- Check balance constraints for the source account
        PERFORM pgledger_check_account_balance_constraints(from_account);

        UPDATE pgledger_accounts
        SET balance = balance + transfer_request.amount,
            version = version + 1,
            updated_at = now()
        WHERE pgledger_accounts.id = transfer_request.to_account_id
        RETURNING * INTO to_account;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Account (id=%) does not exist', transfer_request.to_account_id;
        END IF;

        -- Check balance constraints for the destination account
        PERFORM pgledger_check_account_balance_constraints(to_account);

        -- Check that currencies match
        IF from_account.currency != to_account.currency THEN
            RAISE EXCEPTION 'Cannot transfer between different currencies (% and %)', from_account.currency, to_account.currency;
        END IF;

        -- Create transfer record
        INSERT INTO pgledger_transfers (from_account_id, to_account_id, amount, created_at, event_at, metadata)
        VALUES (transfer_request.from_account_id, transfer_request.to_account_id, transfer_request.amount, now(), coalesce(event_at, now()), metadata)
        RETURNING pgledger_transfers.id INTO transfer_id;

        transfer_ids := array_append(transfer_ids, transfer_id);

        -- Create entry for the source account (negative amount)
        INSERT INTO pgledger_entries (account_id, transfer_id, amount, account_previous_balance, account_current_balance, account_version, created_at)
        VALUES (transfer_request.from_account_id, transfer_id, -transfer_request.amount, from_account.balance + transfer_request.amount, from_account.balance, from_account.version, now());

        -- Create entry for the destination account (positive amount)
        INSERT INTO pgledger_entries (account_id, transfer_id, amount, account_previous_balance, account_current_balance, account_version, created_at)
        VALUES (transfer_request.to_account_id, transfer_id, transfer_request.amount, to_account.balance - transfer_request.amount, to_account.balance, to_account.version, now());
    END LOOP;

    -- Return all created transfers
    RETURN QUERY
    SELECT *
    FROM pgledger_transfers_view
    WHERE id = ANY(transfer_ids)
    ORDER BY id;
END;
$$;


--
-- Name: pgledger_uuidv7(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_uuidv7() RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    result uuid;
BEGIN
    IF pgledger_uuidv7_exists() THEN
        EXECUTE 'select uuidv7()' INTO result;
        RETURN result;
    ELSE
        RETURN pgledger_uuidv7_microsecond();
    END IF;
end
$$;


--
-- Name: pgledger_uuidv7_exists(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_uuidv7_exists() RETURNS boolean
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT EXISTS(SELECT * FROM pg_proc WHERE proname = 'uuidv7');
$$;


--
-- Name: pgledger_uuidv7_microsecond(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.pgledger_uuidv7_microsecond() RETURNS uuid
    LANGUAGE sql
    AS $$
    select encode(
        substring(int8send(floor(t_ms)::int8) from 3) ||
        int2send((7<<12)::int2 | ((t_ms-floor(t_ms))*4096)::int2) ||
        substring(uuid_send(gen_random_uuid()) from 9 for 8)
        , 'hex')::uuid
    from (select extract(epoch from clock_timestamp())*1000 as t_ms) s
$$;


--
-- Name: trust_management_agreements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_management_agreements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    starts_on date NOT NULL,
    fee_percent numeric DEFAULT 0 NOT NULL,
    minimum_fee numeric DEFAULT 0 NOT NULL,
    flat_fee numeric DEFAULT 0 NOT NULL,
    leasing_fee_percent numeric DEFAULT 0 NOT NULL,
    reserve numeric DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    approval_limit numeric DEFAULT 0 NOT NULL,
    CONSTRAINT trust_management_agreements_approval_limit_check CHECK ((approval_limit >= (0)::numeric)),
    CONSTRAINT trust_management_agreements_fee_percent_check CHECK (((fee_percent >= (0)::numeric) AND (fee_percent <= (100)::numeric))),
    CONSTRAINT trust_management_agreements_flat_fee_check CHECK ((flat_fee >= (0)::numeric)),
    CONSTRAINT trust_management_agreements_leasing_fee_percent_check CHECK (((leasing_fee_percent >= (0)::numeric) AND (leasing_fee_percent <= (100)::numeric))),
    CONSTRAINT trust_management_agreements_minimum_fee_check CHECK ((minimum_fee >= (0)::numeric)),
    CONSTRAINT trust_management_agreements_reserve_check CHECK ((reserve >= (0)::numeric))
);


--
-- Name: trust_agreement_on(text, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_agreement_on(p_account text, p_day date) RETURNS public.trust_management_agreements
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT a.*
    FROM trust_management_agreements AS a
    WHERE a.ledger_account_id = p_account AND a.starts_on <= p_day
    ORDER BY a.starts_on DESC
    LIMIT 1
$$;


--
-- Name: trust_apply_payment(uuid, uuid, text, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_apply_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND t.kind = 'owner_property' AND t.property_id = v_property_id
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              f.kind = 'bank_cash'
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property, from cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;


--
-- Name: trust_approve_reconciliation(uuid, uuid, timestamp with time zone, timestamp with time zone, numeric, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_approve_reconciliation(p_pmc_id uuid, p_bank_account_id uuid, p_period_start timestamp with time zone, p_period_end timestamp with time zone, p_statement_balance numeric, p_prepared_by text, p_approved_by text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_next_start timestamptz;
    v_book_balance numeric;
    v_id uuid;
BEGIN
    -- The book balance must count every posting that committed while this waited for the lock
    -- below. At REPEATABLE READ or SERIALIZABLE it would read a snapshot taken before them.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: approve a reconciliation at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;

    IF p_period_end > now() THEN
        RAISE EXCEPTION 'trust: the period ends at %, which has not come yet', p_period_end
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Approve a reconciliation once its period has ended.';
    END IF;

    -- Waits for postings in flight on this account (they hold the row FOR SHARE), and holds new
    -- ones off until this commits.
    SELECT coalesce(b.closed_through, b.opened_at) INTO v_next_start
    FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_period_start <> v_next_start THEN
        RAISE EXCEPTION 'trust: the next period of trust bank account % starts at %',
            p_bank_account_id, v_next_start
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Periods follow one another, from the cutover: no gap, no overlap.';
    END IF;

    -- The cash the books say the bank held at the period's end: -balance of its bank_cash.
    SELECT coalesce(-sum(e.amount), 0) INTO v_book_balance
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash'
      AND tr.event_at < p_period_end;

    INSERT INTO trust_reconciliations (
        pmc_id, bank_account_id, period_start, period_end, statement_balance, book_balance,
        prepared_by, approved_by
    ) VALUES (
        p_pmc_id, p_bank_account_id, p_period_start, p_period_end, p_statement_balance,
        v_book_balance, p_prepared_by, p_approved_by
    ) RETURNING id INTO v_id;

    UPDATE trust_bank_accounts SET closed_through = p_period_end WHERE id = p_bank_account_id;

    RETURN v_id;
END;
$$;


--
-- Name: trust_assess_late_fees(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_assess_late_fees(p_pmc_id uuid, p_as_of date) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rent record;
    v_cutoff timestamptz;
    v_unpaid numeric;
    v_fee numeric;
    v_fee_id uuid;
    v_charged integer := 0;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: assess late fees at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: late fees are assessed as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on, c.amount, p.id AS policy_id, p.grace_days,
               p.flat_fee, p.percent, p.maximum
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.pmc_id = p_pmc_id AND c.kind = 'rent'
          AND c.due_on + p.grace_days < p_as_of
          AND NOT EXISTS (
              SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = c.id
          )
        ORDER BY c.id  -- one lock order for every run, so two at once can't deadlock
    LOOP
        PERFORM c.id FROM trust_charges AS c WHERE c.id = v_rent.id FOR NO KEY UPDATE;
        CONTINUE WHEN EXISTS (
            SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = v_rent.id
        );

        v_cutoff := (v_rent.due_on + v_rent.grace_days + 1)::timestamp AT TIME ZONE 'UTC';
        v_unpaid := v_rent.amount
            - coalesce((
                SELECT sum(cp.amount)
                FROM trust_charge_payments AS cp
                JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                WHERE cp.charge_id = v_rent.id AND t.event_at < v_cutoff
            ), 0)
            + coalesce((
                SELECT sum(r.amount)
                FROM trust_payment_reversals AS r
                JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                WHERE r.charge_id = v_rent.id AND t.event_at < v_cutoff
            ), 0);
        CONTINUE WHEN v_unpaid <= 0;
        v_fee := least(
            round(v_rent.flat_fee + v_rent.percent / 100 * v_unpaid, 2),
            v_rent.maximum
        );
        CONTINUE WHEN v_fee <= 0;

        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, v_rent.lease_id, v_rent.due_on + v_rent.grace_days + 1, 'fee', v_fee,
            'Late fee, rent due ' || to_char(v_rent.due_on, 'YYYY-MM-DD')
        )
        RETURNING id INTO v_fee_id;
        INSERT INTO trust_late_fees (rent_charge_id, pmc_id, policy_id, unpaid, fee, fee_charge_id)
        VALUES (v_rent.id, p_pmc_id, v_rent.policy_id, v_unpaid, v_fee, v_fee_id);
        v_charged := v_charged + 1;
    END LOOP;
    RETURN v_charged;
END;
$$;


--
-- Name: trust_audit(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_audit() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    SET "TimeZone" TO 'UTC'
    AS $$
DECLARE
    v_row jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
    v_pmc uuid;
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
BEGIN
    IF TG_TABLE_NAME = 'pgledger_transfers' THEN
        SELECT t.pmc_id INTO v_pmc
        FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_row ->> 'from_account_id';
    ELSE
        v_pmc := (v_row ->> 'pmc_id')::uuid;
    END IF;

    IF v_staff IS NOT NULL AND NOT EXISTS (
        SELECT s.id FROM trust_staff AS s WHERE s.id = v_staff AND s.pmc_id = v_pmc
    ) THEN
        RAISE EXCEPTION 'trust: % is not a staff member of PMC %', v_staff, v_pmc
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Set trust.staff_id to one of the PMC''s staff (trust_staff), or not at all.';
    END IF;

    INSERT INTO trust_audit_log (pmc_id, staff_id, db_role, action, table_name, row_data, before)
    VALUES (
        v_pmc, v_staff,
        -- The role the session acts as: SET ROLE's, else the one it logged in with. (The
        -- owner's, inside a write path that runs as the owner, is never the one recorded.)
        coalesce(nullif(current_setting('role'), 'none'), session_user),
        lower(TG_OP), TG_TABLE_NAME, v_row,
        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END
    );
    RETURN NULL;
END;
$$;


--
-- Name: trust_charge_rent_due(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_charge_rent_due(p_pmc_id uuid, p_due_on date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charged integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', l.monthly_rent,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD')
    FROM trust_leases AS l
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
END;
$$;


--
-- Name: trust_check_account_bank_kind(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_account_bank_kind() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank_kind text;
    v_belongs_in text;
BEGIN
    SELECT b.kind INTO v_bank_kind
    FROM trust_bank_accounts AS b
    WHERE b.id = NEW.bank_account_id;

    -- bank_cash is the bank side of whichever account it opens in.
    v_belongs_in := CASE NEW.kind
        WHEN 'bank_cash' THEN v_bank_kind
        WHEN 'tenant_deposit' THEN 'security_deposit'
        ELSE 'operating'
    END;

    -- An unknown trust bank account leaves v_bank_kind NULL; the foreign key refuses the row.
    IF v_bank_kind <> v_belongs_in THEN
        RAISE EXCEPTION 'trust: % accounts belong in the % trust bank account, not the % one',
            NEW.kind, v_belongs_in, v_bank_kind
            USING ERRCODE = 'check_violation',
                  HINT = 'Tenant deposits go in the security-deposit trust account; everything '
                         'else held in trust goes in the operating one.';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_agreement_account(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_agreement_account() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: a management agreement is for an owner''s property account in '
            'its own PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bank_tie_out(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bank_tie_out() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
    v_off numeric;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT t.bank_account_id
        FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id IN (NEW.from_account_id, NEW.to_account_id)
    LOOP
        SELECT sum(a.balance) INTO v_off
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.bank_account_id = v_bank;

        IF v_off <> 0 THEN
            RAISE EXCEPTION 'trust: trust bank account % does not tie out (off by %)',
                v_bank, v_off
                USING ERRCODE = 'check_violation',
                      HINT = 'Money held in one trust bank account moves to another only with '
                             'its cash: post both transfers in one transaction.';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;


--
-- Name: trust_check_bill_account(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bill_account() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_property uuid;
BEGIN
    SELECT t.property_id INTO v_property
    FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
      AND t.kind = 'owner_property';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: a bill is paid from an owner''s property account in its own '
            'PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.work_order_id IS NOT NULL AND NOT EXISTS (
        SELECT w.id FROM trust_work_orders AS w
        WHERE w.id = NEW.work_order_id AND w.property_id = v_property
    ) THEN
        RAISE EXCEPTION 'trust: work order % is not for the property of account %',
            NEW.work_order_id, NEW.ledger_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT s.id FROM trust_work_order_steps AS s
        WHERE s.work_order_id = NEW.work_order_id AND s.step = 'cancelled'
    ) THEN
        RAISE EXCEPTION 'trust: work order % was cancelled; it takes no bills', NEW.work_order_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_transfer_scope(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_transfer_scope() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_from_pmc uuid;
    v_to_pmc uuid;
BEGIN
    SELECT pmc_id INTO v_from_pmc
    FROM trust_ledger_accounts WHERE ledger_account_id = NEW.from_account_id;
    SELECT pmc_id INTO v_to_pmc
    FROM trust_ledger_accounts WHERE ledger_account_id = NEW.to_account_id;

    IF v_from_pmc IS NULL OR v_to_pmc IS NULL THEN
        RAISE EXCEPTION 'trust: transfer % -> % touches an account with no trust kind',
            NEW.from_account_id, NEW.to_account_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_from_pmc <> v_to_pmc THEN
        RAISE EXCEPTION 'trust: transfer % -> % crosses PMCs',
            NEW.from_account_id, NEW.to_account_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_work_order(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_work_order() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.unit_id IS NOT NULL AND NOT EXISTS (
        SELECT u.id FROM trust_units AS u
        WHERE u.id = NEW.unit_id AND u.property_id = NEW.property_id
    ) THEN
        RAISE EXCEPTION 'trust: unit % is not one of property %''s', NEW.unit_id, NEW.property_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_work_order_step(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_work_order_step() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_closed trust_work_order_steps;
    v_earliest timestamptz;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: record work order steps at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.work_order_id::text, 0));

    -- Asked outright, not read off "the last step": steps written in one transaction share
    -- their time, so the last can't always be told apart.
    SELECT s.* INTO v_closed
    FROM trust_work_order_steps AS s
    WHERE s.work_order_id = NEW.work_order_id AND s.step IN ('completed', 'cancelled');
    IF FOUND THEN
        RAISE EXCEPTION 'trust: work order % was % at %; nothing follows that',
            NEW.work_order_id, v_closed.step, v_closed.taken_at
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT greatest(w.opened_at, max(s.taken_at)) INTO v_earliest
    FROM trust_work_orders AS w
    LEFT JOIN trust_work_order_steps AS s ON s.work_order_id = w.id
    WHERE w.id = NEW.work_order_id
    GROUP BY w.opened_at;
    IF NEW.taken_at < v_earliest THEN
        RAISE EXCEPTION 'trust: a step of work order % can''t be dated before %',
            NEW.work_order_id, v_earliest
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_draw_owner(uuid, text, text, numeric, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_draw_owner(p_pmc_id uuid, p_account text, p_request_key text, p_amount numeric, p_event_at timestamp with time zone) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    -- pgledger dates a transfer with no date now; the reserve and bills are that day's.
    v_day date := (coalesce(p_event_at, now()) AT TIME ZONE 'UTC')::date;
    v_taken trust_owner_draws;
    v_reserve numeric;
    v_bills numeric;
    v_available numeric;
    v_cash text;
    v_transfer_id text;
BEGIN
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: an owner draw needs a request key, so a retry can''t pay twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a draw pays a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT d.* INTO v_taken
    FROM trust_owner_draws AS d
    WHERE d.pmc_id = p_pmc_id AND d.request_key = p_request_key;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> p_account
            OR v_taken.amount <> coalesce(p_amount, v_taken.amount) THEN
            RAISE EXCEPTION 'trust: request key % already paid % from %',
                p_request_key, v_taken.amount, v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new draw; resend a key only to retry.';
        END IF;
        RETURN v_taken.amount;  -- a retry: the original draw
    END IF;

    v_reserve := coalesce((trust_agreement_on(p_account, v_day)).reserve, 0);
    v_bills := trust_unpaid_bills(p_account, v_day);
    SELECT a.balance - v_reserve - v_bills INTO v_available
    FROM pgledger_accounts AS a WHERE a.id = p_account;
    IF coalesce(p_amount, v_available) > v_available OR v_available <= 0 THEN
        RAISE EXCEPTION 'trust: % is available to draw from % (its balance less a reserve of % '
            'and unpaid bills of %); % is too much', greatest(v_available, 0), p_account,
            v_reserve, v_bills, coalesce(p_amount, v_available)
            USING ERRCODE = 'check_violation';
    END IF;

    -- Money reaches an owner's account only through its trust bank account's cash, so a
    -- balance to draw means that cash account exists.
    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_account.bank_account_id AND t.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(p_account, v_cash, coalesce(p_amount, v_available))::transfer_request],
        p_event_at,
        jsonb_build_object('memo', 'Owner draw')
    ) AS t;

    INSERT INTO trust_owner_draws (pmc_id, request_key, ledger_account_id, amount, transfer_id)
    VALUES (p_pmc_id, p_request_key, p_account, coalesce(p_amount, v_available), v_transfer_id);
    RETURN coalesce(p_amount, v_available);
END;
$$;


--
-- Name: trust_end_lease(uuid, uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_end_lease(p_pmc_id uuid, p_lease_id uuid, p_ends_on date) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    UPDATE trust_leases SET ends_on = p_ends_on WHERE id = p_lease_id AND pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
END;
$$;


--
-- Name: trust_bills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    reference text NOT NULL,
    bill_date date NOT NULL,
    due_on date NOT NULL,
    amount numeric NOT NULL,
    memo text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    work_order_id uuid,
    CONSTRAINT trust_bills_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_bills_check CHECK ((due_on >= bill_date)),
    CONSTRAINT trust_bills_memo_check CHECK ((memo <> ''::text)),
    CONSTRAINT trust_bills_reference_check CHECK ((reference <> ''::text))
);


--
-- Name: trust_lock_bill(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lock_bill(p_pmc_id uuid, p_bill_id uuid) RETURNS public.trust_bills
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b WHERE b.id = p_bill_id AND b.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no bill % in PMC %', p_bill_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM trust_lock_owner_account(p_pmc_id, v_bill.ledger_account_id);
    RETURN v_bill;
END;
$$;


--
-- Name: trust_ledger_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_ledger_accounts (
    ledger_account_id text NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    kind text NOT NULL,
    owner_id uuid,
    property_id uuid,
    tenant_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    vendor_id uuid,
    CONSTRAINT trust_ledger_accounts_kind_shape_v2 CHECK ((((kind = 'owner_property'::text) AND (owner_id IS NOT NULL) AND (property_id IS NOT NULL) AND (tenant_id IS NULL) AND (vendor_id IS NULL)) OR ((kind = ANY (ARRAY['tenant_deposit'::text, 'prepaid_rent'::text])) AND (tenant_id IS NOT NULL) AND (owner_id IS NULL) AND (property_id IS NULL) AND (vendor_id IS NULL)) OR ((kind = 'vendor_payable'::text) AND (vendor_id IS NOT NULL) AND (owner_id IS NULL) AND (property_id IS NULL) AND (tenant_id IS NULL)) OR ((kind = ANY (ARRAY['pmc_income'::text, 'bank_cash'::text])) AND (owner_id IS NULL) AND (property_id IS NULL) AND (tenant_id IS NULL) AND (vendor_id IS NULL))))
);


--
-- Name: trust_lock_owner_account(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lock_owner_account(p_pmc_id uuid, p_account text) RETURNS public.trust_ledger_accounts
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_account trust_ledger_accounts;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: move an owner''s money (fees, draws, bills) at READ COMMITTED, '
            'not %', current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT t.* INTO v_account
    FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = p_account AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account in PMC %', p_account, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_account;
END;
$$;


--
-- Name: trust_open_lease(uuid, uuid, date, date, numeric, uuid[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_lease(p_pmc_id uuid, p_unit_id uuid, p_starts_on date, p_ends_on date, p_monthly_rent numeric, p_tenant_ids uuid[]) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_property_id uuid;
    v_lease_id uuid;
BEGIN
    SELECT u.property_id INTO v_property_id
    FROM trust_units AS u
    WHERE u.id = p_unit_id AND u.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no unit % in PMC %', p_unit_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF coalesce(cardinality(p_tenant_ids), 0) = 0 THEN
        RAISE EXCEPTION 'trust: a lease needs at least one tenant'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT r.tenant_id FROM unnest(p_tenant_ids) AS r (tenant_id)
        LEFT JOIN trust_tenants AS t ON t.id = r.tenant_id AND t.pmc_id = p_pmc_id
        WHERE t.property_id IS DISTINCT FROM v_property_id
    ) THEN
        RAISE EXCEPTION 'trust: every tenant on a lease must be a tenant of the unit''s property'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_leases (pmc_id, unit_id, starts_on, ends_on, monthly_rent)
    VALUES (p_pmc_id, p_unit_id, p_starts_on, p_ends_on, p_monthly_rent)
    RETURNING id INTO v_lease_id;

    INSERT INTO trust_lease_tenants (pmc_id, lease_id, tenant_id)
    SELECT DISTINCT p_pmc_id, v_lease_id, r.tenant_id FROM unnest(p_tenant_ids) AS r (tenant_id);

    RETURN v_lease_id;
END;
$$;


--
-- Name: trust_open_ledger_account(uuid, uuid, text, uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_ledger_account(p_pmc_id uuid, p_bank_account_id uuid, p_kind text, p_owner_id uuid DEFAULT NULL::uuid, p_property_id uuid DEFAULT NULL::uuid, p_tenant_id uuid DEFAULT NULL::uuid) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_ledger_account_id text;
BEGIN
    SELECT a.id INTO v_ledger_account_id
    FROM pgledger_create_account(
        p_kind, 'USD', allow_negative_balance => p_kind = 'bank_cash'
    ) AS a;

    INSERT INTO trust_ledger_accounts (
        ledger_account_id, pmc_id, bank_account_id, kind, owner_id, property_id, tenant_id
    ) VALUES (
        v_ledger_account_id, p_pmc_id, p_bank_account_id, p_kind,
        p_owner_id, p_property_id, p_tenant_id
    );

    RETURN v_ledger_account_id;
END;
$$;


--
-- Name: trust_open_vendor_account(uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_vendor_account(p_pmc_id uuid, p_bank_account_id uuid, p_vendor_id uuid) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_ledger_account_id text;
BEGIN
    SELECT a.id INTO v_ledger_account_id
    FROM pgledger_create_account('vendor_payable', 'USD') AS a;

    INSERT INTO trust_ledger_accounts (
        ledger_account_id, pmc_id, bank_account_id, kind, vendor_id
    ) VALUES (
        v_ledger_account_id, p_pmc_id, p_bank_account_id, 'vendor_payable', p_vendor_id
    );

    RETURN v_ledger_account_id;
END;
$$;


--
-- Name: trust_pay_bill(uuid, uuid, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_pay_bill(p_pmc_id uuid, p_bill_id uuid, p_event_at timestamp with time zone) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(p_pmc_id, p_bill_id);
    v_set_aside pgledger_transfers;
    v_cash text;
    v_transfer_id text;
BEGIN
    SELECT p.transfer_id INTO v_transfer_id
    FROM trust_bill_payments AS p
    WHERE p.bill_id = p_bill_id AND p.step = 'paid';
    IF FOUND THEN
        RETURN v_transfer_id;  -- a retry: the bill is paid already
    END IF;

    SELECT t.* INTO v_set_aside
    FROM trust_bill_payments AS p
    JOIN pgledger_transfers AS t ON t.id = p.transfer_id
    WHERE p.bill_id = p_bill_id AND p.step = 'set_aside';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: bill % is not set aside yet; set it aside, then pay it',
            v_bill.reference
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- pgledger dates a transfer with no date now.
    IF coalesce(p_event_at, now()) < v_set_aside.event_at THEN
        RAISE EXCEPTION 'trust: bill % was set aside at %; it can''t be paid before that',
            v_bill.reference, v_set_aside.event_at
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT c.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS v
    JOIN trust_ledger_accounts AS c ON c.bank_account_id = v.bank_account_id
    WHERE v.ledger_account_id = v_set_aside.to_account_id AND c.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_set_aside.to_account_id, v_cash, v_set_aside.amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', format('Bill %s paid', v_bill.reference))
    ) AS t;
    INSERT INTO trust_bill_payments (bill_id, step, pmc_id, transfer_id)
    VALUES (p_bill_id, 'paid', p_pmc_id, v_transfer_id);
    RETURN v_transfer_id;
END;
$$;


--
-- Name: trust_post_fee(public.trust_ledger_accounts, numeric, timestamp with time zone, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_post_fee(p_account public.trust_ledger_accounts, p_amount numeric, p_event_at timestamp with time zone, p_memo text) RETURNS text
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_income text;
    v_transfer_id text;
BEGIN
    SELECT t.ledger_account_id INTO v_income
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_account.bank_account_id AND t.kind = 'pmc_income';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the PMC''s fee account (pmc_income) in trust bank account % '
            'before taking fees', p_account.bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(p_account.ledger_account_id, v_income, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', p_memo)
    ) AS t;
    RETURN v_transfer_id;
END;
$$;


--
-- Name: trust_post_leasing_fee(uuid, uuid, text, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_post_leasing_fee(p_pmc_id uuid, p_lease_id uuid, p_account text, p_event_at timestamp with time zone) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_lease trust_leases;
    v_property_id uuid;
    v_taken trust_leasing_fees;
    v_agreement trust_management_agreements;
    v_fee numeric;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT u.property_id INTO v_property_id FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    IF v_account.property_id <> v_property_id THEN
        RAISE EXCEPTION 'trust: % is not an owner''s account for the lease''s property', p_account
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.* INTO v_taken FROM trust_leasing_fees AS f WHERE f.lease_id = p_lease_id;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> p_account THEN
            RAISE EXCEPTION 'trust: the leasing fee for lease % was taken from %, not %',
                p_lease_id, v_taken.ledger_account_id, p_account
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_taken.fee;  -- a retry: the fee already taken
    END IF;

    v_agreement := trust_agreement_on(p_account, v_lease.starts_on);
    IF v_agreement.id IS NULL THEN
        RAISE EXCEPTION 'trust: no management agreement for % in force on %',
            p_account, v_lease.starts_on
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_fee := round(v_agreement.leasing_fee_percent / 100 * v_lease.monthly_rent, 2);

    INSERT INTO trust_leasing_fees (
        pmc_id, lease_id, ledger_account_id, agreement_id, fee, transfer_id
    ) VALUES (
        p_pmc_id, p_lease_id, p_account, v_agreement.id, v_fee,
        CASE WHEN v_fee > 0 THEN trust_post_fee(
            v_account, v_fee, p_event_at, format('Leasing fee, lease from %s', v_lease.starts_on)
        ) END
    );
    RETURN v_fee;
END;
$$;


--
-- Name: trust_post_management_fee(uuid, text, date, date, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_post_management_fee(p_pmc_id uuid, p_account text, p_period_start date, p_period_end date, p_event_at timestamp with time zone) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_start timestamptz := p_period_start::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := p_period_end::timestamp AT TIME ZONE 'UTC';
    v_taken trust_management_fees;
    v_agreement trust_management_agreements;
    v_collected numeric;
    v_fee numeric;
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a fee period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.* INTO v_taken
    FROM trust_management_fees AS f
    WHERE f.ledger_account_id = p_account
      AND f.period_start < p_period_end AND p_period_start < f.period_end
    LIMIT 1;
    IF FOUND THEN
        IF (v_taken.period_start, v_taken.period_end) = (p_period_start, p_period_end) THEN
            RETURN v_taken.fee;  -- a retry: the fee already taken
        END IF;
        RAISE EXCEPTION 'trust: the fee for % to % overlaps the one taken for % to %',
            p_period_start, p_period_end, v_taken.period_start, v_taken.period_end
            USING ERRCODE = 'exclusion_violation';
    END IF;

    v_agreement := trust_agreement_on(p_account, p_period_start);
    IF v_agreement.id IS NULL THEN
        RAISE EXCEPTION 'trust: no management agreement for % in force on %',
            p_account, p_period_start
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(sum(cp.amount), 0) INTO v_collected
    FROM trust_charge_payments AS cp
    JOIN trust_charges AS c ON c.id = cp.charge_id AND c.kind = 'rent'
    JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    SELECT v_collected - coalesce(sum(r.amount), 0) INTO v_collected
    FROM trust_payment_reversals AS r
    JOIN trust_charges AS c ON c.id = r.charge_id AND c.kind = 'rent'
    JOIN pgledger_transfers AS t ON t.id = r.reversal_id
    WHERE t.from_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;

    v_fee := greatest(round(v_agreement.fee_percent / 100 * v_collected, 2),
                      v_agreement.minimum_fee)
             + v_agreement.flat_fee;

    INSERT INTO trust_management_fees (
        pmc_id, ledger_account_id, period_start, period_end, agreement_id, collected, fee,
        transfer_id
    ) VALUES (
        p_pmc_id, p_account, p_period_start, p_period_end, v_agreement.id, v_collected, v_fee,
        CASE WHEN v_fee > 0 THEN trust_post_fee(
            v_account, v_fee, p_event_at,
            format('Management fee %s to %s', p_period_start, p_period_end - 1)
        ) END
    );
    RETURN v_fee;
END;
$$;


--
-- Name: trust_post_opening_balances(uuid, uuid, date, jsonb, numeric, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_post_opening_balances(p_pmc_id uuid, p_bank_account_id uuid, p_opened_on date, p_balances jsonb, p_book_cash numeric, p_entered_by text) RETURNS text[]
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank trust_bank_accounts;
    v_cash text;
    v_accounts text[];
    v_amounts numeric[];
    v_balances jsonb;
    v_line text;
    v_opening trust_opening_balances;
    v_transfer_ids text[];
BEGIN
    -- The check that nothing was posted first must see every posting that committed while this
    -- waited for the lock below. At REPEATABLE READ or SERIALIZABLE it would read a snapshot
    -- taken before them.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: post opening balances at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_opened_on IS NULL OR p_book_cash IS NULL OR coalesce(p_entered_by, '') = '' THEN
        RAISE EXCEPTION 'trust: opening balances need the cutover date, the book cash and who '
            'entered them'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A cutover date can't be undone, and nothing may be dated before it: one typed years
    -- ahead would hold every posting off until then.
    IF p_opened_on > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: the cutover date % has not come yet', p_opened_on
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Post opening balances on or after the cutover date.';
    END IF;
    IF jsonb_typeof(p_balances) IS DISTINCT FROM 'object' OR p_balances = '{}' THEN
        RAISE EXCEPTION 'trust: opening balances are a JSON object of ledger account id to '
            'amount, with at least one account'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Waits for postings in flight on this account (they hold the row FOR SHARE), and holds new
    -- ones off until this commits.
    SELECT b.* INTO v_bank
    FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the cash account (bank_cash) of trust bank account % first',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT l.key INTO v_line
    FROM jsonb_each(p_balances) AS l
    WHERE NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = l.key AND t.bank_account_id = p_bank_account_id
          AND t.kind <> 'bank_cash'
    )
    ORDER BY l.key COLLATE "C"
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % is not an account held in trust bank account %',
            v_line, p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Its cash is the opening balances'' total; list what each account holds.';
    END IF;

    SELECT array_agg(l.key ORDER BY l.key COLLATE "C"),
           array_agg((l.value #>> '{}')::numeric ORDER BY l.key COLLATE "C")
    INTO v_accounts, v_amounts
    FROM jsonb_each(p_balances) AS l;

    SELECT l.account INTO v_line
    FROM unnest(v_accounts, v_amounts) AS l (account, amount)
    WHERE l.amount IS NULL OR l.amount <= 0 OR l.amount <> round(l.amount, 2)
    ORDER BY l.account COLLATE "C"
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: the opening balance of % is %; it must be positive and in cents',
            v_line, p_balances ->> v_line
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Leave out an account that held nothing.';
    END IF;

    IF (SELECT sum(a) FROM unnest(v_amounts) AS a) <> p_book_cash THEN
        RAISE EXCEPTION 'trust: the opening balances add up to %, not the book cash of %',
            (SELECT sum(a) FROM unnest(v_amounts) AS a), p_book_cash
            USING ERRCODE = 'check_violation',
                  HINT = 'An account may be missing or mistyped.';
    END IF;

    SELECT jsonb_object_agg(l.account, l.amount) INTO v_balances
    FROM unnest(v_accounts, v_amounts) AS l (account, amount);

    SELECT o.* INTO v_opening
    FROM trust_opening_balances AS o WHERE o.bank_account_id = p_bank_account_id;
    IF FOUND THEN
        IF (v_opening.opened_on, v_opening.balances, v_opening.entered_by)
            IS DISTINCT FROM (p_opened_on, v_balances, p_entered_by) THEN
            RAISE EXCEPTION 'trust: trust bank account % already opened on % with other '
                'balances', p_bank_account_id, v_opening.opened_on
                USING ERRCODE = 'unique_violation',
                      HINT = 'Correct an opening balance with a new transfer.';
        END IF;
        RETURN v_opening.transfer_ids;  -- a retry: the original transfers
    END IF;

    IF v_bank.closed_through IS NOT NULL OR EXISTS (
        SELECT e.id
        FROM pgledger_entries AS e
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id
        WHERE t.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: opening balances come first, and trust bank account % already '
            'has postings or an approved reconciliation', p_bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT array_agg(t.id ORDER BY t.n) INTO v_transfer_ids
    FROM pgledger_create_transfers(
        ARRAY(
            SELECT (v_cash, l.account, l.amount)::transfer_request
            FROM unnest(v_accounts, v_amounts) WITH ORDINALITY AS l (account, amount, n)
            ORDER BY l.n
        ),
        p_opened_on::timestamp AT TIME ZONE 'UTC',
        jsonb_build_object('memo', 'Opening balance')
    ) WITH ORDINALITY AS t (id, n);

    INSERT INTO trust_opening_balances (
        bank_account_id, pmc_id, opened_on, balances, book_cash, entered_by, transfer_ids
    ) VALUES (
        p_bank_account_id, p_pmc_id, p_opened_on, v_balances, p_book_cash, p_entered_by,
        v_transfer_ids
    );
    UPDATE trust_bank_accounts SET opened_at = p_opened_on::timestamp AT TIME ZONE 'UTC'
    WHERE id = p_bank_account_id;

    RETURN v_transfer_ids;
END;
$$;


--
-- Name: trust_post_transfers(uuid, text, public.transfer_request[], timestamp with time zone, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_post_transfers(p_pmc_id uuid, p_idempotency_key text, p_transfers public.transfer_request[], p_event_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_metadata jsonb DEFAULT NULL::jsonb) RETURNS SETOF public.pgledger_transfers_view
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_request jsonb := jsonb_build_object(
        'transfers', to_jsonb(p_transfers),
        'event_at', extract(epoch FROM p_event_at),
        'metadata', p_metadata
    );
    v_existing trust_idempotency_keys;
    v_transfer_ids text[];
BEGIN
    IF coalesce(cardinality(p_transfers), 0) = 0 THEN
        RAISE EXCEPTION 'trust: a posting needs at least one transfer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The key is filed under p_pmc_id, so every account must belong to that PMC. (The transfer
    -- scope trigger already keeps each transfer inside one PMC; this pins which one.)
    IF EXISTS (
        SELECT r.from_account_id
        FROM unnest(p_transfers) AS r
        LEFT JOIN trust_ledger_accounts AS f ON f.ledger_account_id = r.from_account_id
        LEFT JOIN trust_ledger_accounts AS t ON t.ledger_account_id = r.to_account_id
        WHERE f.pmc_id IS DISTINCT FROM p_pmc_id OR t.pmc_id IS DISTINCT FROM p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: a transfer in this posting is not in PMC %', p_pmc_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    -- Same PMC and key: wait here until any earlier call with them commits or rolls back.
    -- (A hash collision with another key only means waiting a little longer.)
    PERFORM pg_advisory_xact_lock(
        hashtext('trust_post_transfers'), hashtext(p_pmc_id || '/' || p_idempotency_key)
    );

    SELECT * INTO v_existing
    FROM trust_idempotency_keys
    WHERE pmc_id = p_pmc_id AND idempotency_key = p_idempotency_key;

    IF NOT FOUND THEN
        SELECT array_agg(t.id ORDER BY t.ordinality) INTO v_transfer_ids
        FROM pgledger_create_transfers(p_transfers, p_event_at, p_metadata)
            WITH ORDINALITY AS t;

        INSERT INTO trust_idempotency_keys (pmc_id, idempotency_key, request, transfer_ids)
        VALUES (p_pmc_id, p_idempotency_key, v_request, v_transfer_ids);
    ELSIF v_existing.request IS DISTINCT FROM v_request THEN
        RAISE EXCEPTION 'trust: idempotency key % was already used for a different posting',
            p_idempotency_key
            USING ERRCODE = 'unique_violation',
                  HINT = 'Use a new key for a new posting; resend a key only to retry.';
    ELSE
        v_transfer_ids := v_existing.transfer_ids;  -- a retry: the original transfers
    END IF;

    RETURN QUERY
    SELECT v.*
    FROM unnest(v_transfer_ids) WITH ORDINALITY AS k (id, n)
    JOIN pgledger_transfers_view AS v ON v.id = k.id
    ORDER BY k.n;
END;
$$;


--
-- Name: trust_refuse_changing_bank_kind(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_changing_bank_kind() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.kind IS DISTINCT FROM OLD.kind THEN
        RAISE EXCEPTION 'trust: trust bank account % is %, and its kind never changes',
            OLD.id, OLD.kind
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Open a new trust bank account of the other kind.';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_refuse_ledger_rewrite(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_ledger_rewrite() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    RAISE EXCEPTION 'trust: % is append-only, % refused', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation',
              HINT = 'Correct a mistake with a new reversing transfer.';
END;
$$;


--
-- Name: trust_refuse_moving_closed_through(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_moving_closed_through() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.closed_through IS DISTINCT FROM (
        SELECT max(r.period_end) FROM trust_reconciliations AS r
        WHERE r.bank_account_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through the end of its newest '
            'approved reconciliation, not %', NEW.id, NEW.closed_through
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only trust_approve_reconciliation moves it.';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_refuse_moving_opened_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_moving_opened_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.opened_at IS DISTINCT FROM (
        SELECT o.opened_on::timestamp AT TIME ZONE 'UTC' FROM trust_opening_balances AS o
        WHERE o.bank_account_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % opened at the start of its cutover date, '
            'not %', NEW.id, NEW.opened_at
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only trust_post_opening_balances sets it.';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_refuse_negative_balance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_negative_balance() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_kind text;
BEGIN
    SELECT kind INTO v_kind FROM trust_ledger_accounts WHERE ledger_account_id = NEW.id;
    IF v_kind IS DISTINCT FROM 'bank_cash' THEN
        RAISE EXCEPTION 'trust: % account % would go below zero (balance %)',
            coalesce(v_kind, 'unmapped'), NEW.id, NEW.balance
            USING ERRCODE = 'check_violation',
                  HINT = 'An account held for someone may never be overdrawn (Cal. Reg. 2832.1).';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_refuse_overlapping_leases(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_overlapping_leases() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_other uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: write a lease at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    PERFORM u.id FROM trust_units AS u WHERE u.id = NEW.unit_id FOR NO KEY UPDATE;

    SELECT l.id INTO v_other
    FROM trust_leases AS l
    WHERE l.unit_id = NEW.unit_id
      AND l.id <> NEW.id
      AND l.starts_on <= coalesce(NEW.ends_on, 'infinity'::date)
      AND NEW.starts_on <= coalesce(l.ends_on, 'infinity'::date)
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: the lease would overlap lease % of the same unit', v_other
            USING ERRCODE = 'exclusion_violation',
                  HINT = 'End the other lease first; a unit has one lease at a time.';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_refuse_posting_into_closed_period(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_posting_into_closed_period() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
    v_closed_through timestamptz;
    v_opened_at timestamptz;
BEGIN
    -- FOR SHARE waits for an approval or an opening in progress on either account and then
    -- reads the date it set. At REPEATABLE READ or SERIALIZABLE, one that committed after this
    -- transaction's snapshot makes the lock fail with a serialization error instead.
    FOR v_bank, v_closed_through, v_opened_at IN
        SELECT b.id, b.closed_through, b.opened_at
        FROM trust_bank_accounts AS b
        JOIN trust_ledger_accounts AS t ON t.bank_account_id = b.id
        WHERE t.ledger_account_id IN (NEW.from_account_id, NEW.to_account_id)
        ORDER BY b.id
        FOR SHARE OF b
    LOOP
        IF NEW.event_at < v_closed_through THEN
            RAISE EXCEPTION 'trust: trust bank account % is reconciled and closed through %; '
                'a transfer dated % can''t be posted', v_bank, v_closed_through, NEW.event_at
                USING ERRCODE = 'check_violation',
                      HINT = 'Date the correction in the next open period.';
        END IF;
        IF NEW.event_at < v_opened_at THEN
            RAISE EXCEPTION 'trust: trust bank account % opened at % with balances carried '
                'over; a transfer dated % can''t be posted', v_bank, v_opened_at, NEW.event_at
                USING ERRCODE = 'check_violation',
                      HINT = 'Its history before the cutover stays in the old system.';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;


--
-- Name: trust_report_audit_log(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_audit_log(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, at text, staff text, db_role text, action text, record text, detail text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: an audit log runs from a day to the same day or a later one '
            '(got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH holders AS (
        SELECT t.ledger_account_id AS id,
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    changes AS (
        SELECT a.id, a.at, coalesce(s.display_name, 'not recorded') AS staff, a.db_role,
               a.action, regexp_replace(a.table_name, '^(trust|pgledger)_', '') AS record,
               CASE
                   -- A transfer: how much, from and to whom, dated when, and its memo.
                   WHEN a.table_name = 'pgledger_transfers' THEN
                       (a.row_data ->> 'amount') || ' from ' || f.holder || ' to ' || t.holder
                       || ' dated ' || (a.row_data ->> 'event_at')
                       || coalesce(' - ' || (a.row_data -> 'metadata' ->> 'memo'), '')
                   -- An update: each field it changed, from what to what.
                   WHEN a.action = 'update' THEN (
                       SELECT coalesce(string_agg(
                           n.key || ': ' || coalesce(a.before ->> n.key, 'null') || ' to '
                           || coalesce(n.value #>> '{}', 'null'),
                           ', ' ORDER BY n.key COLLATE "C"
                       ), 'no change')
                       FROM jsonb_each(a.row_data) AS n
                       WHERE n.value IS DISTINCT FROM a.before -> n.key
                   )
                   -- Anything else: its fields, but ids and the clock's time of writing it
                   -- (the log's own time says when).
                   ELSE (
                       SELECT string_agg(
                           n.key || ': ' || n.value, ', ' ORDER BY n.key COLLATE "C"
                       )
                       FROM jsonb_each_text(a.row_data) AS n
                       WHERE n.value IS NOT NULL AND n.key <> 'id' AND n.key NOT LIKE '%\_id'
                         AND n.key NOT IN ('created_at', 'approved_at')
                   )
               END AS detail
        FROM trust_audit_log AS a
        LEFT JOIN trust_staff AS s ON s.id = a.staff_id
        LEFT JOIN holders AS f ON f.id = a.row_data ->> 'from_account_id'
        LEFT JOIN holders AS t ON t.id = a.row_data ->> 'to_account_id'
        WHERE a.pmc_id = p_pmc_id AND a.at >= v_start AND a.at < v_end
    ),
    lines AS (
        SELECT 0 AS section, NULL::bigint AS id, 'PMC' AS item, NULL::timestamptz AS at,
               pmc.display_name AS staff, NULL::text AS db_role, NULL::text AS action,
               NULL::text AS record, NULL::text AS detail
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, NULL, 'period', NULL,
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 2, c.id, 'change', c.at, c.staff, c.db_role, c.action, c.record, c.detail
        FROM changes AS c
        UNION ALL
        SELECT 3, NULL, 'total', NULL, NULL, NULL, NULL, NULL,
               (SELECT count(*) FROM changes) || ' changes'
    )
    SELECT row_number() OVER (ORDER BY l.section, l.id)::integer,
           l.item,
           to_char(l.at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'),
           -- The PMC's name and the period sit in the staff column of the first two lines.
           l.staff,
           l.db_role,
           l.action,
           l.record,
           l.detail
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_delinquency(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_delinquency(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, tenants text, current_due numeric, days_1_30 numeric, days_31_60 numeric, days_61_90 numeric, over_90 numeric, total numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: delinquency is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, oldest first.
        SELECT c.lease_id, c.due_on, c.amount - p.amount AS left_owing,
               sum(c.amount - p.amount) OVER (
                   PARTITION BY c.lease_id ORDER BY c.due_on, c.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit' AND c.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day, and payments
        -- by then toward charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.lease_id, p_as_of - o.due_on AS late,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),
    by_lease AS (
        SELECT a.lease_id,
               sum(a.owed) FILTER (WHERE a.late = 0) AS b0,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 1 AND 30) AS b1,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 31 AND 60) AS b2,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 61 AND 90) AS b3,
               sum(a.owed) FILTER (WHERE a.late > 90) AS b4,
               sum(a.owed) AS owed
        FROM aged AS a
        GROUP BY a.lease_id
        HAVING sum(a.owed) > 0
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::date AS starts_on, 1 AS step, 'PMC' AS item, pmc.display_name AS names,
               NULL::numeric AS b0, NULL::numeric AS b1, NULL::numeric AS b2,
               NULL::numeric AS b3, NULL::numeric AS b4, NULL::numeric AS owed
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, pr.display_name, u.display_name, l.starts_on, 0, 'lease',
               (SELECT string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C",
                                  tn.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
                WHERE lt.lease_id = l.id),
               coalesce(b.b0, 0), coalesce(b.b1, 0), coalesce(b.b2, 0), coalesce(b.b3, 0),
               coalesce(b.b4, 0), b.owed
        FROM by_lease AS b
        JOIN trust_leases AS l ON l.id = b.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(b.b0), 0), coalesce(sum(b.b1), 0), coalesce(sum(b.b2), 0),
               coalesce(sum(b.b3), 0), coalesce(sum(b.b4), 0), coalesce(sum(b.owed), 0)
        FROM by_lease AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.starts_on, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           -- The PMC's name and the day sit in the tenants column of the first two lines.
           l.names,
           round(l.b0, 2), round(l.b1, 2), round(l.b2, 2), round(l.b3, 2), round(l.b4, 2),
           round(l.owed, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_general_ledger(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_general_ledger(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, bank text, account text, posted_at text, description text, debit numeric, credit numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: a general ledger runs from a day to the same day or a later '
            'one (got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               -- The holder's name, for an entry on the other side of this account.
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               -- +1 where a credit grows the balance (money held for someone), -1 for cash.
               CASE t.kind WHEN 'bank_cash' THEN -1 ELSE 1 END AS sign
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    entries AS (
        SELECT a.id AS account, e.amount, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < v_end
    ),
    openings AS (
        SELECT a.*,
               a.sign * coalesce(sum(x.amount) FILTER (WHERE x.event_at < v_start), 0)
                   AS opening,
               count(x.account) FILTER (WHERE x.event_at >= v_start) AS posted
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.id
        GROUP BY a.id, a.kind, a.bank_account_id, a.bank_name, a.account_name, a.holder, a.sign
    ),
    shown AS (
        SELECT o.* FROM openings AS o
        WHERE o.opening <> 0 OR o.posted > 0 OR o.kind = 'bank_cash'
    ),
    postings AS (
        SELECT x.account, x.event_at, x.version,
               greatest(-x.amount, 0) AS dr, greatest(x.amount, 0) AS cr,
               s.opening + s.sign * sum(x.amount) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.holder || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN shown AS s ON s.id = x.account
        JOIN accounts AS c ON c.id = x.other
        WHERE x.event_at >= v_start
    ),
    lines AS (
        -- (section, bank, bank id, cash first, account, account id, step, event_at, version)
        -- orders the report; names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS cash_first,
               NULL::text AS account_name, NULL::text AS id, 0 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS description, NULL::numeric AS dr,
               NULL::numeric AS cr, NULL::numeric AS balance
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, NULL, NULL, 1, NULL, NULL, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 0, NULL, NULL, 'opening balance', s.account_name, NULL,
               NULL, NULL, s.opening
        FROM shown AS s
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 1, p.event_at, p.version, 'entry', s.account_name,
               p.description, p.dr, p.cr, p.running
        FROM postings AS p
        JOIN shown AS s ON s.id = p.account
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 2, NULL, NULL, 'closing balance', s.account_name, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p WHERE p.account = s.id), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p WHERE p.account = s.id), 0),
               s.opening + s.sign * coalesce((
                   SELECT sum(p.cr) - sum(p.dr) FROM postings AS p WHERE p.account = s.id
               ), 0)
        FROM shown AS s
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 0, NULL, NULL, 'total', NULL, NULL,
               coalesce((SELECT sum(p.dr) FROM postings AS p), 0),
               coalesce((SELECT sum(p.cr) FROM postings AS p), 0),
               NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.cash_first,
                        l.account_name COLLATE "C", l.id, l.step, l.event_at, l.version
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the period sit in the account column of the first two lines.
           l.label,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'),
           l.description,
           round(l.dr, 2),
           round(l.cr, 2),
           round(l.balance, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_owner_balances(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_owner_balances(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, owner text, property text, detail text, balance numeric, reserve numeric, bills numeric, available numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: owner balances are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, o.display_name AS owner_name,
               pr.display_name AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS held,
               coalesce(ag.reserve, 0.00) AS kept,
               -- trust_unpaid_bills, written out: a report runs with the caller's grants, and
               -- the app and the AI may call reports, not helpers.
               coalesce((
                   SELECT sum(b.amount)
                   FROM trust_bills AS b
                   WHERE b.ledger_account_id = t.ledger_account_id AND b.bill_date <= p_as_of
                     AND NOT EXISTS (
                         SELECT p.bill_id
                         FROM trust_bill_payments AS p
                         JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                         WHERE p.bill_id = b.id AND p.step = 'set_aside'
                           AND tr.event_at < v_cutoff
                     )
               ), 0.00) AS owed,
               ag.id, ag.fee_percent, ag.minimum_fee, ag.flat_fee
        FROM trust_ledger_accounts AS t
        JOIN trust_owners AS o ON o.id = t.owner_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        -- The agreement in force that day: the latest one starting on or before it.
        LEFT JOIN LATERAL (
            SELECT g.id, g.fee_percent, g.minimum_fee, g.flat_fee, g.reserve
            FROM trust_management_agreements AS g
            WHERE g.ledger_account_id = t.ledger_account_id AND g.starts_on <= p_as_of
            ORDER BY g.starts_on DESC
            LIMIT 1
        ) AS ag ON true
        WHERE t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS owner_name, NULL::text AS property_name,
               NULL::text AS account, 1 AS step, 'PMC' AS item, pmc.display_name AS detail,
               NULL::numeric AS held, NULL::numeric AS kept, NULL::numeric AS owed,
               NULL::numeric AS free
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, a.owner_name, a.property_name, a.account, 0, 'owner property',
               CASE
                   WHEN a.id IS NULL THEN 'no management agreement'
                   ELSE trim_scale(a.fee_percent)::text || '% of collected rent'
                        || ', minimum ' || to_char(a.minimum_fee, 'FM999999990.00')
                        || ', flat ' || to_char(a.flat_fee, 'FM999999990.00')
               END,
               a.held, a.kept, a.owed, greatest(a.held - a.kept - a.owed, 0.00)
        FROM accounts AS a
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(a.held), 0.00), coalesce(sum(a.kept), 0.00),
               coalesce(sum(a.owed), 0.00),
               coalesce(sum(greatest(a.held - a.kept - a.owed, 0.00)), 0.00)
        FROM accounts AS a
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.owner_name COLLATE "C", l.property_name COLLATE "C",
                        l.account, l.step
           )::integer,
           l.item,
           l.owner_name,
           l.property_name,
           l.detail,
           round(l.held, 2),
           round(l.kept, 2),
           round(l.owed, 2),
           round(l.free, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_owner_statement(uuid, uuid, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_owner_statement(p_pmc_id uuid, p_owner_id uuid, p_period_start timestamp with time zone, p_period_end timestamp with time zone) RETURNS TABLE(line integer, property text, posted_on text, item text, detail text, amount numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a statement period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS property_name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    entries AS (
        SELECT a.account, e.amount AS change, e.account_version AS version, tr.event_at,
               CASE WHEN tr.from_account_id = e.account_id THEN tr.to_account_id
                    ELSE tr.from_account_id END AS other,
               tr.metadata ->> 'memo' AS memo
        FROM accounts AS a
        JOIN pgledger_entries AS e ON e.account_id = a.account
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE tr.event_at < p_period_end
    ),
    openings AS (
        SELECT a.account, a.property_name,
               coalesce(sum(x.change) FILTER (WHERE x.event_at < p_period_start), 0.00) AS opening,
               coalesce(sum(x.change), 0.00) AS closing
        FROM accounts AS a
        LEFT JOIN entries AS x ON x.account = a.account
        GROUP BY a.account, a.property_name
    ),
    postings AS (
        SELECT x.account, o.property_name, x.event_at, x.version, x.change,
               o.opening + sum(x.change) OVER (
                   PARTITION BY x.account ORDER BY x.event_at, x.version
               ) AS running,
               c.kind || ': ' || CASE c.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN ow.display_name || ' / ' || cp.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END || coalesce(' - ' || x.memo, '') AS description
        FROM entries AS x
        JOIN openings AS o ON o.account = x.account
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = x.other
        JOIN trust_bank_accounts AS b ON b.id = c.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = c.pmc_id
        LEFT JOIN trust_owners AS ow ON ow.id = c.owner_id
        LEFT JOIN trust_properties AS cp ON cp.id = c.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = c.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = c.vendor_id
        WHERE x.event_at >= p_period_start
    ),
    lines AS (
        -- (section, property, account, step, event_at, version) orders the statement;
        -- names sort by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS account, 1 AS step,
               NULL::timestamptz AS event_at, NULL::bigint AS version,
               'PMC' AS item, pmc.display_name AS detail, NULL::numeric AS change,
               NULL::numeric AS balance
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, NULL, NULL, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, NULL, NULL, 'period',
               to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
               || to_char(p_period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, o.property_name, o.account, 0, NULL, NULL, 'opening balance', NULL, NULL,
               o.opening
        FROM openings AS o
        UNION ALL
        SELECT 1, p.property_name, p.account, 1, p.event_at, p.version, 'posting',
               p.description, p.change, p.running
        FROM postings AS p
        UNION ALL
        SELECT 1, o.property_name, o.account, 2, NULL, NULL, 'closing balance', NULL, NULL,
               o.closing
        FROM openings AS o
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'all properties: opening balance', NULL, NULL,
               coalesce((SELECT sum(o.opening) FROM openings AS o), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 1, NULL, NULL, 'all properties: money in', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change > 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 2, NULL, NULL, 'all properties: money out', NULL,
               coalesce((SELECT sum(p.change) FROM postings AS p WHERE p.change < 0), 0.00), NULL
        UNION ALL
        SELECT 2, NULL, NULL, 3, NULL, NULL, 'all properties: closing balance', NULL, NULL,
               coalesce((SELECT sum(o.closing) FROM openings AS o), 0.00)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.account, l.step,
                        l.event_at, l.version
           )::integer,
           l.property_name,
           to_char(l.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'),
           l.item,
           l.detail,
           l.change,
           l.balance
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_rent_roll(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_rent_roll(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, tenants text, detail text, monthly_rent numeric, deposits numeric, prepaid numeric, charged numeric, paid numeric, balance_due numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    -- The end of the day, UTC: the first moment that is not on the roll.
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a rent roll is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH held AS (
        -- Each tenant's deposits and prepaid rent at the end of the day.
        SELECT t.tenant_id,
               coalesce(sum(e.amount) FILTER (WHERE t.kind = 'tenant_deposit'), 0.00) AS deposits,
               coalesce(sum(e.amount) FILTER (WHERE t.kind = 'prepaid_rent'), 0.00) AS prepaid
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.pmc_id = p_pmc_id AND t.kind IN ('tenant_deposit', 'prepaid_rent')
          AND tr.event_at < v_cutoff
        GROUP BY t.tenant_id
    ),
    leases AS (
        SELECT l.id, l.unit_id, l.starts_on, l.ends_on, l.monthly_rent,
               l.starts_on <= p_as_of AND p_as_of <= coalesce(l.ends_on, 'infinity'::date)
                   AS is_current,
               coalesce((
                   SELECT sum(CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END)
                   FROM trust_charges AS c
                   WHERE c.lease_id = l.id AND c.due_on <= p_as_of
               ), 0.00) AS charged,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charges AS c
                   JOIN trust_charge_payments AS cp ON cp.charge_id = c.id
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE c.lease_id = l.id AND tr.event_at < v_cutoff
               ), 0.00) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_charges AS c
                   JOIN trust_payment_reversals AS r ON r.charge_id = c.id
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE c.lease_id = l.id AND tr.event_at < v_cutoff
               ), 0.00) AS paid
        FROM trust_leases AS l
        WHERE l.pmc_id = p_pmc_id
    ),
    on_lease AS (
        SELECT lt.lease_id,
               string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C", tn.id)
                   AS names,
               sum(coalesce(h.deposits, 0.00)) AS deposits,
               sum(coalesce(h.prepaid, 0.00)) AS prepaid
        FROM trust_lease_tenants AS lt
        JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
        LEFT JOIN held AS h ON h.tenant_id = lt.tenant_id
        WHERE lt.pmc_id = p_pmc_id
        GROUP BY lt.lease_id
    ),
    current_tenants AS (
        SELECT DISTINCT lt.tenant_id
        FROM trust_lease_tenants AS lt
        JOIN leases AS l ON l.id = lt.lease_id
        WHERE l.is_current
    ),
    units AS (
        SELECT u.id, pr.display_name AS property_name, u.display_name AS unit_name,
               l.id AS lease_id
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN leases AS l ON l.unit_id = u.id AND l.is_current
        WHERE u.pmc_id = p_pmc_id
    ),
    lines AS (
        -- (section, property, unit, unit id, step) orders the roll.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::uuid AS unit_id, 1 AS step, 'PMC' AS item, NULL::text AS names,
               pmc.display_name AS detail, NULL::numeric AS rent, NULL::numeric AS deposits,
               NULL::numeric AS prepaid, NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of', NULL,
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, u.property_name, u.unit_name, u.id, 0, 'unit', o.names,
               CASE
                   WHEN l.id IS NULL THEN 'vacant'
                   WHEN l.ends_on IS NULL
                       THEN to_char(l.starts_on, 'YYYY-MM-DD') || ', month to month'
                   ELSE to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                        || to_char(l.ends_on, 'YYYY-MM-DD')
               END,
               l.monthly_rent, o.deposits, o.prepaid, l.charged, l.paid
        FROM units AS u
        LEFT JOIN leases AS l ON l.id = u.lease_id
        LEFT JOIN on_lease AS o ON o.lease_id = u.lease_id
        UNION ALL
        -- g.on_current: true for current leases, false for the rest, NULL for all of them.
        SELECT 2, NULL, NULL, NULL, g.step, g.item, NULL,
               CASE WHEN g.on_current THEN (
                   SELECT count(u.lease_id) || ' of ' || count(*) || ' units leased'
                   FROM units AS u
               ) END,
               CASE WHEN g.on_current THEN coalesce((
                   SELECT sum(l.monthly_rent) FROM leases AS l WHERE l.is_current
               ), 0.00) END,
               coalesce((
                   SELECT sum(h.deposits) FROM held AS h
                   WHERE g.on_current IS NULL OR g.on_current = EXISTS (
                       SELECT ct.tenant_id FROM current_tenants AS ct
                       WHERE ct.tenant_id = h.tenant_id
                   )
               ), 0.00),
               coalesce((
                   SELECT sum(h.prepaid) FROM held AS h
                   WHERE g.on_current IS NULL OR g.on_current = EXISTS (
                       SELECT ct.tenant_id FROM current_tenants AS ct
                       WHERE ct.tenant_id = h.tenant_id
                   )
               ), 0.00),
               coalesce((
                   SELECT sum(l.charged) FROM leases AS l
                   WHERE g.on_current IS NULL OR g.on_current = l.is_current
               ), 0.00),
               coalesce((
                   SELECT sum(l.paid) FROM leases AS l
                   WHERE g.on_current IS NULL OR g.on_current = l.is_current
               ), 0.00)
        FROM (VALUES
            (0, 'total: current leases', true),
            (1, 'not on a current lease', false),
            (2, 'total: all tenants', NULL)
        ) AS g (step, item, on_current)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.unit_id, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           l.names,
           l.detail,
           l.rent,
           l.deposits,
           l.prepaid,
           l.charged,
           l.paid,
           l.charged - l.paid
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_security_deposits(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_security_deposits(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, bank text, tenant text, property text, held numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a deposit register is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               tn.display_name AS tenant_name, pr.display_name AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_properties AS pr ON pr.id = tn.property_id
        WHERE t.pmc_id = p_pmc_id AND b.kind = 'security_deposit'
          AND t.kind IN ('tenant_deposit', 'bank_cash')
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS tenant_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::text AS property_name, NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL
        UNION ALL
        SELECT 1, b.bank_name, b.bank_account_id, 0, b.tenant_name, b.id, 'deposit',
               b.tenant_name, b.property_name, b.balance
        FROM balances AS b
        WHERE b.kind = 'tenant_deposit' AND b.balance <> 0
        UNION ALL
        SELECT 1, b.bank_name, b.bank_account_id, 1, NULL, NULL, 'deposits held', NULL, NULL,
               sum(b.balance) FILTER (WHERE b.kind = 'tenant_deposit')
        FROM balances AS b
        GROUP BY b.bank_name, b.bank_account_id
        UNION ALL
        SELECT 1, b.bank_name, b.bank_account_id, 2, NULL, NULL, 'book cash', NULL, NULL,
               -sum(b.balance) FILTER (WHERE b.kind = 'bank_cash')
        FROM balances AS b
        GROUP BY b.bank_name, b.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total held', NULL, NULL,
               coalesce(sum(b.balance) FILTER (WHERE b.kind = 'tenant_deposit'), 0)
        FROM balances AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.tenant_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the tenant column of the first two lines.
           l.label,
           l.property_name,
           round(coalesce(l.held, CASE WHEN l.section > 0 THEN 0 END), 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_tenant_ledger(uuid, uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_tenant_ledger(p_pmc_id uuid, p_lease_id uuid, p_as_of date) RETURNS TABLE(line integer, on_date date, item text, detail text, charged numeric, paid numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant ledger is through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT l.id FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH entries AS (
        SELECT c.due_on AS on_date, 1 AS step, c.created_at AS at, c.id::text AS id, c.kind AS item,
               c.memo AS detail,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS charged,
               0::numeric AS paid
        FROM trust_charges AS c
        WHERE c.lease_id = p_lease_id AND c.due_on <= p_as_of
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 2, tr.event_at, tr.id, 'payment',
               tr.metadata ->> 'memo', 0, sum(cp.amount)
        FROM trust_charge_payments AS cp
        JOIN trust_charges AS c ON c.id = cp.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
        UNION ALL
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 3, tr.event_at, tr.id, 'bounced payment',
               tr.metadata ->> 'memo', 0, -sum(r.amount)
        FROM trust_payment_reversals AS r
        JOIN trust_charges AS c ON c.id = r.charge_id
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE c.lease_id = p_lease_id AND tr.event_at < v_cutoff
        GROUP BY tr.id, tr.event_at, tr.metadata
    ),
    lines AS (
        SELECT 0 AS section, NULL::date AS on_date, 0 AS step, NULL::timestamptz AS at,
               NULL::text AS id, 'lease' AS item,
               u.display_name || ', ' || to_char(l.starts_on, 'YYYY-MM-DD')
               || CASE WHEN l.ends_on IS NULL THEN ', month to month'
                       ELSE ' to ' || to_char(l.ends_on, 'YYYY-MM-DD') END AS detail,
               NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
        WHERE l.id = p_lease_id
        UNION ALL
        SELECT 1, e.on_date, e.step, e.at, e.id, e.item, e.detail, e.charged, e.paid
        FROM entries AS e
        UNION ALL
        SELECT 2, p_as_of, 0, NULL, NULL, 'balance', 'end of day UTC',
               coalesce((SELECT sum(e.charged) FROM entries AS e), 0),
               coalesce((SELECT sum(e.paid) FROM entries AS e), 0)
    )
    SELECT row_number() OVER w::integer,
           l.on_date,
           l.item,
           l.detail,
           round(l.charged, 2),
           round(l.paid, 2),
           CASE WHEN l.section > 0 THEN round(coalesce(sum(
               coalesce(l.charged, 0) - coalesce(l.paid, 0)
           ) FILTER (WHERE l.section = 1) OVER w, 0), 2) END
    FROM lines AS l
    WINDOW w AS (ORDER BY l.section, l.on_date, l.step, l.at, l.id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_three_way_reconciliation(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_three_way_reconciliation(p_pmc_id uuid, p_reconciliation_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
WITH reconciliation AS (
    SELECT r.*, b.display_name AS bank_name, b.kind AS bank_kind, p.display_name AS pmc_name
    FROM trust_reconciliations AS r
    JOIN trust_bank_accounts AS b ON b.id = r.bank_account_id
    JOIN trust_pmcs AS p ON p.pmc_id = r.pmc_id
    WHERE r.id = p_reconciliation_id AND r.pmc_id = p_pmc_id
),
balances AS (
    SELECT t.ledger_account_id, t.kind, t.owner_id, t.property_id, t.tenant_id, t.vendor_id,
           sum(e.amount) AS balance
    FROM reconciliation AS r
    JOIN trust_ledger_accounts AS t ON t.bank_account_id = r.bank_account_id
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE tr.event_at < r.period_end
    GROUP BY t.ledger_account_id
),
legs AS (
    SELECT r.statement_balance AS statement,
           coalesce(-(SELECT sum(b.balance) FROM balances AS b WHERE b.kind = 'bank_cash'), 0)
               AS journal,
           coalesce((SELECT sum(b.balance) FROM balances AS b WHERE b.kind <> 'bank_cash'), 0)
               AS ledgers
    FROM reconciliation AS r
),
ledgers AS (
    SELECT b.kind || ': ' || CASE b.kind
               WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
               WHEN 'vendor_payable' THEN v.display_name
               WHEN 'pmc_income' THEN r.pmc_name
               ELSE tn.display_name
           END AS holder,
           b.balance,
           b.ledger_account_id
    FROM balances AS b
    CROSS JOIN reconciliation AS r
    LEFT JOIN trust_owners AS o ON o.id = b.owner_id
    LEFT JOIN trust_properties AS pr ON pr.id = b.property_id
    LEFT JOIN trust_tenants AS tn ON tn.id = b.tenant_id
    LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
    WHERE b.kind <> 'bank_cash'
)
SELECT 1, 'PMC', r.pmc_name, NULL::numeric FROM reconciliation AS r
UNION ALL
SELECT 2, 'trust bank account', r.bank_name || ' (' || r.bank_kind || ')', NULL
FROM reconciliation AS r
UNION ALL
SELECT 3, 'period',
       to_char(r.period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
       || to_char(r.period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
       NULL
FROM reconciliation AS r
UNION ALL
SELECT 4, 'prepared by', r.prepared_by, NULL FROM reconciliation AS r
UNION ALL
SELECT 5, 'approved by',
       r.approved_by || ' at '
       || to_char(r.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
       NULL
FROM reconciliation AS r
UNION ALL
SELECT 6, 'bank statement balance', NULL, l.statement FROM legs AS l
UNION ALL
SELECT 7, 'trust journal (book cash)', NULL, l.journal FROM legs AS l
UNION ALL
SELECT 8, 'beneficiary ledgers', NULL, l.ledgers FROM legs AS l
UNION ALL
SELECT 9, 'difference: bank statement - trust journal', NULL, l.statement - l.journal
FROM legs AS l
UNION ALL
SELECT 10, 'difference: trust journal - beneficiary ledgers', NULL, l.journal - l.ledgers
FROM legs AS l
UNION ALL
SELECT 10 + row_number() OVER (ORDER BY ld.holder COLLATE "C", ld.ledger_account_id)::integer,
       'ledger', ld.holder, ld.balance
FROM ledgers AS ld
ORDER BY 1
$$;


--
-- Name: trust_report_trial_balance(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_trial_balance(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, bank text, account text, debit numeric, credit numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a trial balance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id AS id, t.kind, t.bank_account_id,
               b.display_name || ' (' || b.kind || ')' AS bank_name,
               CASE t.kind
                   WHEN 'bank_cash' THEN 'book cash'
                   ELSE t.kind || ': ' || CASE t.kind
                       WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                       WHEN 'vendor_payable' THEN v.display_name
                       WHEN 'pmc_income' THEN pmc.display_name
                       ELSE tn.display_name
                   END
               END AS account_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0) AS balance
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    -- A balance on the debit side is money the bank holds (-balance of its cash account); every
    -- other balance is money held for someone, on the credit side.
    sides AS (
        SELECT b.*, greatest(-b.balance, 0) AS dr, greatest(b.balance, 0) AS cr
        FROM balances AS b
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               NULL::text AS account_name, NULL::text AS id, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS dr, NULL::numeric AS cr
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, CASE s.kind WHEN 'bank_cash' THEN 0 ELSE 1 END,
               s.account_name, s.id, 'account', s.account_name, s.dr, s.cr
        FROM sides AS s
        WHERE s.balance <> 0 OR s.kind = 'bank_cash'
        UNION ALL
        SELECT 1, s.bank_name, s.bank_account_id, 2, NULL, NULL, 'bank total', NULL,
               sum(s.dr), sum(s.cr)
        FROM sides AS s
        GROUP BY s.bank_name, s.bank_account_id
        UNION ALL
        SELECT 2, NULL, NULL, 0, NULL, NULL, 'total', NULL,
               coalesce(sum(s.dr), 0), coalesce(sum(s.cr), 0)
        FROM sides AS s
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.bank_name COLLATE "C", l.bank_id, l.step,
                        l.account_name COLLATE "C", l.id
           )::integer,
           l.item,
           l.bank_name,
           -- The PMC's name and the day sit in the account column of the first two lines.
           l.label,
           round(l.dr, 2),
           round(l.cr, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_unpaid_bills(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_unpaid_bills(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, vendor text, reference text, property text, bill_date date, due_on date, days_past_due integer, bucket text, amount numeric, set_aside numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: unpaid bills are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH steps AS (
        SELECT p.bill_id, p.step
        FROM trust_bill_payments AS p
        JOIN pgledger_transfers AS t ON t.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND t.event_at < v_cutoff
    ),
    unpaid AS (
        SELECT v.display_name AS vendor_name, b.reference AS ref, pr.display_name AS place,
               b.bill_date AS billed, b.due_on AS due, greatest(p_as_of - b.due_on, 0) AS late,
               b.amount AS owed,
               CASE WHEN EXISTS (
                   SELECT s.bill_id FROM steps AS s
                   WHERE s.bill_id = b.id AND s.step = 'set_aside'
               ) THEN b.amount ELSE 0.00 END AS held
        FROM trust_bills AS b
        JOIN trust_vendors AS v ON v.id = b.vendor_id
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (
              SELECT s.bill_id FROM steps AS s WHERE s.bill_id = b.id AND s.step = 'paid'
          )
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS vendor_name, 1 AS step, NULL::date AS due,
               NULL::text AS ref, 'PMC' AS item, pmc.display_name AS place, NULL::date AS billed,
               NULL::integer AS late, NULL::numeric AS owed, NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, 2, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, u.vendor_name, 0, u.due, u.ref, 'bill', u.place, u.billed, u.late, u.owed,
               u.held
        FROM unpaid AS u
        UNION ALL
        SELECT 1, u.vendor_name, 1, NULL, NULL, 'vendor total', NULL, NULL, NULL,
               sum(u.owed), sum(u.held)
        FROM unpaid AS u GROUP BY u.vendor_name
        UNION ALL
        SELECT 2, NULL, 0, NULL, NULL, 'total', NULL, NULL, NULL,
               coalesce(sum(u.owed), 0.00), coalesce(sum(u.held), 0.00)
        FROM unpaid AS u
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.vendor_name COLLATE "C", l.step, l.due,
                        l.ref COLLATE "C"
           )::integer,
           l.item,
           l.vendor_name,
           l.ref,
           -- The PMC's name and the day sit in the property column of the first two lines.
           l.place,
           l.billed,
           l.due,
           l.late,
           CASE
               WHEN l.late IS NULL THEN NULL
               WHEN l.late = 0 THEN 'current'
               WHEN l.late <= 30 THEN '1-30'
               WHEN l.late <= 60 THEN '31-60'
               WHEN l.late <= 90 THEN '61-90'
               ELSE 'over 90'
           END,
           round(l.owed, 2),
           round(l.held, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_reverse_payment(uuid, uuid, text, text, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_reverse_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_reversal_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_matched numeric;
    v_payment pgledger_transfers;
    v_reversal pgledger_transfers;
    v_existing numeric;
    v_used numeric;
BEGIN
    -- The sums below must count every reversal that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: reverse a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a reversal undoes a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Waits for matches and reversals in progress on the charge (trust_apply_payment takes the
    -- same lock), then for those on the reversing transfer.
    PERFORM c.id FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_matched
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: transfer % pays nothing of charge %', p_transfer_id, p_charge_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.* INTO v_payment FROM pgledger_transfers AS t WHERE t.id = p_transfer_id;

    SELECT t.* INTO v_reversal
    FROM pgledger_transfers AS t
    WHERE t.id = p_reversal_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_reversal_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A draw also moves money from the owner's account to cash, but it paid the owner.
    IF EXISTS (
        SELECT d.transfer_id FROM trust_owner_draws AS d WHERE d.transfer_id = p_reversal_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % paid the owner (an owner draw); it reverses no payment',
            p_reversal_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_reversal.from_account_id <> v_payment.to_account_id
        OR v_reversal.to_account_id <> v_payment.from_account_id THEN
        RAISE EXCEPTION 'trust: transfer % does not move money back the way transfer % came',
            p_reversal_id, p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_reversal.event_at < v_payment.event_at THEN
        RAISE EXCEPTION 'trust: transfer % is dated before the payment it reverses', p_reversal_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT r.amount INTO v_existing
    FROM trust_payment_reversals AS r
    WHERE r.charge_id = p_charge_id AND r.transfer_id = p_transfer_id
      AND r.reversal_id = p_reversal_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already reverses % of that payment, not %',
                p_reversal_id, v_existing, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original reversal
    END IF;

    SELECT coalesce(sum(r.amount), 0) INTO v_used
    FROM trust_payment_reversals AS r
    WHERE r.charge_id = p_charge_id AND r.transfer_id = p_transfer_id;
    IF v_used + p_amount > v_matched THEN
        RAISE EXCEPTION 'trust: transfer % pays % of charge % and % of it is reversed already; '
            '% more is too much', p_transfer_id, v_matched, p_charge_id, v_used, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT coalesce(sum(r.amount), 0) INTO v_used
    FROM trust_payment_reversals AS r WHERE r.reversal_id = p_reversal_id;
    IF v_used + p_amount > v_reversal.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already reverses %; % more is too much',
            p_reversal_id, v_reversal.amount, v_used, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_payment_reversals (pmc_id, charge_id, transfer_id, reversal_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_reversal_id, p_amount);
    RETURN p_amount;
END;
$$;


--
-- Name: trust_set_aside_bill(uuid, uuid, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_set_aside_bill(p_pmc_id uuid, p_bill_id uuid, p_event_at timestamp with time zone) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(p_pmc_id, p_bill_id);
    v_bank uuid;
    v_limit numeric;
    v_payable text;
    v_transfer_id text;
BEGIN
    SELECT p.transfer_id INTO v_transfer_id
    FROM trust_bill_payments AS p
    WHERE p.bill_id = p_bill_id AND p.step = 'set_aside';
    IF FOUND THEN
        RETURN v_transfer_id;  -- a retry: the bill is set aside already
    END IF;

    v_limit := coalesce((
        trust_agreement_on(v_bill.ledger_account_id, v_bill.bill_date)
    ).approval_limit, 0);
    IF v_bill.amount > v_limit AND NOT EXISTS (
        SELECT a.bill_id FROM trust_bill_approvals AS a WHERE a.bill_id = p_bill_id
    ) THEN
        RAISE EXCEPTION 'trust: bill % is %, over the owner''s approval limit of %; record the '
            'owner''s approval first', v_bill.reference, v_bill.amount, v_limit
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT t.bank_account_id INTO v_bank
    FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
    SELECT t.ledger_account_id INTO v_payable
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_bank AND t.kind = 'vendor_payable'
      AND t.vendor_id = v_bill.vendor_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the vendor''s account (vendor_payable) in trust bank '
            'account % before setting its bill aside', v_bank
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_bill.ledger_account_id, v_payable, v_bill.amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', format('Bill %s set aside', v_bill.reference))
    ) AS t;
    INSERT INTO trust_bill_payments (bill_id, step, pmc_id, transfer_id)
    VALUES (p_bill_id, 'set_aside', p_pmc_id, v_transfer_id);
    RETURN v_transfer_id;
END;
$$;


--
-- Name: trust_unpaid_bills(text, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_unpaid_bills(p_account text, p_day date) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce(sum(b.amount), 0)
    FROM trust_bills AS b
    WHERE b.ledger_account_id = p_account AND b.bill_date <= p_day
      AND NOT EXISTS (
          SELECT p.bill_id
          FROM trust_bill_payments AS p
          JOIN pgledger_transfers AS t ON t.id = p.transfer_id
          WHERE p.bill_id = b.id AND p.step = 'set_aside'
            AND t.event_at < (p_day + 1)::timestamp AT TIME ZONE 'UTC'
      )
$$;


--
-- Name: ulid_to_uuid(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ulid_to_uuid(ulid text) RETURNS uuid
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
  RETURN encode(parse_ulid(ulid), 'hex')::uuid;
END
$$;


--
-- Name: uuid_to_ulid(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.uuid_to_ulid(id uuid) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    RETURN format_ulid(uuid_send(id));
END
$$;


--
-- Name: pgledger_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pgledger_entries (
    id text DEFAULT public.pgledger_generate_id('pgle'::text) NOT NULL,
    account_id text NOT NULL,
    transfer_id text NOT NULL,
    amount numeric NOT NULL,
    account_previous_balance numeric NOT NULL,
    account_current_balance numeric NOT NULL,
    account_version bigint NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: pgledger_entries_view; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.pgledger_entries_view AS
 SELECT e.id,
    e.account_id,
    e.transfer_id,
    e.amount,
    e.account_previous_balance,
    e.account_current_balance,
    e.account_version,
    e.created_at,
    t.event_at,
    t.metadata
   FROM (public.pgledger_entries e
     JOIN public.pgledger_transfers t ON ((e.transfer_id = t.id)));


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: trust_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_audit_log (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    staff_id uuid,
    db_role text NOT NULL,
    action text NOT NULL,
    table_name text NOT NULL,
    row_data jsonb NOT NULL,
    before jsonb,
    CONSTRAINT trust_audit_log_action_check CHECK ((action = ANY (ARRAY['insert'::text, 'update'::text, 'delete'::text]))),
    CONSTRAINT trust_audit_log_check CHECK (((action = 'update'::text) = (before IS NOT NULL)))
);


--
-- Name: trust_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_audit_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.trust_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_bank_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    kind text NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_through timestamp with time zone,
    opened_at timestamp with time zone,
    CONSTRAINT trust_bank_accounts_display_name_check CHECK ((display_name <> ''::text)),
    CONSTRAINT trust_bank_accounts_kind_check CHECK ((kind = ANY (ARRAY['operating'::text, 'security_deposit'::text])))
);


--
-- Name: trust_bill_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_approvals (
    bill_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    approved_on date NOT NULL,
    approved_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_bill_approvals_approved_by_check CHECK ((approved_by <> ''::text))
);


--
-- Name: trust_bill_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_payments (
    bill_id uuid NOT NULL,
    step text NOT NULL,
    pmc_id uuid NOT NULL,
    transfer_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_bill_payments_step_check CHECK ((step = ANY (ARRAY['set_aside'::text, 'paid'::text])))
);


--
-- Name: trust_charge_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_charge_payments (
    pmc_id uuid NOT NULL,
    charge_id uuid NOT NULL,
    transfer_id text NOT NULL,
    amount numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_charge_payments_amount_check CHECK ((amount > (0)::numeric))
);


--
-- Name: trust_charges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_charges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    due_on date NOT NULL,
    kind text NOT NULL,
    amount numeric NOT NULL,
    memo text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_charges_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_charges_kind_check CHECK ((kind = ANY (ARRAY['rent'::text, 'fee'::text, 'credit'::text])))
);


--
-- Name: trust_idempotency_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_idempotency_keys (
    pmc_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    request jsonb NOT NULL,
    transfer_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_idempotency_keys_idempotency_key_check CHECK (((idempotency_key <> ''::text) AND (length(idempotency_key) <= 200))),
    CONSTRAINT trust_idempotency_keys_transfer_ids_check CHECK ((cardinality(transfer_ids) > 0))
);


--
-- Name: trust_late_fee_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_late_fee_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    grace_days integer NOT NULL,
    flat_fee numeric DEFAULT 0 NOT NULL,
    percent numeric DEFAULT 0 NOT NULL,
    maximum numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_late_fee_policies_flat_fee_check CHECK ((flat_fee >= (0)::numeric)),
    CONSTRAINT trust_late_fee_policies_grace_days_check CHECK (((grace_days >= 0) AND (grace_days <= 365))),
    CONSTRAINT trust_late_fee_policies_maximum_check CHECK ((maximum >= (0)::numeric)),
    CONSTRAINT trust_late_fee_policies_percent_check CHECK (((percent >= (0)::numeric) AND (percent <= (100)::numeric)))
);


--
-- Name: trust_late_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_late_fees (
    rent_charge_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    policy_id uuid NOT NULL,
    unpaid numeric NOT NULL,
    fee numeric NOT NULL,
    fee_charge_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_late_fees_fee_check CHECK ((fee > (0)::numeric)),
    CONSTRAINT trust_late_fees_unpaid_check CHECK ((unpaid > (0)::numeric))
);


--
-- Name: trust_lease_tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_tenants (
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL
);


--
-- Name: trust_leases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_leases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    starts_on date NOT NULL,
    ends_on date,
    monthly_rent numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_leases_check CHECK ((ends_on >= starts_on)),
    CONSTRAINT trust_leases_monthly_rent_check CHECK ((monthly_rent > (0)::numeric))
);


--
-- Name: trust_leasing_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_leasing_fees (
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    agreement_id uuid NOT NULL,
    fee numeric NOT NULL,
    transfer_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_leasing_fees_check CHECK (((fee = (0)::numeric) = (transfer_id IS NULL))),
    CONSTRAINT trust_leasing_fees_fee_check CHECK ((fee >= (0)::numeric))
);


--
-- Name: trust_management_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_management_fees (
    pmc_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    agreement_id uuid NOT NULL,
    collected numeric NOT NULL,
    fee numeric NOT NULL,
    transfer_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_management_fees_check CHECK ((period_end > period_start)),
    CONSTRAINT trust_management_fees_check1 CHECK (((fee = (0)::numeric) = (transfer_id IS NULL))),
    CONSTRAINT trust_management_fees_fee_check CHECK ((fee >= (0)::numeric))
);


--
-- Name: trust_opening_balances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_opening_balances (
    bank_account_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    opened_on date NOT NULL,
    balances jsonb NOT NULL,
    book_cash numeric NOT NULL,
    entered_by text NOT NULL,
    transfer_ids text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_opening_balances_book_cash_check CHECK ((book_cash > (0)::numeric)),
    CONSTRAINT trust_opening_balances_entered_by_check CHECK ((entered_by <> ''::text)),
    CONSTRAINT trust_opening_balances_transfer_ids_check CHECK ((cardinality(transfer_ids) > 0))
);


--
-- Name: trust_owner_draws; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_owner_draws (
    pmc_id uuid NOT NULL,
    request_key text NOT NULL,
    ledger_account_id text NOT NULL,
    amount numeric NOT NULL,
    transfer_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_owner_draws_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_owner_draws_request_key_check CHECK ((request_key <> ''::text))
);


--
-- Name: trust_owners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_owners (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_owners_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_payment_reversals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_payment_reversals (
    pmc_id uuid NOT NULL,
    charge_id uuid NOT NULL,
    transfer_id text NOT NULL,
    reversal_id text NOT NULL,
    amount numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_payment_reversals_amount_check CHECK ((amount > (0)::numeric))
);


--
-- Name: trust_pmcs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_pmcs (
    pmc_id uuid DEFAULT gen_random_uuid() NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_pmcs_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_properties; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_properties (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_properties_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_reconciliations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_reconciliations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    period_start timestamp with time zone NOT NULL,
    period_end timestamp with time zone NOT NULL,
    statement_balance numeric NOT NULL,
    book_balance numeric NOT NULL,
    prepared_by text NOT NULL,
    approved_by text NOT NULL,
    approved_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_reconciliations_approved_by_check CHECK ((approved_by <> ''::text)),
    CONSTRAINT trust_reconciliations_check CHECK ((period_end > period_start)),
    CONSTRAINT trust_reconciliations_prepared_by_check CHECK ((prepared_by <> ''::text))
);


--
-- Name: trust_staff; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_staff (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_staff_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_tenants_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_units (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_units_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_vendors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_vendors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_vendors_display_name_check CHECK ((display_name <> ''::text))
);


--
-- Name: trust_work_order_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_work_order_steps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    step text NOT NULL,
    vendor_id uuid,
    taken_at timestamp with time zone NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_work_order_steps_check CHECK (((step = 'assigned'::text) = (vendor_id IS NOT NULL))),
    CONSTRAINT trust_work_order_steps_step_check CHECK ((step = ANY (ARRAY['assigned'::text, 'completed'::text, 'cancelled'::text])))
);


--
-- Name: trust_work_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_work_orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    unit_id uuid,
    summary text NOT NULL,
    opened_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_work_orders_summary_check CHECK ((summary <> ''::text))
);


--
-- Name: pgledger_accounts pgledger_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_accounts
    ADD CONSTRAINT pgledger_accounts_pkey PRIMARY KEY (id);


--
-- Name: pgledger_entries pgledger_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_entries
    ADD CONSTRAINT pgledger_entries_pkey PRIMARY KEY (id);


--
-- Name: pgledger_transfers pgledger_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_transfers
    ADD CONSTRAINT pgledger_transfers_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: trust_audit_log trust_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_audit_log
    ADD CONSTRAINT trust_audit_log_pkey PRIMARY KEY (id);


--
-- Name: trust_bank_accounts trust_bank_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_accounts
    ADD CONSTRAINT trust_bank_accounts_pkey PRIMARY KEY (id);


--
-- Name: trust_bank_accounts trust_bank_accounts_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_accounts
    ADD CONSTRAINT trust_bank_accounts_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_bill_approvals trust_bill_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_approvals
    ADD CONSTRAINT trust_bill_approvals_pkey PRIMARY KEY (bill_id);


--
-- Name: trust_bill_payments trust_bill_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_payments
    ADD CONSTRAINT trust_bill_payments_pkey PRIMARY KEY (bill_id, step);


--
-- Name: trust_bills trust_bills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_pkey PRIMARY KEY (id);


--
-- Name: trust_bills trust_bills_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_bills trust_bills_vendor_id_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_vendor_id_reference_key UNIQUE (vendor_id, reference);


--
-- Name: trust_charge_payments trust_charge_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_payments
    ADD CONSTRAINT trust_charge_payments_pkey PRIMARY KEY (charge_id, transfer_id);


--
-- Name: trust_charges trust_charges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charges
    ADD CONSTRAINT trust_charges_pkey PRIMARY KEY (id);


--
-- Name: trust_charges trust_charges_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charges
    ADD CONSTRAINT trust_charges_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_idempotency_keys trust_idempotency_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_idempotency_keys
    ADD CONSTRAINT trust_idempotency_keys_pkey PRIMARY KEY (pmc_id, idempotency_key);


--
-- Name: trust_late_fee_policies trust_late_fee_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fee_policies
    ADD CONSTRAINT trust_late_fee_policies_pkey PRIMARY KEY (id);


--
-- Name: trust_late_fee_policies trust_late_fee_policies_property_id_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fee_policies
    ADD CONSTRAINT trust_late_fee_policies_property_id_starts_on_key UNIQUE (property_id, starts_on);


--
-- Name: trust_late_fees trust_late_fees_fee_charge_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fees
    ADD CONSTRAINT trust_late_fees_fee_charge_id_key UNIQUE (fee_charge_id);


--
-- Name: trust_late_fees trust_late_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fees
    ADD CONSTRAINT trust_late_fees_pkey PRIMARY KEY (rent_charge_id);


--
-- Name: trust_lease_tenants trust_lease_tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_tenants
    ADD CONSTRAINT trust_lease_tenants_pkey PRIMARY KEY (lease_id, tenant_id);


--
-- Name: trust_leases trust_leases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leases
    ADD CONSTRAINT trust_leases_pkey PRIMARY KEY (id);


--
-- Name: trust_leases trust_leases_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leases
    ADD CONSTRAINT trust_leases_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_leasing_fees trust_leasing_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leasing_fees
    ADD CONSTRAINT trust_leasing_fees_pkey PRIMARY KEY (lease_id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pkey PRIMARY KEY (ledger_account_id);


--
-- Name: trust_management_agreements trust_management_agreements_ledger_account_id_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_agreements
    ADD CONSTRAINT trust_management_agreements_ledger_account_id_starts_on_key UNIQUE (ledger_account_id, starts_on);


--
-- Name: trust_management_agreements trust_management_agreements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_agreements
    ADD CONSTRAINT trust_management_agreements_pkey PRIMARY KEY (id);


--
-- Name: trust_management_fees trust_management_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fees
    ADD CONSTRAINT trust_management_fees_pkey PRIMARY KEY (ledger_account_id, period_start);


--
-- Name: trust_opening_balances trust_opening_balances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_opening_balances
    ADD CONSTRAINT trust_opening_balances_pkey PRIMARY KEY (bank_account_id);


--
-- Name: trust_owner_draws trust_owner_draws_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_draws
    ADD CONSTRAINT trust_owner_draws_pkey PRIMARY KEY (pmc_id, request_key);


--
-- Name: trust_owners trust_owners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owners
    ADD CONSTRAINT trust_owners_pkey PRIMARY KEY (id);


--
-- Name: trust_owners trust_owners_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owners
    ADD CONSTRAINT trust_owners_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_payment_reversals trust_payment_reversals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_reversals
    ADD CONSTRAINT trust_payment_reversals_pkey PRIMARY KEY (charge_id, transfer_id, reversal_id);


--
-- Name: trust_pmcs trust_pmcs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_pmcs
    ADD CONSTRAINT trust_pmcs_pkey PRIMARY KEY (pmc_id);


--
-- Name: trust_properties trust_properties_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_properties
    ADD CONSTRAINT trust_properties_pkey PRIMARY KEY (id);


--
-- Name: trust_properties trust_properties_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_properties
    ADD CONSTRAINT trust_properties_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_reconciliations trust_reconciliations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliations
    ADD CONSTRAINT trust_reconciliations_pkey PRIMARY KEY (id);


--
-- Name: trust_staff trust_staff_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff
    ADD CONSTRAINT trust_staff_pkey PRIMARY KEY (id);


--
-- Name: trust_staff trust_staff_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff
    ADD CONSTRAINT trust_staff_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_tenants trust_tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_tenants
    ADD CONSTRAINT trust_tenants_pkey PRIMARY KEY (id);


--
-- Name: trust_tenants trust_tenants_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_tenants
    ADD CONSTRAINT trust_tenants_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_units trust_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_units
    ADD CONSTRAINT trust_units_pkey PRIMARY KEY (id);


--
-- Name: trust_units trust_units_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_units
    ADD CONSTRAINT trust_units_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_vendors trust_vendors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendors
    ADD CONSTRAINT trust_vendors_pkey PRIMARY KEY (id);


--
-- Name: trust_vendors trust_vendors_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendors
    ADD CONSTRAINT trust_vendors_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_work_order_steps trust_work_order_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_pkey PRIMARY KEY (id);


--
-- Name: trust_work_orders trust_work_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pkey PRIMARY KEY (id);


--
-- Name: trust_work_orders trust_work_orders_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: pgledger_entries_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pgledger_entries_account_id_idx ON public.pgledger_entries USING btree (account_id);


--
-- Name: pgledger_entries_transfer_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pgledger_entries_transfer_id_idx ON public.pgledger_entries USING btree (transfer_id);


--
-- Name: pgledger_transfers_event_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pgledger_transfers_event_at_idx ON public.pgledger_transfers USING btree (event_at);


--
-- Name: pgledger_transfers_from_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pgledger_transfers_from_account_id_idx ON public.pgledger_transfers USING btree (from_account_id);


--
-- Name: pgledger_transfers_to_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX pgledger_transfers_to_account_id_idx ON public.pgledger_transfers USING btree (to_account_id);


--
-- Name: trust_audit_log_pmc_id_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_audit_log_pmc_id_at ON public.trust_audit_log USING btree (pmc_id, at);


--
-- Name: trust_bill_payments_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_payments_transfer_id ON public.trust_bill_payments USING btree (transfer_id);


--
-- Name: trust_bills_ledger_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bills_ledger_account_id ON public.trust_bills USING btree (ledger_account_id);


--
-- Name: trust_bills_work_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bills_work_order_id ON public.trust_bills USING btree (work_order_id);


--
-- Name: trust_charge_payments_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charge_payments_transfer_id ON public.trust_charge_payments USING btree (transfer_id);


--
-- Name: trust_charges_lease_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charges_lease_id ON public.trust_charges USING btree (lease_id, due_on);


--
-- Name: trust_charges_one_rent_per_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_charges_one_rent_per_due_date ON public.trust_charges USING btree (lease_id, due_on) WHERE (kind = 'rent'::text);


--
-- Name: trust_late_fees_policy_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_late_fees_policy_id ON public.trust_late_fees USING btree (policy_id);


--
-- Name: trust_lease_tenants_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_tenants_tenant_id ON public.trust_lease_tenants USING btree (tenant_id);


--
-- Name: trust_leases_unit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leases_unit_id ON public.trust_leases USING btree (unit_id, starts_on);


--
-- Name: trust_leasing_fees_agreement_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leasing_fees_agreement_id ON public.trust_leasing_fees USING btree (agreement_id);


--
-- Name: trust_leasing_fees_ledger_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leasing_fees_ledger_account_id ON public.trust_leasing_fees USING btree (ledger_account_id);


--
-- Name: trust_leasing_fees_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leasing_fees_transfer_id ON public.trust_leasing_fees USING btree (transfer_id);


--
-- Name: trust_ledger_accounts_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ledger_accounts_bank_account_id ON public.trust_ledger_accounts USING btree (bank_account_id);


--
-- Name: trust_ledger_accounts_one_bank_cash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_bank_cash ON public.trust_ledger_accounts USING btree (bank_account_id) WHERE (kind = 'bank_cash'::text);


--
-- Name: trust_ledger_accounts_one_owner_property; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_owner_property ON public.trust_ledger_accounts USING btree (bank_account_id, owner_id, property_id) WHERE (kind = 'owner_property'::text);


--
-- Name: trust_ledger_accounts_one_pmc_income; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_pmc_income ON public.trust_ledger_accounts USING btree (bank_account_id) WHERE (kind = 'pmc_income'::text);


--
-- Name: trust_ledger_accounts_one_prepaid_rent; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_prepaid_rent ON public.trust_ledger_accounts USING btree (bank_account_id, tenant_id) WHERE (kind = 'prepaid_rent'::text);


--
-- Name: trust_ledger_accounts_one_tenant_deposit; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_tenant_deposit ON public.trust_ledger_accounts USING btree (bank_account_id, tenant_id) WHERE (kind = 'tenant_deposit'::text);


--
-- Name: trust_ledger_accounts_one_vendor_payable; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_vendor_payable ON public.trust_ledger_accounts USING btree (bank_account_id, vendor_id) WHERE (kind = 'vendor_payable'::text);


--
-- Name: trust_ledger_accounts_pmc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ledger_accounts_pmc_id ON public.trust_ledger_accounts USING btree (pmc_id);


--
-- Name: trust_management_fees_agreement_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_management_fees_agreement_id ON public.trust_management_fees USING btree (agreement_id);


--
-- Name: trust_management_fees_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_management_fees_transfer_id ON public.trust_management_fees USING btree (transfer_id);


--
-- Name: trust_owner_draws_ledger_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_owner_draws_ledger_account_id ON public.trust_owner_draws USING btree (ledger_account_id);


--
-- Name: trust_owner_draws_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_owner_draws_transfer_id ON public.trust_owner_draws USING btree (transfer_id);


--
-- Name: trust_payment_reversals_reversal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_payment_reversals_reversal_id ON public.trust_payment_reversals USING btree (reversal_id);


--
-- Name: trust_reconciliations_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_reconciliations_bank_account_id ON public.trust_reconciliations USING btree (bank_account_id, period_end);


--
-- Name: trust_units_property_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_units_property_id ON public.trust_units USING btree (property_id);


--
-- Name: trust_work_order_steps_one_close; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_work_order_steps_one_close ON public.trust_work_order_steps USING btree (work_order_id) WHERE (step = ANY (ARRAY['completed'::text, 'cancelled'::text]));


--
-- Name: trust_work_orders_property_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_work_orders_property_id ON public.trust_work_orders USING btree (property_id);


--
-- Name: trust_ledger_accounts trust_account_in_its_bank; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_account_in_its_bank BEFORE INSERT ON public.trust_ledger_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_check_account_bank_kind();


--
-- Name: trust_management_agreements trust_agreement_account; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_agreement_account BEFORE INSERT ON public.trust_management_agreements FOR EACH ROW EXECUTE FUNCTION public.trust_check_agreement_account();


--
-- Name: pgledger_entries trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.pgledger_entries FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: pgledger_transfers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.pgledger_transfers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_audit_log trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_approvals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_approvals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_payments trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_payments FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bills trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bills FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_charge_payments trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_charge_payments FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_charges trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_charges FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_idempotency_keys trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_idempotency_keys FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_late_fee_policies trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_late_fee_policies FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_late_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_late_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_tenants trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_tenants FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_leasing_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_leasing_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_ledger_accounts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_ledger_accounts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_management_agreements trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_management_agreements FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_management_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_management_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_opening_balances trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_opening_balances FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_owner_draws trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_owner_draws FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_payment_reversals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_payment_reversals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_reconciliations trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_reconciliations FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_work_order_steps trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_work_order_steps FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_work_orders trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_work_orders FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: pgledger_transfers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_accounts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_approvals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_approvals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_payments trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_payments FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bills trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bills FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_charge_payments trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_charge_payments FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_charges trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_charges FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_late_fee_policies trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_late_fee_policies FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_late_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_late_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_tenants trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_tenants FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_leases trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_leases FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_leasing_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_leasing_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_ledger_accounts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_ledger_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_management_agreements trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_management_agreements FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_management_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_management_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_opening_balances trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_opening_balances FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_owner_draws trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_owner_draws FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_owners trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_owners FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_payment_reversals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_payment_reversals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_pmcs trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_pmcs FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_properties trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_properties FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_reconciliations trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_reconciliations FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_staff trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_staff FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_tenants trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_tenants FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_units trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_units FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendors trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendors FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_work_order_steps trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_work_order_steps FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_work_orders trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_accounts trust_bank_kind_fixed; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bank_kind_fixed BEFORE UPDATE OF kind ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_changing_bank_kind();


--
-- Name: pgledger_transfers trust_bank_tie_out; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_bank_tie_out AFTER INSERT ON public.pgledger_transfers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_tie_out();


--
-- Name: trust_bills trust_bill_account; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_account BEFORE INSERT ON public.trust_bills FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_account();


--
-- Name: pgledger_transfers trust_closed_period; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_closed_period BEFORE INSERT ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_posting_into_closed_period();


--
-- Name: trust_bank_accounts trust_closed_through; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_closed_through BEFORE INSERT OR UPDATE OF closed_through ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_moving_closed_through();


--
-- Name: pgledger_accounts trust_no_negative_balance; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_no_negative_balance BEFORE INSERT OR UPDATE OF balance ON public.pgledger_accounts FOR EACH ROW WHEN ((new.balance < (0)::numeric)) EXECUTE FUNCTION public.trust_refuse_negative_balance();


--
-- Name: trust_leases trust_one_lease_at_a_time; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_one_lease_at_a_time BEFORE INSERT OR UPDATE ON public.trust_leases FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_overlapping_leases();


--
-- Name: trust_bank_accounts trust_opened_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_opened_at BEFORE INSERT OR UPDATE OF opened_at ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_moving_opened_at();


--
-- Name: pgledger_transfers trust_transfer_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_transfer_scope BEFORE INSERT ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_check_transfer_scope();


--
-- Name: trust_work_order_steps trust_work_order_step_order; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_work_order_step_order BEFORE INSERT ON public.trust_work_order_steps FOR EACH ROW EXECUTE FUNCTION public.trust_check_work_order_step();


--
-- Name: trust_work_orders trust_work_order_unit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_work_order_unit BEFORE INSERT ON public.trust_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_check_work_order();


--
-- Name: pgledger_entries pgledger_entries_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_entries
    ADD CONSTRAINT pgledger_entries_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.pgledger_accounts(id);


--
-- Name: pgledger_entries pgledger_entries_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_entries
    ADD CONSTRAINT pgledger_entries_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: pgledger_transfers pgledger_transfers_from_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_transfers
    ADD CONSTRAINT pgledger_transfers_from_account_id_fkey FOREIGN KEY (from_account_id) REFERENCES public.pgledger_accounts(id);


--
-- Name: pgledger_transfers pgledger_transfers_to_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pgledger_transfers
    ADD CONSTRAINT pgledger_transfers_to_account_id_fkey FOREIGN KEY (to_account_id) REFERENCES public.pgledger_accounts(id);


--
-- Name: trust_audit_log trust_audit_log_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_audit_log
    ADD CONSTRAINT trust_audit_log_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_audit_log trust_audit_log_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_audit_log
    ADD CONSTRAINT trust_audit_log_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_bank_accounts trust_bank_accounts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_accounts
    ADD CONSTRAINT trust_bank_accounts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_approvals trust_bill_approvals_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_approvals
    ADD CONSTRAINT trust_bill_approvals_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_bill_approvals trust_bill_approvals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_approvals
    ADD CONSTRAINT trust_bill_approvals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_payments trust_bill_payments_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_payments
    ADD CONSTRAINT trust_bill_payments_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_bill_payments trust_bill_payments_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_payments
    ADD CONSTRAINT trust_bill_payments_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_payments trust_bill_payments_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_payments
    ADD CONSTRAINT trust_bill_payments_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_bills trust_bills_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_bills trust_bills_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bills trust_bills_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_bills trust_bills_pmc_id_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bills
    ADD CONSTRAINT trust_bills_pmc_id_work_order_id_fkey FOREIGN KEY (pmc_id, work_order_id) REFERENCES public.trust_work_orders(pmc_id, id);


--
-- Name: trust_charge_payments trust_charge_payments_pmc_id_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_payments
    ADD CONSTRAINT trust_charge_payments_pmc_id_charge_id_fkey FOREIGN KEY (pmc_id, charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_charge_payments trust_charge_payments_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_payments
    ADD CONSTRAINT trust_charge_payments_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_charge_payments trust_charge_payments_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_payments
    ADD CONSTRAINT trust_charge_payments_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_charges trust_charges_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charges
    ADD CONSTRAINT trust_charges_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_charges trust_charges_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charges
    ADD CONSTRAINT trust_charges_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_idempotency_keys trust_idempotency_keys_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_idempotency_keys
    ADD CONSTRAINT trust_idempotency_keys_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_late_fee_policies trust_late_fee_policies_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fee_policies
    ADD CONSTRAINT trust_late_fee_policies_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_late_fee_policies trust_late_fee_policies_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fee_policies
    ADD CONSTRAINT trust_late_fee_policies_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_late_fees trust_late_fees_pmc_id_fee_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fees
    ADD CONSTRAINT trust_late_fees_pmc_id_fee_charge_id_fkey FOREIGN KEY (pmc_id, fee_charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_late_fees trust_late_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fees
    ADD CONSTRAINT trust_late_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_late_fees trust_late_fees_pmc_id_rent_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fees
    ADD CONSTRAINT trust_late_fees_pmc_id_rent_charge_id_fkey FOREIGN KEY (pmc_id, rent_charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_late_fees trust_late_fees_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_late_fees
    ADD CONSTRAINT trust_late_fees_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.trust_late_fee_policies(id);


--
-- Name: trust_lease_tenants trust_lease_tenants_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_tenants
    ADD CONSTRAINT trust_lease_tenants_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_tenants trust_lease_tenants_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_tenants
    ADD CONSTRAINT trust_lease_tenants_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_tenants trust_lease_tenants_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_tenants
    ADD CONSTRAINT trust_lease_tenants_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_leases trust_leases_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leases
    ADD CONSTRAINT trust_leases_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_leases trust_leases_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leases
    ADD CONSTRAINT trust_leases_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_leasing_fees trust_leasing_fees_agreement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leasing_fees
    ADD CONSTRAINT trust_leasing_fees_agreement_id_fkey FOREIGN KEY (agreement_id) REFERENCES public.trust_management_agreements(id);


--
-- Name: trust_leasing_fees trust_leasing_fees_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leasing_fees
    ADD CONSTRAINT trust_leasing_fees_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_leasing_fees trust_leasing_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leasing_fees
    ADD CONSTRAINT trust_leasing_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_leasing_fees trust_leasing_fees_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leasing_fees
    ADD CONSTRAINT trust_leasing_fees_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_leasing_fees trust_leasing_fees_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leasing_fees
    ADD CONSTRAINT trust_leasing_fees_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.pgledger_accounts(id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_management_agreements trust_management_agreements_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_agreements
    ADD CONSTRAINT trust_management_agreements_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_management_agreements trust_management_agreements_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_agreements
    ADD CONSTRAINT trust_management_agreements_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_management_fees trust_management_fees_agreement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fees
    ADD CONSTRAINT trust_management_fees_agreement_id_fkey FOREIGN KEY (agreement_id) REFERENCES public.trust_management_agreements(id);


--
-- Name: trust_management_fees trust_management_fees_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fees
    ADD CONSTRAINT trust_management_fees_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_management_fees trust_management_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fees
    ADD CONSTRAINT trust_management_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_management_fees trust_management_fees_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fees
    ADD CONSTRAINT trust_management_fees_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_opening_balances trust_opening_balances_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_opening_balances
    ADD CONSTRAINT trust_opening_balances_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_opening_balances trust_opening_balances_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_opening_balances
    ADD CONSTRAINT trust_opening_balances_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_owner_draws trust_owner_draws_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_draws
    ADD CONSTRAINT trust_owner_draws_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_owner_draws trust_owner_draws_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_draws
    ADD CONSTRAINT trust_owner_draws_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_owner_draws trust_owner_draws_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_draws
    ADD CONSTRAINT trust_owner_draws_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_owners trust_owners_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owners
    ADD CONSTRAINT trust_owners_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_payment_reversals trust_payment_reversals_charge_id_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_reversals
    ADD CONSTRAINT trust_payment_reversals_charge_id_transfer_id_fkey FOREIGN KEY (charge_id, transfer_id) REFERENCES public.trust_charge_payments(charge_id, transfer_id);


--
-- Name: trust_payment_reversals trust_payment_reversals_pmc_id_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_reversals
    ADD CONSTRAINT trust_payment_reversals_pmc_id_charge_id_fkey FOREIGN KEY (pmc_id, charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_payment_reversals trust_payment_reversals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_reversals
    ADD CONSTRAINT trust_payment_reversals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_payment_reversals trust_payment_reversals_reversal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_reversals
    ADD CONSTRAINT trust_payment_reversals_reversal_id_fkey FOREIGN KEY (reversal_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_properties trust_properties_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_properties
    ADD CONSTRAINT trust_properties_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_reconciliations trust_reconciliations_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliations
    ADD CONSTRAINT trust_reconciliations_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_reconciliations trust_reconciliations_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliations
    ADD CONSTRAINT trust_reconciliations_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_staff trust_staff_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff
    ADD CONSTRAINT trust_staff_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_tenants trust_tenants_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_tenants
    ADD CONSTRAINT trust_tenants_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_tenants trust_tenants_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_tenants
    ADD CONSTRAINT trust_tenants_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_units trust_units_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_units
    ADD CONSTRAINT trust_units_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_units trust_units_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_units
    ADD CONSTRAINT trust_units_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_vendors trust_vendors_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendors
    ADD CONSTRAINT trust_vendors_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_work_order_steps trust_work_order_steps_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_work_order_steps trust_work_order_steps_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_work_order_steps trust_work_order_steps_pmc_id_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_pmc_id_work_order_id_fkey FOREIGN KEY (pmc_id, work_order_id) REFERENCES public.trust_work_orders(pmc_id, id);


--
-- Name: trust_work_orders trust_work_orders_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_work_orders trust_work_orders_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_work_orders trust_work_orders_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('20261009000001'),
    ('20261009000002'),
    ('20261009000003'),
    ('20261009000004'),
    ('20261009000005'),
    ('20261009000006'),
    ('20261009000007'),
    ('20261009000008'),
    ('20261009000009'),
    ('20261009000010'),
    ('20261009000011'),
    ('20261010000012'),
    ('20261010000013'),
    ('20261010000014'),
    ('20261010000015'),
    ('20261010000016'),
    ('20261010000017'),
    ('20261010000018'),
    ('20261010000019'),
    ('20261010000020'),
    ('20261010000021'),
    ('20261010000022'),
    ('20261010000023'),
    ('20261010000024');
