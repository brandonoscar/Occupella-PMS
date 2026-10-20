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
    bill_markup_percent numeric DEFAULT 0 NOT NULL,
    CONSTRAINT trust_management_agreements_approval_limit_check CHECK ((approval_limit >= (0)::numeric)),
    CONSTRAINT trust_management_agreements_bill_markup_percent_check CHECK (((bill_markup_percent >= (0)::numeric) AND (bill_markup_percent <= (100)::numeric))),
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
-- Name: trust_apply_assistance_payment(uuid, uuid, text, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_apply_assistance_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_assistance_charges;
    v_transfer pgledger_transfers;
    v_property_id uuid;
    v_existing numeric;
    v_open numeric;
    v_used numeric;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT c.* INTO v_charge FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no housing authority charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.adjusts_id IS NOT NULL THEN
        RAISE EXCEPTION 'trust: an adjustment is not paid; pay the charge it adjusts'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer FROM pgledger_transfers AS t
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
          AND f.kind = 'bank_cash'
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into an owner''s account of this charge''s '
            'property from cash', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id)
       OR EXISTS (SELECT 1 FROM trust_owner_contributions AS c WHERE c.transfer_id = p_transfer_id)
    THEN
        RAISE EXCEPTION 'trust: transfer % is a tenant''s payment or an owner''s contribution, '
            'not a housing authority''s', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing FROM trust_assistance_payments AS p
    WHERE p.assistance_charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    v_open := trust_assistance_open(p_charge_id);
    IF p_amount > v_open THEN
        RAISE EXCEPTION 'trust: % is more than is open of the authority''s charge, %',
            p_amount, v_open
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(sum(p.amount), 0) INTO v_used FROM trust_assistance_payments AS p
    WHERE p.transfer_id = p_transfer_id;
    IF v_used + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved %, and % of it pays charges already',
            p_transfer_id, v_transfer.amount, v_used
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_assistance_payments (transfer_id, assistance_charge_id, pmc_id, amount)
    VALUES (p_transfer_id, p_charge_id, p_pmc_id, p_amount);
    RETURN p_amount;
END;
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
    v_hold trust_payment_holds;
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
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
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

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    -- What waivers took off it (credits linked to it, migration 0079) is settled too.
    SELECT v_paid + coalesce(sum(k.amount), 0) INTO v_paid
    FROM trust_charge_waivers AS w
    JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid or waived %; % more is too much',
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
-- Name: trust_approve_bank_reconciliation(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_approve_bank_reconciliation(p_pmc_id uuid, p_preparation_id uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_prepared trust_reconciliation_preparations;
    v_bank trust_bank_accounts;
    v_start timestamptz;
    v_figures numeric[];
BEGIN
    SELECT p.* INTO v_prepared FROM trust_reconciliation_preparations AS p
    WHERE p.id = p_preparation_id AND p.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no reconciliation prepared as % in PMC %', p_preparation_id,
            p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who approves the reconciliation'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff = v_prepared.prepared_by THEN
        RAISE EXCEPTION 'trust: the staff member who prepared a reconciliation does not approve it'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Another staff member who holds the approve role approves it.';
    END IF;
    v_bank := trust_lock_bank_matches(p_pmc_id, v_prepared.bank_account_id);
    -- Matches and statements of the account wait for this (trust_approve_reconciliation then
    -- holds postings off).
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || v_bank.id, 0));
    IF EXISTS (
        SELECT p.id FROM trust_reconciliation_preparations AS p
        WHERE p.bank_account_id = v_bank.id AND p.created_at > v_prepared.created_at
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % was prepared again since; approve the '
            'latest', v_bank.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = v_bank.id;
    IF v_start >= (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC' THEN
        RAISE EXCEPTION 'trust: trust bank account % is approved through % already', v_bank.id,
            v_prepared.through
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT array_agg(r.amount ORDER BY r.line) INTO v_figures
    FROM trust_report_bank_reconciliation(p_pmc_id, v_bank.id, v_prepared.through) AS r
    WHERE r.line IN (4, 8, 9);
    -- (The bank balance through the day can't have changed: its statements are whole and
    -- append-only.)
    IF v_figures[2] <> v_prepared.book_cash THEN
        RAISE EXCEPTION 'trust: the books through % changed since they were prepared: % '
            '(prepared %)', v_prepared.through, v_figures[2], v_prepared.book_cash
            USING ERRCODE = 'check_violation',
                  HINT = 'Prepare it again.';
    END IF;
    IF v_figures[3] <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', v_bank.id, v_figures[3], v_prepared.through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    RETURN trust_approve_reconciliation(
        p_pmc_id, v_bank.id, v_start,
        (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC', v_figures[1],
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_prepared.prepared_by),
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_staff)
    );
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
    v_due uuid[] := '{}'::uuid[];
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

    -- The rents past their grace without a late fee, locked one by one in id order: one lock
    -- order for every run, so two at once can't deadlock.
    FOR v_rent IN
        SELECT c.id
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        -- The terms in force on the rent's due date: the latest starting on or before it.
        JOIN LATERAL (
            SELECT t.grace_days
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
        ORDER BY c.id
    LOOP
        PERFORM c.id FROM trust_charges AS c WHERE c.id = v_rent.id FOR NO KEY UPDATE;
        CONTINUE WHEN EXISTS (
            SELECT f.rent_charge_id FROM trust_late_fees AS f WHERE f.rent_charge_id = v_rent.id
        );
        v_due := v_due || v_rent.id;
    END LOOP;

    -- Then each, in the order its grace ends, as a run every day would reach them.
    FOR v_rent IN
        SELECT c.id, c.lease_id, c.due_on, p.id AS policy_id, p.grace_days, p.flat_fee,
               p.percent, p.maximum
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN LATERAL (
            SELECT t.id, t.grace_days, t.flat_fee, t.percent, t.maximum
            FROM trust_late_fee_policies AS t
            WHERE t.property_id = u.property_id AND t.starts_on <= c.due_on
            ORDER BY t.starts_on DESC
            LIMIT 1
        ) AS p ON true
        WHERE c.id = ANY (v_due)
        ORDER BY c.due_on + p.grace_days, c.due_on, c.id
    LOOP
        v_cutoff := (v_rent.due_on + v_rent.grace_days + 1)::timestamp AT TIME ZONE 'UTC';
        -- What is open of it then: payments and bounces dated before then, and the lease's
        -- credits due by its last day of grace, oldest charges first (the fees this run charged
        -- for the rents whose grace ended before its own among them).
        SELECT o.open INTO v_unpaid
        FROM trust_open_charges(p_pmc_id, v_rent.lease_id, v_cutoff) AS o
        WHERE o.charge_id = v_rent.id;
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
-- Name: trust_rent_assistance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_rent_assistance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    housing_authority_id uuid NOT NULL,
    starts_on date NOT NULL,
    amount numeric NOT NULL,
    contract text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_rent_assistance_amount_check CHECK (((amount >= (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_rent_assistance_contract_check CHECK (((btrim(contract) <> ''::text) AND (length(contract) <= 100))),
    CONSTRAINT trust_rent_assistance_starts_on_check CHECK ((EXTRACT(day FROM starts_on) = (1)::numeric))
);


--
-- Name: trust_assistance_on(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_assistance_on(p_lease_id uuid, p_day date) RETURNS SETOF public.trust_rent_assistance
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT a.* FROM trust_rent_assistance AS a
    WHERE a.lease_id = p_lease_id AND a.starts_on <= p_day
    ORDER BY a.starts_on DESC, a.created_at DESC, a.id DESC
    LIMIT 1
$$;


--
-- Name: trust_assistance_open(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_assistance_open(p_charge_id uuid) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT c.amount
           + coalesce((SELECT sum(CASE a.kind WHEN 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_assistance_charges AS a WHERE a.adjusts_id = c.id), 0)
           - coalesce((SELECT sum(p.amount) FROM trust_assistance_payments AS p
                       WHERE p.assistance_charge_id = c.id), 0)
    FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id
$$;


--
-- Name: trust_assistance_part(numeric, numeric, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_assistance_part(p_amount numeric, p_monthly_rent numeric, p_assistance numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT CASE WHEN p_amount <= 0 THEN 0::numeric
                ELSE least(p_amount, round(p_amount * p_assistance / p_monthly_rent, 2)) END
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
-- Name: trust_bank_cash_moved(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_bank_cash_moved(p_bank_account_id uuid, p_transfer_id text) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce(-sum(e.amount), 0)
    FROM pgledger_entries AS e
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id
    WHERE e.transfer_id = p_transfer_id
      AND t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash'
$$;


--
-- Name: trust_bank_match_closed(timestamp with time zone, uuid[], text[], uuid[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_bank_match_closed(p_closed_through timestamp with time zone, p_line_ids uuid[], p_transfer_ids text[], p_item_ids uuid[]) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT p_closed_through IS NOT NULL
       AND NOT EXISTS (
           SELECT l.id FROM trust_bank_statement_lines AS l
           WHERE l.id = ANY (p_line_ids)
             AND l.posted_on >= (p_closed_through AT TIME ZONE 'UTC')::date
       )
       AND NOT EXISTS (
           SELECT tr.id FROM pgledger_transfers AS tr
           WHERE tr.id = ANY (p_transfer_ids) AND tr.event_at >= p_closed_through
       )
       AND NOT EXISTS (
           SELECT i.id FROM trust_cutover_items AS i
           JOIN trust_opening_balances AS o ON o.bank_account_id = i.bank_account_id
           WHERE i.id = ANY (p_item_ids)
             AND o.opened_on >= (p_closed_through AT TIME ZONE 'UTC')::date
       )
$$;


--
-- Name: trust_bill_back_utility(uuid, uuid, text, date, date, numeric, date, text, numeric, jsonb, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_bill_back_utility(p_pmc_id uuid, p_property_id uuid, p_utility text, p_period_start date, p_period_end date, p_amount numeric, p_due_on date, p_memo text, p_master_usage numeric, p_usage jsonb, p_bill_id uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill uuid;
    v_plan trust_utility_plans;
    v_shares jsonb;
    v_share jsonb;
    v_charge uuid;
    v_memo text;
BEGIN
    -- Bills of a property for a utility wait for each other, so two never cover a day.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('utility bills of ' || p_property_id || ' ' || p_utility, 0)
    );
    -- Its shares first: this refuses a bill the plan can't share.
    SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.lease_id), '[]'::jsonb) INTO v_shares
    FROM trust_utility_allocation(
        p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, p_amount,
        p_master_usage, p_usage
    ) AS a;
    IF EXISTS (
        SELECT 1 FROM trust_utility_bills AS b
        WHERE b.property_id = p_property_id AND b.utility = p_utility
          AND b.period_start <= p_period_end AND b.period_end >= p_period_start
    ) THEN
        RAISE EXCEPTION 'trust: a % bill of the property already covers part of % to %',
            p_utility, p_period_start, p_period_end
            USING ERRCODE = 'unique_violation';
    END IF;
    IF p_bill_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trust_bills AS b
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
        WHERE b.id = p_bill_id AND b.pmc_id = p_pmc_id AND a.property_id = p_property_id
    ) THEN
        RAISE EXCEPTION 'trust: bill % is no bill of the property', p_bill_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.* INTO v_plan FROM trust_utility_plan_on(p_property_id, p_utility, p_period_start) AS p;

    INSERT INTO trust_utility_bills (
        pmc_id, property_id, utility, period_start, period_end, amount, method, owner_percent,
        master_usage, due_on, memo, bill_id
    ) VALUES (
        p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, p_amount,
        v_plan.method, v_plan.owner_percent, p_master_usage, p_due_on, p_memo, p_bill_id
    )
    RETURNING id INTO v_bill;

    v_memo := initcap(p_utility) || ' ' || to_char(p_period_start, 'YYYY-MM-DD') || ' to '
        || to_char(p_period_end, 'YYYY-MM-DD') || ', '
        || CASE v_plan.method
               WHEN 'units' THEN 'by unit'
               WHEN 'square_feet' THEN 'by square feet'
               WHEN 'occupants' THEN 'by occupants'
               ELSE 'by meter'
           END
        || coalesce(': ' || p_memo, '');
    FOR v_share IN SELECT s FROM jsonb_array_elements(v_shares) AS s
    LOOP
        v_charge := NULL;
        IF (v_share ->> 'amount')::numeric > 0 THEN
            INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
            VALUES (
                p_pmc_id, (v_share ->> 'lease_id')::uuid, p_due_on, 'fee',
                (v_share ->> 'amount')::numeric, v_memo
            )
            RETURNING id INTO v_charge;
        END IF;
        INSERT INTO trust_utility_shares (
            utility_bill_id, lease_id, pmc_id, billed, weight, usage, amount, charge_id
        ) VALUES (
            v_bill, (v_share ->> 'lease_id')::uuid, p_pmc_id, (v_share ->> 'billed')::boolean,
            (v_share ->> 'weight')::numeric, (v_share ->> 'usage')::numeric,
            (v_share ->> 'amount')::numeric, v_charge
        );
    END LOOP;
    RETURN v_bill;
END;
$$;


--
-- Name: trust_broker_of(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_broker_of(p_bank_account_id uuid) RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT l.staff_id
    FROM (
        SELECT DISTINCT ON (s.staff_id) s.staff_id, s.role, s.granted
        FROM trust_account_signers AS s
        WHERE s.bank_account_id = p_bank_account_id
        ORDER BY s.staff_id, s.created_at DESC, s.id DESC
    ) AS l
    WHERE l.role = 'broker' AND l.granted
    ORDER BY l.staff_id
    LIMIT 1
$$;


--
-- Name: trust_charge_nsf_fee(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_charge_nsf_fee(p_pmc_id uuid, p_returned_key text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_key trust_idempotency_keys;
    v_lease trust_leases;
    v_on date;
    v_terms trust_nsf_fee_policies;
    v_fee_id uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: charge an NSF fee at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;

    SELECT k.* INTO v_key FROM trust_idempotency_keys AS k
    WHERE k.pmc_id = p_pmc_id AND k.idempotency_key = p_returned_key
    FOR NO KEY UPDATE;
    IF NOT FOUND OR v_key.request -> 'metadata' ->> 'kind' IS DISTINCT FROM 'bounced payment' THEN
        RAISE EXCEPTION 'trust: no returned payment % in PMC %', p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.fee_charge_id INTO v_fee_id FROM trust_nsf_fees AS f
    WHERE f.pmc_id = p_pmc_id AND f.returned_key = p_returned_key;
    IF FOUND THEN
        RETURN v_fee_id;
    END IF;

    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.pmc_id = p_pmc_id AND l.id::text = v_key.request -> 'metadata' ->> 'lease_id';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: returned payment % names no lease of PMC %',
            p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Money the bank sent back: each posting moves it from what the payment paid into (the
    -- owner's account or the tenant's prepaid rent) back to a trust bank account's cash.
    IF EXISTS (
        SELECT t.id
        FROM pgledger_transfers AS t
        JOIN trust_ledger_accounts AS f ON f.ledger_account_id = t.from_account_id
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = t.to_account_id
        WHERE t.id = ANY (v_key.transfer_ids)
          AND (f.kind NOT IN ('owner_property', 'prepaid_rent') OR c.kind <> 'bank_cash')
    ) THEN
        RAISE EXCEPTION 'trust: % moves money that is not a returned payment', p_returned_key
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT (min(t.event_at) AT TIME ZONE 'UTC')::date INTO v_on
    FROM pgledger_transfers AS t WHERE t.id = ANY (v_key.transfer_ids);
    -- The terms in force that day: the latest starting on or before it.
    SELECT p.* INTO v_terms
    FROM trust_nsf_fee_policies AS p
    JOIN trust_units AS u ON u.property_id = p.property_id
    WHERE u.id = v_lease.unit_id AND p.starts_on <= v_on
    ORDER BY p.starts_on DESC
    LIMIT 1;
    IF NOT FOUND OR v_terms.fee = 0 THEN
        RETURN NULL;
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    VALUES (
        p_pmc_id, v_lease.id, v_on, 'fee', v_terms.fee,
        'NSF fee, payment returned ' || to_char(v_on, 'YYYY-MM-DD')
    )
    RETURNING id INTO v_fee_id;
    INSERT INTO trust_nsf_fees (pmc_id, returned_key, policy_id, fee, fee_charge_id)
    VALUES (p_pmc_id, p_returned_key, v_terms.id, v_terms.fee, v_fee_id);
    RETURN v_fee_id;
END;
$$;


--
-- Name: trust_charge_recurring_due(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_charge_recurring_due(p_pmc_id uuid, p_due_on date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charged integer;
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: recurring charges are charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every recurring charge in force on the due date whose lease is in effect then: its amount
    -- for the days of the month the lease is in effect.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, p_due_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(p_due_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, p_due_on) AS p
    WHERE r.pmc_id = p_pmc_id AND r.starts_on <= p_due_on
      AND (e.ends_on IS NULL OR p_due_on < e.ends_on)
      AND l.starts_on <= p_due_on AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND trust_rent_due_day(l.id, p_due_on) = 1
      AND p.amount > 0  -- a day of a few cents rounds to nothing
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every recurring charge in force on the first day of a lease that starts later this month,
    -- with nothing charged for it in the month yet: its first part month, due on that day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, l.starts_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(l.starts_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, l.starts_on) AS p
    WHERE r.pmc_id = p_pmc_id AND l.starts_on > p_due_on
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND r.starts_on <= l.starts_on AND (e.ends_on IS NULL OR l.starts_on < e.ends_on)
      AND trust_rent_due_day(l.id, l.starts_on) = 1
      AND p.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.recurring_charge_id = r.id
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;


--
-- Name: trust_charge_recurring_on_due_days(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_charge_recurring_on_due_days(p_pmc_id uuid, p_month date, p_as_of date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charged integer;
BEGIN
    IF p_month IS NULL OR p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: recurring charges are charged for a month as of a day; one was '
            'not given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, d.due_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(d.due_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL (SELECT trust_rent_due_on(l.id, p_month) AS due_on) AS d
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, d.due_on) AS p
    WHERE r.pmc_id = p_pmc_id
      AND trust_rent_due_day(l.id, p_month) <> 1
      AND d.due_on IS NOT NULL AND d.due_on <= p_as_of
      AND r.starts_on <= d.due_on AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
      AND p.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.recurring_charge_id = r.id
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', p_month::timestamp)
      )
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;
    RETURN v_charged;
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
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every lease in effect on the due date: its rent for the days of the month it is in effect,
    -- at the rent in force that month (migration 0061).
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', r.amount,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, trust_rent_on(l.id, p_due_on), p_due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND trust_rent_due_day(l.id, p_due_on) = 1
      AND r.amount > 0  -- a day of a few cents' rent rounds to nothing
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every lease that starts later this month, with no rent charged in the month yet: its first
    -- month's rent, due on its first day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, l.starts_on, 'rent', r.amount,
           'Rent due ' || to_char(l.starts_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, l.monthly_rent, l.starts_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on > p_due_on AND r.amount > 0
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND trust_rent_due_day(l.id, l.starts_on) = 1
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;


--
-- Name: trust_charge_rent_on_due_days(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_charge_rent_on_due_days(p_pmc_id uuid, p_month date, p_as_of date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charged integer;
BEGIN
    IF p_month IS NULL OR p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a month as of a day; one was not given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, d.due_on, 'rent', r.amount,
           'Rent due ' || to_char(d.due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL (SELECT trust_rent_due_on(l.id, p_month) AS due_on) AS d
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, trust_rent_on(l.id, d.due_on), d.due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id
      AND trust_rent_due_day(l.id, p_month) <> 1
      AND d.due_on IS NOT NULL AND d.due_on <= p_as_of
      AND r.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', p_month::timestamp)
      )
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
-- Name: trust_check_adverse_action(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_adverse_action() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_needed boolean;
    v_given boolean;
BEGIN
    SELECT d.decision IN ('declined', 'conditional') AND d.screening_id IS NOT NULL
    INTO v_needed
    FROM trust_application_decisions AS d
    WHERE d.application_id = NEW.application_id;
    v_given := EXISTS (
        SELECT a.application_id FROM trust_adverse_actions AS a
        WHERE a.application_id = NEW.application_id
    );
    IF v_needed AND NOT v_given THEN
        RAISE EXCEPTION 'trust: application % was % on a screening; record the adverse action '
            'notice with the decision', NEW.application_id,
            (SELECT d.decision FROM trust_application_decisions AS d
             WHERE d.application_id = NEW.application_id)
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_given AND NOT v_needed THEN
        RAISE EXCEPTION 'trust: an adverse action belongs to a declined or conditional decision '
            'that rests on a screening; application % has none', NEW.application_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
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
-- Name: trust_check_application(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_application() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: take applications at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('application link ' || NEW.link_id, 0));
    IF EXISTS (
        SELECT c.link_id FROM trust_application_link_closings AS c WHERE c.link_id = NEW.link_id
    ) THEN
        RAISE EXCEPTION 'trust: application link % is closed', NEW.link_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_application_decision(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_application_decision() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.screening_id IS NOT NULL AND NOT EXISTS (
        SELECT s.id FROM trust_screenings AS s
        WHERE s.id = NEW.screening_id AND s.application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'trust: screening % is not of application %',
            NEW.screening_id, NEW.application_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_application_not_withdrawn(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_application_not_withdrawn() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NEW.application_id IS NULL THEN
        RETURN NEW;  -- a renewal's offer
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease offer of ' || NEW.application_id, 0));
    IF EXISTS (
        SELECT w.application_id FROM trust_application_withdrawals AS w
        WHERE w.application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'trust: the applicant withdrew this application; it takes nothing more'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_application_withdrawal(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_application_withdrawal() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('lease offer of ' || NEW.application_id, 0));
    IF EXISTS (
        SELECT d.application_id FROM trust_application_decisions AS d
        WHERE d.application_id = NEW.application_id AND d.decision = 'declined'
    ) THEN
        RAISE EXCEPTION 'trust: a declined application is not withdrawn'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT o.id FROM trust_lease_offers AS o
        JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
        WHERE o.application_id = NEW.application_id
    ) THEN
        RAISE EXCEPTION 'trust: this application is a lease already; it is not withdrawn'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_archival(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_archival() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'UTC')::date;
    v_name text;
BEGIN
    IF trust_is_archived(NEW.property_id, NEW.unit_id, NEW.tenant_id, NEW.owner_id) = NEW.archived THEN
        RAISE EXCEPTION 'trust: it is % already', CASE WHEN NEW.archived THEN 'archived' ELSE 'not archived' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.archived THEN
        RETURN NEW;
    END IF;
    IF NEW.tenant_id IS NOT NULL THEN
        SELECT t.display_name INTO v_name FROM trust_tenants AS t WHERE t.id = NEW.tenant_id;
        IF EXISTS (SELECT 1 FROM trust_lease_tenants AS lt JOIN trust_leases AS l ON l.id = lt.lease_id
                   WHERE lt.tenant_id = NEW.tenant_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % is on a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                   JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                   WHERE a.tenant_id = NEW.tenant_id AND b.balance <> 0)
        THEN
            RAISE EXCEPTION 'trust: % has prepaid rent or a deposit held; return it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.unit_id IS NOT NULL THEN
        SELECT u.display_name INTO v_name FROM trust_units AS u WHERE u.id = NEW.unit_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l
                   WHERE l.unit_id = NEW.unit_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.property_id IS NOT NULL THEN
        SELECT p.display_name INTO v_name FROM trust_properties AS p WHERE p.id = NEW.property_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
                   WHERE u.property_id = NEW.property_id
                     AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT o.display_name INTO v_name FROM trust_owners AS o WHERE o.id = NEW.owner_id;
    END IF;
    -- A property's owners' accounts, or an owner's accounts: no money held, no bill left open.
    IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
               JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
               WHERE a.kind = 'owner_property' AND b.balance <> 0
                 AND (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id))
    THEN
        RAISE EXCEPTION 'trust: % holds % money; draw it before archiving', v_name,
            CASE WHEN NEW.owner_id IS NULL THEN 'its owners''' ELSE 'their' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bills AS bill
               JOIN trust_ledger_accounts AS a ON a.ledger_account_id = bill.ledger_account_id
               WHERE (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id)
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                                 WHERE p.bill_id = bill.id AND p.step = 'paid')
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                                 WHERE d.bill_id = bill.id))
    THEN
        RAISE EXCEPTION 'trust: % has a bill not paid; pay or decline it before archiving', v_name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_assistance_charge(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_assistance_charge() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_target trust_assistance_charges;
BEGIN
    IF NEW.adjusts_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT c.* INTO v_target FROM trust_assistance_charges AS c
    WHERE c.id = NEW.adjusts_id AND c.pmc_id = NEW.pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key refuses it
    END IF;
    IF v_target.adjusts_id IS NOT NULL OR v_target.lease_id <> NEW.lease_id
       OR v_target.housing_authority_id <> NEW.housing_authority_id THEN
        RAISE EXCEPTION 'trust: an adjustment changes a charge of the same lease and authority, '
            'not another adjustment'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.source = 'staff' AND NEW.amount > trust_assistance_open(v_target.id) THEN
        RAISE EXCEPTION 'trust: % is more than is open of the authority''s charge, %',
            NEW.amount, trust_assistance_open(v_target.id)
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bank_deposit_item(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bank_deposit_item() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
    v_on date;
    v_in numeric;
    v_at timestamptz;
BEGIN
    SELECT d.bank_account_id, d.deposited_on INTO v_bank, v_on
    FROM trust_bank_deposits AS d
    WHERE d.id = NEW.deposit_id AND d.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such deposit of the PMC: the foreign key refuses it
    END IF;
    SELECT -sum(e.amount), min(tr.event_at) INTO v_in, v_at
    FROM pgledger_entries AS e
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE e.transfer_id = NEW.transfer_id AND t.pmc_id = NEW.pmc_id
      AND t.bank_account_id = v_bank AND t.kind = 'bank_cash';
    IF v_in IS NULL OR v_in <= 0 THEN
        RAISE EXCEPTION 'trust: transfer % brought no money into the deposit''s bank account',
            NEW.transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (v_at AT TIME ZONE 'UTC')::date > v_on THEN
        RAISE EXCEPTION 'trust: transfer % came in after the deposit of %', NEW.transfer_id, v_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bank_statement(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bank_statement() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_last trust_bank_statements;
    v_bank trust_bank_accounts;
BEGIN
    -- Statements of one bank account wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || NEW.bank_account_id, 0));
    SELECT s.* INTO v_last FROM trust_bank_statements AS s
    WHERE s.bank_account_id = NEW.bank_account_id
    ORDER BY s.last_day DESC LIMIT 1;
    IF FOUND THEN
        IF NEW.first_day <> v_last.last_day + 1 THEN
            RAISE EXCEPTION 'trust: the next statement of trust bank account % starts on %, '
                'the day after the last one ends', NEW.bank_account_id, v_last.last_day + 1
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.opening_balance <> v_last.closing_balance THEN
            RAISE EXCEPTION 'trust: the next statement of trust bank account % opens at %, '
                'where the last one closed, not %', NEW.bank_account_id, v_last.closing_balance,
                NEW.opening_balance
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.opening_balance <> 0 THEN
        RAISE EXCEPTION 'trust: the first statement of trust bank account % opens at 0, not %',
            NEW.bank_account_id, NEW.opening_balance
            USING ERRCODE = 'check_violation',
                  HINT = 'Bring its opening balance in as its first line.';
    END IF;
    SELECT b.* INTO v_bank FROM trust_bank_accounts AS b WHERE b.id = NEW.bank_account_id;
    IF NEW.first_day < (v_bank.closed_through AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; a statement starts '
            'no earlier', NEW.bank_account_id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.first_day < (v_bank.opened_at AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: trust bank account % opened on %; a statement starts no earlier',
            NEW.bank_account_id, (v_bank.opened_at AT TIME ZONE 'UTC')::date
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.last_day > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: a statement covers days that have come, not %', NEW.last_day
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bank_statement_line(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bank_statement_line() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_statement trust_bank_statements;
BEGIN
    SELECT s.* INTO v_statement FROM trust_bank_statements AS s WHERE s.id = NEW.statement_id;
    IF NEW.line_no > v_statement.line_count THEN
        RAISE EXCEPTION 'trust: statement % has % lines, not line %', NEW.statement_id,
            v_statement.line_count, NEW.line_no
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.posted_on NOT BETWEEN v_statement.first_day AND v_statement.last_day THEN
        RAISE EXCEPTION 'trust: statement % covers % to %, not %', NEW.statement_id,
            v_statement.first_day, v_statement.last_day, NEW.posted_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bank_statement_total(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bank_statement_total() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_statement trust_bank_statements;
    v_lines bigint;
    v_total numeric;
BEGIN
    SELECT s.* INTO v_statement FROM trust_bank_statements AS s WHERE s.id = NEW.id;
    SELECT count(*), coalesce(sum(l.amount), 0) INTO v_lines, v_total
    FROM trust_bank_statement_lines AS l WHERE l.statement_id = v_statement.id;
    IF v_lines <> v_statement.line_count
        OR v_statement.opening_balance + v_total <> v_statement.closing_balance THEN
        RAISE EXCEPTION 'trust: statement % has % lines coming to %; it says % lines from % to %',
            v_statement.id, v_lines, v_total, v_statement.line_count,
            v_statement.opening_balance, v_statement.closing_balance
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
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
-- Name: trust_check_bill_decision(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bill_decision() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills;
    v_owner uuid;
BEGIN
    SELECT b.* INTO v_bill FROM trust_bills AS b
    WHERE b.id = NEW.bill_id AND b.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the key to trust_bills refuses it
    END IF;
    PERFORM trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    IF EXISTS (SELECT a.bill_id FROM trust_bill_approvals AS a WHERE a.bill_id = NEW.bill_id)
       OR EXISTS (SELECT d.bill_id FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id)
    THEN
        RAISE EXCEPTION 'trust: bill % has its owner''s decision already', v_bill.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_bill_declines' AND EXISTS (
        SELECT p.bill_id FROM trust_bill_payments AS p
        WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside'
    ) THEN
        RAISE EXCEPTION 'trust: bill % is set aside already; it can''t be declined',
            v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.owner_id IS NOT NULL THEN
        SELECT t.owner_id INTO v_owner
        FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
        IF v_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'trust: only the owner of bill %''s property account decides it in '
                'the portal', v_bill.reference
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bill_payment_signer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bill_payment_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM trust_require_signer(a.bank_account_id, (t.event_at AT TIME ZONE 'UTC')::date)
    FROM pgledger_transfers AS t
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.from_account_id
    WHERE t.id = NEW.transfer_id;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bill_reference(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bill_reference() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('trust bill reference ' || NEW.vendor_id || ' ' || NEW.reference, 0)
    );
    IF TG_TABLE_NAME = 'trust_shared_bills' THEN
        IF EXISTS (SELECT 1 FROM trust_bills AS b
                   WHERE b.vendor_id = NEW.vendor_id AND b.reference = NEW.reference) THEN
            RAISE EXCEPTION 'trust: the vendor has a bill % already', NEW.reference
                USING ERRCODE = 'unique_violation';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM trust_shared_bills AS s
                  WHERE s.vendor_id = NEW.vendor_id AND s.reference = NEW.reference) THEN
        RAISE EXCEPTION 'trust: the vendor has a bill % already, shared across properties',
            NEW.reference
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_bill_split(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_bill_split() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills;
    v_total numeric;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_bill_expense_accounts AS x
        WHERE x.id = NEW.categorized_id AND x.bill_id = NEW.bill_id AND x.pmc_id = NEW.pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: a bill''s lines split a row of that bill''s expense account'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO v_bill FROM trust_bills AS b WHERE b.id = NEW.bill_id;
    SELECT sum(s.amount) INTO v_total
    FROM trust_bill_splits AS s WHERE s.categorized_id = NEW.categorized_id;
    IF v_total <> v_bill.amount THEN
        RAISE EXCEPTION 'trust: bill %''s lines come to %, not its amount %',
            v_bill.reference, v_total, v_bill.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: trust_check_budget(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_budget() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account in PMC %',
            NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_charge_waiver(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_charge_waiver() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_credit trust_charges;
    v_charge trust_charges;
    v_paid numeric;
    v_waived numeric;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: waive a charge at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT c.* INTO v_credit FROM trust_charges AS c
    WHERE c.id = NEW.credit_charge_id AND c.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such charge of the PMC: the foreign key refuses it
    END IF;
    IF v_credit.kind <> 'credit' THEN
        RAISE EXCEPTION 'trust: a waiver is a credit, not a %', v_credit.kind
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT c.* INTO v_charge FROM trust_charges AS c
    WHERE c.id = NEW.waived_charge_id AND c.pmc_id = NEW.pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not waived; it is owed to no one'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_charge.lease_id <> v_credit.lease_id THEN
        RAISE EXCEPTION 'trust: a waiver is a credit of the waived charge''s own lease'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = v_charge.id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = v_charge.id;
    SELECT coalesce(sum(k.amount), 0) INTO v_waived
    FROM trust_charge_waivers AS w JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = v_charge.id;
    IF v_paid + v_waived + v_credit.amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: % is left of charge % (% paid, % waived); % can''t be waived',
            v_charge.amount - v_paid - v_waived, v_charge.id, v_paid, v_waived, v_credit.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_completion_not_withdrawn(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_completion_not_withdrawn() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF EXISTS (
        SELECT w.application_id FROM trust_lease_offers AS o
        JOIN trust_application_withdrawals AS w ON w.application_id = o.application_id
        WHERE o.id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: the applicant withdrew this application; it is made no lease'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_cutover_entry(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_cutover_entry() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_opened date;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('cutover of ' || NEW.bank_account_id, 0));
    SELECT o.opened_on INTO v_opened FROM trust_opening_balances AS o
    WHERE o.bank_account_id = NEW.bank_account_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % has no opening balances to carry over to',
            NEW.bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT s.bank_account_id FROM trust_cutover_signoffs AS s
               WHERE s.bank_account_id = NEW.bank_account_id) THEN
        RAISE EXCEPTION 'trust: the cutover of trust bank account % is signed off already',
            NEW.bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'trust_cutover_items' AND (to_jsonb(NEW) ->> 'issued_on')::date > v_opened
    THEN
        RAISE EXCEPTION 'trust: an item carried over was issued by the cutover, %, not %',
            v_opened, to_jsonb(NEW) ->> 'issued_on'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_cutover_signer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_cutover_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM trust_require_role(NEW.pmc_id, NEW.signed_by, 'approve', 'sign off a cutover');
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_decision_override(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_decision_override() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_recommendation text;
BEGIN
    SELECT s.recommendation INTO v_recommendation
    FROM trust_screenings AS s
    WHERE s.application_id = NEW.application_id
    ORDER BY s.reported_at DESC, s.id DESC
    LIMIT 1;
    IF (v_recommendation IS NOT NULL AND NEW.decision <> CASE v_recommendation
            WHEN 'approve' THEN 'approved'
            WHEN 'conditional' THEN 'conditional'
            ELSE 'declined'
        END) <> (NEW.override_reason IS NOT NULL) THEN
        RAISE EXCEPTION 'trust: a decision against its screening''s recommendation says why, and no other decision does'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_deposit_return_signer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_deposit_return_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
BEGIN
    FOR v_bank IN
        SELECT DISTINCT a.bank_account_id
        FROM trust_lease_tenants AS lt
        JOIN trust_ledger_accounts AS a
          ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
        WHERE lt.lease_id = NEW.lease_id
        ORDER BY a.bank_account_id
    LOOP
        PERFORM trust_require_signer(v_bank, (NEW.returned_at AT TIME ZONE 'UTC')::date);
    END LOOP;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_document_file(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_document_file() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_document trust_documents;
    v_typed boolean;
BEGIN
    SELECT d.* INTO v_document FROM trust_documents AS d
    WHERE d.id = NEW.document_id AND d.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        -- No such document of this PMC: the foreign key refuses the file.
        RETURN NEW;
    END IF;
    IF length(NEW.content) <> v_document.size OR sha256(NEW.content) <> v_document.sha256 THEN
        RAISE EXCEPTION 'trust: the file of document % is not the % bytes it says',
            NEW.document_id, v_document.size
            USING ERRCODE = 'check_violation';
    END IF;
    -- The first bytes of a PDF ("%PDF-"), a PNG and a JPEG.
    v_typed := CASE v_document.content_type
        WHEN 'application/pdf' THEN substring(NEW.content FROM 1 FOR 5) = '\x255044462d'::bytea
        WHEN 'image/png' THEN substring(NEW.content FROM 1 FOR 8) = '\x89504e470d0a1a0a'::bytea
        ELSE substring(NEW.content FROM 1 FOR 3) = '\xffd8ff'::bytea
    END;
    IF NOT v_typed THEN
        RAISE EXCEPTION 'trust: the file of document % is not a %', NEW.document_id,
            v_document.content_type
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_document_has_file(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_document_has_file() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (SELECT f.document_id FROM trust_document_files AS f
                   WHERE f.document_id = NEW.id) THEN
        RAISE EXCEPTION 'trust: document % has no file', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: trust_check_draw_signer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_draw_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM trust_require_signer(a.bank_account_id, (t.event_at AT TIME ZONE 'UTC')::date)
    FROM trust_ledger_accounts AS a, pgledger_transfers AS t
    WHERE a.ledger_account_id = NEW.ledger_account_id AND t.id = NEW.transfer_id;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_group_member(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_group_member() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_group text;
    v_property text;
BEGIN
    SELECT g.display_name INTO v_group FROM trust_property_groups AS g
    WHERE g.id = NEW.group_id FOR UPDATE;
    IF (NEW.property_id IN (SELECT trust_group_properties(NEW.group_id))) = NEW.member THEN
        SELECT p.display_name INTO v_property FROM trust_properties AS p
        WHERE p.id = NEW.property_id;
        RAISE EXCEPTION 'trust: % is % in %', v_property,
            CASE WHEN NEW.member THEN 'already' ELSE 'not' END, v_group
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_hold_allowance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_hold_allowance() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id
        FROM pgledger_transfers AS tr
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = tr.from_account_id
        WHERE tr.id = NEW.transfer_id AND t.pmc_id = NEW.pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is not in PMC %', NEW.transfer_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_hold_release(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_hold_release() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_starts_on date;
BEGIN
    SELECT h.starts_on INTO v_starts_on FROM trust_payment_holds AS h WHERE h.id = NEW.hold_id;
    IF NEW.ends_on <= v_starts_on THEN
        RAISE EXCEPTION 'trust: hold % starts on %; its release must end it later, not on %',
            NEW.hold_id, v_starts_on, NEW.ends_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_journal_signer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_journal_signer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
    v_day date;
BEGIN
    FOR v_bank, v_day IN
        SELECT DISTINCT c.bank_account_id, e.event_on
        FROM trust_journal_entry_lines AS l
        JOIN trust_journal_entries AS e ON e.id = l.entry_id
        JOIN trust_ledger_accounts AS c
          ON c.ledger_account_id = l.to_account AND c.kind = 'bank_cash'
        WHERE l.entry_id = NEW.entry_id
        ORDER BY c.bank_account_id
    LOOP
        PERFORM trust_require_signer(v_bank, v_day);
    END LOOP;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_lead_open(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_lead_open() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('trust lead ' || NEW.lead_id::text, 0));
    IF TG_TABLE_NAME = 'trust_showings' AND EXISTS (
        SELECT 1 FROM trust_lead_closings AS c WHERE c.lead_id = NEW.lead_id
    ) THEN
        RAISE EXCEPTION 'trust: this lead is closed' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_lease_offer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_lease_offer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_unit uuid;
    v_starts date;
BEGIN
    IF NEW.application_id IS NOT NULL THEN
        SELECT l.unit_id INTO v_unit
        FROM trust_applications AS a
        JOIN trust_application_links AS l ON l.id = a.link_id
        JOIN trust_application_decisions AS d ON d.application_id = a.id
        WHERE a.id = NEW.application_id AND d.decision IN ('approved', 'conditional');
        IF NOT FOUND THEN
            RAISE EXCEPTION 'trust: application % is not approved; offer it no lease',
                NEW.application_id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT l.unit_id, l.starts_on INTO v_unit, v_starts
        FROM trust_leases AS l WHERE l.id = NEW.renews_lease_id;
        IF NEW.starts_on <= v_starts THEN
            RAISE EXCEPTION 'trust: a renewal of lease % starts after %', NEW.renews_lease_id,
                v_starts
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.unit_id IS DISTINCT FROM v_unit THEN
        RAISE EXCEPTION 'trust: the offer is for unit %, not %', v_unit, NEW.unit_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_lease_offer_charge(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_lease_offer_charge() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF EXISTS (
        SELECT r.offer_id FROM trust_signature_requests AS r WHERE r.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: a lease offer''s terms are set before it is sent'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_lease_offer_not_void(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_lease_offer_not_void() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer).
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(o.application_id, o.renews_lease_id), 0
    ))
    FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    IF EXISTS (
        SELECT v.offer_id FROM trust_lease_offer_voids AS v WHERE v.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this lease offer was voided; it takes nothing more'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_lease_offer_void(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_lease_offer_void() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer).
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(o.application_id, o.renews_lease_id), 0
    ))
    FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    IF EXISTS (
        SELECT c.offer_id FROM trust_lease_offer_completions AS c WHERE c.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this offer is a lease already; it is not voided'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_ownership_share_line(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_ownership_share_line() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_ownership_shares AS s
        JOIN trust_ledger_accounts AS a ON a.property_id = s.property_id
        WHERE s.id = NEW.share_id AND a.ledger_account_id = NEW.ledger_account_id
          AND a.kind = 'owner_property' AND a.pmc_id = NEW.pmc_id AND s.pmc_id = NEW.pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: % is not an owner''s account at the property', NEW.ledger_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_ownership_shares(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_ownership_shares() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_share uuid := coalesce(to_jsonb(NEW) ->> 'share_id', to_jsonb(NEW) ->> 'id')::uuid;
    v_total numeric;
    v_banks bigint;
BEGIN
    SELECT coalesce(sum(l.percent), 0), count(DISTINCT a.bank_account_id)
    INTO v_total, v_banks
    FROM trust_ownership_share_lines AS l
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = l.ledger_account_id
    WHERE l.share_id = v_share;
    IF v_total <> 100 THEN
        RAISE EXCEPTION 'trust: a property''s ownership shares come to 100%%, not %',
            v_total || '%'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_banks <> 1 THEN
        RAISE EXCEPTION 'trust: a property''s owners in shares have their accounts in one trust '
            'bank account'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: trust_check_payment_hold(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_payment_hold() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    -- Waits for matches in flight on the lease (they hold its row FOR SHARE), and holds new
    -- ones off until this commits.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = NEW.lease_id FOR NO KEY UPDATE;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_payout_method(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_payout_method() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_owner_draws AS d
        WHERE d.transfer_id = NEW.transfer_id AND d.pmc_id = NEW.pmc_id
    ) AND NOT EXISTS (
        SELECT 1 FROM trust_bill_payments AS b
        WHERE b.transfer_id = NEW.transfer_id AND b.pmc_id = NEW.pmc_id AND b.step = 'paid'
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is not an owner draw or a bill paid of the PMC',
            NEW.transfer_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_recurring_bill(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_recurring_bill() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: a recurring bill is paid from an owner''s property account in '
            'its own PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_recurring_charge_amount(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_recurring_charge_amount() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_amount numeric;
BEGIN
    SELECT p.amount INTO v_amount
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, NEW.due_on) AS p
    WHERE r.id = NEW.recurring_charge_id AND r.pmc_id = NEW.pmc_id
      AND r.lease_id = NEW.lease_id AND r.kind = NEW.kind;
    IF FOUND AND NEW.amount IS DISTINCT FROM v_amount THEN
        RAISE EXCEPTION 'trust: a recurring charge due % is % for the days its lease is in '
            'effect that month, not %', NEW.due_on, v_amount, NEW.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_rent_assistance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_rent_assistance() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
    v_charged date;
    v_rent numeric;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such lease of the PMC: the foreign key refuses it
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    IF NEW.starts_on < date_trunc('month', v_lease.starts_on::timestamp)::date
       OR NEW.starts_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: housing assistance starts in a month of the lease, from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(d.due_on) INTO v_charged
    FROM (
        SELECT c.due_on FROM trust_charges AS c
        WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.starts_on
        UNION ALL
        SELECT a.due_on FROM trust_assistance_charges AS a
        WHERE a.lease_id = NEW.lease_id AND a.source = 'rent' AND a.due_on >= NEW.starts_on
    ) AS d;
    IF v_charged IS NOT NULL THEN
        RAISE EXCEPTION 'trust: rent due % is charged already; housing assistance starts after '
            'its month', v_charged
            USING ERRCODE = 'check_violation';
    END IF;
    v_rent := trust_rent_on(NEW.lease_id, greatest(NEW.starts_on, v_lease.starts_on));
    IF NEW.amount > v_rent THEN
        RAISE EXCEPTION 'trust: a housing authority pays no more than the rent, %, not %',
            v_rent, NEW.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_rent_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_rent_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NEW.effective_on <= v_lease.starts_on
        OR (v_lease.ends_on IS NOT NULL AND NEW.effective_on > v_lease.ends_on) THEN
        RAISE EXCEPTION 'trust: a rent change starts on the first of a month after the lease starts '
            '(%) and before it ends', v_lease.starts_on
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_lease.ends_on IS NOT NULL AND NEW.lease_terms IS NULL THEN
        RAISE EXCEPTION 'trust: a lease with an end date changes its rent during it only as its '
            'terms allow; name the term'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT c.id FROM trust_charges AS c
        WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.effective_on
    ) THEN
        RAISE EXCEPTION 'trust: rent from % is charged already; change it from a later month',
            to_char(NEW.effective_on, 'YYYY-MM-DD')
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_rent_due_day(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_rent_due_day() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
    v_charged date;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such lease of the PMC: the foreign key refuses it
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    IF NEW.starts_on < date_trunc('month', v_lease.starts_on::timestamp)::date
       OR NEW.starts_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: a rent due day starts in a month of the lease, from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    -- A month the authority pays all of the rent has only its charge (migration 0095).
    SELECT max(d.due_on) INTO v_charged
    FROM (
        SELECT c.due_on FROM trust_charges AS c
        WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.starts_on
        UNION ALL
        SELECT a.due_on FROM trust_assistance_charges AS a
        WHERE a.lease_id = NEW.lease_id AND a.source = 'rent' AND a.due_on >= NEW.starts_on
    ) AS d;
    IF v_charged IS NOT NULL THEN
        RAISE EXCEPTION 'trust: rent due % is charged already; a new due day starts after its month',
            v_charged
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_request_work_order(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_request_work_order() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT w.id
        FROM trust_maintenance_requests AS r
        JOIN trust_leases AS l ON l.id = r.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_work_orders AS w ON w.id = NEW.work_order_id AND w.unit_id = u.id
        WHERE r.id = NEW.request_id
    ) THEN
        RAISE EXCEPTION 'trust: work order % is not for the unit of request %''s lease',
            NEW.work_order_id, NEW.request_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_shared_bill(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_shared_bill() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_shared trust_shared_bills;
    v_parts bigint;
    v_top integer;
    v_accounts bigint;
    v_kinds bigint;
    v_total numeric;
BEGIN
    -- An invoice's row (its id) or a part's (its shared_bill_id).
    SELECT s.* INTO v_shared FROM trust_shared_bills AS s
    WHERE s.id = coalesce(to_jsonb(NEW) ->> 'shared_bill_id', to_jsonb(NEW) ->> 'id')::uuid;
    SELECT count(*), max(p.part), count(DISTINCT b.ledger_account_id),
           count(DISTINCT (b.bill_date, b.due_on, b.memo)), coalesce(sum(b.amount), 0)
    INTO v_parts, v_top, v_accounts, v_kinds, v_total
    FROM trust_shared_bill_parts AS p
    JOIN trust_bills AS b ON b.id = p.bill_id
    WHERE p.shared_bill_id = v_shared.id;
    IF v_parts < 2 OR v_top <> v_parts THEN
        RAISE EXCEPTION 'trust: bill % is shared in parts 1 to n, two at least; it has %',
            v_shared.reference, v_parts
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_accounts <> v_parts THEN
        RAISE EXCEPTION 'trust: bill %''s parts are each an owner''s property account of their own',
            v_shared.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_kinds <> 1 OR EXISTS (
        SELECT 1 FROM trust_shared_bill_parts AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        WHERE p.shared_bill_id = v_shared.id
          AND (b.vendor_id <> v_shared.vendor_id
               OR b.reference <> v_shared.reference || '/' || p.part)
    ) THEN
        RAISE EXCEPTION 'trust: bill %''s parts are the vendor''s, under its reference and '
            'part number, with one date, due day and memo', v_shared.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_total <> v_shared.amount THEN
        RAISE EXCEPTION 'trust: bill %''s parts come to %, not its %',
            v_shared.reference, v_total, v_shared.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: trust_check_signature_evidence(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_signature_evidence() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_link trust_signing_links;
BEGIN
    NEW.signed_at := clock_timestamp();
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer),
    -- so a void and a signature do too.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(o.application_id, o.renews_lease_id), 0
    ))
    FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    SELECT l.* INTO v_link FROM trust_signing_links AS l WHERE l.id = NEW.link_id;
    IF v_link.offer_id IS DISTINCT FROM NEW.offer_id OR v_link.position <> NEW.position THEN
        RAISE EXCEPTION 'trust: this link is another signer''s' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT trust_signing_link_open(NEW.link_id) THEN
        RAISE EXCEPTION 'trust: this signing link no longer signs; ask for a new one'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_signing_events AS e WHERE e.link_id = NEW.link_id AND e.kind = 'paper'
    ) THEN
        RAISE EXCEPTION 'trust: the signer asked to sign on paper through this link'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'consented'
    ) OR NOT EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'opened'
    ) THEN
        RAISE EXCEPTION 'trust: the signer agrees to sign electronically and opens the lease first'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_lease_offer_signers AS s
        WHERE s.offer_id = NEW.offer_id AND s.position = NEW.position
          AND lower(btrim(s.display_name)) = lower(btrim(NEW.typed_name))
    ) THEN
        RAISE EXCEPTION 'trust: type your name as it is on the lease'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_lease_offers AS o
        WHERE o.id = NEW.offer_id AND sha256(o.document) = NEW.document_sha256
    ) THEN
        RAISE EXCEPTION 'trust: this is not the lease sent to sign' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_signature_evidenced(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_signature_evidenced() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM trust_signature_requests AS r
        WHERE r.offer_id = NEW.offer_id AND r.partner = 'PMS e-sign'
    ) AND NOT EXISTS (
        SELECT 1 FROM trust_signature_evidence AS s
        WHERE s.offer_id = NEW.offer_id AND s.position = NEW.position
    ) THEN
        RAISE EXCEPTION 'trust: a signature here is made through the signer''s link'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_signer_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_signer_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_by uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_was trust_account_signers;
    v_broker uuid;
BEGIN
    NEW.changed_by := v_by;
    -- Signers' changes wait for each other, account by account, and read who is on file only
    -- once the change before them committed, so two brokers named at once can't both stand.
    PERFORM pg_advisory_xact_lock(hashtextextended('signers of ' || NEW.bank_account_id, 0));
    v_was := trust_signer_of(NEW.bank_account_id, NEW.staff_id);
    v_broker := trust_broker_of(NEW.bank_account_id);
    IF NEW.granted IS NOT TRUE AND (v_was.id IS NULL OR NOT v_was.granted OR v_was.role <> NEW.role)
    THEN
        RAISE EXCEPTION 'trust: % is not the % of this trust bank account; nothing to take away',
            (SELECT s.display_name FROM trust_staff AS s WHERE s.id = NEW.staff_id), NEW.role
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.role = 'broker' THEN
        IF v_by IS NOT NULL AND NOT trust_staff_holds(NEW.pmc_id, v_by, 'admin') THEN
            RAISE EXCEPTION 'trust: % may not name a trust bank account''s broker (the admin role)',
                (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_by)
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'An admin of the PMC names the broker.';
        END IF;
        IF NEW.granted AND v_broker IS NOT NULL AND v_broker <> NEW.staff_id THEN
            RAISE EXCEPTION 'trust: this trust bank account has a broker on file; take that away '
                'first, in the same change'
                USING ERRCODE = 'unique_violation';
        END IF;
    ELSE
        IF v_by IS NOT NULL AND v_by IS DISTINCT FROM v_broker THEN
            RAISE EXCEPTION 'trust: only this trust bank account''s broker authorizes its signers'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'The broker on file signs in and authorizes them.';
        END IF;
        IF NEW.granted AND NEW.staff_id = v_broker THEN
            RAISE EXCEPTION 'trust: the broker signs this trust bank account already'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_signers_have_a_broker(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_signers_have_a_broker() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_brokers bigint;
    v_signers bigint;
BEGIN
    SELECT count(*) FILTER (WHERE l.role = 'broker'), count(*) FILTER (WHERE l.role = 'signer')
    INTO v_brokers, v_signers
    FROM (
        SELECT DISTINCT ON (s.staff_id) s.role, s.granted
        FROM trust_account_signers AS s
        WHERE s.bank_account_id = NEW.bank_account_id
        ORDER BY s.staff_id, s.created_at DESC, s.id DESC
    ) AS l
    WHERE l.granted;
    IF v_signers > 0 AND v_brokers = 0 THEN
        RAISE EXCEPTION 'trust: a trust bank account''s signers need its broker on file'
            USING ERRCODE = 'check_violation',
                  HINT = 'Name the new broker in the same change, or take the signers away.';
    END IF;
    RETURN NULL;
END;
$$;


--
-- Name: trust_check_signing_event(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_signing_event() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    NEW.created_at := clock_timestamp();
    IF NOT trust_signing_link_open(NEW.link_id) THEN
        RAISE EXCEPTION 'trust: this signing link no longer signs; ask for a new one'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind <> 'paper' AND EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'paper'
    ) THEN
        RAISE EXCEPTION 'trust: the signer asked to sign on paper through this link'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'opened' AND NOT EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'consented'
    ) THEN
        RAISE EXCEPTION 'trust: the signer agrees to sign electronically before opening the lease'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_signing_link(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_signing_link() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_signature_requests AS r
        WHERE r.offer_id = NEW.offer_id AND r.partner = 'PMS e-sign'
    ) THEN
        RAISE EXCEPTION 'trust: this offer is not sent to sign here'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_lease_offer_completions AS c WHERE c.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this offer is a lease already' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_signatures AS g
        WHERE g.offer_id = NEW.offer_id AND g.position = NEW.position
    ) THEN
        RAISE EXCEPTION 'trust: this signer has signed already' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_staff_invite(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_staff_invite() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF (SELECT l.kind FROM trust_logins AS l WHERE l.id = NEW.login_id) = 'staff' THEN
        PERFORM trust_require_role(
            NEW.pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid,
            'admin', 'invite a staff member'
        );
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_staff_role(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_staff_role() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM trust_require_role(
        NEW.pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid,
        TG_ARGV[0], TG_ARGV[1]
    );
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_staff_role_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_staff_role_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_has_admin boolean;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('staff roles of ' || NEW.pmc_id, 0));
    NEW.changed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    NEW.created_at := clock_timestamp();
    v_has_admin := EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
    );
    IF NEW.changed_by IS NULL THEN
        IF v_has_admin OR NEW.role <> 'admin' OR NOT NEW.granted THEN
            RAISE EXCEPTION 'trust: name the admin who changes a role'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'With nobody named, only a PMC''s first admin is granted.';
        END IF;
    ELSE
        PERFORM trust_require_role(NEW.pmc_id, NEW.changed_by, 'admin', 'change a role');
    END IF;
    IF trust_staff_holds(NEW.pmc_id, NEW.staff_id, NEW.role) = NEW.granted THEN
        RAISE EXCEPTION 'trust: staff member % % the % role already', NEW.staff_id,
            CASE WHEN NEW.granted THEN 'holds' ELSE 'does not hold' END, NEW.role
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.granted AND NEW.role = 'admin' AND NOT EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND s.id <> NEW.staff_id
          AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
    ) THEN
        RAISE EXCEPTION 'trust: staff member % is the PMC''s last admin', NEW.staff_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Make another staff member an admin first.';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_check_tenant_payment_transfer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_tenant_payment_transfer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM trust_assistance_payments AS p WHERE p.transfer_id = NEW.transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is a housing authority''s payment, not a tenant''s',
            NEW.transfer_id
            USING ERRCODE = 'invalid_parameter_value';
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
-- Name: trust_check_vendor_link_step(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_vendor_link_step() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_link record;
    v_assigned uuid;
BEGIN
    IF NEW.step NOT IN ('scheduled', 'completed') THEN
        RAISE EXCEPTION 'trust: a vendor schedules a visit or says the work is done, nothing else'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT l.vendor_id,
           EXISTS (SELECT 1 FROM trust_vendor_link_revocations AS r WHERE r.link_id = l.id)
               AS revoked
    INTO v_link
    FROM trust_vendor_links AS l
    WHERE l.id = NEW.vendor_link_id;
    IF v_link.revoked THEN
        RAISE EXCEPTION 'trust: this link was taken back' USING ERRCODE = 'check_violation';
    END IF;
    SELECT s.vendor_id INTO v_assigned
    FROM trust_work_order_steps AS s
    WHERE s.work_order_id = NEW.work_order_id AND s.step = 'assigned'
    ORDER BY s.taken_at DESC, s.created_at DESC
    LIMIT 1;
    IF v_assigned IS DISTINCT FROM v_link.vendor_id THEN
        RAISE EXCEPTION 'trust: this work order is not assigned to the link''s vendor'
            USING ERRCODE = 'check_violation';
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
-- Name: trust_check_work_order_charge(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_work_order_charge() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM trust_work_orders AS w
        JOIN trust_leases AS l ON l.id = NEW.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE w.id = NEW.work_order_id
          AND u.property_id = w.property_id
          AND (w.unit_id IS NULL OR w.unit_id = u.id)
    ) THEN
        RAISE EXCEPTION 'trust: a work order is charged only to a lease at its unit or property'
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
-- Name: trust_check_work_order_step_vendor(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_check_work_order_step_vendor() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_day date := (NEW.taken_at AT TIME ZONE 'UTC')::date;
    v_expires date;
BEGIN
    IF NEW.step = 'scheduled' AND NOT EXISTS (
        SELECT s.id FROM trust_work_order_steps AS s
        WHERE s.work_order_id = NEW.work_order_id AND s.step = 'assigned'
    ) THEN
        RAISE EXCEPTION 'trust: a work order is scheduled once a vendor is assigned to it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.step = 'assigned' THEN
        SELECT i.expires_on INTO v_expires
        FROM trust_vendor_insurance AS i
        WHERE i.vendor_id = NEW.vendor_id AND i.starts_on <= v_day
        ORDER BY i.expires_on DESC
        LIMIT 1;
        IF v_expires < v_day THEN
            RAISE EXCEPTION 'trust: the vendor''s insurance expired on %; record its renewal '
                'before assigning them', to_char(v_expires, 'MM/DD/YYYY')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_complete_lease_offer(uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_complete_lease_offer(p_pmc_id uuid, p_offer_id uuid, p_completed_by uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_offer trust_lease_offers;
    v_lease uuid;
    v_old trust_leases;
    v_tenants uuid[];
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: complete lease offers at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT o.* INTO v_offer FROM trust_lease_offers AS o
    WHERE o.id = p_offer_id AND o.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease offer % in PMC %', p_offer_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Offers for one application or one lease wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(v_offer.application_id, v_offer.renews_lease_id), 0
    ));

    SELECT c.lease_id INTO v_lease FROM trust_lease_offer_completions AS c
    WHERE c.offer_id = p_offer_id;
    IF FOUND THEN
        RETURN v_lease;
    END IF;
    IF EXISTS (
        SELECT s.position FROM trust_lease_offer_signers AS s
        WHERE s.offer_id = p_offer_id AND NOT EXISTS (
            SELECT g.position FROM trust_signatures AS g
            WHERE g.offer_id = s.offer_id AND g.position = s.position
        )
    ) OR NOT EXISTS (
        SELECT s.position FROM trust_lease_offer_signers AS s WHERE s.offer_id = p_offer_id
    ) THEN
        RAISE EXCEPTION 'trust: lease offer % is not signed by everyone yet', p_offer_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT o.id FROM trust_lease_offers AS o
        JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
        WHERE o.application_id = v_offer.application_id
           OR o.renews_lease_id = v_offer.renews_lease_id
    ) THEN
        RAISE EXCEPTION 'trust: another offer for this % became its lease already',
            CASE WHEN v_offer.application_id IS NULL THEN 'lease' ELSE 'application' END
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_offer.application_id IS NOT NULL THEN
        WITH made AS (
            INSERT INTO trust_tenants (pmc_id, property_id, display_name)
            SELECT p_pmc_id, u.property_id, s.display_name
            FROM trust_lease_offer_signers AS s
            JOIN trust_units AS u ON u.id = v_offer.unit_id
            WHERE s.offer_id = p_offer_id AND s.role = 'tenant'
            ORDER BY s.position
            RETURNING id
        )
        SELECT array_agg(m.id) INTO v_tenants FROM made AS m;
    ELSE
        SELECT l.* INTO v_old FROM trust_leases AS l WHERE l.id = v_offer.renews_lease_id;
        SELECT array_agg(t.tenant_id) INTO v_tenants
        FROM trust_lease_tenants AS t
        WHERE t.lease_id = v_old.id AND NOT EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = t.lease_id AND d.tenant_id = t.tenant_id
        );
        IF v_old.ends_on IS NULL OR v_old.ends_on >= v_offer.starts_on THEN
            PERFORM trust_end_lease(p_pmc_id, v_old.id, v_offer.starts_on - 1);
        END IF;
    END IF;
    IF coalesce(cardinality(v_tenants), 0) = 0 THEN
        RAISE EXCEPTION 'trust: lease offer % has no tenant to make its lease with', p_offer_id
            USING ERRCODE = 'check_violation';
    END IF;

    v_lease := trust_open_lease(
        p_pmc_id, v_offer.unit_id, v_offer.starts_on, v_offer.ends_on, v_offer.monthly_rent,
        v_tenants
    );
    -- A renewal keeps the housing assistance in force on its first day (migration 0095), from
    -- its first month and never past its rent, so its rent is split as the old lease's was,
    -- the part month charged below included.
    IF v_offer.application_id IS NULL THEN
        INSERT INTO trust_rent_assistance (
            pmc_id, lease_id, housing_authority_id, starts_on, amount, contract
        )
        SELECT p_pmc_id, v_lease, a.housing_authority_id,
               date_trunc('month', v_offer.starts_on::timestamp)::date,
               least(a.amount, v_offer.monthly_rent), a.contract
        FROM trust_assistance_on(v_old.id, v_offer.starts_on) AS a
        WHERE a.amount > 0;
    END IF;
    INSERT INTO trust_lease_guarantors (pmc_id, lease_id, display_name, email, offer_id)
    SELECT p_pmc_id, v_lease, s.display_name, s.email, p_offer_id
    FROM trust_lease_offer_signers AS s
    WHERE s.offer_id = p_offer_id AND s.role = 'guarantor'
    ORDER BY s.position;
    -- A renewal starting in a month whose rent the old lease was charged: the old lease's days
    -- after its end come off (trust_end_lease above), and the new lease is charged its days of
    -- that month, due on its first day, as the rent run would.
    IF v_offer.application_id IS NULL THEN
        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        SELECT p_pmc_id, v_lease, v_offer.starts_on, 'rent', r.amount,
               'Rent due ' || to_char(v_offer.starts_on, 'YYYY-MM-DD') || r.days
        FROM trust_rent_for_month(
            v_offer.starts_on, v_offer.ends_on, v_offer.monthly_rent, v_offer.starts_on
        ) AS r
        WHERE r.amount > 0 AND EXISTS (
            SELECT c.id FROM trust_charges AS c
            WHERE c.lease_id = v_old.id AND c.kind = 'rent'
              AND date_trunc('month', c.due_on::timestamp)
                  = date_trunc('month', v_offer.starts_on::timestamp)
        );
    END IF;
    INSERT INTO trust_lease_offer_completions (pmc_id, offer_id, lease_id, completed_by)
    VALUES (p_pmc_id, p_offer_id, v_lease, p_completed_by);
    RETURN v_lease;
END;
$$;


--
-- Name: trust_decline_journal_entry(uuid, uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_decline_journal_entry(p_pmc_id uuid, p_entry_id uuid, p_reason text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who declines the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM trust_lock_journal_entry(p_pmc_id, p_entry_id);
    IF EXISTS (
        SELECT 1 FROM trust_journal_entry_decisions AS d WHERE d.entry_id = p_entry_id
    ) THEN
        RAISE EXCEPTION 'trust: journal entry % was decided already', p_entry_id
            USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO trust_journal_entry_decisions (entry_id, pmc_id, decision, decided_by, reason)
    VALUES (p_entry_id, p_pmc_id, 'declined', v_staff, p_reason);
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
DECLARE
    v_old trust_leases;
    v_new trust_leases;
    v_charge record;
    v_adjustment uuid;
BEGIN
    -- Wait for a rent run of the PMC under way, so the charges it makes are seen below, and keep
    -- the next one from charging until this commits.
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || p_pmc_id, 0));
    SELECT l.* INTO v_old FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    UPDATE trust_leases SET ends_on = p_ends_on WHERE id = p_lease_id AND pmc_id = p_pmc_id
    RETURNING * INTO v_new;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The tenant's charges the rent run makes (rent, and a recurring charge's).
    FOR v_charge IN
        SELECT k.id, k.due_on, k.label, k.was, k.now
        FROM (
            SELECT c.id, c.due_on,
                   coalesce(rc.memo, 'Rent') AS label,
                   (CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
                   + coalesce((
                       SELECT sum(CASE WHEN a.kind = 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_lease_end_adjustments AS x
                       JOIN trust_charges AS a ON a.id = x.adjustment_charge_id
                       WHERE x.charge_id = c.id
                   ), 0) AS net,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * (
                       w.amount - CASE WHEN aid.id IS NULL THEN 0 ELSE
                           trust_assistance_part(w.amount, aid.monthly_rent, aid.monthly_assistance)
                       END
                   ) AS was,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * (
                       n.amount - CASE WHEN aid.id IS NULL THEN 0 ELSE
                           trust_assistance_part(n.amount, aid.monthly_rent, aid.monthly_assistance)
                       END
                   ) AS now
            FROM trust_charges AS c
            LEFT JOIN trust_recurring_charges AS rc ON rc.id = c.recurring_charge_id
            -- The authority's part of the same month's rent, if it pays part of it.
            LEFT JOIN trust_assistance_charges AS aid
                ON c.kind = 'rent' AND aid.lease_id = c.lease_id AND aid.source = 'rent'
                AND date_trunc('month', aid.due_on::timestamp)
                    = date_trunc('month', c.due_on::timestamp)
            CROSS JOIN LATERAL (
                SELECT greatest((
                    SELECT r.amount FROM trust_rent_for_month(
                        v_old.starts_on, v_old.ends_on,
                        coalesce(rc.amount, trust_rent_on(p_lease_id, c.due_on)), c.due_on
                    ) AS r
                ), 0) AS amount
            ) AS w
            CROSS JOIN LATERAL (
                SELECT greatest((
                    SELECT r.amount FROM trust_rent_for_month(
                        v_new.starts_on, v_new.ends_on,
                        coalesce(rc.amount, trust_rent_on(p_lease_id, c.due_on)), c.due_on
                    ) AS r
                ), 0) AS amount
            ) AS n
            WHERE c.lease_id = p_lease_id
              AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        ) AS k
        WHERE k.net = k.was AND k.now <> k.was
        ORDER BY k.due_on, k.id
    LOOP
        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, p_lease_id, v_charge.due_on,
            CASE WHEN v_charge.now < v_charge.was THEN 'credit' ELSE 'fee' END,
            abs(v_charge.now - v_charge.was),
            v_charge.label || ' due ' || to_char(v_charge.due_on, 'YYYY-MM-DD') || CASE
                WHEN abs(v_charge.now) < abs(v_charge.was)
                    THEN ': the days after the lease ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
                WHEN p_ends_on IS NULL THEN ': the days back, the lease no longer ends'
                ELSE ': the days back, the lease now ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
            END
        )
        RETURNING id INTO v_adjustment;
        INSERT INTO trust_lease_end_adjustments (adjustment_charge_id, pmc_id, charge_id, ends_on)
        VALUES (v_adjustment, p_pmc_id, v_charge.id, p_ends_on);
    END LOOP;

    -- The authority's part of each month's rent, the same way: one that, with the lease ends'
    -- adjustments of it, stands at its part of the month's days under the old end.
    FOR v_charge IN
        SELECT k.id, k.due_on, k.housing_authority_id, k.was, k.now
        FROM (
            SELECT a.id, a.due_on, a.housing_authority_id,
                   a.amount + coalesce((
                       SELECT sum(CASE x.kind WHEN 'credit' THEN -x.amount ELSE x.amount END)
                       FROM trust_assistance_charges AS x
                       WHERE x.adjusts_id = a.id AND x.source = 'lease end'
                   ), 0) AS net,
                   trust_assistance_part(greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_old.starts_on, v_old.ends_on, trust_rent_on(p_lease_id, a.due_on),
                           a.due_on
                       ) AS r
                   ), 0), a.monthly_rent, a.monthly_assistance) AS was,
                   trust_assistance_part(greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_new.starts_on, v_new.ends_on, trust_rent_on(p_lease_id, a.due_on),
                           a.due_on
                       ) AS r
                   ), 0), a.monthly_rent, a.monthly_assistance) AS now
            FROM trust_assistance_charges AS a
            WHERE a.lease_id = p_lease_id AND a.source = 'rent'
        ) AS k
        WHERE k.net = k.was AND k.now <> k.was
        ORDER BY k.due_on, k.id
    LOOP
        INSERT INTO trust_assistance_charges (
            pmc_id, lease_id, housing_authority_id, due_on, kind, amount, memo, source, adjusts_id
        ) VALUES (
            p_pmc_id, p_lease_id, v_charge.housing_authority_id, v_charge.due_on,
            CASE WHEN v_charge.now < v_charge.was THEN 'credit' ELSE 'charge' END,
            abs(v_charge.now - v_charge.was),
            'Rent due ' || to_char(v_charge.due_on, 'YYYY-MM-DD') || CASE
                WHEN v_charge.now < v_charge.was
                    THEN ': the days after the lease ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
                WHEN p_ends_on IS NULL THEN ': the days back, the lease no longer ends'
                ELSE ': the days back, the lease now ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
            END,
            'lease end', v_charge.id
        );
    END LOOP;
END;
$$;


--
-- Name: trust_enter_recurring_bills(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_enter_recurring_bills(p_pmc_id uuid, p_through date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_entered integer;
BEGIN
    IF p_through IS NULL THEN
        RAISE EXCEPTION 'trust: enter recurring bills through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    WITH due AS (
        SELECT r.id AS recurring_bill_id, r.pmc_id, r.vendor_id, r.ledger_account_id,
               r.reference_prefix || '-' || to_char(d.due_on, 'YYYY-MM') AS reference,
               d.due_on, r.amount, r.memo
        FROM trust_recurring_bills AS r
        LEFT JOIN trust_recurring_bill_ends AS e ON e.recurring_bill_id = r.id
        CROSS JOIN LATERAL generate_series(
            date_trunc('month', r.starts_on), p_through::timestamp, interval '1 month'
        ) AS m (month)
        CROSS JOIN LATERAL (SELECT (m.month::date + r.day_of_month - 1) AS due_on) AS d
        WHERE r.pmc_id = p_pmc_id
          AND d.due_on >= r.starts_on
          AND d.due_on <= p_through
          AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
    ),
    entered AS (
        INSERT INTO trust_bills (
            pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
        )
        SELECT d.pmc_id, d.vendor_id, d.ledger_account_id, d.reference, d.due_on, d.due_on,
               d.amount, d.memo
        FROM due AS d
        ORDER BY d.recurring_bill_id, d.due_on
        ON CONFLICT (vendor_id, reference) DO NOTHING
        RETURNING id, pmc_id, vendor_id, reference
    ),
    categorized AS (
        INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
        SELECT b.pmc_id, b.id, c.expense_account_id
        FROM entered AS b
        JOIN due AS d ON d.vendor_id = b.vendor_id AND d.reference = b.reference
        CROSS JOIN LATERAL (
            SELECT x.expense_account_id
            FROM trust_recurring_bill_expense_accounts AS x
            WHERE x.recurring_bill_id = d.recurring_bill_id
            ORDER BY x.id DESC
            LIMIT 1
        ) AS c
        RETURNING bill_id
    )
    SELECT count(*)::integer INTO v_entered FROM entered;
    RETURN v_entered;
END;
$$;


--
-- Name: trust_fill_bill_markup(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_fill_bill_markup() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bill trust_bills := trust_lock_bill(NEW.pmc_id, NEW.bill_id);
    v_percent numeric;
BEGIN
    IF EXISTS (SELECT 1 FROM trust_bill_payments AS p
               WHERE p.bill_id = NEW.bill_id AND p.step = 'set_aside') THEN
        RAISE EXCEPTION 'trust: bill % is set aside already; a markup is added before',
            v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bill_declines AS d WHERE d.bill_id = NEW.bill_id) THEN
        RAISE EXCEPTION 'trust: bill % was declined; it takes no markup', v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    v_percent := coalesce(
        (trust_agreement_on(v_bill.ledger_account_id, v_bill.bill_date)).bill_markup_percent, 0
    );
    IF v_percent = 0 THEN
        RAISE EXCEPTION 'trust: the owner''s agreement in force on % sets no markup on bills',
            v_bill.bill_date
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.percent := v_percent;
    NEW.amount := round(v_bill.amount * v_percent / 100, 2);
    IF NEW.amount = 0 THEN
        RAISE EXCEPTION 'trust: a markup of % percent on bill % comes to nothing',
            trim_scale(v_percent), v_bill.reference
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: trust_group_properties(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_group_properties(p_group_id uuid) RETURNS SETOF uuid
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT latest.property_id
    FROM (
        SELECT DISTINCT ON (m.property_id) m.property_id, m.member
        FROM trust_property_group_members AS m
        WHERE m.group_id = p_group_id
        ORDER BY m.property_id, m.created_at DESC, m.id DESC
    ) AS latest
    WHERE latest.member
$$;


--
-- Name: trust_is_archived(uuid, uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_is_archived(p_property_id uuid, p_unit_id uuid, p_tenant_id uuid DEFAULT NULL::uuid, p_owner_id uuid DEFAULT NULL::uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce((
        SELECT a.archived FROM trust_archivals AS a
        WHERE (p_property_id IS NOT NULL AND a.property_id = p_property_id)
           OR (p_unit_id IS NOT NULL AND a.unit_id = p_unit_id)
           OR (p_tenant_id IS NOT NULL AND a.tenant_id = p_tenant_id)
           OR (p_owner_id IS NOT NULL AND a.owner_id = p_owner_id)
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 1
    ), false)
$$;


--
-- Name: trust_lease_billed(uuid, text, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lease_billed(p_lease_id uuid, p_utility text, p_day date) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce((
        SELECT u.billed FROM trust_lease_utilities AS u
        WHERE u.lease_id = p_lease_id AND u.utility = p_utility AND u.starts_on <= p_day
        ORDER BY u.starts_on DESC, u.created_at DESC, u.id DESC
        LIMIT 1
    ), false)
$$;


--
-- Name: trust_lease_moved_from(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lease_moved_from(p_lease_id uuid) RETURNS TABLE(lease_id uuid, owner_account text)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH RECURSIVE back AS (
        SELECT t.from_lease_id, t.owner_account, 1 AS step
        FROM trust_lease_transfers AS t WHERE t.to_lease_id = p_lease_id
        UNION ALL
        -- Each move opens its new lease, so the leases moved from come before it: no cycle.
        SELECT t.from_lease_id, t.owner_account, b.step + 1
        FROM trust_lease_transfers AS t JOIN back AS b ON t.to_lease_id = b.from_lease_id
    )
    SELECT b.from_lease_id, b.owner_account FROM back AS b ORDER BY b.step DESC
$$;


--
-- Name: trust_lease_owed(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lease_owed(p_lease_id uuid) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT greatest(
        coalesce((SELECT sum(CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
                  FROM trust_charges AS c WHERE c.lease_id = p_lease_id), 0)
        - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
                    JOIN trust_charges AS c ON c.id = p.charge_id
                    WHERE c.lease_id = p_lease_id), 0)
        + coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
                    JOIN trust_charges AS c ON c.id = r.charge_id
                    WHERE c.lease_id = p_lease_id), 0),
        0
    )
$$;


--
-- Name: trust_lease_people(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lease_people(p_lease_id uuid, p_day date) RETURNS integer
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT (
        SELECT count(*) FROM trust_lease_tenants AS t
        WHERE t.lease_id = p_lease_id AND NOT EXISTS (
            SELECT 1 FROM trust_lease_departures AS d
            WHERE d.lease_id = t.lease_id AND d.tenant_id = t.tenant_id AND d.left_on <= p_day
        )
    )::integer + (
        SELECT count(*) FROM trust_lease_occupants AS o
        WHERE o.lease_id = p_lease_id AND NOT EXISTS (
            SELECT 1 FROM trust_lease_household_removals AS r WHERE r.occupant_id = o.id
        )
    )::integer
$$;


--
-- Name: trust_lock_application_link(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lock_application_link() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('application link ' || NEW.link_id, 0));
    RETURN NEW;
END;
$$;


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
-- Name: trust_lock_bank_matches(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lock_bank_matches(p_pmc_id uuid, p_bank_account_id uuid) RETURNS public.trust_bank_accounts
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank trust_bank_accounts;
BEGIN
    SELECT b.* INTO v_bank FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('bank matches of ' || p_bank_account_id, 0));
    RETURN v_bank;
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
-- Name: trust_journal_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_journal_entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    event_on date NOT NULL,
    memo text NOT NULL,
    proposed_by uuid NOT NULL,
    proposed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_journal_entries_memo_check CHECK (((memo ~ '\S'::text) AND (length(memo) <= 500)))
);


--
-- Name: trust_lock_journal_entry(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_lock_journal_entry(p_pmc_id uuid, p_entry_id uuid) RETURNS public.trust_journal_entries
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_entry trust_journal_entries;
BEGIN
    SELECT e.* INTO v_entry FROM trust_journal_entries AS e
    WHERE e.id = p_entry_id AND e.pmc_id = p_pmc_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no journal entry % in PMC %', p_entry_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_entry;
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
    deposit_kind text,
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
-- Name: trust_match_bank_lines(uuid, uuid, uuid[], text[], uuid[], uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_match_bank_lines(p_pmc_id uuid, p_bank_account_id uuid, p_line_ids uuid[], p_transfer_ids text[], p_item_ids uuid[], p_matched_by uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank trust_bank_accounts := trust_lock_bank_matches(p_pmc_id, p_bank_account_id);
    v_lines uuid[] := coalesce(p_line_ids, '{}');
    v_transfers text[] := coalesce(p_transfer_ids, '{}');
    v_items uuid[] := coalesce(p_item_ids, '{}');
    v_unmoved text;
    v_line_total numeric;
    v_book_total numeric;
    v_id uuid;
BEGIN
    IF cardinality(v_lines) + cardinality(v_transfers) + cardinality(v_items) = 0 THEN
        RAISE EXCEPTION 'trust: a match holds at least one line, transfer or cutover item'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_lines) <> (SELECT count(DISTINCT x) FROM unnest(v_lines) AS x)
        OR cardinality(v_transfers) <> (SELECT count(DISTINCT x) FROM unnest(v_transfers) AS x)
        OR cardinality(v_items) <> (SELECT count(DISTINCT x) FROM unnest(v_items) AS x)
    THEN
        RAISE EXCEPTION 'trust: a match names each line, transfer and item once'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_lines) <> (
        SELECT count(*) FROM trust_bank_statement_lines AS l
        JOIN trust_bank_statements AS s ON s.id = l.statement_id
        WHERE l.id = ANY (v_lines) AND s.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: a line named is not on a statement of trust bank account %',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(v_items) <> (
        SELECT count(*) FROM trust_cutover_items AS i
        WHERE i.id = ANY (v_items) AND i.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: an item named was not carried over into trust bank account %',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT min(x) INTO v_unmoved FROM unnest(v_transfers) AS x
    WHERE trust_bank_cash_moved(p_bank_account_id, x) = 0;
    IF v_unmoved IS NOT NULL THEN
        RAISE EXCEPTION 'trust: transfer % moved no cash of trust bank account %', v_unmoved,
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT ml.line_id FROM trust_bank_match_lines AS ml
        WHERE ml.line_id = ANY (v_lines)
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = ml.match_id)
    ) OR EXISTS (
        SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
        JOIN trust_bank_matches AS m ON m.id = mt.match_id
        WHERE mt.transfer_id = ANY (v_transfers) AND m.bank_account_id = p_bank_account_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mt.match_id)
    ) OR EXISTS (
        SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
        WHERE mi.item_id = ANY (v_items) AND mi.side = 'cleared'
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mi.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: a line, transfer or item named is matched already'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    IF cardinality(v_items) <> (
        SELECT count(DISTINCT mi.item_id) FROM trust_bank_match_cutover_items AS mi
        WHERE mi.item_id = ANY (v_items) AND mi.side = 'carried'
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mi.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: an item carried over clears once the opening is matched'
            USING ERRCODE = 'check_violation',
                  HINT = 'Match the balance brought forward to the opening first.';
    END IF;
    SELECT coalesce(sum(l.amount), 0) INTO v_line_total
    FROM trust_bank_statement_lines AS l WHERE l.id = ANY (v_lines);
    SELECT coalesce(sum(trust_bank_cash_moved(p_bank_account_id, x)), 0)
           + coalesce((SELECT sum(i.amount) FROM trust_cutover_items AS i
                       WHERE i.id = ANY (v_items)), 0)
    INTO v_book_total
    FROM unnest(v_transfers) AS x;
    IF v_line_total <> v_book_total THEN
        RAISE EXCEPTION 'trust: the lines come to % and the transfers and items to %; a match '
            'comes to the same on both sides', v_line_total, v_book_total
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_bank_match_closed(v_bank.closed_through, v_lines, v_transfers, v_items) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; this match would '
            'change a reconciliation already approved', p_bank_account_id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_bank_matches (pmc_id, bank_account_id, matched_by)
    VALUES (p_pmc_id, p_bank_account_id, p_matched_by)
    RETURNING id INTO v_id;
    INSERT INTO trust_bank_match_lines (match_id, line_id, pmc_id)
    SELECT v_id, x, p_pmc_id FROM unnest(v_lines) AS x;
    INSERT INTO trust_bank_match_transfers (match_id, transfer_id, pmc_id)
    SELECT v_id, x, p_pmc_id FROM unnest(v_transfers) AS x;
    INSERT INTO trust_bank_match_cutover_items (match_id, item_id, side, pmc_id)
    SELECT v_id, x, 'cleared', p_pmc_id FROM unnest(v_items) AS x;
    RETURN v_id;
END;
$$;


--
-- Name: trust_match_cutover_opening(uuid, uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_match_cutover_opening(p_pmc_id uuid, p_bank_account_id uuid, p_line_id uuid, p_matched_by uuid) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank trust_bank_accounts := trust_lock_bank_matches(p_pmc_id, p_bank_account_id);
    v_opening trust_opening_balances;
    v_items uuid[];
    v_line numeric;
    v_carried numeric;
    v_id uuid;
BEGIN
    SELECT o.* INTO v_opening FROM trust_opening_balances AS o
    WHERE o.bank_account_id = p_bank_account_id;
    IF NOT FOUND OR NOT EXISTS (
        SELECT s.bank_account_id FROM trust_cutover_signoffs AS s
        WHERE s.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % has no signed-off cutover to match',
            p_bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT l.amount INTO v_line FROM trust_bank_statement_lines AS l
    JOIN trust_bank_statements AS s ON s.id = l.statement_id
    WHERE l.id = p_line_id AND s.bank_account_id = p_bank_account_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: the line named is not on a statement of trust bank account %',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT coalesce(array_agg(i.id ORDER BY i.id), '{}'), coalesce(sum(i.amount), 0)
    INTO v_items, v_carried
    FROM trust_cutover_items AS i WHERE i.bank_account_id = p_bank_account_id;
    IF EXISTS (
        SELECT ml.line_id FROM trust_bank_match_lines AS ml
        WHERE ml.line_id = p_line_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = ml.match_id)
    ) OR EXISTS (
        SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
        JOIN trust_bank_matches AS m ON m.id = mt.match_id
        WHERE mt.transfer_id = ANY (v_opening.transfer_ids)
          AND m.bank_account_id = p_bank_account_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = mt.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: the line or the opening is matched already'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    IF v_line <> v_opening.book_cash - v_carried THEN
        RAISE EXCEPTION 'trust: the balance brought forward is %; the opening balances (%) less '
            'the items not yet cleared (%) come to %', v_line, v_opening.book_cash, v_carried,
            v_opening.book_cash - v_carried
            USING ERRCODE = 'check_violation';
    END IF;
    IF trust_bank_match_closed(v_bank.closed_through, ARRAY[p_line_id],
                               v_opening.transfer_ids, v_items) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; this match would '
            'change a reconciliation already approved', p_bank_account_id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_bank_matches (pmc_id, bank_account_id, matched_by)
    VALUES (p_pmc_id, p_bank_account_id, p_matched_by)
    RETURNING id INTO v_id;
    INSERT INTO trust_bank_match_lines (match_id, line_id, pmc_id)
    VALUES (v_id, p_line_id, p_pmc_id);
    INSERT INTO trust_bank_match_transfers (match_id, transfer_id, pmc_id)
    SELECT v_id, x, p_pmc_id FROM unnest(v_opening.transfer_ids) AS x;
    INSERT INTO trust_bank_match_cutover_items (match_id, item_id, side, pmc_id)
    SELECT v_id, x, 'carried', p_pmc_id FROM unnest(v_items) AS x;
    RETURN v_id;
END;
$$;


--
-- Name: trust_name_custom_role_changer(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_name_custom_role_changer() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    NEW.changed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;


--
-- Name: trust_name_document_remover(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_name_document_remover() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    NEW.removed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;


--
-- Name: trust_name_document_uploader(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_name_document_uploader() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    NEW.uploaded_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;


--
-- Name: trust_name_recorder(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_name_recorder() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    NEW.recorded_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    RETURN NEW;
END;
$$;


--
-- Name: trust_open_charges(uuid, uuid, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamp with time zone) RETURNS TABLE(lease_id uuid, charge_id uuid, open numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at,
               trust_pay_rank(c) AS pay_rank,
               c.amount
               - coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND t.event_at < p_at
               ), 0)
               + coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                   WHERE r.charge_id = c.id AND t.event_at < p_at
               ), 0) AS left_owing,
               EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                       WHERE w.credit_charge_id = c.id) AS waiver
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id AND (p_lease_id IS NULL OR c.lease_id = p_lease_id)
    ),
    -- What waivers due before the moment's day take off the charge each one names.
    waived AS (
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM charged AS k
        JOIN trust_charge_waivers AS w ON w.credit_charge_id = k.id
        WHERE k.due_on < (p_at AT TIME ZONE 'UTC')::date
        GROUP BY w.waived_charge_id
    ),
    -- Each charge after its own waivers, and what of them it could not take.
    settled AS (
        SELECT k.lease_id, k.id, k.due_on, k.created_at, k.pay_rank,
               k.left_owing - least(greatest(k.left_owing, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0) - least(greatest(k.left_owing, 0), coalesce(w.amount, 0))
                   AS spare
        FROM charged AS k
        LEFT JOIN waived AS w ON w.id = k.id
        WHERE k.kind <> 'credit'
    ),
    -- The rest come off the open charges rent first (migration 0093): credits that waive nothing,
    -- and what a waiver could not take off its own charge.
    credits AS (
        SELECT x.lease_id, sum(x.amount) AS amount
        FROM (
            SELECT k.lease_id, k.amount FROM charged AS k
            WHERE k.kind = 'credit' AND NOT k.waiver
              AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT s.lease_id, s.spare FROM settled AS s WHERE s.spare > 0
        ) AS x
        GROUP BY x.lease_id
    ),
    owing AS (
        SELECT s.lease_id, s.id, s.left_owing,
               coalesce(sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM settled AS s
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;


--
-- Name: trust_open_deposit_account(uuid, uuid, uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_deposit_account(p_pmc_id uuid, p_bank_account_id uuid, p_tenant_id uuid, p_deposit_kind text) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_kind text := nullif(p_deposit_kind, 'security');
    v_ledger_account_id text;
BEGIN
    IF p_deposit_kind IS NULL OR p_deposit_kind NOT IN ('security', 'pet', 'key') THEN
        RAISE EXCEPTION 'trust: a deposit is a security, pet or key deposit, not %',
            coalesce(p_deposit_kind, 'none')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.ledger_account_id INTO v_ledger_account_id FROM trust_ledger_accounts AS t
    WHERE t.pmc_id = p_pmc_id AND t.bank_account_id = p_bank_account_id
      AND t.kind = 'tenant_deposit' AND t.tenant_id = p_tenant_id
      AND t.deposit_kind IS NOT DISTINCT FROM v_kind;
    IF FOUND THEN
        RETURN v_ledger_account_id;
    END IF;

    SELECT a.id INTO v_ledger_account_id
    FROM pgledger_create_account('tenant_deposit', 'USD', allow_negative_balance => false) AS a;
    -- The account's own checks (and its trust bank account's kind) hold as for any other.
    INSERT INTO trust_ledger_accounts (
        ledger_account_id, pmc_id, bank_account_id, kind, tenant_id, deposit_kind
    ) VALUES (
        v_ledger_account_id, p_pmc_id, p_bank_account_id, 'tenant_deposit', p_tenant_id, v_kind
    );
    RETURN v_ledger_account_id;
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
-- Name: trust_open_lease_offer_charges(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_lease_offer_charges() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on)
    SELECT c.pmc_id, NEW.lease_id, c.kind, c.amount, c.memo, o.starts_on
    FROM trust_lease_offer_charges AS c
    JOIN trust_lease_offers AS o ON o.id = c.offer_id
    WHERE c.offer_id = NEW.offer_id
    ORDER BY c.position;
    RETURN NULL;
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
-- Name: trust_open_recurring_work_orders(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_open_recurring_work_orders(p_pmc_id uuid, p_through date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_due record;
    v_order uuid;
    v_opened integer := 0;
BEGIN
    IF p_through IS NULL THEN
        RAISE EXCEPTION 'trust: open recurring work orders through a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    FOR v_due IN
        SELECT r.id, r.pmc_id, r.property_id, r.unit_id, r.summary, r.priority, r.vendor_id,
               d.due_on
        FROM trust_recurring_work_orders AS r
        LEFT JOIN trust_recurring_work_order_ends AS e ON e.recurring_work_order_id = r.id
        CROSS JOIN LATERAL (
            -- The first day of the month on or after it starts.
            SELECT date_trunc('month', r.starts_on)
                + CASE WHEN extract(DAY FROM r.starts_on) > r.day_of_month
                    THEN interval '1 month' ELSE interval '0' END AS first_month
        ) AS f
        CROSS JOIN LATERAL generate_series(
            f.first_month, p_through::timestamp, make_interval(months => r.every_months)
        ) AS m (month)
        CROSS JOIN LATERAL (SELECT (m.month::date + r.day_of_month - 1) AS due_on) AS d
        WHERE r.pmc_id = p_pmc_id
          AND d.due_on <= p_through
          AND (e.ends_on IS NULL OR d.due_on < e.ends_on)
        ORDER BY d.due_on, r.id
    LOOP
        INSERT INTO trust_work_orders (
            pmc_id, property_id, unit_id, summary, opened_at, recurring_work_order_id,
            recurring_on
        )
        VALUES (
            v_due.pmc_id, v_due.property_id, v_due.unit_id, v_due.summary,
            v_due.due_on::timestamp AT TIME ZONE 'UTC', v_due.id, v_due.due_on
        )
        ON CONFLICT (recurring_work_order_id, recurring_on) DO NOTHING
        RETURNING id INTO v_order;
        CONTINUE WHEN v_order IS NULL;
        v_opened := v_opened + 1;
        INSERT INTO trust_work_order_details (pmc_id, work_order_id, priority)
        VALUES (v_due.pmc_id, v_order, v_due.priority);
        IF v_due.vendor_id IS NOT NULL THEN
            BEGIN
                INSERT INTO trust_work_order_steps (
                    pmc_id, work_order_id, step, vendor_id, taken_at
                )
                VALUES (v_due.pmc_id, v_order, 'assigned', v_due.vendor_id, now());
            EXCEPTION
                WHEN check_violation THEN
                    -- The vendor's insurance ran out: it waits for staff to assign one.
                    v_order := NULL;
            END;
        END IF;
    END LOOP;
    RETURN v_opened;
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
-- Name: trust_ownership_on(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_ownership_on(p_property_id uuid, p_day date) RETURNS TABLE(ledger_account_id text, percent numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT l.ledger_account_id, l.percent
    FROM trust_ownership_share_lines AS l
    WHERE l.share_id = (
        SELECT s.id FROM trust_ownership_shares AS s
        WHERE s.property_id = p_property_id AND s.starts_on <= p_day
        ORDER BY s.starts_on DESC, s.created_at DESC, s.id DESC
        LIMIT 1
    )
    ORDER BY l.ledger_account_id COLLATE "C"
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
    recurring_charge_id uuid,
    work_order_id uuid,
    CONSTRAINT trust_charges_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_charges_kind_check CHECK ((kind = ANY (ARRAY['rent'::text, 'fee'::text, 'credit'::text])))
);


--
-- Name: trust_pay_rank(public.trust_charges); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_pay_rank(p_charge public.trust_charges) RETURNS integer
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT CASE
        WHEN p_charge.kind = 'rent' THEN 0
        WHEN p_charge.kind = 'fee' AND (
            p_charge.recurring_charge_id IS NOT NULL
            OR EXISTS (
                SELECT 1 FROM trust_lease_end_adjustments AS x
                JOIN trust_charges AS o ON o.id = x.charge_id
                WHERE x.adjustment_charge_id = p_charge.id
                  AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
            )
        ) THEN 1
        ELSE 2
    END
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
-- Name: trust_post_journal_entry(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_post_journal_entry(p_pmc_id uuid, p_entry_id uuid) RETURNS text[]
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_entry trust_journal_entries;
    v_decided trust_journal_entry_decisions;
    v_transfers text[];
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who posts the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_entry := trust_lock_journal_entry(p_pmc_id, p_entry_id);
    SELECT d.* INTO v_decided FROM trust_journal_entry_decisions AS d
    WHERE d.entry_id = p_entry_id;
    IF FOUND THEN
        IF v_decided.decision = 'declined' THEN
            RAISE EXCEPTION 'trust: journal entry % was declined; it is never posted', p_entry_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN v_decided.transfer_ids;  -- a retry: what it posted
    END IF;
    IF v_staff = v_entry.proposed_by THEN
        RAISE EXCEPTION 'trust: the staff member who proposed a journal entry does not post it'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Another staff member who holds the approve role posts it.';
    END IF;

    SELECT array_agg(t.id ORDER BY t.ordinality) INTO v_transfers
    FROM pgledger_create_transfers(
        ARRAY(
            SELECT (l.from_account, l.to_account, l.amount)::transfer_request
            FROM trust_journal_entry_lines AS l
            WHERE l.entry_id = p_entry_id
            ORDER BY l.line
        ),
        v_entry.event_on::timestamp AT TIME ZONE 'UTC',
        jsonb_build_object(
            'memo', 'Journal entry: ' || v_entry.memo, 'journal_entry_id', p_entry_id
        )
    ) WITH ORDINALITY AS t;

    INSERT INTO trust_journal_entry_decisions (
        entry_id, pmc_id, decision, decided_by, transfer_ids
    ) VALUES (p_entry_id, p_pmc_id, 'posted', v_staff, v_transfers);
    RETURN v_transfers;
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
    -- Rent, or a fee the rent run made from a recurring charge. (A recurring credit is never
    -- paid, so it has no payments here.)
    JOIN trust_charges AS c ON c.id = cp.charge_id
        AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
    JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    -- A housing authority's part of the rent it paid (migration 0095).
    SELECT v_collected + coalesce(sum(ap.amount), 0) INTO v_collected
    FROM trust_assistance_payments AS ap
    JOIN pgledger_transfers AS t ON t.id = ap.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    SELECT v_collected - coalesce(sum(r.amount), 0) INTO v_collected
    FROM trust_payment_reversals AS r
    JOIN trust_charges AS c ON c.id = r.charge_id
        AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
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
-- Name: trust_prepare_bank_reconciliation(uuid, uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_prepare_bank_reconciliation(p_pmc_id uuid, p_bank_account_id uuid, p_through date) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_start timestamptz;
    v_figures numeric[];
    v_id uuid;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who prepares the reconciliation'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF (p_through + 1)::timestamp AT TIME ZONE 'UTC' <= v_start THEN
        RAISE EXCEPTION 'trust: the next period of trust bank account % starts at %',
            p_bank_account_id, v_start
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF (p_through + 1)::timestamp AT TIME ZONE 'UTC' > now() THEN
        RAISE EXCEPTION 'trust: % has not ended yet', p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The bank balance, the book cash and the difference.
    SELECT array_agg(r.amount ORDER BY r.line) INTO v_figures
    FROM trust_report_bank_reconciliation(p_pmc_id, p_bank_account_id, p_through) AS r
    WHERE r.line IN (4, 8, 9);
    IF v_figures[3] <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', p_bank_account_id, v_figures[3], p_through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    INSERT INTO trust_reconciliation_preparations (
        pmc_id, bank_account_id, through, bank_balance, book_cash, prepared_by
    ) VALUES (p_pmc_id, p_bank_account_id, p_through, v_figures[1], v_figures[2], v_staff)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;


--
-- Name: trust_propose_journal_entry(uuid, date, text, public.transfer_request[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_propose_journal_entry(p_pmc_id uuid, p_event_on date, p_memo text, p_lines public.transfer_request[]) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_entry uuid;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who proposes the journal entry'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_event_on IS NULL THEN
        RAISE EXCEPTION 'trust: a journal entry is dated a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(cardinality(p_lines), 0) NOT BETWEEN 1 AND 20 THEN
        RAISE EXCEPTION 'trust: a journal entry has 1 to 20 lines, not %',
            coalesce(cardinality(p_lines), 0)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(p_lines) AS l
        WHERE NOT EXISTS (
                  SELECT 1 FROM trust_ledger_accounts AS a
                  WHERE a.ledger_account_id = l.from_account_id AND a.pmc_id = p_pmc_id)
           OR NOT EXISTS (
                  SELECT 1 FROM trust_ledger_accounts AS a
                  WHERE a.ledger_account_id = l.to_account_id AND a.pmc_id = p_pmc_id)
    ) THEN
        RAISE EXCEPTION 'trust: a journal entry moves money between ledger accounts of its own '
            'PMC only'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO trust_journal_entries (pmc_id, event_on, memo, proposed_by)
    VALUES (p_pmc_id, p_event_on, p_memo, v_staff)
    RETURNING id INTO v_entry;
    INSERT INTO trust_journal_entry_lines (
        entry_id, pmc_id, line, from_account, to_account, amount
    )
    SELECT v_entry, p_pmc_id, l.n, l.from_account_id, l.to_account_id, l.amount
    FROM unnest(p_lines) WITH ORDINALITY AS l (from_account_id, to_account_id, amount, n);
    RETURN v_entry;
END;
$$;


--
-- Name: trust_record_evidenced_signature(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_record_evidenced_signature() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    INSERT INTO trust_signatures (pmc_id, offer_id, position, signed_at)
    VALUES (NEW.pmc_id, NEW.offer_id, NEW.position, NEW.signed_at);
    RETURN NULL;
END;
$$;


--
-- Name: trust_record_owner_contribution(uuid, text, text, numeric, timestamp with time zone, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_record_owner_contribution(p_pmc_id uuid, p_account text, p_request_key text, p_amount numeric, p_event_at timestamp with time zone, p_memo text DEFAULT NULL::text) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_account trust_ledger_accounts;
    v_taken trust_owner_contributions;
    v_cash text;
    v_transfer_id text;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 OR p_amount <> round(p_amount, 2) THEN
        RAISE EXCEPTION 'trust: a contribution is a positive amount in cents, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- READ COMMITTED, and contributions to one account wait for each other.
    v_account := trust_lock_owner_account(p_pmc_id, p_account);

    SELECT c.* INTO v_taken
    FROM trust_owner_contributions AS c
    WHERE c.pmc_id = p_pmc_id AND c.request_key = p_request_key;
    IF FOUND THEN
        IF (v_taken.ledger_account_id, v_taken.amount) IS DISTINCT FROM (p_account, p_amount) THEN
            RAISE EXCEPTION 'trust: request key % already recorded % into %',
                p_request_key, v_taken.amount, v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new contribution; resend a key only to retry.';
        END IF;
        RETURN v_taken.transfer_id;  -- a retry: the original contribution
    END IF;

    -- Money reaches an owner's account through its trust bank account's cash.
    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_account.bank_account_id AND t.kind = 'bank_cash';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the cash account (bank_cash) of trust bank account % first',
            v_account.bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_cash, p_account, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', coalesce(nullif(p_memo, ''), 'Owner contribution'))
    ) AS t;

    INSERT INTO trust_owner_contributions (
        pmc_id, request_key, ledger_account_id, amount, transfer_id
    ) VALUES (p_pmc_id, p_request_key, p_account, p_amount, v_transfer_id);
    RETURN v_transfer_id;
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
-- Name: trust_remove_lease_tenant(uuid, uuid, uuid, date, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_remove_lease_tenant(p_pmc_id uuid, p_lease_id uuid, p_tenant_id uuid, p_left_on date, p_deposit_to uuid) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_lease_departures;
    v_name text;
    v_still record;
    v_deposit record;
    v_to text;
    v_moved numeric := 0.00;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: remove tenants at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A lease's rent work, deposit return and departures wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));

    SELECT t.display_name INTO v_name FROM trust_lease_tenants AS lt
    JOIN trust_tenants AS t ON t.id = lt.tenant_id
    WHERE lt.lease_id = p_lease_id AND lt.tenant_id = p_tenant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: tenant % is not on lease %', p_tenant_id, p_lease_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT d.* INTO v_done FROM trust_lease_departures AS d
    WHERE d.lease_id = p_lease_id AND d.tenant_id = p_tenant_id;
    IF FOUND THEN
        IF v_done.left_on = p_left_on AND v_done.deposit_to IS NOT DISTINCT FROM p_deposit_to THEN
            RETURN v_done.moved;
        END IF;
        RAISE EXCEPTION 'trust: % left this lease on % already', v_name, v_done.left_on
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT r.lease_id FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id) THEN
        RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_left_on IS NULL OR p_left_on < v_lease.starts_on OR p_left_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: a tenant leaves a lease within its days: from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT lt.tenant_id FROM trust_lease_tenants AS lt
        WHERE lt.lease_id = p_lease_id AND lt.tenant_id <> p_tenant_id AND NOT EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id
        )
    ) THEN
        RAISE EXCEPTION 'trust: % is the last tenant on this lease: end the lease instead',
            v_name
            USING ERRCODE = 'check_violation';
    END IF;

    IF p_deposit_to IS NOT NULL THEN
        IF p_deposit_to = p_tenant_id OR NOT EXISTS (
            SELECT lt.tenant_id FROM trust_lease_tenants AS lt
            WHERE lt.lease_id = p_lease_id AND lt.tenant_id = p_deposit_to
        ) OR EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = p_lease_id AND d.tenant_id = p_deposit_to
        ) THEN
            RAISE EXCEPTION 'trust: tenant % is not staying on lease %', p_deposit_to, p_lease_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- A deposit is held per tenant: on another lease still, theirs may be that lease's.
        SELECT u.display_name AS unit INTO v_still
        FROM trust_lease_tenants AS other
        JOIN trust_leases AS l ON l.id = other.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE other.tenant_id = p_tenant_id AND l.id <> p_lease_id
          AND (l.ends_on IS NULL OR l.ends_on >= p_left_on)
          AND NOT EXISTS (
              SELECT d.tenant_id FROM trust_lease_departures AS d
              WHERE d.lease_id = l.id AND d.tenant_id = p_tenant_id
          )
        ORDER BY l.starts_on, l.id
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'trust: % is on the lease of % still, and their deposit stays with it',
                v_name, v_still.unit
                USING ERRCODE = 'check_violation';
        END IF;
        FOR v_deposit IN
            SELECT t.ledger_account_id AS account, t.bank_account_id, t.deposit_kind, a.balance
            FROM trust_ledger_accounts AS t
            JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
            WHERE t.pmc_id = p_pmc_id AND t.tenant_id = p_tenant_id
              AND t.kind = 'tenant_deposit' AND a.balance > 0
            ORDER BY t.ledger_account_id
            FOR UPDATE OF a
        LOOP
            -- Into the staying tenant's deposit of the same kind (migration 0092).
            v_to := trust_open_deposit_account(
                p_pmc_id, v_deposit.bank_account_id, p_deposit_to,
                coalesce(v_deposit.deposit_kind, 'security')
            );
            -- Within one trust bank account: its cash doesn't move.
            PERFORM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_to, v_deposit.balance)::transfer_request],
                now(),
                jsonb_build_object(
                    'memo', 'Security deposit moved as ' || v_name || ' left the lease',
                    'kind', 'deposit moved', 'lease_id', p_lease_id
                )
            );
            v_moved := v_moved + v_deposit.balance;
        END LOOP;
    END IF;

    INSERT INTO trust_lease_departures (pmc_id, lease_id, tenant_id, left_on, deposit_to, moved)
    VALUES (p_pmc_id, p_lease_id, p_tenant_id, p_left_on, p_deposit_to, v_moved);
    RETURN v_moved;
END;
$$;


--
-- Name: trust_rent_due_day(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_rent_due_day(p_lease_id uuid, p_day date) RETURNS integer
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce((
        SELECT d.due_day FROM trust_rent_due_days AS d
        WHERE d.lease_id = p_lease_id AND d.starts_on <= p_day
        ORDER BY d.starts_on DESC
        LIMIT 1
    ), 1)
$$;


--
-- Name: trust_rent_due_on(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_rent_due_on(p_lease_id uuid, p_day date) RETURNS date
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT CASE
        WHEN l.starts_on > m.month_end OR l.ends_on < m.month_start THEN NULL
        ELSE least(
            greatest(m.month_start + (trust_rent_due_day(l.id, m.month_start) - 1), l.starts_on),
            coalesce(l.ends_on, m.month_end)
        )
    END
    FROM trust_leases AS l
    CROSS JOIN (
        SELECT date_trunc('month', p_day::timestamp)::date AS month_start,
               (date_trunc('month', p_day::timestamp) + interval '1 month - 1 day')::date
                   AS month_end
    ) AS m
    WHERE l.id = p_lease_id
$$;


--
-- Name: trust_rent_for_month(date, date, numeric, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_rent_for_month(p_starts_on date, p_ends_on date, p_monthly_rent numeric, p_day date) RETURNS TABLE(amount numeric, days text)
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT round(p_monthly_rent * (d.last_day - d.first_day + 1) / d.month_days, 2),
           CASE WHEN d.last_day - d.first_day + 1 = d.month_days THEN ''
                ELSE ', ' || to_char(d.first_day, 'YYYY-MM-DD') || ' to '
                     || to_char(d.last_day, 'YYYY-MM-DD') || ' ('
                     || (d.last_day - d.first_day + 1) || ' of ' || d.month_days || ' days)'
           END
    FROM (
        SELECT greatest(p_starts_on, m.month_start) AS first_day,
               least(coalesce(p_ends_on, m.month_end), m.month_end) AS last_day,
               m.month_end - m.month_start + 1 AS month_days
        FROM (
            SELECT date_trunc('month', p_day::timestamp)::date AS month_start,
                   (date_trunc('month', p_day::timestamp) + interval '1 month - 1 day')::date
                       AS month_end
        ) AS m
    ) AS d
$$;


--
-- Name: trust_rent_on(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_rent_on(p_lease_id uuid, p_day date) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce((
        SELECT c.monthly_rent FROM trust_rent_changes AS c
        WHERE c.lease_id = p_lease_id AND c.effective_on <= p_day
        ORDER BY c.effective_on DESC
        LIMIT 1
    ), (SELECT l.monthly_rent FROM trust_leases AS l WHERE l.id = p_lease_id))
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
-- Name: trust_report_balance_sheet(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_balance_sheet(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, section text, bank text, item text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.bank_account_id, t.kind,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS balance
        FROM trust_ledger_accounts AS t
        WHERE t.pmc_id = p_pmc_id
    ),
    -- What a trust bank account holds, by kind, in this order.
    kinds (kind, step, label) AS (
        VALUES ('owner_property', 1, 'owners'' funds'),
               ('tenant_deposit', 2, 'tenants'' security deposits'),
               ('prepaid_rent', 3, 'tenants'' prepaid rent'),
               ('vendor_payable', 4, 'set aside for vendors'' bills'),
               ('pmc_income', 5, 'the company''s fees not yet taken')
    ),
    banks AS (
        -- Book cash is what the bank holds: the cash account's balance runs below zero as
        -- money arrives.
        SELECT b.id, b.display_name || ' (' || b.kind || ')' AS name,
               -coalesce(sum(x.balance) FILTER (WHERE x.kind = 'bank_cash'), 0.00) AS cash,
               coalesce(sum(x.balance) FILTER (WHERE x.kind <> 'bank_cash'), 0.00) AS held
        FROM trust_bank_accounts AS b
        LEFT JOIN balances AS x ON x.bank_account_id = b.id
        WHERE b.pmc_id = p_pmc_id
        GROUP BY b.id, b.display_name, b.kind
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               'PMC' AS part_name, pmc.display_name AS label, NULL::numeric AS value
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL
        UNION ALL
        SELECT 1, b.name, b.id, 0, 'assets', 'cash in the bank, by the books', b.cash
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 10 + k.step, 'held in trust', k.label, sum(x.balance)
        FROM banks AS b
        JOIN balances AS x ON x.bank_account_id = b.id
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY b.name, b.id, k.step, k.label
        UNION ALL
        SELECT 1, b.name, b.id, 20, 'held in trust', 'total held in trust', b.held
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 21, 'difference', 'cash less what is held', b.cash - b.held
        FROM banks AS b
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'all trust bank accounts', 'cash in the bank, by the books',
               coalesce((SELECT sum(b.cash) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 10 + k.step, 'all trust bank accounts', k.label, sum(x.balance)
        FROM balances AS x
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY k.step, k.label
        UNION ALL
        SELECT 2, NULL, NULL, 20, 'all trust bank accounts', 'total held in trust',
               coalesce((SELECT sum(b.held) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 21, 'all trust bank accounts', 'cash less what is held',
               coalesce((SELECT sum(b.cash - b.held) FROM banks AS b), 0.00)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.bank_name COLLATE "C", l.bank_id, l.step
            ))::integer,
           l.part_name, l.bank_name, l.label, l.value
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_bank_reconciliation(uuid, uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_bank_reconciliation(p_pmc_id uuid, p_bank_account_id uuid, p_through date) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_end timestamptz := (p_through + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF NOT EXISTS (
        SELECT b.id FROM trust_bank_accounts AS b
        WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT s.id FROM trust_bank_statements AS s
        WHERE s.bank_account_id = p_bank_account_id
          AND p_through BETWEEN s.first_day AND s.last_day
    ) THEN
        RAISE EXCEPTION 'trust: no statement of trust bank account % covers %',
            p_bank_account_id, p_through
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Import the statement first.';
    END IF;

    RETURN QUERY
    WITH moved AS (
        SELECT tr.id, tr.event_at, tr.metadata ->> 'memo' AS memo, -sum(e.amount) AS cash
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash'
          AND tr.event_at < v_end
        GROUP BY tr.id
        HAVING sum(e.amount) <> 0
    ),
    lines AS (
        SELECT l.id, l.posted_on, l.amount, l.description, l.bank_reference, l.line_no,
               s.first_day
        FROM trust_bank_statement_lines AS l
        JOIN trust_bank_statements AS s ON s.id = l.statement_id
        WHERE s.bank_account_id = p_bank_account_id AND l.posted_on <= p_through
    ),
    cleared AS (
        SELECT m.id FROM trust_bank_matches AS m
        WHERE m.bank_account_id = p_bank_account_id
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = m.id)
          AND NOT EXISTS (
              SELECT ml.line_id FROM trust_bank_match_lines AS ml
              JOIN trust_bank_statement_lines AS l ON l.id = ml.line_id
              WHERE ml.match_id = m.id AND l.posted_on > p_through
          )
          AND NOT EXISTS (
              SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
              JOIN pgledger_transfers AS tr ON tr.id = mt.transfer_id
              WHERE mt.match_id = m.id AND tr.event_at >= v_end
          )
    ),
    -- Items carried over that a cleared opening match carries and no cleared match clears.
    carried AS (
        SELECT i.id, i.issued_on, i.amount, i.description, i.reference
        FROM trust_cutover_items AS i
        WHERE i.bank_account_id = p_bank_account_id
          AND EXISTS (SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
                      JOIN cleared AS c ON c.id = mi.match_id
                      WHERE mi.item_id = i.id AND mi.side = 'carried')
          AND NOT EXISTS (SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
                          JOIN cleared AS c ON c.id = mi.match_id
                          WHERE mi.item_id = i.id AND mi.side = 'cleared')
    ),
    outstanding AS (
        SELECT mv.* FROM moved AS mv
        WHERE NOT EXISTS (
            SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
            JOIN cleared AS c ON c.id = mt.match_id
            WHERE mt.transfer_id = mv.id
        )
    ),
    missing AS (
        SELECT ln.* FROM lines AS ln
        WHERE NOT EXISTS (
            SELECT ml.line_id FROM trust_bank_match_lines AS ml
            JOIN cleared AS c ON c.id = ml.match_id
            WHERE ml.line_id = ln.id
        )
    ),
    uncleared AS (
        SELECT o.cash AS amount FROM outstanding AS o
        UNION ALL
        SELECT ci.amount FROM carried AS ci
    ),
    totals AS (
        SELECT (SELECT coalesce(sum(ln.amount), 0) FROM lines AS ln) AS bank,
               (SELECT coalesce(sum(u.amount), 0) FROM uncleared AS u WHERE u.amount > 0)
                   AS in_transit,
               (SELECT coalesce(sum(u.amount), 0) FROM uncleared AS u WHERE u.amount < 0)
                   AS outstanding,
               (SELECT coalesce(sum(mv.cash), 0) FROM moved AS mv) AS book
    ),
    heads AS (
        SELECT 1 AS n, 'PMC' AS item, p.display_name AS detail, NULL::numeric AS amount
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL
        FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id
        UNION ALL
        SELECT 3, 'at the end of', to_char(p_through, 'YYYY-MM-DD') || ' (UTC)', NULL
        UNION ALL
        SELECT 4, 'bank balance (statements)', NULL, t.bank FROM totals AS t
        UNION ALL
        SELECT 5, 'deposits in transit', NULL, t.in_transit FROM totals AS t
        UNION ALL
        SELECT 6, 'outstanding withdrawals', NULL, t.outstanding FROM totals AS t
        UNION ALL
        SELECT 7, 'adjusted bank balance', NULL, t.bank + t.in_transit + t.outstanding
        FROM totals AS t
        UNION ALL
        SELECT 8, 'trust journal (book cash)', NULL, t.book FROM totals AS t
        UNION ALL
        SELECT 9, 'difference: adjusted bank balance - trust journal', NULL,
               t.bank + t.in_transit + t.outstanding - t.book
        FROM totals AS t
    ),
    items AS (
        SELECT CASE WHEN o.cash > 0 THEN 1 ELSE 2 END AS section,
               CASE WHEN o.cash > 0 THEN 'deposit in transit' ELSE 'outstanding withdrawal' END
                   AS item,
               to_char(o.event_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') || ' '
                   || coalesce(o.memo, '') AS detail,
               o.cash AS amount, o.event_at::text AS happened, o.id::text AS id
        FROM outstanding AS o
        UNION ALL
        SELECT CASE WHEN ci.amount > 0 THEN 1 ELSE 2 END,
               CASE WHEN ci.amount > 0 THEN 'deposit in transit' ELSE 'outstanding withdrawal'
               END,
               to_char(ci.issued_on, 'YYYY-MM-DD') || ' ' || ci.description
                   || coalesce(' (' || ci.reference || ')', '') || ', from before the cutover',
               ci.amount, ci.issued_on::text, ci.id::text
        FROM carried AS ci
        UNION ALL
        SELECT 3, 'bank line not in the books',
               to_char(ms.posted_on, 'YYYY-MM-DD') || ' ' || ms.description
                   || coalesce(' (' || ms.bank_reference || ')', ''),
               ms.amount, ms.posted_on::text, ms.first_day::text || lpad(ms.line_no::text, 6, '0')
        FROM missing AS ms
    )
    SELECT h.n, h.item, h.detail, h.amount FROM heads AS h
    UNION ALL
    SELECT 9 + row_number() OVER (
               ORDER BY i.section, i.happened COLLATE "C", i.detail COLLATE "C", i.id COLLATE "C"
           )::integer,
           i.item, i.detail, i.amount
    FROM items AS i
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_budget_vs_actual(uuid, uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_budget_vs_actual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, budget numeric, actual numeric, actual_less_budget numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    -- The income statement checks the PMC, the owner and the days, and says what is wrong.
    RETURN QUERY
    WITH statement AS (
        SELECT s.line AS n, s.property AS place, s.item AS what, s.amount
        FROM trust_report_income_statement(p_pmc_id, p_owner_id, p_from, p_through) AS s
    ),
    accounts AS (
        SELECT t.ledger_account_id, pr.display_name AS place
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- Each line's budget for each month that starts within the days: its latest row.
    latest AS (
        SELECT DISTINCT ON (b.ledger_account_id, b.month, b.line, b.expense_account_id)
               b.ledger_account_id, b.line AS kind, b.expense_account_id, b.amount
        FROM trust_budgets AS b
        WHERE b.pmc_id = p_pmc_id AND b.month >= p_from AND b.month <= p_through
          AND b.ledger_account_id IN (SELECT a.ledger_account_id FROM accounts AS a)
        ORDER BY b.ledger_account_id, b.month, b.line, b.expense_account_id, b.id DESC
    ),
    budgeted AS (
        SELECT a.place,
               CASE WHEN l.kind IN ('rent', 'recurring charges', 'late fees', 'NSF fees',
                                    'other charges') THEN 'income: ' || l.kind
                    WHEN l.kind = 'expense account' THEN 'expense: ' || e.name
                    ELSE 'expense: ' || l.kind END AS what,
               sum(l.amount) AS amount
        FROM latest AS l
        JOIN accounts AS a ON a.ledger_account_id = l.ledger_account_id
        LEFT JOIN trust_expense_accounts AS e ON e.id = l.expense_account_id
        GROUP BY 1, 2
    ),
    happened AS (
        SELECT s.place, s.what, s.amount FROM statement AS s
        WHERE s.place IS NOT NULL AND (s.what LIKE 'income: %' OR s.what LIKE 'expense: %')
    ),
    items AS (
        SELECT coalesce(h.place, b.place) AS place, coalesce(h.what, b.what) AS what,
               coalesce(b.amount, 0.00) AS budget, coalesce(h.amount, 0.00) AS actual
        FROM happened AS h
        FULL JOIN budgeted AS b ON b.place = h.place AND b.what = h.what
    ),
    -- Income in the statement's order, then the fees, each expense account by name, and bills
    -- in none last.
    sorted AS (
        SELECT i.*,
               CASE i.what
                   WHEN 'income: rent' THEN 1
                   WHEN 'income: recurring charges' THEN 2
                   WHEN 'income: late fees' THEN 3
                   WHEN 'income: NSF fees' THEN 4
                   WHEN 'income: other charges' THEN 5
                   WHEN 'expense: management fees' THEN 11
                   WHEN 'expense: leasing fees' THEN 12
                   WHEN 'expense: bills not in an expense account' THEN 14
                   ELSE 13
               END AS step
        FROM items AS i
    ),
    places AS (
        SELECT DISTINCT s.place FROM sorted AS s
    ),
    lines AS (
        -- The statement's own heading: the PMC, the owner and the days.
        SELECT 0 AS part, NULL::text AS place, s.n AS step, NULL::text AS name,
               s.place AS label, s.what, NULL::numeric AS budget, NULL::numeric AS actual
        FROM statement AS s
        WHERE s.what IN ('PMC', 'owner', 'period')
        UNION ALL
        SELECT 0, NULL, 4, NULL, 'budgets of the months that start within these days',
               'budget', NULL, NULL
        UNION ALL
        SELECT 1, s.place, s.step, s.what, s.place, s.what, s.budget, s.actual
        FROM sorted AS s
        UNION ALL
        SELECT 1, p.place, 6, NULL, p.place, 'total income',
               coalesce(sum(s.budget) FILTER (WHERE s.step < 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step < 10), 0.00)
        FROM places AS p JOIN sorted AS s ON s.place = p.place
        GROUP BY p.place
        UNION ALL
        SELECT 1, p.place, 15, NULL, p.place, 'total expenses',
               coalesce(sum(s.budget) FILTER (WHERE s.step > 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step > 10), 0.00)
        FROM places AS p JOIN sorted AS s ON s.place = p.place
        GROUP BY p.place
        UNION ALL
        SELECT 1, p.place, 16, NULL, p.place, 'net income',
               coalesce(sum(CASE WHEN s.step < 10 THEN s.budget ELSE -s.budget END), 0.00),
               coalesce(sum(CASE WHEN s.step < 10 THEN s.actual ELSE -s.actual END), 0.00)
        FROM places AS p JOIN sorted AS s ON s.place = p.place
        GROUP BY p.place
        UNION ALL
        SELECT 2, NULL, s.step, s.what, NULL, 'all properties: ' || s.what, sum(s.budget),
               sum(s.actual)
        FROM sorted AS s
        GROUP BY s.step, s.what
        UNION ALL
        SELECT 2, NULL, 6, NULL, NULL, 'all properties: total income',
               coalesce(sum(s.budget) FILTER (WHERE s.step < 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step < 10), 0.00)
        FROM sorted AS s
        UNION ALL
        SELECT 2, NULL, 15, NULL, NULL, 'all properties: total expenses',
               coalesce(sum(s.budget) FILTER (WHERE s.step > 10), 0.00),
               coalesce(sum(s.actual) FILTER (WHERE s.step > 10), 0.00)
        FROM sorted AS s
        UNION ALL
        SELECT 2, NULL, 16, NULL, NULL, 'all properties: net income',
               coalesce(sum(CASE WHEN s.step < 10 THEN s.budget ELSE -s.budget END), 0.00),
               coalesce(sum(CASE WHEN s.step < 10 THEN s.actual ELSE -s.actual END), 0.00)
        FROM sorted AS s
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.step, l.name COLLATE "C"
            ))::integer,
           l.label, l.what, l.budget, l.actual, l.actual - l.budget
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_cash_flow(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_cash_flow(p_pmc_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, section text, bank text, item text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_through + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: cash flow is over days, from one through another no earlier'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH cash AS (
        SELECT t.ledger_account_id AS id, b.id AS bank_id,
               b.display_name || ' (' || b.kind || ')' AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        WHERE t.pmc_id = p_pmc_id AND t.kind = 'bank_cash'
    ),
    -- Book cash at a moment: what the bank held, by the books, just before it.
    opening AS (
        SELECT c.bank_id,
               -coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = c.id AND tr.event_at < v_start
               ), 0.00) AS amount
        FROM cash AS c
    ),
    -- Each transfer in the days that moved a bank's cash: in when money came into the bank
    -- (from its cash account to what holds it), out when it left. A move between two banks'
    -- cash is out of one and into the other.
    moves AS (
        SELECT c.bank_id, tr.id AS transfer_id, tr.amount,
               tr.from_account_id = c.id AS came_in,
               o.kind AS other_kind
        FROM cash AS c
        JOIN pgledger_transfers AS tr
          ON (tr.from_account_id = c.id OR tr.to_account_id = c.id)
         AND tr.event_at >= v_start AND tr.event_at < v_end
        JOIN trust_ledger_accounts AS o
          ON o.ledger_account_id = CASE WHEN tr.from_account_id = c.id
                                        THEN tr.to_account_id ELSE tr.from_account_id END
    ),
    named AS (
        SELECT m.bank_id, m.amount, m.came_in,
               CASE
                   WHEN m.other_kind = 'bank_cash' THEN 7
                   WHEN m.came_in AND EXISTS (
                       SELECT o.bank_account_id FROM trust_opening_balances AS o
                       WHERE m.transfer_id = ANY (o.transfer_ids)
                   ) THEN 1
                   WHEN m.came_in AND EXISTS (
                       SELECT x.transfer_id FROM trust_owner_contributions AS x
                       WHERE x.transfer_id = m.transfer_id
                   ) THEN 3
                   WHEN m.came_in AND m.other_kind = 'owner_property' THEN 2
                   WHEN m.came_in AND m.other_kind = 'prepaid_rent' THEN 4
                   WHEN m.came_in AND m.other_kind = 'tenant_deposit' THEN 5
                   WHEN m.came_in THEN 6
                   WHEN m.other_kind = 'vendor_payable' THEN 11
                   WHEN m.other_kind = 'owner_property' AND EXISTS (
                       SELECT x.transfer_id FROM trust_owner_draws AS x
                       WHERE x.transfer_id = m.transfer_id
                   ) THEN 12
                   WHEN m.other_kind = 'owner_property' THEN 13
                   WHEN m.other_kind = 'prepaid_rent' THEN 14
                   WHEN m.other_kind = 'tenant_deposit' THEN 15
                   WHEN m.other_kind = 'pmc_income' THEN 16
                   ELSE 17
               END AS step
        FROM moves AS m
    ),
    labels (step, label) AS (
        VALUES (1, 'in: opening balances carried over'),
               (2, 'in: received for owners'),
               (3, 'in: owner contributions'),
               (4, 'in: received as prepaid rent'),
               (5, 'in: security deposits received'),
               (6, 'in: other'),
               (7, 'moved between trust bank accounts'),
               (11, 'out: bills paid'),
               (12, 'out: owner draws'),
               (13, 'out: paid back from owners'' accounts (returned payments, other)'),
               (14, 'out: paid back from prepaid rent'),
               (15, 'out: security deposits paid out (returned, or kept for owners)'),
               (16, 'out: fees paid to the company'),
               (17, 'out: other')
    ),
    sums AS (
        -- A move between banks is signed: in is more, out is less.
        SELECT n.bank_id, n.step,
               sum(CASE WHEN n.came_in OR n.step <> 7 THEN n.amount ELSE -n.amount END) AS amount
        FROM named AS n
        GROUP BY n.bank_id, n.step
    ),
    totals AS (
        SELECT c.bank_id, c.name, op.amount AS opening,
               coalesce((SELECT sum(n.amount) FROM named AS n
                         WHERE n.bank_id = c.bank_id AND n.came_in), 0.00) AS money_in,
               coalesce((SELECT sum(n.amount) FROM named AS n
                         WHERE n.bank_id = c.bank_id AND NOT n.came_in), 0.00) AS money_out
        FROM cash AS c
        JOIN opening AS op ON op.bank_id = c.bank_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               'PMC' AS part_name, pmc.display_name AS label, NULL::numeric AS value
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_through, 'YYYY-MM-DD')
               || ' UTC', NULL
        UNION ALL
        SELECT 1, t.name, t.bank_id, 0, 'cash', 'opening cash, by the books', t.opening
        FROM totals AS t
        UNION ALL
        SELECT 1, t.name, t.bank_id, s.step, 'cash', l.label, s.amount
        FROM totals AS t
        JOIN sums AS s ON s.bank_id = t.bank_id
        JOIN labels AS l ON l.step = s.step
        UNION ALL
        SELECT 1, t.name, t.bank_id, 30, 'cash', 'total in', t.money_in FROM totals AS t
        UNION ALL
        SELECT 1, t.name, t.bank_id, 31, 'cash', 'total out', t.money_out FROM totals AS t
        UNION ALL
        SELECT 1, t.name, t.bank_id, 32, 'cash', 'closing cash, by the books',
               t.opening + t.money_in - t.money_out
        FROM totals AS t
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'all trust bank accounts', 'opening cash, by the books',
               coalesce((SELECT sum(t.opening) FROM totals AS t), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, s.step, 'all trust bank accounts', l.label, sum(s.amount)
        FROM sums AS s
        JOIN labels AS l ON l.step = s.step
        GROUP BY s.step, l.label
        UNION ALL
        SELECT 2, NULL, NULL, 30, 'all trust bank accounts', 'total in',
               coalesce((SELECT sum(t.money_in) FROM totals AS t), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 31, 'all trust bank accounts', 'total out',
               coalesce((SELECT sum(t.money_out) FROM totals AS t), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 32, 'all trust bank accounts', 'closing cash, by the books',
               coalesce((SELECT sum(t.opening + t.money_in - t.money_out) FROM totals AS t), 0.00)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.bank_name COLLATE "C", l.bank_id, l.step
            ))::integer,
           l.part_name, l.bank_name, l.label, l.value
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_cutover(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_cutover(p_pmc_id uuid, p_bank_account_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_opening trust_opening_balances;
BEGIN
    SELECT o.* INTO v_opening FROM trust_opening_balances AS o
    WHERE o.bank_account_id = p_bank_account_id AND o.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % of PMC % has no opening balances',
            p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT b.key AS account, (b.value #>> '{}')::numeric AS amount,
               t.kind || ': ' || CASE t.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM jsonb_each(v_opening.balances) AS b
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.key
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
    ),
    carried AS (
        SELECT i.* FROM trust_cutover_items AS i WHERE i.bank_account_id = p_bank_account_id
    ),
    lines AS (
        SELECT 0 AS section, 1 AS n, 'PMC' AS item, p.display_name AS detail,
               NULL::numeric AS amount, NULL::text AS sort, NULL::text AS id
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL, NULL,
               NULL
        FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id
        UNION ALL
        SELECT 0, 3, 'cutover', to_char(v_opening.opened_on, 'YYYY-MM-DD')
                   || ' (opening balances at the start of the day, UTC)', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 4, 'entered by', v_opening.entered_by || ' at '
                   || to_char(v_opening.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                   || ' UTC', NULL, NULL, NULL
        UNION ALL
        SELECT 0, 5, 'old system''s book cash', NULL, v_opening.book_cash, NULL, NULL
        UNION ALL
        SELECT 1, 0, 'opening balance', b.holder, b.amount, b.holder, b.account
        FROM balances AS b
        UNION ALL
        SELECT 2, 0, 'opening balances, total', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b), NULL, NULL
        UNION ALL
        SELECT 2, 1, 'difference: total - book cash', NULL,
               (SELECT coalesce(sum(b.amount), 0) FROM balances AS b) - v_opening.book_cash,
               NULL, NULL
        UNION ALL
        SELECT 3, 0, CASE WHEN c.amount < 0 THEN 'outstanding check' ELSE 'deposit in transit'
                     END,
               to_char(c.issued_on, 'YYYY-MM-DD') || ' ' || c.description
                   || coalesce(' (' || c.reference || ')', ''),
               c.amount, to_char(c.issued_on, 'YYYY-MM-DD') || c.description, c.id::text
        FROM carried AS c
        UNION ALL
        SELECT 4, 0, 'bank balance to expect at the cutover',
               'book cash less the items the bank had not cleared',
               v_opening.book_cash - (SELECT coalesce(sum(c.amount), 0) FROM carried AS c),
               NULL, NULL
        UNION ALL
        SELECT 4, 1, 'signed off by', coalesce(
                   (SELECT st.display_name || ' at '
                           || to_char(s.signed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
                           || ' UTC'
                    FROM trust_cutover_signoffs AS s
                    JOIN trust_staff AS st ON st.id = s.signed_by
                    WHERE s.bank_account_id = p_bank_account_id),
                   'not signed off yet'), NULL, NULL, NULL
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.n, l.sort COLLATE "C", l.id COLLATE "C"
           )::integer,
           l.item, l.detail, l.amount
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
    waived AS (
        -- What waivers due by the day take off the charge each one names (migration 0079).
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM trust_charge_waivers AS w
        JOIN trust_charges AS k ON k.id = w.credit_charge_id
        WHERE w.pmc_id = p_pmc_id AND k.due_on <= p_as_of
        GROUP BY w.waived_charge_id
    ),
    settled AS (
        -- Each charge less what was paid of it and what its own waivers take (up to what was
        -- left), and what of its waivers it could not take.
        -- Each charge's place, as trust_pay_rank gives it (written out here: the AI role runs
        -- this report and calls no function but the reports).
        SELECT c.id, c.lease_id, c.due_on, c.kind, c.created_at,
               CASE
                   WHEN c.kind = 'rent' THEN 0
                   WHEN c.kind = 'fee' AND (
                       c.recurring_charge_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1 FROM trust_lease_end_adjustments AS x
                           JOIN trust_charges AS o ON o.id = x.charge_id
                           WHERE x.adjustment_charge_id = c.id
                             AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                       )
                   ) THEN 1
                   ELSE 2
               END AS pay_rank,
               c.amount - p.amount
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0)
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS spare
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN waived AS w ON w.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit'
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, rent first (migration 0093).
        SELECT s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM settled AS s
        WHERE s.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day that waive
        -- nothing, what a waiver could not take off its own charge, and payments by then toward
        -- charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of
                         AND NOT EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                                         WHERE w.credit_charge_id = c.id)
                        THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
               + coalesce(sum(s.spare), 0)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN settled AS s ON s.id = c.id
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
-- Name: trust_report_deposit_return(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_deposit_return(p_pmc_id uuid, p_lease_id uuid) RETURNS TABLE(line integer, item text, detail text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_done trust_deposit_returns;
BEGIN
    SELECT r.* INTO v_done FROM trust_deposit_returns AS r
    WHERE r.lease_id = p_lease_id AND r.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: the deposit of lease % of PMC % has not been returned',
            p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH kinds AS (
        -- Each line with its tenant and the kind of deposit it came from (the transfer's).
        SELECT x.*, tn.display_name || CASE a.deposit_kind
                                          WHEN 'pet' THEN ' (pet deposit)'
                                          WHEN 'key' THEN ' (key deposit)'
                                          ELSE '' END AS whose
        FROM trust_deposit_return_lines AS x
        JOIN trust_tenants AS tn ON tn.id = x.tenant_id
        JOIN pgledger_transfers AS tr ON tr.id = x.transfer_id
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = tr.from_account_id
        WHERE x.lease_id = p_lease_id
    ),
    held AS (
        -- Each tenant's deposit of each kind when it was returned: what was kept and returned.
        SELECT k.whose AS tenant, sum(k.amount) AS amount
        FROM kinds AS k
        GROUP BY k.tenant_id, k.whose
    ),
    lines AS (
        SELECT 0 AS section, 1 AS n, 'PMC' AS item, p.display_name AS detail,
               NULL::numeric AS amount, NULL::text AS sort, NULL::bigint AS no
        FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 2, 'property and unit', pr.display_name || ' / ' || u.display_name, NULL,
               NULL, NULL
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.id = p_lease_id
        UNION ALL
        SELECT 0, 3, 'tenants', string_agg(tn.display_name, '; '
                                           ORDER BY tn.display_name COLLATE "C"), NULL, NULL,
               NULL
        FROM trust_lease_tenants AS lt JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
        WHERE lt.lease_id = p_lease_id
        UNION ALL
        SELECT 0, 4, 'lease', to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                   || to_char(l.ends_on, 'YYYY-MM-DD'), NULL, NULL, NULL
        FROM trust_leases AS l WHERE l.id = p_lease_id
        UNION ALL
        -- Each lease the tenants moved from, oldest first.
        SELECT 0, 5, 'moved from', pr.display_name || ' / ' || u.display_name || ', '
                   || to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                   || to_char(l.ends_on, 'YYYY-MM-DD'),
               NULL, to_char(l.starts_on, 'YYYY-MM-DD'), NULL
        FROM trust_lease_moved_from(p_lease_id) AS m
        JOIN trust_leases AS l ON l.id = m.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 1, 0, 'deposit held', h.tenant, h.amount, h.tenant, NULL
        FROM held AS h
        UNION ALL
        -- Each charge once, with what all the deposits paid of it; one of a lease moved from
        -- names its home.
        SELECT 2, 0, 'kept for', to_char(c.due_on, 'YYYY-MM-DD') || ' ' || c.kind
                   || coalesce(': ' || c.memo, '')
                   || CASE WHEN c.lease_id = p_lease_id THEN ''
                           ELSE ' (' || pr.display_name || ' / ' || u.display_name || ')' END,
               sum(x.amount), NULL, min(x.line_no)
        FROM trust_deposit_return_lines AS x
        JOIN trust_charges AS c ON c.id = x.charge_id
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE x.lease_id = p_lease_id
        GROUP BY c.id, c.due_on, c.kind, c.memo, c.lease_id, pr.display_name, u.display_name
        UNION ALL
        SELECT 3, 0, 'deposits held, total', NULL, v_done.held, NULL, NULL
        UNION ALL
        SELECT 3, 1, 'kept, total', NULL, v_done.kept, NULL, NULL
        UNION ALL
        SELECT 4, 0, 'returned to', k.whose, k.amount, NULL, k.line_no
        FROM kinds AS k
        WHERE k.charge_id IS NULL
        UNION ALL
        SELECT 5, 0, 'returned, total', NULL, v_done.returned, NULL, NULL
        UNION ALL
        SELECT 5, 1, 'returned on', to_char(v_done.returned_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')
                   || ' (UTC), by ' || coalesce(
                       (SELECT s.display_name FROM trust_staff AS s
                        WHERE s.id = v_done.returned_by), 'staff not recorded'),
               NULL, NULL, NULL
    )
    SELECT row_number() OVER (ORDER BY l.section, l.n, l.sort COLLATE "C", l.no)::integer,
           l.item, l.detail, l.amount
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
-- Name: trust_report_income_statement(uuid, uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_income_statement(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: an income statement runs from a day through a day no earlier, not % to %',
            p_from, p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := p_from::timestamp AT TIME ZONE 'UTC';
    v_end := (p_through + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH schedule_e (n, label) AS (
        VALUES (3, 'Rents received'), (5, 'Advertising'), (6, 'Auto and travel'),
               (7, 'Cleaning and maintenance'), (8, 'Commissions'), (9, 'Insurance'),
               (10, 'Legal and other professional fees'), (11, 'Management fees'),
               (12, 'Mortgage interest paid to banks, etc.'), (13, 'Other interest'),
               (14, 'Repairs'), (15, 'Supplies'), (16, 'Taxes'), (17, 'Utilities'),
               (18, 'Depreciation expense or depletion'), (19, 'Other')
    ),
    incomes (part, label) AS (
        VALUES (1, 'income: rent'), (2, 'income: recurring charges'), (3, 'income: late fees'),
               (4, 'income: NSF fees'), (5, 'income: other charges')
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge's money is: rent, a recurring charge (pet rent, parking), a late fee, an
    -- NSF fee, or another charge. A lease end's adjustment of a rent or recurring charge
    -- (migration 0051) is what it adjusts.
    classes AS (
        SELECT c.id,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = adj.charge_id
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = c.id
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = c.id
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Collected from tenants in the days: payments matched to charges, less what bounced back,
    -- as the year-end package counts them (migration 0039).
    collected AS (
        SELECT tr.to_account_id AS account, k.part, cp.amount
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        JOIN classes AS k ON k.id = cp.charge_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, k.part, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        JOIN classes AS k ON k.id = r.charge_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A housing authority's part of the rent, as it paid it (migration 0095): rent.
        SELECT tr.to_account_id, 1, ap.amount
        FROM trust_assistance_payments AS ap
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    fees AS (
        SELECT f.ledger_account_id AS account, 0 AS ord, tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 1, tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees.
        SELECT tr.from_account_id, 0, tr.amount
        FROM trust_bill_markup_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills, when their money left the owner's account (set aside to pay them): a split bill
    -- (migration 0069) by its lines, each in its expense account, which come to the bill.
    spent AS (
        SELECT tr.from_account_id AS account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, tr.amount) AS amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        LEFT JOIN categorized AS cz ON cz.bill_id = b.bill_id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM collected AS c
                         WHERE c.account = a.account AND c.part = i.part), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        SELECT a.account, 3, f.ord,
               CASE f.ord WHEN 0 THEN 'expense: management fees' ELSE 'expense: leasing fees' END,
               CASE f.ord WHEN 0 THEN 11 ELSE 8 END,
               coalesce((SELECT sum(x.amount) FROM fees AS x
                         WHERE x.account = a.account AND x.ord = f.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (0), (1)) AS f (ord)
        UNION ALL
        SELECT s.account, 3, coalesce(ea.schedule_e_line, 99),
               CASE WHEN ea.id IS NULL THEN 'expense: bills not in an expense account'
                    ELSE 'expense: ' || ea.name END,
               ea.schedule_e_line,
               sum(s.amount)
        FROM spent AS s
        JOIN accounts AS a ON a.account = s.account
        LEFT JOIN trust_expense_accounts AS ea ON ea.id = s.expense
        GROUP BY s.account, ea.id, ea.name, ea.schedule_e_line
    ),
    sums AS (
        SELECT i.account,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 1), 0) AS income,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 3), 0) AS expenses
        FROM items AS i
        GROUP BY i.account
    ),
    -- The same by Schedule E line (group 6): rents received, and each expense line with money
    -- on it; bills in no expense account are on no line until they are given one.
    schedule AS (
        SELECT i.account, 6 AS grp, i.sched AS ord,
               'Schedule E line ' || i.sched || ': ' || e.label AS item, i.sched,
               sum(i.total) AS total
        FROM items AS i
        JOIN schedule_e AS e ON e.n = i.sched
        GROUP BY i.account, i.sched, e.label
        HAVING i.sched = 3 OR sum(i.total) <> 0
        UNION ALL
        SELECT i.account, 6, 99, 'Schedule E: on no line, bills not in an expense account',
               NULL::integer, sum(i.total)
        FROM items AS i
        WHERE i.grp = 3 AND i.sched IS NULL
        GROUP BY i.account
    ),
    rows AS (
        SELECT i.account, i.grp, i.ord, i.item, i.sched, i.total FROM items AS i
        UNION ALL
        SELECT s.account, 2, 0, 'total income', NULL::integer, s.income FROM sums AS s
        UNION ALL
        SELECT s.account, 4, 0, 'total expenses', NULL::integer, s.expenses FROM sums AS s
        UNION ALL
        SELECT s.account, 5, 0, 'net income', NULL::integer, s.income - s.expenses
        FROM sums AS s
        UNION ALL
        SELECT h.account, h.grp, h.ord, h.item, h.sched, h.total FROM schedule AS h
    ),
    lines AS (
        -- (section, property, account, group, order, item) orders the statement; names sort
        -- by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 0 AS grp, 1 AS ord,
               'PMC' AS item, pmc.display_name AS label, NULL::integer AS sched,
               NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 2, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 3, 'period',
               p_from::text || ' to ' || p_through::text || ' UTC, cash basis', NULL, NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.grp, r.ord, r.item, a.name, r.sched, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.grp, r.ord, 'all properties: ' || r.item, NULL, r.sched,
               sum(r.total)
        FROM rows AS r
        GROUP BY r.grp, r.ord, r.item, r.sched
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.grp, l.ord,
                        l.item COLLATE "C"
           )::integer,
           -- The PMC's, owner's and period's names sit in the property column of the first
           -- lines.
           l.label,
           l.item,
           l.sched,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_inspection(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_inspection(p_pmc_id uuid, p_lease_id uuid) RETURNS TABLE(line integer, item text, room text, move_in text, move_out text, changed boolean, detail text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_in trust_inspections;
    v_out trust_inspections;
BEGIN
    IF NOT EXISTS (
        SELECT l.id FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The latest of each kind: by the day it was done, then as recorded.
    SELECT i.* INTO v_in FROM trust_inspections AS i
    WHERE i.lease_id = p_lease_id AND i.kind = 'move-in'
    ORDER BY i.inspected_on DESC, i.created_at DESC, i.id
    LIMIT 1;
    SELECT i.* INTO v_out FROM trust_inspections AS i
    WHERE i.lease_id = p_lease_id AND i.kind = 'move-out'
    ORDER BY i.inspected_on DESC, i.created_at DESC, i.id
    LIMIT 1;

    RETURN QUERY
    WITH rooms AS (
        -- A room as the move-in inspection names it, else as the move-out one does.
        SELECT lower(x.room) AS key,
               coalesce(max(x.room) FILTER (WHERE x.inspection_id = v_in.id),
                        max(x.room) FILTER (WHERE x.inspection_id = v_out.id)) AS name,
               min(x.line) FILTER (WHERE x.inspection_id = v_in.id) AS in_line,
               min(x.line) FILTER (WHERE x.inspection_id = v_out.id) AS out_line,
               max(x.condition) FILTER (WHERE x.inspection_id = v_in.id) AS at_in,
               max(x.condition) FILTER (WHERE x.inspection_id = v_out.id) AS at_out,
               max(x.note) FILTER (WHERE x.inspection_id = v_in.id) AS in_note,
               max(x.note) FILTER (WHERE x.inspection_id = v_out.id) AS out_note
        FROM trust_inspection_items AS x
        WHERE x.inspection_id IN (v_in.id, v_out.id)
        GROUP BY lower(x.room)
    ),
    lines AS (
        SELECT 0 AS part, 0 AS step, NULL::integer AS in_line, NULL::integer AS out_line,
               'PMC' AS what, pmc.display_name AS name, NULL::text AS at_in, NULL::text AS at_out,
               NULL::boolean AS moved, NULL::text AS said
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 1, NULL, NULL, 'lease', pr.display_name || ', ' || u.display_name, NULL,
               NULL, NULL,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id)
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.id = p_lease_id
        UNION ALL
        -- The days each was done, or that none was.
        SELECT 0, 2, NULL, NULL, 'inspected', NULL,
               coalesce(to_char(v_in.inspected_on, 'YYYY-MM-DD'), 'no move-in inspection'),
               coalesce(to_char(v_out.inspected_on, 'YYYY-MM-DD'), 'no move-out inspection'),
               NULL, NULL
        UNION ALL
        SELECT 0, 3, NULL, NULL, 'notes', NULL, v_in.notes, v_out.notes, NULL, NULL
        WHERE v_in.notes IS NOT NULL OR v_out.notes IS NOT NULL
        UNION ALL
        -- Rooms in the move-in inspection's order, then rooms seen only at move-out in theirs.
        SELECT 1, 0, r.in_line, r.out_line, 'room', r.name, r.at_in, r.at_out,
               CASE WHEN r.at_in IS NULL OR r.at_out IS NULL THEN NULL
                    ELSE r.at_in <> r.at_out END,
               nullif(concat_ws('; ', 'move-in: ' || r.in_note, 'move-out: ' || r.out_note), '')
        FROM rooms AS r
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.step, l.in_line NULLS LAST, l.out_line, l.name COLLATE "C"
            ))::integer,
           l.what, l.name, l.at_in, l.at_out, l.moved, l.said
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_lease_expirations(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_lease_expirations(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, tenants text, ends_on date, days_left integer, bucket text, monthly_rent numeric, renewal text)
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: lease expirations are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH offers AS (
        -- Each renewal offer made by the day and not voided by then, and how far along it was.
        SELECT o.renews_lease_id AS lease_id,
               CASE
                   WHEN EXISTS (
                       SELECT c.offer_id FROM trust_lease_offer_completions AS c
                       WHERE c.offer_id = o.id AND c.completed_at < v_cutoff
                   ) THEN 4
                   WHEN EXISTS (
                       SELECT r.offer_id FROM trust_signature_requests AS r
                       WHERE r.offer_id = o.id AND r.sent_at < v_cutoff
                   ) AND NOT EXISTS (
                       SELECT s.position FROM trust_lease_offer_signers AS s
                       WHERE s.offer_id = o.id AND NOT EXISTS (
                           SELECT g.position FROM trust_signatures AS g
                           WHERE g.offer_id = o.id AND g.position = s.position
                             AND g.recorded_at < v_cutoff
                       )
                   ) THEN 3
                   WHEN EXISTS (
                       SELECT r.offer_id FROM trust_signature_requests AS r
                       WHERE r.offer_id = o.id AND r.sent_at < v_cutoff
                   ) THEN 2
                   ELSE 1
               END AS stage
        FROM trust_lease_offers AS o
        WHERE o.pmc_id = p_pmc_id AND o.renews_lease_id IS NOT NULL
          AND o.offered_at < v_cutoff
          -- An offer voided by then is no renewal out.
          AND NOT EXISTS (
              SELECT v.offer_id FROM trust_lease_offer_voids AS v
              WHERE v.offer_id = o.id AND v.recorded_at < v_cutoff
          )
    ),
    ending AS (
        SELECT l.id, pr.display_name AS place, u.display_name AS unit_name,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS names,
               l.ends_on AS last_day, l.ends_on - p_as_of AS left_days,
               CASE WHEN l.ends_on - p_as_of <= 30 THEN 1
                    WHEN l.ends_on - p_as_of <= 60 THEN 2
                    ELSE 3 END AS grp,
               coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                WHERE rc.lease_id = l.id AND rc.effective_on <= p_as_of
                                ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS rent,
               coalesce((SELECT max(f.stage) FROM offers AS f WHERE f.lease_id = l.id), 0) AS stage
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.pmc_id = p_pmc_id AND l.ends_on BETWEEN p_as_of AND p_as_of + 90
    ),
    spans (grp, label) AS (VALUES (1, '0-30'), (2, '31-60'), (3, '61-90')),
    lines AS (
        SELECT 0 AS section, 0 AS grp, 1 AS step, NULL::date AS last_day, NULL::text AS place,
               NULL::text AS unit_name, NULL::uuid AS id, 'PMC' AS item,
               pmc.display_name AS head, NULL::text AS names, NULL::integer AS left_days,
               NULL::numeric AS rent, NULL::text AS status
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 0, 2, NULL, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC; leases ending in the next '
               || '90 days', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, e.grp, 0, e.last_day, e.place, e.unit_name, e.id, 'lease', e.place, e.names,
               e.left_days, e.rent,
               (ARRAY['none', 'drafted', 'sent', 'signed', 'renewed'])[e.stage + 1]
        FROM ending AS e
        UNION ALL
        SELECT 1, g.grp, 1, NULL, NULL, NULL, NULL, 'group total', NULL, NULL, NULL,
               coalesce(sum(e.rent), 0.00),
               count(e.id) FILTER (WHERE e.stage < 2) || ' of ' || count(e.id)
               || ' with no renewal offer out'
        FROM spans AS g LEFT JOIN ending AS e ON e.grp = g.grp
        GROUP BY g.grp
        UNION ALL
        SELECT 2, 0, 0, NULL, NULL, NULL, NULL, 'total', NULL, NULL, NULL,
               coalesce(sum(e.rent), 0.00),
               count(e.id) FILTER (WHERE e.stage < 2) || ' of ' || count(e.id)
               || ' with no renewal offer out'
        FROM ending AS e
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.grp, l.step, l.last_day, l.place COLLATE "C",
                        l.unit_name COLLATE "C", l.id
           )::integer,
           l.item,
           -- The PMC's name and the day sit in the property column of the first two lines.
           l.head,
           l.unit_name,
           l.names,
           l.last_day,
           l.left_days,
           (SELECT g.label FROM spans AS g WHERE g.grp = l.grp),
           l.rent,
           l.status
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
               -- trust_unpaid_bills (a bill with its markup), written out: a report runs with the caller's grants, and
               -- the app and the AI may call reports, not helpers.
               coalesce((
                   SELECT sum(b.amount + coalesce(m.amount, 0))
                   FROM trust_bills AS b
                   LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
                   WHERE b.ledger_account_id = t.ledger_account_id AND b.bill_date <= p_as_of
                     AND NOT EXISTS (
                         SELECT p.bill_id
                         FROM trust_bill_payments AS p
                         JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                         WHERE p.bill_id = b.id AND p.step = 'set_aside'
                           AND tr.event_at < v_cutoff
                     )
                     AND NOT EXISTS (
                         SELECT d.bill_id FROM trust_bill_declines AS d
                         WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
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
-- Name: trust_report_owner_directory(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_owner_directory(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, owner text, property text, bank text, fee_percent numeric, minimum_fee numeric, flat_fee numeric, reserve numeric, approval_limit numeric, balance numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: an owner directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT o.id AS owner_id, o.display_name AS who, pr.display_name AS place,
               b.display_name || ' (' || b.kind || ')' AS bank_name, t.ledger_account_id,
               -- The agreement in force on the day, as the fee and draw functions take it.
               (SELECT a FROM trust_management_agreements AS a
                WHERE a.ledger_account_id = t.ledger_account_id AND a.starts_on <= p_as_of
                ORDER BY a.starts_on DESC
                LIMIT 1) AS terms,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS held
        FROM trust_owners AS o
        JOIN trust_ledger_accounts AS t ON t.owner_id = o.id AND t.kind = 'owner_property'
        JOIN trust_properties AS pr ON pr.id = t.property_id
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        WHERE o.pmc_id = p_pmc_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS owner_id, NULL::text AS place,
               NULL::text AS account_id, 'PMC' AS what, pmc.display_name AS label,
               NULL::text AS bank_name, NULL::trust_management_agreements AS terms,
               NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL
        UNION ALL
        -- No agreement in force leaves its terms empty: unknown, not zero.
        SELECT 1, x.who, x.owner_id, x.place, x.ledger_account_id,
               CASE WHEN x.terms IS NULL THEN 'property, no agreement in force' ELSE 'property'
               END,
               x.who, x.bank_name, x.terms, x.held
        FROM accounts AS x
        UNION ALL
        SELECT 1, o.display_name, o.id, NULL, NULL, 'owner, no property account',
               o.display_name, NULL, NULL, NULL
        FROM trust_owners AS o
        WHERE o.pmc_id = p_pmc_id
          AND NOT EXISTS (SELECT x.owner_id FROM accounts AS x WHERE x.owner_id = o.id)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.who COLLATE "C", l.owner_id, l.place COLLATE "C",
                         l.account_id
            ))::integer,
           l.what, l.label, l.place, l.bank_name, (l.terms).fee_percent, (l.terms).minimum_fee,
           (l.terms).flat_fee, (l.terms).reserve, (l.terms).approval_limit, l.held
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
-- Name: trust_report_owner_year_end(uuid, uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_owner_year_end(p_pmc_id uuid, p_owner_id uuid, p_year integer) RETURNS TABLE(line integer, property text, item text, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: a year-end package is for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH labels AS (
        SELECT v.part, v.label
        FROM (VALUES
            (0, 'opening balance'),
            (1, 'collected from tenants'),
            (2, 'management fees'),
            (3, 'leasing fees'),
            (4, 'bills paid'),
            (5, 'owner draws'),
            (6, 'owner contributions'),
            (7, 'other'),
            (9, 'closing balance')
        ) AS v (part, label)
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What moved each account in the year, by part, signed as it moved the account.
    moved AS (
        -- 1: collected from tenants: payments matched to the leases' charges...
        SELECT tr.to_account_id AS account, 1 AS part, cp.amount AS change
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...less what of them bounced back...
        SELECT tr.from_account_id, 1, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...and a housing authority's part of the rent, as it paid it (migration 0095).
        SELECT tr.to_account_id, 1, ap.amount
        FROM trust_assistance_payments AS ap
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 2, -tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 3, -tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 3 ELSE 2 END, -tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees.
        SELECT tr.from_account_id, 2, -tr.amount
        FROM trust_bill_markup_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- 4: bills, when their money left the owner's account (set aside to pay them).
        SELECT tr.from_account_id, 4, -tr.amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT d.ledger_account_id, 5, -tr.amount
        FROM trust_owner_draws AS d
        JOIN pgledger_transfers AS tr ON tr.id = d.transfer_id
        WHERE d.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT c.ledger_account_id, 6, tr.amount
        FROM trust_owner_contributions AS c
        JOIN pgledger_transfers AS tr ON tr.id = c.transfer_id
        WHERE c.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    balances AS (
        SELECT a.account, a.name,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_start), 0) AS opening,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_end), 0) AS closing
        FROM accounts AS a
        LEFT JOIN pgledger_entries AS e ON e.account_id = a.account
        LEFT JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        GROUP BY a.account, a.name
    ),
    parts AS (
        SELECT b.account, b.name, p.part, p.label,
               CASE p.part
                   WHEN 0 THEN b.opening
                   WHEN 9 THEN b.closing
                   WHEN 7 THEN b.closing - b.opening - coalesce(
                       (SELECT sum(m.change) FROM moved AS m WHERE m.account = b.account), 0)
                   ELSE coalesce(
                       (SELECT sum(m.change) FROM moved AS m
                        WHERE m.account = b.account AND m.part = p.part), 0)
               END AS total
        FROM balances AS b
        CROSS JOIN labels AS p
    ),
    lines AS (
        -- (section, property, account, part) orders the package; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS part,
               'PMC' AS item, pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'year',
               p_year::text || ', January 1 to December 31 UTC', NULL
        UNION ALL
        SELECT 1, p.name, p.account, p.part, p.label, p.name, p.total
        FROM parts AS p
        UNION ALL
        SELECT 2, NULL, NULL, x.part, 'all properties: ' || x.label, NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = x.part), 0)
        FROM labels AS x
        UNION ALL
        SELECT 3, NULL, NULL, 0, '1099-MISC rents (box 1)', NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = 1), 0)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.part
           )::integer,
           -- The PMC's, owner's and year's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
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
        SELECT l.id, l.unit_id, l.starts_on, l.ends_on,
               -- The rent in force on the day (migration 0061).
               coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                WHERE rc.lease_id = l.id AND rc.effective_on <= p_as_of
                                ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS monthly_rent,
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
-- Name: trust_report_renters_insurance(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_renters_insurance(p_pmc_id uuid, p_on date) RETURNS TABLE(line integer, lease_id uuid, property text, unit text, tenants text, required boolean, minimum_liability numeric, carrier text, policy_number text, starts_on date, expires_on date, liability_limit numeric, standing text, below_minimum boolean)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH leases AS (
        SELECT l.id, pr.display_name AS property, u.display_name AS unit,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS tenants,
               terms.required, terms.minimum_liability,
               p.carrier, p.policy_number, p.starts_on, p.expires_on, p.liability_limit
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN LATERAL (
            SELECT r.required, r.minimum_liability FROM trust_renters_insurance_terms AS r
            WHERE r.lease_id = l.id AND r.starts_on <= p_on
            ORDER BY r.starts_on DESC, r.created_at DESC, r.id DESC
            LIMIT 1
        ) AS terms ON true
        LEFT JOIN LATERAL (
            SELECT i.carrier, i.policy_number, i.starts_on, i.expires_on, i.liability_limit
            FROM trust_renters_insurance AS i
            WHERE i.lease_id = l.id AND i.starts_on <= p_on
            ORDER BY i.expires_on DESC, i.created_at DESC, i.id DESC
            LIMIT 1
        ) AS p ON true
        WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_on
          AND (l.ends_on IS NULL OR p_on <= l.ends_on)
    )
    SELECT (row_number() OVER (
                ORDER BY x.property COLLATE "C", x.unit COLLATE "C", x.id
            ))::integer,
           x.id, x.property, x.unit, x.tenants, coalesce(x.required, false),
           x.minimum_liability, x.carrier, x.policy_number, x.starts_on, x.expires_on,
           x.liability_limit,
           CASE
               WHEN x.expires_on IS NULL THEN 'none on file'
               WHEN x.expires_on < p_on THEN 'expired'
               WHEN x.expires_on = p_on THEN 'expires today'
               WHEN x.expires_on - p_on <= 30 THEN
                   'expires in ' || (x.expires_on - p_on) || ' day'
                   || CASE WHEN x.expires_on - p_on = 1 THEN '' ELSE 's' END
               ELSE 'current'
           END,
           x.minimum_liability IS NOT NULL AND x.expires_on IS NOT NULL
               AND coalesce(x.liability_limit, 0) < x.minimum_liability
    FROM leases AS x
    ORDER BY 1
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
        SELECT t.ledger_account_id AS id, t.kind, t.deposit_kind, t.bank_account_id,
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
        -- A tenant's security deposit first, then their key and pet deposits.
        SELECT 1, b.bank_name, b.bank_account_id, 0, b.tenant_name,
               CASE b.deposit_kind WHEN 'key' THEN '1' WHEN 'pet' THEN '2' ELSE '0' END || b.id,
               CASE b.deposit_kind WHEN 'pet' THEN 'pet deposit' WHEN 'key' THEN 'key deposit'
                                   ELSE 'deposit' END,
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
-- Name: trust_report_tenant_directory(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_tenant_directory(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, tenant text, property text, unit text, lease_starts date, lease_ends date, monthly_rent numeric, co_tenants text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT t.id, t.display_name AS who, coalesce(lp.display_name, tp.display_name) AS place,
               lu.display_name AS unit_name, l.lease_id, l.starts_on, l.ends_on, l.rent,
               coalesce(l.standing, 4) AS standing
        FROM trust_tenants AS t
        JOIN trust_properties AS tp ON tp.id = t.property_id
        -- Their lease in effect on the day; or else the next one; or else the last one.
        LEFT JOIN LATERAL (
            SELECT l.id AS lease_id, l.unit_id, l.starts_on, l.ends_on,
                   coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                    WHERE rc.lease_id = l.id AND rc.effective_on <= p_as_of
                                    ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS rent,
                   CASE WHEN l.starts_on <= p_as_of
                             AND (l.ends_on IS NULL OR l.ends_on >= p_as_of) THEN 1
                        WHEN l.starts_on > p_as_of THEN 2
                        ELSE 3 END AS standing
            FROM trust_lease_tenants AS lt
            JOIN trust_leases AS l ON l.id = lt.lease_id
            WHERE lt.tenant_id = t.id
            ORDER BY standing,
                     CASE WHEN l.starts_on > p_as_of THEN l.starts_on - p_as_of
                          WHEN l.ends_on < p_as_of THEN p_as_of - l.ends_on
                          ELSE 0 END,
                     l.starts_on, l.id
            LIMIT 1
        ) AS l ON true
        LEFT JOIN trust_units AS lu ON lu.id = l.unit_id
        LEFT JOIN trust_properties AS lp ON lp.id = lu.property_id
        WHERE t.pmc_id = p_pmc_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::text AS who,
               NULL::uuid AS tenant_id, 0 AS step, 'PMC' AS what, pmc.display_name AS label,
               NULL::date AS starts_on, NULL::date AS ends_on, NULL::numeric AS rent,
               NULL::text AS others
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, x.place, x.unit_name, x.who, x.id, 0,
               (ARRAY['current lease', 'next lease', 'last lease', 'no lease'])[x.standing],
               x.who, x.starts_on, x.ends_on, x.rent,
               (SELECT string_agg(o.display_name, ', ' ORDER BY o.display_name COLLATE "C", o.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS o ON o.id = lt.tenant_id
                WHERE lt.lease_id = x.lease_id AND lt.tenant_id <> x.id)
        FROM tenants AS x
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C" NULLS LAST,
                         l.who COLLATE "C", l.tenant_id, l.step
            ))::integer,
           l.what, l.label, l.place, l.unit_name, l.starts_on, l.ends_on, l.rent, l.others
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
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rec trust_reconciliations;
    v_through date;
    v_unknown text;
    v_journal numeric;
    v_ledgers numeric;
    v_in_transit numeric;
    v_outstanding numeric;
    v_items integer := 0;
BEGIN
    SELECT r.* INTO v_rec FROM trust_reconciliations AS r
    WHERE r.id = p_reconciliation_id AND r.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- The books at the period's end: the account's cash, and what it holds for everyone.
    SELECT coalesce(-sum(e.amount) FILTER (WHERE t.kind = 'bank_cash'), 0),
           coalesce(sum(e.amount) FILTER (WHERE t.kind <> 'bank_cash'), 0)
    INTO v_journal, v_ledgers
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end;

    -- What had not cleared the bank by the period's last day, from the imported statements and
    -- their matches, as the bank reconciliation shows it.
    v_through := (v_rec.period_end AT TIME ZONE 'UTC')::date - 1;
    IF (v_through + 1)::timestamp AT TIME ZONE 'UTC' <> v_rec.period_end THEN
        v_unknown := 'not known: the period does not end at midnight (UTC)';
    ELSIF NOT EXISTS (
        SELECT s.id FROM trust_bank_statements AS s
        WHERE s.bank_account_id = v_rec.bank_account_id
          AND v_through BETWEEN s.first_day AND s.last_day
    ) THEN
        v_unknown := 'not known: no imported bank statement covers '
            || to_char(v_through, 'YYYY-MM-DD');
    ELSE
        SELECT max(b.amount) FILTER (WHERE b.item = 'deposits in transit'),
               max(b.amount) FILTER (WHERE b.item = 'outstanding withdrawals')
        INTO v_in_transit, v_outstanding
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b;
    END IF;

    RETURN QUERY
    SELECT 1, 'PMC', p.display_name, NULL::numeric
    FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id
    UNION ALL
    SELECT 2, 'trust bank account', b.display_name || ' (' || b.kind || ')', NULL
    FROM trust_bank_accounts AS b WHERE b.id = v_rec.bank_account_id
    UNION ALL
    SELECT 3, 'period',
           to_char(v_rec.period_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' to '
           || to_char(v_rec.period_end AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 4, 'prepared by', v_rec.prepared_by, NULL
    UNION ALL
    SELECT 5, 'approved by',
           v_rec.approved_by || ' at '
           || to_char(v_rec.approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC',
           NULL
    UNION ALL
    SELECT 6, 'bank statement balance', NULL, v_rec.statement_balance
    UNION ALL
    SELECT 7, 'trust journal (book cash)', NULL, v_journal
    UNION ALL
    SELECT 8, 'beneficiary ledgers', NULL, v_ledgers
    UNION ALL
    SELECT 9, 'difference: bank statement - trust journal', NULL,
           v_rec.statement_balance - v_journal
    UNION ALL
    SELECT 10, 'difference: trust journal - beneficiary ledgers', NULL, v_journal - v_ledgers
    UNION ALL
    SELECT 11, 'deposits in transit', v_unknown, v_in_transit
    UNION ALL
    SELECT 12, 'outstanding withdrawals', v_unknown, v_outstanding
    UNION ALL
    SELECT 13, 'adjusted bank balance', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding
    UNION ALL
    SELECT 14, 'difference: adjusted bank balance - trust journal', v_unknown,
           v_rec.statement_balance + v_in_transit + v_outstanding - v_journal
    ORDER BY 1;

    -- Each item that had not cleared, and each bank line the books lack, oldest first.
    IF v_unknown IS NULL THEN
        RETURN QUERY
        SELECT (14 + row_number() OVER (ORDER BY b.line))::integer, b.item, b.detail, b.amount
        FROM trust_report_bank_reconciliation(p_pmc_id, v_rec.bank_account_id, v_through) AS b
        WHERE b.item IN (
            'deposit in transit', 'outstanding withdrawal', 'bank line not in the books'
        )
        ORDER BY 1;
        GET DIAGNOSTICS v_items = ROW_COUNT;
    END IF;

    -- Each beneficiary ledger in the account at the period's end, in byte order.
    RETURN QUERY
    WITH balances AS (
        SELECT t.ledger_account_id, t.kind, t.owner_id, t.property_id, t.tenant_id, t.vendor_id,
               sum(e.amount) AS balance
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = v_rec.bank_account_id AND tr.event_at < v_rec.period_end
          AND t.kind <> 'bank_cash'
        GROUP BY t.ledger_account_id
    ),
    ledgers AS (
        SELECT b.kind || ': ' || CASE b.kind
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder,
               b.balance,
               b.ledger_account_id
        FROM balances AS b
        CROSS JOIN trust_pmcs AS pmc
        LEFT JOIN trust_owners AS o ON o.id = b.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = b.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = b.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
        WHERE pmc.pmc_id = p_pmc_id
    )
    SELECT (14 + v_items + row_number() OVER (
               ORDER BY ld.holder COLLATE "C", ld.ledger_account_id
           ))::integer,
           'ledger', ld.holder, ld.balance
    FROM ledgers AS ld
    ORDER BY 1;
END;
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
-- Name: trust_report_trust_account_signers(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_trust_account_signers(p_pmc_id uuid, p_on date) RETURNS TABLE(line integer, bank_account_id uuid, bank_account text, staff_id uuid, signer text, role text, license_number text, bond_amount numeric, bond_expires_on date, authorized_on date, standing text)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH latest AS (
        SELECT DISTINCT ON (s.bank_account_id, s.staff_id) s.*
        FROM trust_account_signers AS s
        WHERE s.pmc_id = p_pmc_id AND s.authorized_on <= p_on
        ORDER BY s.bank_account_id, s.staff_id, s.created_at DESC, s.id DESC
    ),
    rows AS (
        SELECT b.id AS bank_id, b.display_name AS bank, l.staff_id, st.display_name AS signer,
               l.role, l.license_number, l.bond_amount, l.bond_expires_on, l.authorized_on,
               CASE
                   WHEN l.license_number IS NOT NULL THEN 'licensed'
                   WHEN l.bond_expires_on < p_on THEN 'bond ran out'
                   WHEN l.bond_expires_on - p_on <= 30 THEN
                       'bond runs out in ' || (l.bond_expires_on - p_on) || ' day'
                       || CASE WHEN l.bond_expires_on - p_on = 1 THEN '' ELSE 's' END
                   ELSE 'bonded'
               END AS standing,
               CASE l.role WHEN 'broker' THEN 0 ELSE 1 END AS rank
        FROM trust_bank_accounts AS b
        JOIN latest AS l ON l.bank_account_id = b.id AND l.granted
        JOIN trust_staff AS st ON st.id = l.staff_id
        WHERE b.pmc_id = p_pmc_id
        UNION ALL
        SELECT b.id, b.display_name, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
               'no broker on file', 0
        FROM trust_bank_accounts AS b
        WHERE b.pmc_id = p_pmc_id
          AND NOT EXISTS (
              SELECT 1 FROM latest AS l
              WHERE l.bank_account_id = b.id AND l.role = 'broker' AND l.granted
          )
    )
    SELECT (row_number() OVER (
                ORDER BY r.bank COLLATE "C", r.bank_id, r.rank, r.signer COLLATE "C", r.staff_id
            ))::integer,
           r.bank_id, r.bank, r.staff_id, r.signer, r.role, r.license_number, r.bond_amount,
           r.bond_expires_on, r.authorized_on, r.standing
    FROM rows AS r
    ORDER BY 1
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
          -- Not a bill its owner declined by the day: it won't be paid.
          AND NOT EXISTS (
              SELECT d.bill_id FROM trust_bill_declines AS d
              WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
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
-- Name: trust_report_vacancy(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_vacancy(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, vacant_since date, days_vacant integer, last_rent numeric, next_lease_starts date)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vacancy is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH units AS (
        SELECT u.id, pr.display_name AS place, u.display_name AS unit_name,
               EXISTS (
                   SELECT l.id FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.starts_on <= p_as_of
                     AND (l.ends_on IS NULL OR l.ends_on >= p_as_of)
               ) AS occupied,
               last.ends_on + 1 AS since,
               last.monthly_rent AS rent,
               (SELECT min(l.starts_on) FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on > p_as_of) AS next_start
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN LATERAL (
            SELECT l.ends_on,
                   coalesce((SELECT rc.monthly_rent FROM trust_rent_changes AS rc
                                    WHERE rc.lease_id = l.id AND rc.effective_on <= l.ends_on
                                    ORDER BY rc.effective_on DESC LIMIT 1), l.monthly_rent) AS monthly_rent
            FROM trust_leases AS l
            WHERE l.unit_id = u.id AND l.ends_on < p_as_of
            ORDER BY l.ends_on DESC
            LIMIT 1
        ) AS last ON true
        WHERE u.pmc_id = p_pmc_id
    ),
    counted AS (
        SELECT count(*) AS total, count(*) FILTER (WHERE o.occupied) AS occupied FROM units AS o
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::uuid AS unit_id,
               0 AS step, 'PMC' AS what, pmc.display_name AS label, NULL::date AS since,
               NULL::integer AS days, NULL::numeric AS rent, NULL::date AS next_start
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        -- A unit never leased has no day it became vacant: unknown, not zero.
        SELECT 1, o.place, o.unit_name, o.id, 0, 'vacant', o.place, o.since,
               p_as_of - o.since + 1, o.rent, o.next_start
        FROM units AS o
        WHERE NOT o.occupied
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total',
               CASE WHEN c.total = 0 THEN 'no units'
                    ELSE c.total || ' units: ' || c.occupied || ' occupied, '
                         || (c.total - c.occupied) || ' vacant ('
                         || round(100.0 * c.occupied / c.total, 1) || '% occupied)'
               END,
               NULL, NULL, NULL, NULL
        FROM counted AS c
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C", l.unit_id, l.step
            ))::integer,
           l.what, l.label, l.unit_name, l.since, l.days, l.rent, l.next_start
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_vendor_directory(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_vendor_directory(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, vendor text, w9_on_file text, open_work_orders integer, unpaid_bills integer, unpaid numeric, paid_this_year numeric, last_paid_on date)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
    v_year_start timestamptz := date_trunc('year', p_as_of::timestamp) AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a vendor directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- Each bill paid by the end of the day, and when the bank paid it.
        SELECT b.vendor_id, b.amount, tr.event_at
        FROM trust_bills AS b
        JOIN trust_bill_payments AS p ON p.bill_id = b.id AND p.step = 'paid'
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE b.pmc_id = p_pmc_id AND tr.event_at < v_cutoff
    ),
    unpaid AS (
        -- Entered by the day, not paid by its end and not declined by then.
        SELECT b.vendor_id, b.amount
        FROM trust_bills AS b
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (
              SELECT p.bill_id FROM trust_bill_payments AS p
              JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
              WHERE p.bill_id = b.id AND p.step = 'paid' AND tr.event_at < v_cutoff
          )
          AND NOT EXISTS (
              SELECT d.bill_id FROM trust_bill_declines AS d
              WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
          )
    ),
    assigned AS (
        -- Each work order open at the day's end, and the vendor it was last assigned to.
        SELECT (SELECT s.vendor_id FROM trust_work_order_steps AS s
                WHERE s.work_order_id = w.id AND s.step = 'assigned' AND s.taken_at < v_cutoff
                ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                LIMIT 1) AS vendor_id
        FROM trust_work_orders AS w
        WHERE w.pmc_id = p_pmc_id AND w.opened_at < v_cutoff
          AND NOT EXISTS (
              SELECT s.work_order_id FROM trust_work_order_steps AS s
              WHERE s.work_order_id = w.id AND s.step IN ('completed', 'cancelled')
                AND s.taken_at < v_cutoff
          )
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS vendor_id, 'PMC' AS what,
               pmc.display_name AS label, NULL::text AS w9, NULL::integer AS orders,
               NULL::integer AS bills, NULL::numeric AS owed, NULL::numeric AS year_paid,
               NULL::date AS last_paid
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, v.display_name, v.id, 'vendor', v.display_name,
               -- A W-9 kept by the day's end and not removed by then.
               CASE WHEN EXISTS (
                   SELECT d.id FROM trust_documents AS d
                   WHERE d.vendor_id = v.id AND d.kind = 'w9' AND d.created_at < v_cutoff
                     AND NOT EXISTS (
                         SELECT r.document_id FROM trust_document_removals AS r
                         WHERE r.document_id = d.id AND r.created_at < v_cutoff
                     )
               ) THEN 'yes' ELSE 'no' END,
               (SELECT count(*) FROM assigned AS a WHERE a.vendor_id = v.id)::integer,
               (SELECT count(*) FROM unpaid AS u WHERE u.vendor_id = v.id)::integer,
               coalesce((SELECT sum(u.amount) FROM unpaid AS u WHERE u.vendor_id = v.id), 0.00),
               coalesce((SELECT sum(p.amount) FROM paid AS p
                         WHERE p.vendor_id = v.id AND p.event_at >= v_year_start), 0.00),
               (SELECT max((p.event_at AT TIME ZONE 'UTC')::date) FROM paid AS p
                WHERE p.vendor_id = v.id)
        FROM trust_vendors AS v
        WHERE v.pmc_id = p_pmc_id
    )
    SELECT (row_number() OVER (ORDER BY l.part, l.who COLLATE "C", l.vendor_id))::integer,
           l.what, l.label, l.w9, l.orders, l.bills, l.owed, l.year_paid, l.last_paid
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_vendor_insurance(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_vendor_insurance(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, vendor text, vendor_id uuid, coverage text, carrier text, policy_number text, starts_on date, expires_on date, limit_amount numeric, standing text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vendor insurance is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH policies AS (
        -- Each vendor's coverage as of the day: of the policies started by then, the one
        -- that expires last (so one running then, the one running longest, before any that
        -- had run out). One that starts later is not yet theirs. The vendors page reads this
        -- report for how each stands.
        SELECT DISTINCT ON (p.vendor_id, p.coverage) p.*
        FROM trust_vendor_insurance AS p
        WHERE p.pmc_id = p_pmc_id AND p.starts_on <= p_as_of
        ORDER BY p.vendor_id, p.coverage, p.expires_on DESC, p.created_at DESC, p.id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS vendor_id, NULL::text AS kind,
               'PMC' AS what, pmc.display_name AS label, NULL::text AS insurer,
               NULL::text AS number, NULL::date AS begins_on, NULL::date AS ends_on,
               NULL::numeric AS covered, NULL::text AS stands
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 'as of', to_char(p_as_of, 'YYYY-MM-DD'), NULL, NULL, NULL,
               NULL, NULL, NULL
        UNION ALL
        SELECT 1, v.display_name, v.id, p.coverage, 'policy', v.display_name, p.carrier,
               p.policy_number, p.starts_on, p.expires_on, p.limit_amount,
               CASE WHEN p.expires_on < p_as_of THEN 'expired'
                    WHEN p.expires_on = p_as_of THEN 'expires today'
                    WHEN p.expires_on - p_as_of <= 30 THEN 'expires in ' || (p.expires_on - p_as_of)
                         || CASE WHEN p.expires_on - p_as_of = 1 THEN ' day' ELSE ' days' END
                    ELSE 'current' END
        FROM trust_vendors AS v
        JOIN policies AS p ON p.vendor_id = v.id
        WHERE v.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, v.display_name, v.id, NULL, 'no insurance on file', v.display_name, NULL,
               NULL, NULL, NULL, NULL, NULL
        FROM trust_vendors AS v
        WHERE v.pmc_id = p_pmc_id
          AND NOT EXISTS (SELECT p.vendor_id FROM policies AS p WHERE p.vendor_id = v.id)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.who COLLATE "C", l.vendor_id, l.kind COLLATE "C"
            ))::integer,
           l.what, l.label, l.vendor_id, l.kind, l.insurer, l.number, l.begins_on, l.ends_on,
           l.covered, l.stands
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_vendor_payments(uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_vendor_payments(p_pmc_id uuid, p_year integer) RETURNS TABLE(line integer, item text, vendor text, payments bigint, paid numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: vendor payments are for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH here AS (
        SELECT b.vendor_id, count(*) AS payments, sum(tr.amount) AS total
        FROM trust_bill_payments AS p
        JOIN trust_bills AS b ON b.id = p.bill_id
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE p.pmc_id = p_pmc_id AND p.step = 'paid'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        GROUP BY b.vendor_id
    ),
    before AS (
        SELECT c.vendor_id, c.paid_before, c.amount
        FROM trust_vendor_payments_carried_over AS c
        WHERE c.pmc_id = p_pmc_id AND c.year = p_year
    ),
    paid AS (
        SELECT v.id AS vendor_id, v.display_name, coalesce(h.payments, 0) AS payments,
               coalesce(h.total, 0) + coalesce(c.amount, 0) AS total,
               c.paid_before, c.amount AS carried
        FROM trust_vendors AS v
        LEFT JOIN here AS h ON h.vendor_id = v.id
        LEFT JOIN before AS c ON c.vendor_id = v.id
        WHERE v.pmc_id = p_pmc_id AND (h.vendor_id IS NOT NULL OR c.vendor_id IS NOT NULL)
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS name, NULL::uuid AS id, 0 AS part, 'PMC' AS item,
               pmc.display_name AS label, NULL::bigint AS payments, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'year', p_year::text || ', paid January 1 to December 31 UTC',
               NULL, NULL
        UNION ALL
        SELECT 1, p.display_name, p.vendor_id, 0, 'vendor', p.display_name, p.payments, p.total
        FROM paid AS p
        UNION ALL
        -- What of the vendor's total the old system paid, under the vendor's line.
        SELECT 1, p.display_name, p.vendor_id, 1, 'carried over',
               'paid in the old system before ' || to_char(p.paid_before, 'YYYY-MM-DD'),
               NULL, p.carried
        FROM paid AS p WHERE p.carried IS NOT NULL
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'total', NULL, coalesce(sum(p.payments), 0)::bigint,
               coalesce(sum(p.total), 0)
        FROM paid AS p
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.id, l.part
           )::integer,
           l.item,
           -- The PMC's name and the year sit in the vendor column of the first two lines.
           l.label,
           l.payments,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_report_work_orders(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_report_work_orders(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, summary text, opened_on date, status text, vendor text, days_open integer, billed numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: work orders are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH orders AS (
        SELECT w.id, pr.display_name AS place, u.display_name AS unit_name, w.summary AS what,
               (w.opened_at AT TIME ZONE 'UTC')::date AS opened,
               coalesce(last.step, 'open') AS state,
               (last.taken_at AT TIME ZONE 'UTC')::date AS last_on,
               coalesce(last.step IN ('completed', 'cancelled'), false) AS closed,
               (SELECT v.display_name
                FROM trust_work_order_steps AS s
                JOIN trust_vendors AS v ON v.id = s.vendor_id
                WHERE s.work_order_id = w.id AND s.step = 'assigned' AND s.taken_at < v_cutoff
                ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                LIMIT 1) AS vendor_name,
               -- Bills entered for it by the day and not declined by then.
               coalesce((
                   SELECT sum(b.amount) FROM trust_bills AS b
                   WHERE b.work_order_id = w.id AND b.bill_date <= p_as_of
                     AND NOT EXISTS (
                         SELECT d.bill_id FROM trust_bill_declines AS d
                         WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
                     )
               ), 0.00) AS billed_total
        FROM trust_work_orders AS w
        JOIN trust_properties AS pr ON pr.id = w.property_id
        LEFT JOIN trust_units AS u ON u.id = w.unit_id
        LEFT JOIN LATERAL (
            SELECT s.step, s.taken_at FROM trust_work_order_steps AS s
            WHERE s.work_order_id = w.id AND s.taken_at < v_cutoff
            ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
            LIMIT 1
        ) AS last ON true
        WHERE w.pmc_id = p_pmc_id AND w.opened_at < v_cutoff
    ),
    lines AS (
        SELECT 0 AS part, 0 AS step, NULL::date AS opened, NULL::text AS place,
               NULL::uuid AS order_id, 'PMC' AS what, pmc.display_name AS label,
               NULL::text AS unit_name, NULL::text AS about, NULL::text AS state,
               NULL::text AS vendor_name, NULL::integer AS days, NULL::numeric AS billed_total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 1, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL,
               NULL, NULL
        UNION ALL
        -- Open ones first, oldest first; then the closed ones.
        SELECT 1, CASE WHEN o.closed THEN 1 ELSE 0 END, o.opened, o.place, o.id,
               CASE WHEN o.closed THEN 'closed' ELSE 'open' END, o.place, o.unit_name, o.what,
               o.state, o.vendor_name,
               CASE WHEN o.closed THEN o.last_on ELSE p_as_of END - o.opened, o.billed_total
        FROM orders AS o
        UNION ALL
        SELECT 2, 0, NULL, NULL, NULL, 'total', NULL, NULL,
               count(*) FILTER (WHERE NOT o.closed) || ' open ('
               || count(*) FILTER (WHERE o.state IN ('assigned', 'scheduled')) || ' assigned), '
               || count(*) FILTER (WHERE o.state = 'completed') || ' completed, '
               || count(*) FILTER (WHERE o.state = 'cancelled') || ' cancelled',
               NULL, NULL, NULL, coalesce(sum(o.billed_total), 0.00)
        FROM orders AS o
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.step, l.opened, l.place COLLATE "C", l.order_id
            ))::integer,
           l.what, l.label, l.unit_name, l.about, l.opened, l.state, l.vendor_name, l.days,
           l.billed_total
    FROM lines AS l
    ORDER BY 1;
END;
$$;


--
-- Name: trust_require_role(uuid, uuid, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_require_role(p_pmc_id uuid, p_staff_id uuid, p_role text, p_doing text) RETURNS void
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
BEGIN
    IF p_staff_id IS NOT NULL AND NOT trust_staff_holds(p_pmc_id, p_staff_id, p_role) THEN
        RAISE EXCEPTION 'trust: % may not % (the % role)',
            coalesce((SELECT s.display_name FROM trust_staff AS s WHERE s.id = p_staff_id),
                     p_staff_id::text),
            p_doing, p_role
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'An admin of the PMC grants the role.';
    END IF;
END;
$$;


--
-- Name: trust_require_signer(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_require_signer(p_bank_account_id uuid, p_day date) RETURNS void
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_signer trust_account_signers;
    v_name text;
    v_account text;
BEGIN
    IF v_staff IS NULL OR trust_broker_of(p_bank_account_id) IS NULL THEN
        RETURN;
    END IF;
    v_signer := trust_signer_of(p_bank_account_id, v_staff);
    SELECT s.display_name INTO v_name FROM trust_staff AS s WHERE s.id = v_staff;
    SELECT b.display_name INTO v_account FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id;
    IF v_signer.id IS NULL OR NOT v_signer.granted THEN
        RAISE EXCEPTION 'trust: % is not an authorized signer of %', v_name, v_account
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'The account''s broker authorizes its signers.';
    END IF;
    IF v_signer.license_number IS NULL AND v_signer.bond_expires_on < p_day THEN
        RAISE EXCEPTION 'trust: %''s bond to sign % ran out on %', v_name, v_account,
            v_signer.bond_expires_on
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'The broker records the renewed bond.';
    END IF;
END;
$$;


--
-- Name: trust_return_deposit(uuid, uuid, text, text, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_return_deposit(p_pmc_id uuid, p_lease_id uuid, p_owner_account text, p_request_key text, p_event_at timestamp with time zone) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_deposit_returns;
    v_day date := (p_event_at AT TIME ZONE 'UTC')::date;
    v_owner trust_ledger_accounts;
    v_owner_cash text;
    v_moved uuid;
    v_still record;
    v_held numeric;
    v_kept numeric;
    v_keep numeric;
    v_owners bigint;
    v_line bigint := 0::bigint;
    v_deposit record;
    v_link record;
    v_charge record;
    v_take numeric;
    v_rest numeric;
    v_piece numeric;
    v_left numeric;
    v_part numeric;
    v_transfer text;
    -- The leases kept for, oldest first, the owner's account of each and what each still owes
    -- of its rent and of its other charges (migration 0093).
    v_leases uuid[] := ARRAY[]::uuid[];
    v_accounts text[] := ARRAY[]::text[];
    v_rent numeric[] := ARRAY[]::numeric[];
    v_fees numeric[] := ARRAY[]::numeric[];
    v_rent_open numeric;
    v_fees_open numeric;
    v_fees_pass boolean;
    i integer;
    -- The lease's property, and whether its owners own it in shares on the day (migration 0094).
    v_property uuid;
    v_split boolean;
    v_pay record;
BEGIN
    -- What the lease owes must count every payment that committed while this waited.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: return a deposit at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' OR p_event_at IS NULL THEN
        RAISE EXCEPTION 'trust: a deposit''s return needs a request key and the time it was paid'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The lease's rent work (src/pms/api/rent.py) waits for this, and this for it. The lease's
    -- row, as trust_apply_payment takes it, holds off a change of its days or a new hold.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT r.* INTO v_done FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id;
    IF FOUND THEN
        IF v_done.request_key <> p_request_key THEN
            RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Resend a request key only to retry.';
        END IF;
        RETURN v_done.returned;  -- a retry: what went back then
    END IF;

    SELECT t.to_lease_id INTO v_moved FROM trust_lease_transfers AS t
    WHERE t.from_lease_id = p_lease_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease %''s tenants moved to lease %, and their deposits with them',
            p_lease_id, v_moved
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_lease.ends_on IS NULL OR v_lease.ends_on >= v_day THEN
        RAISE EXCEPTION 'trust: lease % has not ended by %; end it first', p_lease_id, v_day
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT t.display_name AS tenant, l.id AS lease INTO v_still
    FROM trust_lease_tenants AS mine
    JOIN trust_lease_tenants AS other ON other.tenant_id = mine.tenant_id
    JOIN trust_leases AS l ON l.id = other.lease_id
    JOIN trust_tenants AS t ON t.id = mine.tenant_id
    WHERE mine.lease_id = p_lease_id AND l.id <> p_lease_id
      AND (l.ends_on IS NULL OR l.ends_on >= v_day)
    ORDER BY t.display_name COLLATE "C", l.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % is on lease % still, and their deposit stays with it',
            v_still.tenant, v_still.lease
            USING ERRCODE = 'check_violation';
    END IF;

    -- A property its owners own in shares on the day keeps for each of them by their share; one
    -- without is kept for its one owner's account, or the one named.
    SELECT u.property_id INTO v_property FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    v_split := EXISTS (SELECT 1 FROM trust_ownership_on(v_property, v_day));
    IF NOT v_split THEN
        SELECT count(*), min(t.ledger_account_id) INTO v_owners, v_owner.ledger_account_id
        FROM trust_ledger_accounts AS t
        JOIN trust_units AS u ON u.property_id = t.property_id
        WHERE u.id = v_lease.unit_id AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
          AND (p_owner_account IS NULL OR t.ledger_account_id = p_owner_account);
        IF v_owners <> 1 THEN
            RAISE EXCEPTION 'trust: % is not the one owner''s account of the lease''s property',
                coalesce(p_owner_account, 'no account given')
                USING ERRCODE = 'invalid_parameter_value',
                      HINT = 'Name the owner''s account when the property has more than one.';
        END IF;
    END IF;

    -- The leases the tenants moved from, oldest first, then this one. Each moved from waits for
    -- its own rent work too, taken after this lease's as the rent page takes them.
    FOR v_link IN
        SELECT m.lease_id, m.owner_account
        FROM trust_lease_moved_from(p_lease_id) WITH ORDINALITY AS m (lease_id, owner_account, n)
        ORDER BY m.n
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || v_link.lease_id, 0));
        v_leases := v_leases || v_link.lease_id;
        v_accounts := v_accounts || v_link.owner_account;
    END LOOP;
    v_leases := v_leases || p_lease_id;
    v_accounts := v_accounts || v_owner.ledger_account_id;
    -- What is open of each lease's rent (its rent and monthly charges) and of its other charges,
    -- its credits coming off its rent first (trust_open_charges).
    FOR n IN 1 .. cardinality(v_leases) LOOP
        SELECT coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) < 2), 0),
               coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) = 2), 0)
        INTO v_rent_open, v_fees_open
        FROM trust_open_charges(p_pmc_id, v_leases[n], 'infinity') AS o
        JOIN trust_charges AS c ON c.id = o.charge_id;
        v_rent := v_rent || v_rent_open;
        v_fees := v_fees || v_fees_open;
    END LOOP;

    -- The deposits held for the lease's tenants. One paid in meanwhile stays held (the
    -- transfers below take each account's lock); one taken out meanwhile, by the return of
    -- another lease of the same tenant, makes this refuse, as no deposit goes below zero.
    SELECT coalesce(sum(a.balance), 0) INTO v_held FROM pgledger_accounts AS a
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = a.id
    JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
    WHERE lt.lease_id = p_lease_id AND t.kind = 'tenant_deposit';
    IF v_held <= 0 THEN
        RAISE EXCEPTION 'trust: no security deposit is held for lease %', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    v_kept := least(
        (SELECT sum(o) FROM unnest(v_rent) AS o) + (SELECT sum(o) FROM unnest(v_fees) AS o), v_held
    );
    v_keep := v_kept;  -- what is left to keep, deposit by deposit

    INSERT INTO trust_deposit_returns (
        lease_id, pmc_id, request_key, returned_at, held, kept, returned, returned_by
    ) VALUES (
        p_lease_id, p_pmc_id, p_request_key, p_event_at, v_held, v_kept, v_held - v_kept,
        nullif(current_setting('trust.staff_id', true), '')::uuid
    );

    FOR v_deposit IN
        SELECT t.ledger_account_id AS account, t.tenant_id, a.balance,
               (SELECT c.ledger_account_id FROM trust_ledger_accounts AS c
                WHERE c.bank_account_id = t.bank_account_id AND c.kind = 'bank_cash') AS cash
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
        JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        WHERE lt.lease_id = p_lease_id AND t.kind = 'tenant_deposit'
        -- Each tenant's security deposit first, then their key and pet deposits (0092).
        ORDER BY tn.display_name COLLATE "C", t.tenant_id,
                 CASE t.deposit_kind WHEN 'key' THEN 1 WHEN 'pet' THEN 2 ELSE 0 END,
                 t.ledger_account_id
    LOOP
        v_take := least(v_keep, v_deposit.balance);
        v_rest := v_take;
        -- Kept: the rent of every lease first, lease by lease, oldest first, then their other
        -- charges the same way (rent first, migration 0093), each part into that lease's owner's
        -- account, its cash moving to the owner's trust bank account.
        FOREACH v_fees_pass IN ARRAY ARRAY[false, true] LOOP
            i := 1;
            WHILE v_rest > 0 AND i <= cardinality(v_leases) LOOP
                v_piece := least(v_rest, CASE WHEN v_fees_pass THEN v_fees[i] ELSE v_rent[i] END);
                IF v_piece > 0 THEN
                    -- This lease's part goes to its owners by their shares, a lease moved from
                    -- to the owner's account its move names.
                    FOR v_pay IN
                        SELECT s.ledger_account_id COLLATE "C" AS account, s.amount
                        FROM trust_split_by_ownership(v_property, v_day, v_piece) AS s
                        WHERE v_split AND i = cardinality(v_leases)
                        UNION ALL
                        SELECT v_accounts[i] COLLATE "C", v_piece
                        WHERE NOT (v_split AND i = cardinality(v_leases))
                        ORDER BY account
                    LOOP
                        SELECT t.* INTO v_owner FROM trust_ledger_accounts AS t
                        WHERE t.ledger_account_id = v_pay.account;
                        SELECT t.ledger_account_id INTO v_owner_cash
                        FROM trust_ledger_accounts AS t
                        WHERE t.bank_account_id = v_owner.bank_account_id
                          AND t.kind = 'bank_cash';
                        SELECT t.id INTO v_transfer
                        FROM pgledger_create_transfers(
                            ARRAY[
                                (v_deposit.account, v_owner.ledger_account_id, v_pay.amount)
                                    ::transfer_request,
                                (v_owner_cash, v_deposit.cash, v_pay.amount)::transfer_request
                            ],
                            p_event_at,
                            jsonb_build_object('memo', 'Security deposit kept')
                        ) AS t
                        WHERE t.from_account_id = v_deposit.account;
                        v_left := v_pay.amount;
                        -- What is open of each, rent first: what a waiver or a credit took off
                        -- it is not kept from the deposit.
                        FOR v_charge IN
                            SELECT c.id, o.open
                            FROM trust_open_charges(p_pmc_id, v_leases[i], 'infinity') AS o
                            JOIN trust_charges AS c ON c.id = o.charge_id
                            WHERE (trust_pay_rank(c) = 2) = v_fees_pass
                            ORDER BY c.due_on, trust_pay_rank(c), c.created_at, c.id
                        LOOP
                            EXIT WHEN v_left <= 0;
                            CONTINUE WHEN v_charge.open <= 0;
                            v_part := least(v_left, v_charge.open);
                            PERFORM trust_apply_payment(
                                p_pmc_id, v_charge.id, v_transfer, v_part
                            );
                            v_line := v_line + 1;
                            INSERT INTO trust_deposit_return_lines (
                                lease_id, line_no, pmc_id, tenant_id, charge_id, amount,
                                transfer_id
                            ) VALUES (
                                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id,
                                v_part, v_transfer
                            );
                            v_left := v_left - v_part;
                        END LOOP;
                    END LOOP;
                    IF v_fees_pass THEN
                        v_fees[i] := v_fees[i] - v_piece;
                    ELSE
                        v_rent[i] := v_rent[i] - v_piece;
                    END IF;
                    v_rest := v_rest - v_piece;
                END IF;
                i := i + 1;
            END LOOP;
        END LOOP;
        v_keep := v_keep - v_take;
        IF v_deposit.balance > v_take THEN
            -- Returned: out of the security-deposit trust bank account's cash.
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_deposit.cash, v_deposit.balance - v_take)
                      ::transfer_request],
                p_event_at,
                jsonb_build_object('memo', 'Security deposit returned')
            ) AS t;
            v_line := v_line + 1;
            INSERT INTO trust_deposit_return_lines (
                lease_id, line_no, pmc_id, tenant_id, charge_id, amount, transfer_id
            ) VALUES (
                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, NULL,
                v_deposit.balance - v_take, v_transfer
            );
        END IF;
    END LOOP;
    RETURN v_held - v_kept;
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
-- Name: trust_run_rent(uuid, date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_run_rent(p_pmc_id uuid, p_due_on date, p_late_fees_as_of date) RETURNS uuid
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_rents integer;
    v_recurring integer;
    v_late_fees integer;
    v_id uuid;
BEGIN
    -- By hand, only an admin runs it; the nightly run names nobody.
    PERFORM trust_require_role(
        p_pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid, 'admin',
        'run rent by hand'
    );
    v_rents := trust_charge_rent_due(p_pmc_id, p_due_on)
        + trust_charge_rent_on_due_days(p_pmc_id, p_due_on, p_late_fees_as_of);
    v_recurring := trust_charge_recurring_due(p_pmc_id, p_due_on)
        + trust_charge_recurring_on_due_days(p_pmc_id, p_due_on, p_late_fees_as_of);
    v_late_fees := trust_assess_late_fees(p_pmc_id, p_late_fees_as_of);
    INSERT INTO trust_rent_runs (pmc_id, due_on, late_fees_as_of, rents, recurring, late_fees)
    VALUES (p_pmc_id, p_due_on, p_late_fees_as_of, v_rents, v_recurring, v_late_fees)
    RETURNING id INTO v_id;
    RETURN v_id;
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
    v_declined trust_bill_declines;
    v_markup trust_bill_markups;
    v_owner trust_ledger_accounts;
BEGIN
    SELECT p.transfer_id INTO v_transfer_id
    FROM trust_bill_payments AS p
    WHERE p.bill_id = p_bill_id AND p.step = 'set_aside';
    IF FOUND THEN
        RETURN v_transfer_id;  -- a retry: the bill is set aside already
    END IF;

    SELECT d.* INTO v_declined FROM trust_bill_declines AS d WHERE d.bill_id = p_bill_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: bill % was declined by % on %; it is not paid',
            v_bill.reference, v_declined.declined_by, v_declined.declined_on
            USING ERRCODE = 'check_violation';
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

    SELECT t.* INTO v_owner
    FROM trust_ledger_accounts AS t WHERE t.ledger_account_id = v_bill.ledger_account_id;
    v_bank := v_owner.bank_account_id;
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

    SELECT m.* INTO v_markup FROM trust_bill_markups AS m WHERE m.bill_id = p_bill_id;
    IF FOUND THEN
        INSERT INTO trust_bill_markup_fees (bill_id, pmc_id, transfer_id)
        VALUES (
            p_bill_id, p_pmc_id,
            trust_post_fee(
                v_owner, v_markup.amount, p_event_at,
                format('Markup of %s%% on bill %s', trim_scale(v_markup.percent),
                       v_bill.reference)
            )
        );
    END IF;
    RETURN v_transfer_id;
END;
$$;


--
-- Name: trust_account_signers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_account_signers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    staff_id uuid NOT NULL,
    role text NOT NULL,
    granted boolean NOT NULL,
    license_number text,
    bond_amount numeric,
    bond_expires_on date,
    authorized_on date NOT NULL,
    changed_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_account_signers_bond_amount_check CHECK (((bond_amount > (0)::numeric) AND (bond_amount = round(bond_amount, 2)))),
    CONSTRAINT trust_account_signers_check CHECK (((bond_amount IS NULL) = (bond_expires_on IS NULL))),
    CONSTRAINT trust_account_signers_check1 CHECK (((NOT granted) OR (license_number IS NOT NULL) OR (bond_amount IS NOT NULL))),
    CONSTRAINT trust_account_signers_check2 CHECK (((NOT granted) OR (role = 'signer'::text) OR (license_number IS NOT NULL))),
    CONSTRAINT trust_account_signers_check3 CHECK ((granted OR ((license_number IS NULL) AND (bond_amount IS NULL)))),
    CONSTRAINT trust_account_signers_license_number_check CHECK (((license_number ~ '\S'::text) AND (length(license_number) <= 50))),
    CONSTRAINT trust_account_signers_role_check CHECK ((role = ANY (ARRAY['broker'::text, 'signer'::text])))
);


--
-- Name: trust_signer_of(uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_signer_of(p_bank_account_id uuid, p_staff_id uuid) RETURNS public.trust_account_signers
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT s.* FROM trust_account_signers AS s
    WHERE s.bank_account_id = p_bank_account_id AND s.staff_id = p_staff_id
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1
$$;


--
-- Name: trust_signing_link_open(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_signing_link_open(p_link_id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT EXISTS (
        SELECT 1
        FROM trust_signing_links AS l
        JOIN trust_signature_requests AS r
            ON r.offer_id = l.offer_id AND r.partner = 'PMS e-sign'
        WHERE l.id = p_link_id
          AND l.created_at > now() - interval '30 days'
          AND NOT EXISTS (
              SELECT 1 FROM trust_lease_offer_voids AS v WHERE v.offer_id = l.offer_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM trust_lease_offer_completions AS c WHERE c.offer_id = l.offer_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM trust_signatures AS g
              WHERE g.offer_id = l.offer_id AND g.position = l.position
          )
    );
$$;


--
-- Name: trust_split_by_ownership(uuid, date, numeric); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_split_by_ownership(p_property_id uuid, p_day date, p_amount numeric) RETURNS TABLE(ledger_account_id text, amount numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH exact AS (
        SELECT o.ledger_account_id, round(p_amount * 100) * o.percent / 100 AS cents
        FROM trust_ownership_on(p_property_id, p_day) AS o
        WHERE p_amount > 0
    ), ranked AS (
        SELECT e.ledger_account_id, floor(e.cents) AS whole,
               row_number() OVER (
                   ORDER BY e.cents - floor(e.cents) DESC, e.ledger_account_id COLLATE "C"
               ) AS n,
               round(p_amount * 100) - sum(floor(e.cents)) OVER () AS spare
        FROM exact AS e
    )
    SELECT r.ledger_account_id, (r.whole + CASE WHEN r.n <= r.spare THEN 1 ELSE 0 END) / 100
    FROM ranked AS r
    WHERE r.whole + CASE WHEN r.n <= r.spare THEN 1 ELSE 0 END > 0
    ORDER BY r.ledger_account_id COLLATE "C"
$$;


--
-- Name: trust_split_rent_assistance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_split_rent_assistance() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_aid trust_rent_assistance;
    v_rent numeric;
    v_part numeric;
BEGIN
    SELECT a.* INTO v_aid FROM trust_assistance_on(NEW.lease_id, NEW.due_on) AS a;
    IF NOT FOUND OR v_aid.amount = 0 THEN
        RETURN NEW;
    END IF;
    -- The lease's rent that day: always above 0 (its lease and rent changes check it).
    v_rent := trust_rent_on(NEW.lease_id, NEW.due_on);
    v_part := trust_assistance_part(NEW.amount, v_rent, v_aid.amount);
    IF v_part <= 0 THEN
        RETURN NEW;
    END IF;
    INSERT INTO trust_assistance_charges (
        pmc_id, lease_id, housing_authority_id, due_on, kind, amount, memo, source,
        monthly_rent, monthly_assistance
    ) VALUES (
        NEW.pmc_id, NEW.lease_id, v_aid.housing_authority_id, NEW.due_on, 'charge', v_part,
        coalesce(NEW.memo, 'Rent due ' || to_char(NEW.due_on, 'YYYY-MM-DD')), 'rent',
        v_rent, v_aid.amount
    )
    ON CONFLICT (lease_id, (date_trunc('month', due_on::timestamp))) WHERE source = 'rent'
    DO NOTHING;
    NEW.amount := NEW.amount - v_part;
    IF NEW.amount <= 0 THEN
        RETURN NULL;  -- the authority pays all of it
    END IF;
    NEW.memo := coalesce(NEW.memo, 'Rent due ' || to_char(NEW.due_on, 'YYYY-MM-DD'))
                || ', the tenant''s part';
    RETURN NEW;
END;
$$;


--
-- Name: trust_staff_holds(uuid, uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_staff_holds(p_pmc_id uuid, p_staff_id uuid, p_role text) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce((
        SELECT r.granted FROM trust_staff_roles AS r
        WHERE r.pmc_id = p_pmc_id AND r.staff_id = p_staff_id AND r.role = p_role
        ORDER BY r.created_at DESC
        LIMIT 1
    ), false)
$$;


--
-- Name: trust_staff_may(uuid, uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_staff_may(p_pmc_id uuid, p_staff_id uuid, p_area text) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH held AS (
        SELECT DISTINCT ON (s.role_id) s.role_id, s.granted
        FROM trust_staff_custom_roles AS s
        WHERE s.pmc_id = p_pmc_id AND s.staff_id = p_staff_id
        ORDER BY s.role_id, s.id DESC
    )
    SELECT NOT EXISTS (SELECT h.role_id FROM held AS h WHERE h.granted)
        OR EXISTS (
            SELECT h.role_id FROM held AS h
            WHERE h.granted AND coalesce((
                SELECT a.granted FROM trust_custom_role_areas AS a
                WHERE a.role_id = h.role_id AND a.area = p_area
                ORDER BY a.id DESC
                LIMIT 1
            ), false)
        )
$$;


--
-- Name: trust_take_monthly_management_fees(uuid, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_take_monthly_management_fees(p_pmc_id uuid, p_day date) RETURNS integer
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_settings trust_management_fee_settings;
    v_start date := (date_trunc('month', p_day::timestamp) - interval '1 month')::date;
    v_end date := date_trunc('month', p_day::timestamp)::date;
    v_account text;
    v_taken integer := 0;
BEGIN
    IF p_day IS NULL THEN
        RAISE EXCEPTION 'trust: management fees are taken on a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.* INTO v_settings
    FROM trust_management_fee_settings AS s
    WHERE s.pmc_id = p_pmc_id
    ORDER BY s.created_at DESC, s.id DESC
    LIMIT 1;
    IF NOT FOUND OR NOT v_settings.automatic
       OR extract(day FROM p_day) < v_settings.day_of_month THEN
        RETURN 0;
    END IF;

    FOR v_account IN
        SELECT a.ledger_account_id
        FROM trust_ledger_accounts AS a
        WHERE a.pmc_id = p_pmc_id AND a.kind = 'owner_property'
          AND EXISTS (SELECT 1 FROM trust_management_agreements AS g
                      WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on < v_end)
          AND NOT EXISTS (SELECT 1 FROM trust_management_fees AS f
                          WHERE f.ledger_account_id = a.ledger_account_id
                            AND f.period_start < v_end AND v_start < f.period_end)
        ORDER BY a.ledger_account_id
    LOOP
        BEGIN
            PERFORM trust_post_management_fee(
                p_pmc_id, v_account, v_start, v_end, p_day::timestamp AT TIME ZONE 'UTC'
            );
            v_taken := v_taken + 1;
        EXCEPTION
            WHEN check_violation OR exclusion_violation OR invalid_parameter_value THEN
                -- Left as it is (Management fees says why); on to the next account. A handler
                -- with a statement, so coverage sees it taken.
                CONTINUE;
        END;
    END LOOP;
    RETURN v_taken;
END;
$$;


--
-- Name: trust_take_owner_fee(uuid, text, text, numeric, text, text, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_take_owner_fee(p_pmc_id uuid, p_account text, p_kind text, p_amount numeric, p_memo text, p_request_key text, p_event_at timestamp with time zone) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_taken trust_owner_fees;
    v_label text;
    v_transfer_id text;
BEGIN
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: a fee from an owner needs a request key, so a retry can''t '
            'take it twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.* INTO v_taken
    FROM trust_owner_fees AS f
    WHERE f.pmc_id = p_pmc_id AND f.request_key = p_request_key;
    IF FOUND THEN
        IF (v_taken.ledger_account_id, v_taken.kind, v_taken.amount, v_taken.memo)
           IS DISTINCT FROM (p_account, p_kind, p_amount, btrim(p_memo)) THEN
            RAISE EXCEPTION 'trust: request key % already took a fee of % from %',
                p_request_key, round(v_taken.amount, 2), v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new fee; resend a key only to retry.';
        END IF;
        RETURN v_taken.transfer_id;  -- a retry: the fee already taken
    END IF;

    v_label := CASE p_kind
        WHEN 'inspection' THEN 'Inspection fee'
        WHEN 'lease_up' THEN 'Lease-up fee'
        WHEN 'other' THEN 'Fee'
    END;
    IF v_label IS NULL THEN
        RAISE EXCEPTION 'trust: a fee from an owner is an inspection, lease-up or other fee, '
            'not %', p_kind
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a fee from an owner is a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(btrim(p_memo), '') = '' THEN
        RAISE EXCEPTION 'trust: a fee from an owner says what it is for'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_transfer_id := trust_post_fee(
        v_account, p_amount, p_event_at, v_label || ': ' || btrim(p_memo)
    );
    INSERT INTO trust_owner_fees (pmc_id, request_key, ledger_account_id, kind, amount, memo,
                                  transfer_id)
    VALUES (p_pmc_id, p_request_key, p_account, p_kind, p_amount, btrim(p_memo), v_transfer_id);
    RETURN v_transfer_id;
END;
$$;


--
-- Name: trust_transfer_lease(uuid, uuid, uuid, date, date, numeric, text, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_transfer_lease(p_pmc_id uuid, p_lease_id uuid, p_unit_id uuid, p_moved_on date, p_ends_on date, p_monthly_rent numeric, p_owner_account text, p_deposit_agreed_on date) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_lease_transfers;
    v_from_property uuid;
    v_to_property uuid;
    v_owner text;
    v_owners bigint;
    v_tenants uuid[];
    v_who record;
    v_new uuid;
BEGIN
    -- As trust_open_lease and the leases' own trigger ask.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: move a lease at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_moved_on IS NULL THEN
        RAISE EXCEPTION 'trust: a move needs its day'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The lease's rent work and its deposit's return wait for this, and this for them.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Once per lease: the same move again (a retry) is the move made then.
    SELECT t.* INTO v_done FROM trust_lease_transfers AS t WHERE t.from_lease_id = p_lease_id;
    IF FOUND THEN
        IF v_done.moved_on = p_moved_on AND EXISTS (
            SELECT 1 FROM trust_leases AS l WHERE l.id = v_done.to_lease_id AND l.unit_id = p_unit_id
        ) THEN
            RETURN v_done.to_lease_id;
        END IF;
        RAISE EXCEPTION 'trust: lease %''s tenants moved already, to lease %',
            p_lease_id, v_done.to_lease_id
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id) THEN
        RAISE EXCEPTION 'trust: the deposit of lease % was returned: its tenants moved out',
            p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT u.property_id INTO v_from_property FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    SELECT u.property_id INTO v_to_property FROM trust_units AS u
    WHERE u.id = p_unit_id AND u.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no unit % in PMC %', p_unit_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_unit_id = v_lease.unit_id THEN
        RAISE EXCEPTION 'trust: a lease moves to another unit; at its own unit it is renewed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The old lease keeps the days up to the move: it started before and is in effect the day
    -- before.
    IF p_moved_on <= v_lease.starts_on
       OR (v_lease.ends_on IS NOT NULL AND v_lease.ends_on < p_moved_on - 1) THEN
        RAISE EXCEPTION 'trust: lease % is not in effect on %, the day before the move',
            p_lease_id, p_moved_on - 1
            USING ERRCODE = 'check_violation';
    END IF;
    -- A lease on a payment hold (an eviction filed) stays where it is until the hold is released.
    IF EXISTS (
        SELECT 1 FROM trust_payment_holds AS h
        LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
        WHERE h.lease_id = p_lease_id AND (r.ends_on IS NULL OR r.ends_on >= p_moved_on)
    ) THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold; it moves once the hold is released',
            p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- The tenants staying on it move. One who left it with a deposit still under their name keeps
    -- that deposit with this lease until its return, which a lease moved from never has.
    v_tenants := array(
        SELECT lt.tenant_id FROM trust_lease_tenants AS lt
        WHERE lt.lease_id = p_lease_id
          AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                          WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id)
        ORDER BY lt.tenant_id
    );
    SELECT t.display_name AS tenant INTO v_who
    FROM trust_lease_departures AS d
    JOIN trust_tenants AS t ON t.id = d.tenant_id
    JOIN trust_ledger_accounts AS a ON a.tenant_id = d.tenant_id AND a.kind = 'tenant_deposit'
    JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
    WHERE d.lease_id = p_lease_id AND d.deposit_to IS NULL AND b.balance > 0
    ORDER BY t.display_name COLLATE "C", t.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % left lease % with a deposit under their name, returned with the lease''s at move-out',
            v_who.tenant, p_lease_id
            USING ERRCODE = 'check_violation',
                  HINT = 'End the lease and return its deposits at move-out instead.';
    END IF;

    -- The old lease's owner's account: the only one of its property, or the one named.
    SELECT count(*), min(a.ledger_account_id) INTO v_owners, v_owner
    FROM trust_ledger_accounts AS a
    WHERE a.pmc_id = p_pmc_id AND a.kind = 'owner_property' AND a.property_id = v_from_property
      AND (p_owner_account IS NULL OR a.ledger_account_id = p_owner_account);
    IF v_owners <> 1 THEN
        RAISE EXCEPTION 'trust: % is not the one owner''s account of lease %''s property',
            coalesce(p_owner_account, 'no account given'), p_lease_id
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Name the owner''s account when the property has more than one.';
    END IF;

    -- Deposits stay held for the new lease only as the tenants agreed in writing.
    IF p_deposit_agreed_on IS NULL AND EXISTS (
        SELECT 1 FROM trust_ledger_accounts AS a
        JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
        WHERE a.tenant_id = ANY (v_tenants) AND a.kind = 'tenant_deposit' AND b.balance > 0
    ) THEN
        RAISE EXCEPTION 'trust: the tenants hold a deposit: record the day they agreed in writing that it stays held for the new lease'
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_deposit_agreed_on > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: the tenants agreed on a day that has come, not on %',
            p_deposit_agreed_on
            USING ERRCODE = 'check_violation';
    END IF;

    -- Moving to another property, they become its tenants, as long as no other lease of theirs
    -- runs on there.
    IF v_to_property <> v_from_property THEN
        SELECT t.display_name AS tenant, l.id AS lease INTO v_who
        FROM trust_lease_tenants AS lt
        JOIN trust_leases AS l ON l.id = lt.lease_id
        JOIN trust_tenants AS t ON t.id = lt.tenant_id
        WHERE lt.tenant_id = ANY (v_tenants) AND l.id <> p_lease_id
          AND (l.ends_on IS NULL OR l.ends_on >= p_moved_on)
        ORDER BY t.display_name COLLATE "C", l.id
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'trust: % is on lease % too; they move to another property with every lease of theirs ended',
                v_who.tenant, v_who.lease
                USING ERRCODE = 'check_violation';
        END IF;
        UPDATE trust_tenants SET property_id = v_to_property
        WHERE pmc_id = p_pmc_id AND id = ANY (v_tenants);
    END IF;

    IF v_lease.ends_on IS DISTINCT FROM p_moved_on - 1 THEN
        PERFORM trust_end_lease(p_pmc_id, p_lease_id, p_moved_on - 1);
    END IF;
    v_new := trust_open_lease(p_pmc_id, p_unit_id, p_moved_on, p_ends_on, p_monthly_rent, v_tenants);
    INSERT INTO trust_lease_transfers (
        from_lease_id, pmc_id, to_lease_id, moved_on, owner_account, deposit_agreed_on
    ) VALUES (p_lease_id, p_pmc_id, v_new, p_moved_on, v_owner, p_deposit_agreed_on);
    RETURN v_new;
END;
$$;


--
-- Name: trust_unit_area(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_unit_area(p_unit_id uuid) RETURNS bigint
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT a.square_feet FROM trust_unit_areas AS a
    WHERE a.unit_id = p_unit_id
    ORDER BY a.created_at DESC, a.id DESC
    LIMIT 1
$$;


--
-- Name: trust_unmatch_bank_lines(uuid, uuid, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_unmatch_bank_lines(p_pmc_id uuid, p_match_id uuid, p_unmatched_by uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank_account uuid;
    v_bank trust_bank_accounts;
BEGIN
    SELECT m.bank_account_id INTO v_bank_account FROM trust_bank_matches AS m
    WHERE m.id = p_match_id AND m.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no bank match % in PMC %', p_match_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_bank := trust_lock_bank_matches(p_pmc_id, v_bank_account);
    IF EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u WHERE u.match_id = p_match_id)
    THEN
        RETURN;
    END IF;
    IF trust_bank_match_closed(
        v_bank.closed_through,
        ARRAY(SELECT ml.line_id FROM trust_bank_match_lines AS ml
              WHERE ml.match_id = p_match_id),
        ARRAY(SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt
              WHERE mt.match_id = p_match_id),
        ARRAY(SELECT mi.item_id FROM trust_bank_match_cutover_items AS mi
              WHERE mi.match_id = p_match_id)
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through %; undoing this match '
            'would change a reconciliation already approved', v_bank.id, v_bank.closed_through
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT carried.item_id FROM trust_bank_match_cutover_items AS carried
        JOIN trust_bank_match_cutover_items AS cleared
          ON cleared.item_id = carried.item_id AND cleared.side = 'cleared'
        WHERE carried.match_id = p_match_id AND carried.side = 'carried'
          AND NOT EXISTS (SELECT u.match_id FROM trust_bank_unmatches AS u
                          WHERE u.match_id = cleared.match_id)
    ) THEN
        RAISE EXCEPTION 'trust: a later match clears an item this opening carries'
            USING ERRCODE = 'check_violation',
                  HINT = 'Undo that match first.';
    END IF;
    INSERT INTO trust_bank_unmatches (pmc_id, match_id, unmatched_by)
    VALUES (p_pmc_id, p_match_id, p_unmatched_by);
END;
$$;


--
-- Name: trust_unpaid_bills(text, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_unpaid_bills(p_account text, p_day date) RETURNS numeric
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT coalesce(sum(b.amount + coalesce(m.amount, 0)), 0)
    FROM trust_bills AS b
    LEFT JOIN trust_bill_markups AS m ON m.bill_id = b.id
    WHERE b.ledger_account_id = p_account AND b.bill_date <= p_day
      AND NOT EXISTS (
          SELECT p.bill_id
          FROM trust_bill_payments AS p
          JOIN pgledger_transfers AS t ON t.id = p.transfer_id
          WHERE p.bill_id = b.id AND p.step = 'set_aside'
            AND t.event_at < (p_day + 1)::timestamp AT TIME ZONE 'UTC'
      )
      -- A bill its owner declined holds nothing back from the day it was declined.
      AND NOT EXISTS (
          SELECT d.bill_id FROM trust_bill_declines AS d
          WHERE d.bill_id = b.id AND d.declined_on <= p_day
      )
$$;


--
-- Name: trust_utility_allocation(uuid, uuid, text, date, date, numeric, numeric, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_utility_allocation(p_pmc_id uuid, p_property_id uuid, p_utility text, p_period_start date, p_period_end date, p_amount numeric, p_master_usage numeric, p_usage jsonb) RETURNS TABLE(lease_id uuid, unit_id uuid, billed boolean, weight numeric, usage numeric, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_plan trust_utility_plans;
    v_pool numeric;
    v_total numeric;
    v_least numeric;
    v_unknown text;
BEGIN
    IF p_period_end < p_period_start OR p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a utility bill is for a period of days and an amount above 0'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_properties AS p WHERE p.id = p_property_id AND p.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no property % in PMC %', p_property_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.* INTO v_plan FROM trust_utility_plan_on(p_property_id, p_utility, p_period_start) AS p;
    IF NOT FOUND OR v_plan.method = 'none' THEN
        RAISE EXCEPTION 'trust: the property bills no % back on %', p_utility, p_period_start
            USING ERRCODE = 'check_violation';
    END IF;
    IF (v_plan.method = 'submeter') <> (p_master_usage IS NOT NULL) THEN
        RAISE EXCEPTION 'trust: a submetered utility''s bill gives the master meter''s reading, '
            'and only it'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_pool := p_amount * (100 - v_plan.owner_percent) / 100;

    IF v_plan.method = 'submeter' THEN
        SELECT k INTO v_unknown FROM jsonb_object_keys(coalesce(p_usage, '{}')) AS k
        WHERE k NOT IN (
            SELECT w.lease_id::text FROM trust_utility_weights(
                p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, 'units', NULL
            ) AS w
        )
        ORDER BY k
        LIMIT 1;
        IF v_unknown IS NOT NULL THEN
            RAISE EXCEPTION 'trust: % is no lease of the property in effect in the period',
                v_unknown
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT coalesce(sum(w.usage), 0), min(w.usage) INTO v_total, v_least
        FROM trust_utility_weights(
            p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, 'submeter', p_usage
        ) AS w;
        IF v_least < 0 THEN
            RAISE EXCEPTION 'trust: a reading is 0 or more'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_total > p_master_usage THEN
            RAISE EXCEPTION 'trust: the units'' readings, %, come to more than the master '
                'meter''s, %', v_total, p_master_usage
                USING ERRCODE = 'check_violation';
        END IF;
        v_total := p_master_usage;
    ELSIF v_plan.method = 'occupants' THEN
        SELECT coalesce(sum(w.weight), 0) INTO v_total
        FROM trust_utility_weights(
            p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, 'occupants', NULL
        ) AS w;
    ELSE
        -- Every unit of the property for every day: not archived, or leased in the period.
        SELECT u.display_name INTO v_unknown
        FROM trust_units AS u
        WHERE v_plan.method = 'square_feet'
          AND u.pmc_id = p_pmc_id AND u.property_id = p_property_id
          AND trust_unit_area(u.id) IS NULL
          AND (NOT trust_is_archived(NULL, u.id) OR EXISTS (
              SELECT 1 FROM trust_leases AS l
              WHERE l.unit_id = u.id AND l.starts_on <= p_period_end
                AND (l.ends_on IS NULL OR l.ends_on >= p_period_start)
          ))
        ORDER BY u.display_name COLLATE "C", u.id
        LIMIT 1;
        IF v_unknown IS NOT NULL THEN
            RAISE EXCEPTION 'trust: % has no square feet on file', v_unknown
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT coalesce(sum(
                   CASE v_plan.method WHEN 'units' THEN 1 ELSE trust_unit_area(u.id) END
               ), 0) * (p_period_end - p_period_start + 1)
        INTO v_total
        FROM trust_units AS u
        WHERE u.pmc_id = p_pmc_id AND u.property_id = p_property_id
          AND (NOT trust_is_archived(NULL, u.id) OR EXISTS (
              SELECT 1 FROM trust_leases AS l
              WHERE l.unit_id = u.id AND l.starts_on <= p_period_end
                AND (l.ends_on IS NULL OR l.ends_on >= p_period_start)
          ));
    END IF;

    RETURN QUERY
    SELECT w.lease_id, w.unit_id, w.billed, w.weight, w.usage,
           CASE WHEN w.billed AND v_total > 0
                THEN floor(v_pool * w.weight / v_total * 100) / 100
                ELSE 0::numeric END
    FROM trust_utility_weights(
        p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, v_plan.method, p_usage
    ) AS w
    ORDER BY w.lease_id;
END;
$$;


--
-- Name: trust_utility_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_utility_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    utility text NOT NULL,
    starts_on date NOT NULL,
    method text NOT NULL,
    owner_percent numeric DEFAULT 0 NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_utility_plans_method_check CHECK ((method = ANY (ARRAY['units'::text, 'square_feet'::text, 'occupants'::text, 'submeter'::text, 'none'::text]))),
    CONSTRAINT trust_utility_plans_owner_percent_check CHECK (((owner_percent >= (0)::numeric) AND (owner_percent <= (100)::numeric) AND (owner_percent = round(owner_percent, 2)))),
    CONSTRAINT trust_utility_plans_utility_check CHECK ((utility = ANY (ARRAY['water'::text, 'sewer'::text, 'trash'::text, 'gas'::text, 'electric'::text, 'other'::text])))
);


--
-- Name: trust_utility_plan_on(uuid, text, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_utility_plan_on(p_property_id uuid, p_utility text, p_day date) RETURNS SETOF public.trust_utility_plans
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT p.* FROM trust_utility_plans AS p
    WHERE p.property_id = p_property_id AND p.utility = p_utility AND p.starts_on <= p_day
    ORDER BY p.starts_on DESC, p.created_at DESC, p.id DESC
    LIMIT 1
$$;


--
-- Name: trust_utility_weights(uuid, uuid, text, date, date, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_utility_weights(p_pmc_id uuid, p_property_id uuid, p_utility text, p_period_start date, p_period_end date, p_method text, p_usage jsonb) RETURNS TABLE(lease_id uuid, unit_id uuid, billed boolean, weight numeric, usage numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    SELECT x.id, x.unit_id, trust_lease_billed(x.id, p_utility, x.last_day),
           CASE p_method
               WHEN 'units' THEN x.days::numeric
               WHEN 'square_feet' THEN trust_unit_area(x.unit_id)::numeric * x.days
               WHEN 'occupants' THEN trust_lease_people(x.id, x.last_day)::numeric * x.days
               ELSE coalesce((p_usage ->> x.id::text)::numeric, 0)
           END,
           CASE WHEN p_method = 'submeter'
                THEN coalesce((p_usage ->> x.id::text)::numeric, 0) END
    FROM (
        SELECT l.id, l.unit_id,
               least(p_period_end, coalesce(l.ends_on, p_period_end)) AS last_day,
               least(p_period_end, coalesce(l.ends_on, p_period_end))
               - greatest(p_period_start, l.starts_on) + 1 AS days
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE l.pmc_id = p_pmc_id AND u.property_id = p_property_id
          AND l.starts_on <= p_period_end
          AND (l.ends_on IS NULL OR l.ends_on >= p_period_start)
    ) AS x
$$;


--
-- Name: trust_withdraw_pmc_fees(uuid, uuid, text, numeric, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_withdraw_pmc_fees(p_pmc_id uuid, p_bank_account_id uuid, p_request_key text, p_amount numeric, p_event_at timestamp with time zone) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_fees text;
    v_cash text;
    v_taken trust_pmc_fee_withdrawals;
    v_held numeric;
    v_transfer_id text;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: take the company''s fees at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: taking the company''s fees needs a request key, so a retry '
            'can''t pay twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: taking the company''s fees pays a positive amount, not %',
            p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The fee account of the trust bank account, locked so two withdrawals wait for each other
    -- and the second reads the balance the first left.
    SELECT t.ledger_account_id INTO v_fees
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_bank_account_id AND t.pmc_id = p_pmc_id
      AND t.kind = 'pmc_income'
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: trust bank account % of PMC % holds no fee account of the '
            'company', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT w.* INTO v_taken
    FROM trust_pmc_fee_withdrawals AS w
    WHERE w.pmc_id = p_pmc_id AND w.request_key = p_request_key;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> v_fees
            OR v_taken.amount <> coalesce(p_amount, v_taken.amount) THEN
            RAISE EXCEPTION 'trust: request key % already took % of the company''s fees from %',
                p_request_key, round(v_taken.amount, 2), v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new withdrawal; resend a key only to retry.';
        END IF;
        RETURN v_taken.amount;  -- a retry: the original withdrawal
    END IF;

    SELECT a.balance INTO v_held FROM pgledger_accounts AS a WHERE a.id = v_fees;
    IF v_held <= 0 OR coalesce(p_amount, v_held) > v_held THEN
        RAISE EXCEPTION 'trust: % of the company''s fees is in %; % is too much',
            round(v_held, 2), v_fees, round(coalesce(p_amount, v_held), 2)
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(v_fees, v_cash, coalesce(p_amount, v_held))::transfer_request],
        p_event_at,
        jsonb_build_object('memo', 'Company fees taken out of trust')
    ) AS t;

    INSERT INTO trust_pmc_fee_withdrawals (pmc_id, request_key, ledger_account_id, amount,
                                           transfer_id)
    VALUES (p_pmc_id, p_request_key, v_fees, coalesce(p_amount, v_held), v_transfer_id);
    RETURN coalesce(p_amount, v_held);
END;
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
-- Name: trust_adverse_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_adverse_actions (
    application_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    reasons text[] NOT NULL,
    agency text NOT NULL,
    notice_given_on date NOT NULL,
    recorded_by uuid NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_adverse_actions_agency_check CHECK ((agency ~ '\S'::text)),
    CONSTRAINT trust_adverse_actions_reasons_check CHECK (((cardinality(reasons) >= 1) AND (cardinality(reasons) <= 20)))
);


--
-- Name: trust_applicants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_applicants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    application_id uuid NOT NULL,
    "position" bigint NOT NULL,
    display_name text NOT NULL,
    email text NOT NULL,
    CONSTRAINT trust_applicants_display_name_check CHECK (((display_name ~ '\S'::text) AND (length(display_name) <= 200))),
    CONSTRAINT trust_applicants_email_check CHECK (((email = lower(email)) AND (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'::text) AND (length(email) <= 254))),
    CONSTRAINT trust_applicants_position_check CHECK ((("position" >= 1) AND ("position" <= 6)))
);


--
-- Name: trust_application_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_application_decisions (
    application_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    decision text NOT NULL,
    conditions text,
    screening_id uuid,
    decided_by uuid NOT NULL,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    override_reason text,
    CONSTRAINT trust_application_decisions_check CHECK (((decision = 'conditional'::text) = (conditions IS NOT NULL))),
    CONSTRAINT trust_application_decisions_conditions_check CHECK (((conditions ~ '\S'::text) AND (length(conditions) <= 2000))),
    CONSTRAINT trust_application_decisions_decision_check CHECK ((decision = ANY (ARRAY['approved'::text, 'conditional'::text, 'declined'::text])))
);


--
-- Name: trust_application_link_closings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_application_link_closings (
    link_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    closed_by uuid NOT NULL,
    closed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_application_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_application_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    token_sha256 bytea NOT NULL,
    monthly_rent numeric NOT NULL,
    opened_by uuid NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_application_links_monthly_rent_check CHECK ((monthly_rent > (0)::numeric)),
    CONSTRAINT trust_application_links_token_sha256_check CHECK ((length(token_sha256) = 32))
);


--
-- Name: trust_application_withdrawals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_application_withdrawals (
    application_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    reason text,
    recorded_by uuid,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_application_withdrawals_reason_check CHECK (((btrim(reason) <> ''::text) AND (char_length(reason) <= 500)))
);


--
-- Name: trust_applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_applications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    link_id uuid NOT NULL,
    status_token_sha256 bytea NOT NULL,
    move_in_on date NOT NULL,
    monthly_income numeric NOT NULL,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_applications_monthly_income_check CHECK ((monthly_income >= (0)::numeric)),
    CONSTRAINT trust_applications_status_token_sha256_check CHECK ((length(status_token_sha256) = 32))
);


--
-- Name: trust_archivals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_archivals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid,
    unit_id uuid,
    archived boolean NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    tenant_id uuid,
    owner_id uuid,
    CONSTRAINT trust_archivals_one_record CHECK ((num_nonnulls(property_id, unit_id, tenant_id, owner_id) = 1))
);


--
-- Name: trust_assistance_charges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_assistance_charges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    housing_authority_id uuid NOT NULL,
    due_on date NOT NULL,
    kind text NOT NULL,
    amount numeric NOT NULL,
    memo text NOT NULL,
    source text NOT NULL,
    monthly_rent numeric,
    monthly_assistance numeric,
    adjusts_id uuid,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_assistance_charges_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_assistance_charges_check CHECK (((source = 'rent'::text) = ((monthly_rent IS NOT NULL) AND (monthly_assistance IS NOT NULL)))),
    CONSTRAINT trust_assistance_charges_check1 CHECK (((source <> 'rent'::text) OR ((kind = 'charge'::text) AND (adjusts_id IS NULL)))),
    CONSTRAINT trust_assistance_charges_check2 CHECK (((source <> 'lease end'::text) OR (adjusts_id IS NOT NULL))),
    CONSTRAINT trust_assistance_charges_check3 CHECK (((kind = 'charge'::text) OR (adjusts_id IS NOT NULL))),
    CONSTRAINT trust_assistance_charges_kind_check CHECK ((kind = ANY (ARRAY['charge'::text, 'credit'::text]))),
    CONSTRAINT trust_assistance_charges_memo_check CHECK (((btrim(memo) <> ''::text) AND (length(memo) <= 200))),
    CONSTRAINT trust_assistance_charges_monthly_assistance_check CHECK ((monthly_assistance > (0)::numeric)),
    CONSTRAINT trust_assistance_charges_monthly_rent_check CHECK ((monthly_rent > (0)::numeric)),
    CONSTRAINT trust_assistance_charges_source_check CHECK ((source = ANY (ARRAY['rent'::text, 'lease end'::text, 'staff'::text])))
);


--
-- Name: trust_assistance_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_assistance_payments (
    transfer_id text NOT NULL,
    assistance_charge_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    amount numeric NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_assistance_payments_amount_check CHECK ((amount > (0)::numeric))
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
-- Name: trust_bank_deposit_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_deposit_items (
    deposit_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    transfer_id text NOT NULL
);


--
-- Name: trust_bank_deposits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_deposits (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    deposited_on date NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_bank_match_cutover_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_match_cutover_items (
    match_id uuid NOT NULL,
    item_id uuid NOT NULL,
    side text NOT NULL,
    pmc_id uuid NOT NULL,
    CONSTRAINT trust_bank_match_cutover_items_side_check CHECK ((side = ANY (ARRAY['carried'::text, 'cleared'::text])))
);


--
-- Name: trust_bank_match_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_match_lines (
    match_id uuid NOT NULL,
    line_id uuid NOT NULL,
    pmc_id uuid NOT NULL
);


--
-- Name: trust_bank_match_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_match_transfers (
    match_id uuid NOT NULL,
    transfer_id text NOT NULL,
    pmc_id uuid NOT NULL
);


--
-- Name: trust_bank_matches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_matches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    matched_by uuid NOT NULL,
    matched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_bank_statement_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_statement_lines (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    statement_id uuid NOT NULL,
    line_no bigint NOT NULL,
    posted_on date NOT NULL,
    amount numeric NOT NULL,
    description text NOT NULL,
    bank_reference text,
    CONSTRAINT trust_bank_statement_lines_amount_check CHECK (((amount <> (0)::numeric) AND (amount = round(amount, 2)) AND (abs(amount) < (1000000000)::numeric))),
    CONSTRAINT trust_bank_statement_lines_bank_reference_check CHECK (((bank_reference ~ '\S'::text) AND (length(bank_reference) <= 255))),
    CONSTRAINT trust_bank_statement_lines_description_check CHECK ((length(description) <= 500)),
    CONSTRAINT trust_bank_statement_lines_line_no_check CHECK ((line_no >= 1))
);


--
-- Name: trust_bank_statements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_statements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    first_day date NOT NULL,
    last_day date NOT NULL,
    opening_balance numeric NOT NULL,
    closing_balance numeric NOT NULL,
    line_count bigint NOT NULL,
    source text NOT NULL,
    file_sha256 text NOT NULL,
    imported_by uuid NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_bank_statements_check CHECK ((last_day >= first_day)),
    CONSTRAINT trust_bank_statements_closing_balance_check CHECK ((closing_balance = round(closing_balance, 2))),
    CONSTRAINT trust_bank_statements_file_sha256_check CHECK ((file_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT trust_bank_statements_line_count_check CHECK (((line_count >= 0) AND (line_count <= 20000))),
    CONSTRAINT trust_bank_statements_opening_balance_check CHECK ((opening_balance = round(opening_balance, 2))),
    CONSTRAINT trust_bank_statements_source_check CHECK ((source = ANY (ARRAY['csv'::text, 'ofx'::text])))
);


--
-- Name: trust_bank_unmatches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_unmatches (
    match_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    unmatched_by uuid NOT NULL,
    unmatched_at timestamp with time zone DEFAULT now() NOT NULL
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
    owner_id uuid,
    CONSTRAINT trust_bill_approvals_approved_by_check CHECK ((approved_by <> ''::text))
);


--
-- Name: trust_bill_declines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_declines (
    bill_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    declined_on date NOT NULL,
    declined_by text NOT NULL,
    reason text NOT NULL,
    owner_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_bill_declines_declined_by_check CHECK (((declined_by <> ''::text) AND (length(declined_by) <= 200))),
    CONSTRAINT trust_bill_declines_reason_check CHECK (((reason <> ''::text) AND (length(reason) <= 1000)))
);


--
-- Name: trust_bill_expense_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_expense_accounts (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    bill_id uuid NOT NULL,
    expense_account_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_bill_expense_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_bill_expense_accounts ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trust_bill_expense_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_bill_markup_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_markup_fees (
    bill_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    transfer_id text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_bill_markups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_markups (
    bill_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    percent numeric NOT NULL,
    amount numeric NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_bill_markups_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_bill_markups_percent_check CHECK (((percent > (0)::numeric) AND (percent <= (100)::numeric)))
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
-- Name: trust_bill_splits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bill_splits (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    bill_id uuid NOT NULL,
    categorized_id bigint NOT NULL,
    expense_account_id uuid NOT NULL,
    amount numeric NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_bill_splits_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2))))
);


--
-- Name: trust_bill_splits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_bill_splits ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trust_bill_splits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_budgets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_budgets (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    month date NOT NULL,
    line text NOT NULL,
    expense_account_id uuid,
    amount numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_budgets_amount_check CHECK (((amount >= (0)::numeric) AND (amount = round(amount, 2)) AND (amount <= (100000000)::numeric))),
    CONSTRAINT trust_budgets_check CHECK (((line = 'expense account'::text) = (expense_account_id IS NOT NULL))),
    CONSTRAINT trust_budgets_line_check CHECK ((line = ANY (ARRAY['rent'::text, 'recurring charges'::text, 'late fees'::text, 'NSF fees'::text, 'other charges'::text, 'management fees'::text, 'leasing fees'::text, 'expense account'::text, 'bills not in an expense account'::text]))),
    CONSTRAINT trust_budgets_month_check CHECK ((month = (date_trunc('month'::text, (month)::timestamp with time zone))::date))
);


--
-- Name: trust_budgets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_budgets ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trust_budgets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_calendar_feed_revocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_calendar_feed_revocations (
    feed_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_calendar_feeds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_calendar_feeds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    staff_id uuid,
    tenant_id uuid,
    group_id uuid,
    token_sha256 bytea NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_calendar_feeds_check CHECK ((num_nonnulls(staff_id, tenant_id) = 1)),
    CONSTRAINT trust_calendar_feeds_check1 CHECK (((group_id IS NULL) OR (staff_id IS NOT NULL))),
    CONSTRAINT trust_calendar_feeds_token_sha256_check CHECK ((length(token_sha256) = 32))
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
-- Name: trust_charge_waivers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_charge_waivers (
    credit_charge_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    waived_charge_id uuid NOT NULL,
    reason text NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_charge_waivers_reason_check CHECK (((reason ~ '\S'::text) AND (char_length(reason) <= 500)))
);


--
-- Name: trust_company_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_company_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    address_line_1 text,
    address_line_2 text,
    city text,
    state text,
    postal_code text,
    late_grace_days integer,
    late_flat_fee numeric,
    late_percent numeric,
    late_maximum numeric,
    nsf_fee numeric,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    reply_to text,
    CONSTRAINT trust_company_profiles_address_line_1_check CHECK (((btrim(address_line_1) <> ''::text) AND (char_length(address_line_1) <= 200))),
    CONSTRAINT trust_company_profiles_address_line_2_check CHECK (((btrim(address_line_2) <> ''::text) AND (char_length(address_line_2) <= 200))),
    CONSTRAINT trust_company_profiles_check CHECK (((num_nonnulls(address_line_1, city, state, postal_code) = ANY (ARRAY[0, 4])) AND ((address_line_2 IS NULL) OR (address_line_1 IS NOT NULL)))),
    CONSTRAINT trust_company_profiles_check1 CHECK (((num_nonnulls(late_grace_days, late_flat_fee, late_percent) = ANY (ARRAY[0, 3])) AND ((late_maximum IS NULL) OR (late_grace_days IS NOT NULL)))),
    CONSTRAINT trust_company_profiles_city_check CHECK (((btrim(city) <> ''::text) AND (char_length(city) <= 100))),
    CONSTRAINT trust_company_profiles_late_flat_fee_check CHECK (((late_flat_fee >= (0)::numeric) AND (late_flat_fee = round(late_flat_fee, 2)))),
    CONSTRAINT trust_company_profiles_late_grace_days_check CHECK (((late_grace_days >= 0) AND (late_grace_days <= 365))),
    CONSTRAINT trust_company_profiles_late_maximum_check CHECK (((late_maximum >= (0)::numeric) AND (late_maximum = round(late_maximum, 2)))),
    CONSTRAINT trust_company_profiles_late_percent_check CHECK (((late_percent >= (0)::numeric) AND (late_percent <= (100)::numeric))),
    CONSTRAINT trust_company_profiles_nsf_fee_check CHECK (((nsf_fee >= (0)::numeric) AND (nsf_fee = round(nsf_fee, 2)))),
    CONSTRAINT trust_company_profiles_postal_code_check CHECK (((btrim(postal_code) <> ''::text) AND (char_length(postal_code) <= 20))),
    CONSTRAINT trust_company_profiles_reply_to_check CHECK (((reply_to ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'::text) AND (char_length(reply_to) <= 254))),
    CONSTRAINT trust_company_profiles_state_check CHECK (((btrim(state) <> ''::text) AND (char_length(state) <= 50)))
);


--
-- Name: trust_contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_contacts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    email text,
    phone text,
    alternate_phone text,
    address_line_1 text,
    address_line_2 text,
    city text,
    state text,
    postal_code text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    by_person boolean DEFAULT false NOT NULL,
    CONSTRAINT trust_contacts_address_line_1_check CHECK (((char_length(address_line_1) >= 1) AND (char_length(address_line_1) <= 200))),
    CONSTRAINT trust_contacts_address_line_2_check CHECK (((char_length(address_line_2) >= 1) AND (char_length(address_line_2) <= 200))),
    CONSTRAINT trust_contacts_alternate_phone_check CHECK ((alternate_phone ~ '^[+]?[0-9 ().-]{7,30}$'::text)),
    CONSTRAINT trust_contacts_by_person CHECK (((NOT by_person) OR ((tenant_id IS NOT NULL) AND (recorded_by IS NULL)))),
    CONSTRAINT trust_contacts_check CHECK ((num_nonnulls(tenant_id, owner_id, vendor_id) = 1)),
    CONSTRAINT trust_contacts_check1 CHECK (((num_nonnulls(address_line_1, city, state, postal_code) = ANY (ARRAY[0, 4])) AND ((address_line_2 IS NULL) OR (address_line_1 IS NOT NULL)))),
    CONSTRAINT trust_contacts_city_check CHECK (((char_length(city) >= 1) AND (char_length(city) <= 100))),
    CONSTRAINT trust_contacts_email_check CHECK (((email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'::text) AND (char_length(email) <= 254))),
    CONSTRAINT trust_contacts_phone_check CHECK ((phone ~ '^[+]?[0-9 ().-]{7,30}$'::text)),
    CONSTRAINT trust_contacts_postal_code_check CHECK (((char_length(postal_code) >= 1) AND (char_length(postal_code) <= 20))),
    CONSTRAINT trust_contacts_state_check CHECK (((char_length(state) >= 1) AND (char_length(state) <= 50)))
);


--
-- Name: trust_custom_role_areas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_custom_role_areas (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    role_id uuid NOT NULL,
    area text NOT NULL,
    granted boolean NOT NULL,
    changed_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_custom_role_areas_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_custom_role_areas ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trust_custom_role_areas_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_custom_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_custom_roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    name text NOT NULL,
    changed_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_custom_roles_name_check CHECK (((name ~ '\S'::text) AND (length(name) <= 100)))
);


--
-- Name: trust_cutover_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_cutover_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    issued_on date NOT NULL,
    amount numeric NOT NULL,
    description text NOT NULL,
    reference text,
    entered_by uuid NOT NULL,
    entered_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_cutover_items_amount_check CHECK (((amount <> (0)::numeric) AND (amount = round(amount, 2)) AND (abs(amount) < (1000000000)::numeric))),
    CONSTRAINT trust_cutover_items_description_check CHECK (((description ~ '\S'::text) AND (length(description) <= 200))),
    CONSTRAINT trust_cutover_items_reference_check CHECK (((reference ~ '\S'::text) AND (length(reference) <= 100)))
);


--
-- Name: trust_cutover_signoffs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_cutover_signoffs (
    bank_account_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    signed_by uuid NOT NULL,
    signed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_deposit_return_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_deposit_return_lines (
    lease_id uuid NOT NULL,
    line_no bigint NOT NULL,
    pmc_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    charge_id uuid,
    amount numeric NOT NULL,
    transfer_id text NOT NULL,
    CONSTRAINT trust_deposit_return_lines_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_deposit_return_lines_line_no_check CHECK ((line_no >= 1))
);


--
-- Name: trust_deposit_returns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_deposit_returns (
    lease_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    request_key text NOT NULL,
    returned_at timestamp with time zone NOT NULL,
    held numeric NOT NULL,
    kept numeric NOT NULL,
    returned numeric NOT NULL,
    returned_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_deposit_returns_check CHECK (((kept + returned) = held)),
    CONSTRAINT trust_deposit_returns_held_check CHECK ((held > (0)::numeric)),
    CONSTRAINT trust_deposit_returns_kept_check CHECK ((kept >= (0)::numeric)),
    CONSTRAINT trust_deposit_returns_request_key_check CHECK ((request_key <> ''::text)),
    CONSTRAINT trust_deposit_returns_returned_check CHECK ((returned >= (0)::numeric))
);


--
-- Name: trust_document_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_document_files (
    document_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    content bytea NOT NULL
);


--
-- Name: trust_document_removals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_document_removals (
    document_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    removed_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    kind text NOT NULL,
    vendor_id uuid,
    bill_id uuid,
    lease_id uuid,
    owner_id uuid,
    title text NOT NULL,
    content_type text NOT NULL,
    size bigint NOT NULL,
    sha256 bytea NOT NULL,
    uploaded_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    work_order_id uuid,
    property_id uuid,
    unit_id uuid,
    tenant_id uuid,
    request_id uuid,
    CONSTRAINT trust_documents_check1 CHECK (((kind = 'receipt'::text) = (bill_id IS NOT NULL))),
    CONSTRAINT trust_documents_check3 CHECK (((kind = 'owner_document'::text) = (owner_id IS NOT NULL))),
    CONSTRAINT trust_documents_content_type_check CHECK ((content_type = ANY (ARRAY['application/pdf'::text, 'image/jpeg'::text, 'image/png'::text]))),
    CONSTRAINT trust_documents_sha256_check CHECK ((length(sha256) = 32)),
    CONSTRAINT trust_documents_size_check CHECK (((size >= 1) AND (size <= 5242880))),
    CONSTRAINT trust_documents_title_check CHECK (((title ~ '\S'::text) AND (length(title) <= 200)))
);


--
-- Name: trust_email_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_email_deliveries (
    email_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    attempted_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    sender text NOT NULL,
    error text,
    CONSTRAINT trust_email_deliveries_error_check CHECK (((error IS NULL) OR ((error <> ''::text) AND (length(error) <= 1000)))),
    CONSTRAINT trust_email_deliveries_sender_check CHECK ((sender = ANY (ARRAY['fake'::text, 'smtp'::text])))
);


--
-- Name: trust_email_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_email_settings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    rent_reminder_days integer,
    late_notices boolean NOT NULL,
    owner_statement_day integer,
    work_order_notices boolean NOT NULL,
    adverse_action_notices boolean NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_email_settings_owner_statement_day_check CHECK (((owner_statement_day >= 1) AND (owner_statement_day <= 28))),
    CONSTRAINT trust_email_settings_rent_reminder_days_check CHECK (((rent_reminder_days >= 1) AND (rent_reminder_days <= 15)))
);


--
-- Name: trust_emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_emails (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    kind text NOT NULL,
    bill_id uuid,
    to_address text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    staff_id uuid,
    sent_by uuid,
    needs_link boolean DEFAULT false NOT NULL,
    lease_id uuid,
    charge_id uuid,
    period date,
    work_order_step_id uuid,
    application_id uuid,
    applicant_id uuid,
    request_id uuid,
    offer_id uuid,
    signer_position bigint,
    CONSTRAINT trust_emails_body_check CHECK (((body <> ''::text) AND (length(body) <= 10000))),
    CONSTRAINT trust_emails_check CHECK (((kind = 'bill_approval'::text) = (bill_id IS NOT NULL))),
    CONSTRAINT trust_emails_subject_check CHECK (((subject <> ''::text) AND (length(subject) <= 200))),
    CONSTRAINT trust_emails_to_address_check CHECK (((to_address <> ''::text) AND (length(to_address) <= 320)))
);


--
-- Name: trust_expense_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_expense_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    name text NOT NULL,
    schedule_e_line integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_expense_accounts_name_check CHECK (((name ~ '\S'::text) AND (length(name) <= 200))),
    CONSTRAINT trust_expense_accounts_schedule_e_line_check CHECK (((schedule_e_line >= 5) AND (schedule_e_line <= 19)))
);


--
-- Name: trust_housing_authorities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_housing_authorities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    name text NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_housing_authorities_name_check CHECK (((btrim(name) <> ''::text) AND (length(name) <= 200)))
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
-- Name: trust_inspection_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_inspection_items (
    inspection_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    line integer NOT NULL,
    room text NOT NULL,
    condition text NOT NULL,
    note text,
    CONSTRAINT trust_inspection_items_condition_check CHECK ((condition = ANY (ARRAY['good'::text, 'fair'::text, 'poor'::text, 'damaged'::text, 'missing'::text]))),
    CONSTRAINT trust_inspection_items_line_check CHECK (((line >= 1) AND (line <= 100))),
    CONSTRAINT trust_inspection_items_note_check CHECK (((note ~ '\S'::text) AND (length(note) <= 500))),
    CONSTRAINT trust_inspection_items_room_check CHECK (((room ~ '\S'::text) AND (length(room) <= 100)))
);


--
-- Name: trust_inspections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_inspections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    kind text NOT NULL,
    inspected_on date NOT NULL,
    notes text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_inspections_kind_check CHECK ((kind = ANY (ARRAY['move-in'::text, 'move-out'::text]))),
    CONSTRAINT trust_inspections_notes_check CHECK (((notes ~ '\S'::text) AND (length(notes) <= 2000)))
);


--
-- Name: trust_invites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_invites (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    login_id uuid NOT NULL,
    token_hash bytea NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    CONSTRAINT trust_invites_check CHECK ((expires_at > created_at)),
    CONSTRAINT trust_invites_token_hash_check CHECK ((length(token_hash) = 32))
);


--
-- Name: trust_journal_entry_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_journal_entry_decisions (
    entry_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    decision text NOT NULL,
    decided_by uuid NOT NULL,
    transfer_ids text[],
    reason text,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_journal_entry_decisions_check CHECK (((decision = 'posted'::text) = (transfer_ids IS NOT NULL))),
    CONSTRAINT trust_journal_entry_decisions_check1 CHECK (((decision = 'declined'::text) = (reason IS NOT NULL))),
    CONSTRAINT trust_journal_entry_decisions_decision_check CHECK ((decision = ANY (ARRAY['posted'::text, 'declined'::text]))),
    CONSTRAINT trust_journal_entry_decisions_reason_check CHECK (((reason ~ '\S'::text) AND (length(reason) <= 1000))),
    CONSTRAINT trust_journal_entry_decisions_transfer_ids_check CHECK ((cardinality(transfer_ids) > 0))
);


--
-- Name: trust_journal_entry_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_journal_entry_lines (
    entry_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    line integer NOT NULL,
    from_account text NOT NULL,
    to_account text NOT NULL,
    amount numeric NOT NULL,
    CONSTRAINT trust_journal_entry_lines_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)) AND (amount <= (10000000)::numeric))),
    CONSTRAINT trust_journal_entry_lines_check CHECK ((from_account <> to_account)),
    CONSTRAINT trust_journal_entry_lines_line_check CHECK (((line >= 1) AND (line <= 20)))
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
-- Name: trust_lead_closings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lead_closings (
    lead_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    outcome text NOT NULL,
    reason text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lead_closings_outcome_check CHECK ((outcome = ANY (ARRAY['applied'::text, 'lost'::text]))),
    CONSTRAINT trust_lead_closings_reason_check CHECK (((reason IS NULL) OR ((reason ~ '\S'::text) AND (length(reason) <= 500))))
);


--
-- Name: trust_leads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_leads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    email text,
    phone text,
    property_id uuid,
    unit_id uuid,
    source text,
    move_in_on date,
    note text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_leads_check CHECK (((email IS NOT NULL) OR (phone IS NOT NULL))),
    CONSTRAINT trust_leads_display_name_check CHECK (((display_name ~ '\S'::text) AND (length(display_name) <= 200))),
    CONSTRAINT trust_leads_email_check CHECK (((email IS NULL) OR ((email ~ '^[^@\s]+@[^@\s]+$'::text) AND (length(email) <= 320)))),
    CONSTRAINT trust_leads_note_check CHECK (((note IS NULL) OR ((note ~ '\S'::text) AND (length(note) <= 2000)))),
    CONSTRAINT trust_leads_phone_check CHECK (((phone IS NULL) OR ((phone ~ '\S'::text) AND (length(phone) <= 40)))),
    CONSTRAINT trust_leads_source_check CHECK (((source IS NULL) OR ((source ~ '\S'::text) AND (length(source) <= 60))))
);


--
-- Name: trust_lease_break_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_break_fees (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    flat_fee numeric DEFAULT 0 NOT NULL,
    rent_months numeric DEFAULT 0 NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_break_fees_flat_fee_check CHECK (((flat_fee >= (0)::numeric) AND (flat_fee = round(flat_fee, 2)))),
    CONSTRAINT trust_lease_break_fees_rent_months_check CHECK (((rent_months >= (0)::numeric) AND (rent_months <= (12)::numeric) AND (rent_months = round(rent_months, 2))))
);


--
-- Name: trust_lease_departures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_departures (
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    left_on date NOT NULL,
    deposit_to uuid,
    moved numeric DEFAULT 0 NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_departures_check CHECK ((deposit_to <> tenant_id)),
    CONSTRAINT trust_lease_departures_check1 CHECK (((deposit_to IS NOT NULL) OR (moved = (0)::numeric))),
    CONSTRAINT trust_lease_departures_moved_check CHECK ((moved >= (0)::numeric))
);


--
-- Name: trust_lease_end_adjustments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_end_adjustments (
    adjustment_charge_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    charge_id uuid NOT NULL,
    ends_on date,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_lease_guarantors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_guarantors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    display_name text NOT NULL,
    email text,
    phone text,
    offer_id uuid,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_guarantors_display_name_check CHECK (((display_name ~ '\S'::text) AND (length(display_name) <= 200))),
    CONSTRAINT trust_lease_guarantors_email_check CHECK (((email = lower(email)) AND (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'::text) AND (length(email) <= 254))),
    CONSTRAINT trust_lease_guarantors_phone_check CHECK (((phone ~ '\S'::text) AND (length(phone) <= 30)))
);


--
-- Name: trust_lease_household_removals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_household_removals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    occupant_id uuid,
    pet_id uuid,
    vehicle_id uuid,
    guarantor_id uuid,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_household_removals_check CHECK ((num_nonnulls(occupant_id, pet_id, vehicle_id, guarantor_id) = 1))
);


--
-- Name: trust_lease_occupants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_occupants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    display_name text NOT NULL,
    relationship text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_occupants_display_name_check CHECK (((display_name ~ '\S'::text) AND (length(display_name) <= 200))),
    CONSTRAINT trust_lease_occupants_relationship_check CHECK (((relationship ~ '\S'::text) AND (length(relationship) <= 100)))
);


--
-- Name: trust_lease_offer_charges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_offer_charges (
    offer_id uuid NOT NULL,
    "position" bigint NOT NULL,
    pmc_id uuid NOT NULL,
    kind text NOT NULL,
    amount numeric NOT NULL,
    memo text NOT NULL,
    CONSTRAINT trust_lease_offer_charges_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_lease_offer_charges_kind_check CHECK ((kind = ANY (ARRAY['fee'::text, 'credit'::text]))),
    CONSTRAINT trust_lease_offer_charges_memo_check CHECK (((btrim(memo) <> ''::text) AND (char_length(memo) <= 200))),
    CONSTRAINT trust_lease_offer_charges_position_check CHECK ((("position" >= 1) AND ("position" <= 10)))
);


--
-- Name: trust_lease_offer_completions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_offer_completions (
    offer_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    completed_by uuid NOT NULL,
    completed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_lease_offer_signers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_offer_signers (
    offer_id uuid NOT NULL,
    "position" bigint NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    email text NOT NULL,
    role text DEFAULT 'tenant'::text NOT NULL,
    CONSTRAINT trust_lease_offer_signers_display_name_check CHECK (((display_name ~ '\S'::text) AND (length(display_name) <= 200))),
    CONSTRAINT trust_lease_offer_signers_email_check CHECK (((email = lower(email)) AND (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'::text) AND (length(email) <= 254))),
    CONSTRAINT trust_lease_offer_signers_position_check CHECK ((("position" >= 1) AND ("position" <= 6))),
    CONSTRAINT trust_lease_offer_signers_role CHECK ((role = ANY (ARRAY['tenant'::text, 'guarantor'::text])))
);


--
-- Name: trust_lease_offer_voids; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_offer_voids (
    offer_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    reason text,
    recorded_by uuid,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_offer_voids_reason_check CHECK (((btrim(reason) <> ''::text) AND (char_length(reason) <= 500)))
);


--
-- Name: trust_lease_offers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_offers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    application_id uuid,
    renews_lease_id uuid,
    unit_id uuid NOT NULL,
    starts_on date NOT NULL,
    ends_on date,
    monthly_rent numeric NOT NULL,
    document_title text NOT NULL,
    document bytea NOT NULL,
    offered_by uuid NOT NULL,
    offered_at timestamp with time zone DEFAULT now() NOT NULL,
    security_deposit numeric,
    CONSTRAINT trust_lease_offers_check CHECK (((application_id IS NULL) <> (renews_lease_id IS NULL))),
    CONSTRAINT trust_lease_offers_check1 CHECK ((ends_on >= starts_on)),
    CONSTRAINT trust_lease_offers_document_check CHECK ((((length(document) >= 5) AND (length(document) <= 5000000)) AND (SUBSTRING(document FROM 1 FOR 5) = '\x255044462d'::bytea))),
    CONSTRAINT trust_lease_offers_document_title_check CHECK (((document_title ~ '\S'::text) AND (length(document_title) <= 200))),
    CONSTRAINT trust_lease_offers_monthly_rent_check CHECK ((monthly_rent > (0)::numeric)),
    CONSTRAINT trust_lease_offers_security_deposit_check CHECK (((security_deposit > (0)::numeric) AND (security_deposit = round(security_deposit, 2))))
);


--
-- Name: trust_lease_pets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_pets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    display_name text NOT NULL,
    kind text NOT NULL,
    breed text,
    weight_lbs numeric,
    assistance_animal boolean DEFAULT false NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_pets_breed_check CHECK (((breed ~ '\S'::text) AND (length(breed) <= 100))),
    CONSTRAINT trust_lease_pets_display_name_check CHECK (((display_name ~ '\S'::text) AND (length(display_name) <= 100))),
    CONSTRAINT trust_lease_pets_kind_check CHECK (((kind ~ '\S'::text) AND (length(kind) <= 50))),
    CONSTRAINT trust_lease_pets_weight_lbs_check CHECK (((weight_lbs > (0)::numeric) AND (weight_lbs <= (500)::numeric) AND (weight_lbs = round(weight_lbs, 1))))
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
-- Name: trust_lease_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_transfers (
    from_lease_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    to_lease_id uuid NOT NULL,
    moved_on date NOT NULL,
    owner_account text NOT NULL,
    deposit_agreed_on date,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_transfers_check CHECK ((from_lease_id <> to_lease_id))
);


--
-- Name: trust_lease_utilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_utilities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    utility text NOT NULL,
    starts_on date NOT NULL,
    billed boolean NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_utilities_utility_check CHECK ((utility = ANY (ARRAY['water'::text, 'sewer'::text, 'trash'::text, 'gas'::text, 'electric'::text, 'other'::text])))
);


--
-- Name: trust_lease_vehicles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_lease_vehicles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    make text NOT NULL,
    model text,
    color text,
    plate text NOT NULL,
    plate_state text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_lease_vehicles_color_check CHECK (((color ~ '\S'::text) AND (length(color) <= 30))),
    CONSTRAINT trust_lease_vehicles_make_check CHECK (((make ~ '\S'::text) AND (length(make) <= 50))),
    CONSTRAINT trust_lease_vehicles_model_check CHECK (((model ~ '\S'::text) AND (length(model) <= 50))),
    CONSTRAINT trust_lease_vehicles_plate_check CHECK (((plate ~ '\S'::text) AND (length(plate) <= 15))),
    CONSTRAINT trust_lease_vehicles_plate_state_check CHECK (((plate_state ~ '\S'::text) AND (length(plate_state) <= 30)))
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
-- Name: trust_listing_removals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_listing_removals (
    listing_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    removed_by uuid NOT NULL,
    removed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_listings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_listings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    link_id uuid NOT NULL,
    description text,
    listed_by uuid NOT NULL,
    listed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_listings_description_check CHECK (((description ~ '\S'::text) AND (length(description) <= 2000)))
);


--
-- Name: trust_login_secrets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_login_secrets (
    login_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    password_hash text NOT NULL,
    totp_secret text,
    totp_confirmed_at timestamp with time zone,
    totp_last_step bigint,
    failed_sign_ins bigint DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_login_secrets_check CHECK (((totp_confirmed_at IS NULL) OR (totp_secret IS NOT NULL))),
    CONSTRAINT trust_login_secrets_failed_sign_ins_check CHECK ((failed_sign_ins >= 0)),
    CONSTRAINT trust_login_secrets_password_hash_check CHECK ((password_hash ~~ '$argon2id$%'::text))
);


--
-- Name: trust_logins; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_logins (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    kind text NOT NULL,
    staff_id uuid,
    owner_id uuid,
    tenant_id uuid,
    email text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    disabled_at timestamp with time zone,
    CONSTRAINT trust_logins_check CHECK ((((kind = 'staff'::text) AND (staff_id IS NOT NULL) AND (owner_id IS NULL) AND (tenant_id IS NULL)) OR ((kind = 'owner'::text) AND (owner_id IS NOT NULL) AND (staff_id IS NULL) AND (tenant_id IS NULL)) OR ((kind = 'tenant'::text) AND (tenant_id IS NOT NULL) AND (staff_id IS NULL) AND (owner_id IS NULL)))),
    CONSTRAINT trust_logins_email_check CHECK (((email = lower(email)) AND (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'::text))),
    CONSTRAINT trust_logins_kind_check CHECK ((kind = ANY (ARRAY['staff'::text, 'owner'::text, 'tenant'::text])))
);


--
-- Name: trust_maintenance_request_work_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_maintenance_request_work_orders (
    request_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_maintenance_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_maintenance_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    summary text NOT NULL,
    entry_allowed boolean NOT NULL,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    emergency boolean DEFAULT false NOT NULL,
    CONSTRAINT trust_maintenance_requests_summary_check CHECK (((summary ~ '\S'::text) AND (length(summary) <= 2000)))
);


--
-- Name: trust_management_fee_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_management_fee_settings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    automatic boolean NOT NULL,
    day_of_month integer NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_management_fee_settings_day_of_month_check CHECK (((day_of_month >= 1) AND (day_of_month <= 28)))
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
-- Name: trust_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_notes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid,
    unit_id uuid,
    lease_id uuid,
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    body text NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_notes_body_check CHECK (((btrim(body) <> ''::text) AND (char_length(body) <= 5000))),
    CONSTRAINT trust_notes_check CHECK ((num_nonnulls(property_id, unit_id, lease_id, tenant_id, owner_id, vendor_id) = 1))
);


--
-- Name: trust_nsf_fee_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_nsf_fee_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    fee numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_nsf_fee_policies_fee_check CHECK (((fee >= (0)::numeric) AND (fee = round(fee, 2))))
);


--
-- Name: trust_nsf_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_nsf_fees (
    pmc_id uuid NOT NULL,
    returned_key text NOT NULL,
    policy_id uuid NOT NULL,
    fee numeric NOT NULL,
    fee_charge_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_nsf_fees_fee_check CHECK ((fee > (0)::numeric))
);


--
-- Name: trust_on_call_staff; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_on_call_staff (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    staff_id uuid NOT NULL,
    on_call boolean NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
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
-- Name: trust_owner_contributions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_owner_contributions (
    pmc_id uuid NOT NULL,
    request_key text NOT NULL,
    ledger_account_id text NOT NULL,
    amount numeric NOT NULL,
    transfer_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_owner_contributions_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_owner_contributions_request_key_check CHECK (((request_key <> ''::text) AND (length(request_key) <= 200)))
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
-- Name: trust_owner_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_owner_fees (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    request_key text NOT NULL,
    ledger_account_id text NOT NULL,
    kind text NOT NULL,
    amount numeric NOT NULL,
    memo text NOT NULL,
    transfer_id text NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_owner_fees_amount_check CHECK (((amount > (0)::numeric) AND (amount <= (1000000)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_owner_fees_kind_check CHECK ((kind = ANY (ARRAY['inspection'::text, 'lease_up'::text, 'other'::text]))),
    CONSTRAINT trust_owner_fees_memo_check CHECK (((btrim(memo) <> ''::text) AND (length(memo) <= 200))),
    CONSTRAINT trust_owner_fees_request_key_check CHECK (((length(request_key) >= 1) AND (length(request_key) <= 100)))
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
-- Name: trust_ownership_share_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_ownership_share_lines (
    share_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    pmc_id uuid NOT NULL,
    percent numeric NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_ownership_share_lines_percent_check CHECK (((percent > (0)::numeric) AND (percent <= (100)::numeric) AND (percent = round(percent, 4))))
);


--
-- Name: trust_ownership_shares; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_ownership_shares (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_payment_hold_allowances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_payment_hold_allowances (
    hold_id uuid NOT NULL,
    transfer_id text NOT NULL,
    pmc_id uuid NOT NULL,
    allowed_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_payment_hold_allowances_allowed_by_check CHECK ((allowed_by <> ''::text))
);


--
-- Name: trust_payment_hold_releases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_payment_hold_releases (
    hold_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    ends_on date NOT NULL,
    released_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_payment_hold_releases_released_by_check CHECK ((released_by <> ''::text))
);


--
-- Name: trust_payment_holds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_payment_holds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    starts_on date NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_payment_holds_reason_check CHECK ((reason <> ''::text))
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
-- Name: trust_payout_methods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_payout_methods (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    transfer_id text NOT NULL,
    method text NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_payout_methods_method_check CHECK ((method = ANY (ARRAY['check'::text, 'EFT'::text])))
);


--
-- Name: trust_pmc_fee_withdrawals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_pmc_fee_withdrawals (
    pmc_id uuid NOT NULL,
    request_key text NOT NULL,
    ledger_account_id text NOT NULL,
    amount numeric NOT NULL,
    transfer_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_pmc_fee_withdrawals_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT trust_pmc_fee_withdrawals_request_key_check CHECK ((request_key <> ''::text))
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
-- Name: trust_property_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_property_group_members (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    group_id uuid NOT NULL,
    property_id uuid NOT NULL,
    member boolean NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_property_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_property_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_property_groups_display_name_check CHECK (((display_name <> ''::text) AND (display_name = btrim(display_name)) AND (length(display_name) <= 100)))
);


--
-- Name: trust_reconciliation_preparations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_reconciliation_preparations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    bank_account_id uuid NOT NULL,
    through date NOT NULL,
    bank_balance numeric NOT NULL,
    book_cash numeric NOT NULL,
    prepared_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
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
-- Name: trust_recurring_bill_ends; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_bill_ends (
    recurring_bill_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    ends_on date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_recurring_bill_expense_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_bill_expense_accounts (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    recurring_bill_id uuid NOT NULL,
    expense_account_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_recurring_bill_expense_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_recurring_bill_expense_accounts ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trust_recurring_bill_expense_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_recurring_bills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_bills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    ledger_account_id text NOT NULL,
    reference_prefix text NOT NULL,
    amount numeric NOT NULL,
    memo text NOT NULL,
    day_of_month integer NOT NULL,
    starts_on date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_recurring_bills_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_recurring_bills_day_of_month_check CHECK (((day_of_month >= 1) AND (day_of_month <= 28))),
    CONSTRAINT trust_recurring_bills_memo_check CHECK ((memo <> ''::text)),
    CONSTRAINT trust_recurring_bills_reference_prefix_check CHECK ((reference_prefix <> ''::text))
);


--
-- Name: trust_recurring_charge_ends; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_charge_ends (
    recurring_charge_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    ends_on date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_recurring_charges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_charges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    kind text NOT NULL,
    amount numeric NOT NULL,
    memo text NOT NULL,
    starts_on date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_recurring_charges_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_recurring_charges_kind_check CHECK ((kind = ANY (ARRAY['fee'::text, 'credit'::text]))),
    CONSTRAINT trust_recurring_charges_memo_check CHECK ((memo <> ''::text))
);


--
-- Name: trust_recurring_work_order_ends; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_work_order_ends (
    recurring_work_order_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    ends_on date NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_recurring_work_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_recurring_work_orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    unit_id uuid,
    summary text NOT NULL,
    every_months integer NOT NULL,
    day_of_month integer NOT NULL,
    starts_on date NOT NULL,
    priority text DEFAULT 'normal'::text NOT NULL,
    vendor_id uuid,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_recurring_work_orders_day_of_month_check CHECK (((day_of_month >= 1) AND (day_of_month <= 28))),
    CONSTRAINT trust_recurring_work_orders_every_months_check CHECK (((every_months >= 1) AND (every_months <= 12))),
    CONSTRAINT trust_recurring_work_orders_priority_check CHECK ((priority = ANY (ARRAY['low'::text, 'normal'::text, 'high'::text, 'urgent'::text]))),
    CONSTRAINT trust_recurring_work_orders_summary_check CHECK (((summary ~ '\S'::text) AND (length(summary) <= 500)))
);


--
-- Name: trust_rent_changes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_rent_changes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    effective_on date NOT NULL,
    monthly_rent numeric NOT NULL,
    notice_given_on date NOT NULL,
    served_by text NOT NULL,
    lease_terms text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_rent_changes_check CHECK ((notice_given_on <= ((effective_on - '1 mon'::interval))::date)),
    CONSTRAINT trust_rent_changes_effective_on_check CHECK ((EXTRACT(day FROM effective_on) = (1)::numeric)),
    CONSTRAINT trust_rent_changes_lease_terms_check CHECK (((lease_terms ~ '\S'::text) AND (length(lease_terms) <= 500))),
    CONSTRAINT trust_rent_changes_monthly_rent_check CHECK (((monthly_rent > (0)::numeric) AND (monthly_rent = round(monthly_rent, 2)) AND (monthly_rent <= (1000000)::numeric))),
    CONSTRAINT trust_rent_changes_served_by_check CHECK ((served_by = ANY (ARRAY['in person'::text, 'mail'::text, 'posted'::text, 'email'::text, 'other'::text])))
);


--
-- Name: trust_rent_due_days; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_rent_due_days (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    starts_on date NOT NULL,
    due_day integer NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_rent_due_days_due_day_check CHECK (((due_day >= 1) AND (due_day <= 28))),
    CONSTRAINT trust_rent_due_days_starts_on_check CHECK ((EXTRACT(day FROM starts_on) = (1)::numeric))
);


--
-- Name: trust_rent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_rent_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    due_on date NOT NULL,
    late_fees_as_of date NOT NULL,
    rents bigint NOT NULL,
    recurring bigint NOT NULL,
    late_fees bigint NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_rent_runs_late_fees_check CHECK ((late_fees >= 0)),
    CONSTRAINT trust_rent_runs_recurring_check CHECK ((recurring >= 0)),
    CONSTRAINT trust_rent_runs_rents_check CHECK ((rents >= 0))
);


--
-- Name: trust_renters_insurance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_renters_insurance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    carrier text NOT NULL,
    policy_number text NOT NULL,
    starts_on date NOT NULL,
    expires_on date NOT NULL,
    liability_limit numeric,
    landlord_interested_party boolean DEFAULT false NOT NULL,
    recorded_by uuid,
    submitted_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_renters_insurance_carrier_check CHECK (((carrier ~ '\S'::text) AND (length(carrier) <= 200))),
    CONSTRAINT trust_renters_insurance_check CHECK ((expires_on >= starts_on)),
    CONSTRAINT trust_renters_insurance_check1 CHECK (((recorded_by IS NULL) OR (submitted_by IS NULL))),
    CONSTRAINT trust_renters_insurance_liability_limit_check CHECK (((liability_limit > (0)::numeric) AND (liability_limit = round(liability_limit, 2)))),
    CONSTRAINT trust_renters_insurance_policy_number_check CHECK (((policy_number ~ '\S'::text) AND (length(policy_number) <= 100)))
);


--
-- Name: trust_renters_insurance_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_renters_insurance_terms (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    required boolean NOT NULL,
    minimum_liability numeric,
    starts_on date NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_renters_insurance_terms_check CHECK ((required OR (minimum_liability IS NULL))),
    CONSTRAINT trust_renters_insurance_terms_minimum_liability_check CHECK (((minimum_liability > (0)::numeric) AND (minimum_liability = round(minimum_liability, 2))))
);


--
-- Name: trust_screening_criteria; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_screening_criteria (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    approve_multiple numeric(4,2) NOT NULL,
    conditional_multiple numeric(4,2) NOT NULL,
    written text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_screening_criteria_approve_multiple_check CHECK (((approve_multiple >= (1)::numeric) AND (approve_multiple <= (10)::numeric))),
    CONSTRAINT trust_screening_criteria_check CHECK ((conditional_multiple <= approve_multiple)),
    CONSTRAINT trust_screening_criteria_conditional_multiple_check CHECK ((conditional_multiple >= (0)::numeric)),
    CONSTRAINT trust_screening_criteria_written_check CHECK (((written IS NULL) OR ((written ~ '\S'::text) AND (length(written) <= 4000))))
);


--
-- Name: trust_screenings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_screenings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    application_id uuid NOT NULL,
    partner text NOT NULL,
    partner_reference text NOT NULL,
    agency text NOT NULL,
    recommendation text NOT NULL,
    reasons text[] NOT NULL,
    requested_by uuid NOT NULL,
    reported_at timestamp with time zone DEFAULT now() NOT NULL,
    criteria_id uuid,
    CONSTRAINT trust_screenings_agency_check CHECK ((agency ~ '\S'::text)),
    CONSTRAINT trust_screenings_check CHECK (((recommendation = 'approve'::text) = (cardinality(reasons) = 0))),
    CONSTRAINT trust_screenings_partner_check CHECK ((partner ~ '\S'::text)),
    CONSTRAINT trust_screenings_partner_reference_check CHECK ((partner_reference ~ '\S'::text)),
    CONSTRAINT trust_screenings_recommendation_check CHECK ((recommendation = ANY (ARRAY['approve'::text, 'conditional'::text, 'decline'::text])))
);


--
-- Name: trust_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    login_id uuid NOT NULL,
    token_hash bytea NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    CONSTRAINT trust_sessions_check CHECK ((expires_at > created_at)),
    CONSTRAINT trust_sessions_token_hash_check CHECK ((length(token_hash) = 32))
);


--
-- Name: trust_shared_bill_parts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_shared_bill_parts (
    shared_bill_id uuid NOT NULL,
    part integer NOT NULL,
    pmc_id uuid NOT NULL,
    bill_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_shared_bill_parts_part_check CHECK (((part >= 1) AND (part <= 20)))
);


--
-- Name: trust_shared_bills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_shared_bills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    reference text NOT NULL,
    amount numeric NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_shared_bills_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_shared_bills_reference_check CHECK (((reference ~ '\S'::text) AND (length(reference) <= 100)))
);


--
-- Name: trust_showing_outcomes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_showing_outcomes (
    showing_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    outcome text NOT NULL,
    note text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_showing_outcomes_note_check CHECK (((note IS NULL) OR ((note ~ '\S'::text) AND (length(note) <= 2000)))),
    CONSTRAINT trust_showing_outcomes_outcome_check CHECK ((outcome = ANY (ARRAY['shown'::text, 'no_show'::text, 'cancelled'::text])))
);


--
-- Name: trust_showings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_showings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    shown_by uuid,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_signature_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_signature_evidence (
    offer_id uuid NOT NULL,
    "position" bigint NOT NULL,
    pmc_id uuid NOT NULL,
    link_id uuid NOT NULL,
    typed_name text NOT NULL,
    drawing bytea,
    document_sha256 bytea NOT NULL,
    ip inet,
    user_agent text,
    signed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_signature_evidence_document_sha256_check CHECK ((length(document_sha256) = 32)),
    CONSTRAINT trust_signature_evidence_drawing_check CHECK ((((length(drawing) >= 9) AND (length(drawing) <= 100000)) AND (SUBSTRING(drawing FROM 1 FOR 8) = '\x89504e470d0a1a0a'::bytea))),
    CONSTRAINT trust_signature_evidence_typed_name_check CHECK (((typed_name ~ '\S'::text) AND (char_length(typed_name) <= 200))),
    CONSTRAINT trust_signature_evidence_user_agent_check CHECK ((char_length(user_agent) <= 500))
);


--
-- Name: trust_signature_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_signature_requests (
    offer_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    partner text NOT NULL,
    partner_reference text NOT NULL,
    sent_by uuid NOT NULL,
    sent_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_signature_requests_partner_check CHECK ((partner ~ '\S'::text)),
    CONSTRAINT trust_signature_requests_partner_reference_check CHECK ((partner_reference ~ '\S'::text))
);


--
-- Name: trust_signatures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_signatures (
    offer_id uuid NOT NULL,
    "position" bigint NOT NULL,
    pmc_id uuid NOT NULL,
    signed_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_signing_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_signing_events (
    link_id uuid NOT NULL,
    kind text NOT NULL,
    pmc_id uuid NOT NULL,
    disclosure text,
    ip inet,
    user_agent text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_signing_events_check CHECK (((kind = 'consented'::text) = (disclosure IS NOT NULL))),
    CONSTRAINT trust_signing_events_disclosure_check CHECK (((btrim(disclosure) <> ''::text) AND (char_length(disclosure) <= 5000))),
    CONSTRAINT trust_signing_events_kind_check CHECK ((kind = ANY (ARRAY['consented'::text, 'opened'::text, 'paper'::text]))),
    CONSTRAINT trust_signing_events_user_agent_check CHECK ((char_length(user_agent) <= 500))
);


--
-- Name: trust_signing_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_signing_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    offer_id uuid NOT NULL,
    "position" bigint NOT NULL,
    token_sha256 bytea NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_signing_links_token_sha256_check CHECK ((length(token_sha256) = 32))
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
-- Name: trust_staff_custom_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_staff_custom_roles (
    id bigint NOT NULL,
    pmc_id uuid NOT NULL,
    staff_id uuid NOT NULL,
    role_id uuid NOT NULL,
    granted boolean NOT NULL,
    changed_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: trust_staff_custom_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.trust_staff_custom_roles ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.trust_staff_custom_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: trust_staff_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_staff_roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    staff_id uuid NOT NULL,
    role text NOT NULL,
    granted boolean NOT NULL,
    changed_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_staff_roles_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'approve'::text, 'disburse'::text])))
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
-- Name: trust_unit_areas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_unit_areas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    unit_id uuid NOT NULL,
    square_feet bigint NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_unit_areas_square_feet_check CHECK (((square_feet > 0) AND (square_feet <= 1000000)))
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
-- Name: trust_utility_bills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_utility_bills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    property_id uuid NOT NULL,
    utility text NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    amount numeric NOT NULL,
    method text NOT NULL,
    owner_percent numeric NOT NULL,
    master_usage numeric,
    due_on date NOT NULL,
    memo text,
    bill_id uuid,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_utility_bills_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)) AND (amount <= (10000000)::numeric))),
    CONSTRAINT trust_utility_bills_check CHECK (((period_end >= period_start) AND (period_end < (period_start + 366)))),
    CONSTRAINT trust_utility_bills_check1 CHECK (((method = 'submeter'::text) = (master_usage IS NOT NULL))),
    CONSTRAINT trust_utility_bills_master_usage_check CHECK ((master_usage > (0)::numeric)),
    CONSTRAINT trust_utility_bills_memo_check CHECK (((btrim(memo) <> ''::text) AND (length(memo) <= 100))),
    CONSTRAINT trust_utility_bills_method_check CHECK ((method = ANY (ARRAY['units'::text, 'square_feet'::text, 'occupants'::text, 'submeter'::text]))),
    CONSTRAINT trust_utility_bills_owner_percent_check CHECK (((owner_percent >= (0)::numeric) AND (owner_percent <= (100)::numeric))),
    CONSTRAINT trust_utility_bills_utility_check CHECK ((utility = ANY (ARRAY['water'::text, 'sewer'::text, 'trash'::text, 'gas'::text, 'electric'::text, 'other'::text])))
);


--
-- Name: trust_utility_shares; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_utility_shares (
    utility_bill_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    billed boolean NOT NULL,
    weight numeric NOT NULL,
    usage numeric,
    amount numeric NOT NULL,
    charge_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_utility_shares_amount_check CHECK (((amount >= (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_utility_shares_check CHECK (((charge_id IS NULL) = (amount = (0)::numeric))),
    CONSTRAINT trust_utility_shares_check1 CHECK ((billed OR (amount = (0)::numeric))),
    CONSTRAINT trust_utility_shares_usage_check CHECK ((usage >= (0)::numeric)),
    CONSTRAINT trust_utility_shares_weight_check CHECK ((weight >= (0)::numeric))
);


--
-- Name: trust_vendor_insurance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_vendor_insurance (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    coverage text NOT NULL,
    carrier text NOT NULL,
    policy_number text NOT NULL,
    starts_on date NOT NULL,
    expires_on date NOT NULL,
    limit_amount numeric,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_vendor_insurance_carrier_check CHECK (((carrier ~ '\S'::text) AND (length(carrier) <= 200))),
    CONSTRAINT trust_vendor_insurance_check CHECK ((expires_on >= starts_on)),
    CONSTRAINT trust_vendor_insurance_coverage_check CHECK ((coverage = ANY (ARRAY['general liability'::text, 'workers compensation'::text, 'commercial auto'::text, 'professional liability'::text, 'other'::text]))),
    CONSTRAINT trust_vendor_insurance_limit_amount_check CHECK (((limit_amount > (0)::numeric) AND (limit_amount = round(limit_amount, 2)) AND (limit_amount <= (1000000000)::numeric))),
    CONSTRAINT trust_vendor_insurance_policy_number_check CHECK (((policy_number ~ '\S'::text) AND (length(policy_number) <= 100)))
);


--
-- Name: trust_vendor_link_revocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_vendor_link_revocations (
    link_id uuid NOT NULL,
    pmc_id uuid NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trust_vendor_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_vendor_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    token_sha256 bytea NOT NULL,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_vendor_links_token_sha256_check CHECK ((length(token_sha256) = 32))
);


--
-- Name: trust_vendor_payments_carried_over; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_vendor_payments_carried_over (
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    year integer NOT NULL,
    paid_before date NOT NULL,
    amount numeric NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT trust_vendor_payments_carried_over_amount_check CHECK (((amount > (0)::numeric) AND (amount = round(amount, 2)))),
    CONSTRAINT trust_vendor_payments_carried_over_check CHECK (((paid_before > make_date(year, 1, 1)) AND (paid_before <= make_date((year + 1), 1, 1)))),
    CONSTRAINT trust_vendor_payments_carried_over_year_check CHECK (((year >= 1900) AND (year <= 9998)))
);


--
-- Name: trust_vendor_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_vendor_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    trade text,
    gets_1099 boolean NOT NULL,
    payment_method text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_vendor_profiles_payment_method_check CHECK ((payment_method = ANY (ARRAY['check'::text, 'EFT'::text]))),
    CONSTRAINT trust_vendor_profiles_trade_check CHECK (((btrim(trade) <> ''::text) AND (char_length(trade) <= 100)))
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
-- Name: trust_work_order_details; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_work_order_details (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    priority text NOT NULL,
    description text,
    due_on date,
    entry text,
    recorded_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT trust_work_order_details_description_check CHECK (((btrim(description) <> ''::text) AND (char_length(description) <= 5000))),
    CONSTRAINT trust_work_order_details_entry_check CHECK ((entry = ANY (ARRAY['allowed'::text, 'call first'::text, 'not allowed'::text]))),
    CONSTRAINT trust_work_order_details_priority_check CHECK ((priority = ANY (ARRAY['low'::text, 'normal'::text, 'high'::text, 'urgent'::text])))
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
    scheduled_for date,
    visit_window text,
    vendor_link_id uuid,
    CONSTRAINT trust_work_order_steps_check CHECK (((step = 'assigned'::text) = (vendor_id IS NOT NULL)))
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
    recurring_work_order_id uuid,
    recurring_on date,
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
-- Name: trust_account_signers trust_account_signers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_account_signers
    ADD CONSTRAINT trust_account_signers_pkey PRIMARY KEY (id);


--
-- Name: trust_adverse_actions trust_adverse_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_adverse_actions
    ADD CONSTRAINT trust_adverse_actions_pkey PRIMARY KEY (application_id);


--
-- Name: trust_applicants trust_applicants_application_id_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applicants
    ADD CONSTRAINT trust_applicants_application_id_email_key UNIQUE (application_id, email);


--
-- Name: trust_applicants trust_applicants_application_id_position_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applicants
    ADD CONSTRAINT trust_applicants_application_id_position_key UNIQUE (application_id, "position");


--
-- Name: trust_applicants trust_applicants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applicants
    ADD CONSTRAINT trust_applicants_pkey PRIMARY KEY (id);


--
-- Name: trust_application_decisions trust_application_decisions_override_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_override_check CHECK (((override_reason IS NULL) OR ((override_reason ~ '\S'::text) AND (length(override_reason) <= 500)))) NOT VALID;


--
-- Name: trust_application_decisions trust_application_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_pkey PRIMARY KEY (application_id);


--
-- Name: trust_application_decisions trust_application_decisions_pmc_id_application_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_pmc_id_application_id_key UNIQUE (pmc_id, application_id);


--
-- Name: trust_application_link_closings trust_application_link_closings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_link_closings
    ADD CONSTRAINT trust_application_link_closings_pkey PRIMARY KEY (link_id);


--
-- Name: trust_application_links trust_application_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_links
    ADD CONSTRAINT trust_application_links_pkey PRIMARY KEY (id);


--
-- Name: trust_application_links trust_application_links_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_links
    ADD CONSTRAINT trust_application_links_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_application_links trust_application_links_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_links
    ADD CONSTRAINT trust_application_links_token_sha256_key UNIQUE (token_sha256);


--
-- Name: trust_application_withdrawals trust_application_withdrawals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_withdrawals
    ADD CONSTRAINT trust_application_withdrawals_pkey PRIMARY KEY (application_id);


--
-- Name: trust_applications trust_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applications
    ADD CONSTRAINT trust_applications_pkey PRIMARY KEY (id);


--
-- Name: trust_applications trust_applications_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applications
    ADD CONSTRAINT trust_applications_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_applications trust_applications_status_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applications
    ADD CONSTRAINT trust_applications_status_token_sha256_key UNIQUE (status_token_sha256);


--
-- Name: trust_archivals trust_archivals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_pkey PRIMARY KEY (id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pkey PRIMARY KEY (id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_assistance_payments trust_assistance_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_payments
    ADD CONSTRAINT trust_assistance_payments_pkey PRIMARY KEY (transfer_id, assistance_charge_id);


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
-- Name: trust_bank_deposit_items trust_bank_deposit_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposit_items
    ADD CONSTRAINT trust_bank_deposit_items_pkey PRIMARY KEY (transfer_id);


--
-- Name: trust_bank_deposits trust_bank_deposits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposits
    ADD CONSTRAINT trust_bank_deposits_pkey PRIMARY KEY (id);


--
-- Name: trust_bank_deposits trust_bank_deposits_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposits
    ADD CONSTRAINT trust_bank_deposits_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_bank_match_cutover_items trust_bank_match_cutover_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_cutover_items
    ADD CONSTRAINT trust_bank_match_cutover_items_pkey PRIMARY KEY (match_id, item_id);


--
-- Name: trust_bank_match_lines trust_bank_match_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_lines
    ADD CONSTRAINT trust_bank_match_lines_pkey PRIMARY KEY (match_id, line_id);


--
-- Name: trust_bank_match_transfers trust_bank_match_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_transfers
    ADD CONSTRAINT trust_bank_match_transfers_pkey PRIMARY KEY (match_id, transfer_id);


--
-- Name: trust_bank_matches trust_bank_matches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_matches
    ADD CONSTRAINT trust_bank_matches_pkey PRIMARY KEY (id);


--
-- Name: trust_bank_matches trust_bank_matches_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_matches
    ADD CONSTRAINT trust_bank_matches_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_bank_statement_lines trust_bank_statement_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statement_lines
    ADD CONSTRAINT trust_bank_statement_lines_pkey PRIMARY KEY (id);


--
-- Name: trust_bank_statement_lines trust_bank_statement_lines_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statement_lines
    ADD CONSTRAINT trust_bank_statement_lines_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_bank_statement_lines trust_bank_statement_lines_statement_id_line_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statement_lines
    ADD CONSTRAINT trust_bank_statement_lines_statement_id_line_no_key UNIQUE (statement_id, line_no);


--
-- Name: trust_bank_statements trust_bank_statements_bank_account_id_file_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_bank_account_id_file_sha256_key UNIQUE (bank_account_id, file_sha256);


--
-- Name: trust_bank_statements trust_bank_statements_bank_account_id_first_day_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_bank_account_id_first_day_key UNIQUE (bank_account_id, first_day);


--
-- Name: trust_bank_statements trust_bank_statements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_pkey PRIMARY KEY (id);


--
-- Name: trust_bank_statements trust_bank_statements_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_bank_unmatches trust_bank_unmatches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_unmatches
    ADD CONSTRAINT trust_bank_unmatches_pkey PRIMARY KEY (match_id);


--
-- Name: trust_bill_approvals trust_bill_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_approvals
    ADD CONSTRAINT trust_bill_approvals_pkey PRIMARY KEY (bill_id);


--
-- Name: trust_bill_declines trust_bill_declines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_declines
    ADD CONSTRAINT trust_bill_declines_pkey PRIMARY KEY (bill_id);


--
-- Name: trust_bill_expense_accounts trust_bill_expense_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_expense_accounts
    ADD CONSTRAINT trust_bill_expense_accounts_pkey PRIMARY KEY (id);


--
-- Name: trust_bill_markup_fees trust_bill_markup_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markup_fees
    ADD CONSTRAINT trust_bill_markup_fees_pkey PRIMARY KEY (bill_id);


--
-- Name: trust_bill_markup_fees trust_bill_markup_fees_transfer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markup_fees
    ADD CONSTRAINT trust_bill_markup_fees_transfer_id_key UNIQUE (transfer_id);


--
-- Name: trust_bill_markups trust_bill_markups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markups
    ADD CONSTRAINT trust_bill_markups_pkey PRIMARY KEY (bill_id);


--
-- Name: trust_bill_markups trust_bill_markups_pmc_id_bill_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markups
    ADD CONSTRAINT trust_bill_markups_pmc_id_bill_id_key UNIQUE (pmc_id, bill_id);


--
-- Name: trust_bill_payments trust_bill_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_payments
    ADD CONSTRAINT trust_bill_payments_pkey PRIMARY KEY (bill_id, step);


--
-- Name: trust_bill_splits trust_bill_splits_categorized_id_expense_account_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_categorized_id_expense_account_id_key UNIQUE (categorized_id, expense_account_id);


--
-- Name: trust_bill_splits trust_bill_splits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_pkey PRIMARY KEY (id);


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
-- Name: trust_budgets trust_budgets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_budgets
    ADD CONSTRAINT trust_budgets_pkey PRIMARY KEY (id);


--
-- Name: trust_calendar_feed_revocations trust_calendar_feed_revocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feed_revocations
    ADD CONSTRAINT trust_calendar_feed_revocations_pkey PRIMARY KEY (feed_id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pkey PRIMARY KEY (id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_token_sha256_key UNIQUE (token_sha256);


--
-- Name: trust_charge_payments trust_charge_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_payments
    ADD CONSTRAINT trust_charge_payments_pkey PRIMARY KEY (charge_id, transfer_id);


--
-- Name: trust_charge_waivers trust_charge_waivers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_waivers
    ADD CONSTRAINT trust_charge_waivers_pkey PRIMARY KEY (credit_charge_id);


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
-- Name: trust_charges trust_charges_work_order_fee; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_charges
    ADD CONSTRAINT trust_charges_work_order_fee CHECK (((work_order_id IS NULL) OR (kind = 'fee'::text))) NOT VALID;


--
-- Name: trust_company_profiles trust_company_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_company_profiles
    ADD CONSTRAINT trust_company_profiles_pkey PRIMARY KEY (id);


--
-- Name: trust_contacts trust_contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_contacts
    ADD CONSTRAINT trust_contacts_pkey PRIMARY KEY (id);


--
-- Name: trust_custom_role_areas trust_custom_role_areas_area_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_custom_role_areas
    ADD CONSTRAINT trust_custom_role_areas_area_check CHECK ((area = ANY (ARRAY['rent'::text, 'bills'::text, 'owners'::text, 'budgets'::text, 'journal'::text, 'month-end'::text, 'switching'::text, 'setup'::text, 'move-out'::text, 'documents'::text, 'leasing'::text, 'maintenance'::text, 'people'::text, 'email'::text]))) NOT VALID;


--
-- Name: trust_custom_role_areas trust_custom_role_areas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_role_areas
    ADD CONSTRAINT trust_custom_role_areas_pkey PRIMARY KEY (id);


--
-- Name: trust_custom_roles trust_custom_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_roles
    ADD CONSTRAINT trust_custom_roles_pkey PRIMARY KEY (id);


--
-- Name: trust_custom_roles trust_custom_roles_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_roles
    ADD CONSTRAINT trust_custom_roles_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_custom_roles trust_custom_roles_pmc_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_roles
    ADD CONSTRAINT trust_custom_roles_pmc_id_name_key UNIQUE (pmc_id, name);


--
-- Name: trust_cutover_items trust_cutover_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_items
    ADD CONSTRAINT trust_cutover_items_pkey PRIMARY KEY (id);


--
-- Name: trust_cutover_items trust_cutover_items_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_items
    ADD CONSTRAINT trust_cutover_items_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_cutover_signoffs trust_cutover_signoffs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_signoffs
    ADD CONSTRAINT trust_cutover_signoffs_pkey PRIMARY KEY (bank_account_id);


--
-- Name: trust_deposit_return_lines trust_deposit_return_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_return_lines
    ADD CONSTRAINT trust_deposit_return_lines_pkey PRIMARY KEY (lease_id, line_no);


--
-- Name: trust_deposit_returns trust_deposit_returns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_returns
    ADD CONSTRAINT trust_deposit_returns_pkey PRIMARY KEY (lease_id);


--
-- Name: trust_deposit_returns trust_deposit_returns_pmc_id_lease_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_returns
    ADD CONSTRAINT trust_deposit_returns_pmc_id_lease_id_key UNIQUE (pmc_id, lease_id);


--
-- Name: trust_deposit_returns trust_deposit_returns_pmc_id_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_returns
    ADD CONSTRAINT trust_deposit_returns_pmc_id_request_key_key UNIQUE (pmc_id, request_key);


--
-- Name: trust_document_files trust_document_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_files
    ADD CONSTRAINT trust_document_files_pkey PRIMARY KEY (document_id);


--
-- Name: trust_document_removals trust_document_removals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_removals
    ADD CONSTRAINT trust_document_removals_pkey PRIMARY KEY (document_id);


--
-- Name: trust_documents trust_documents_kind_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_kind_check CHECK ((kind = ANY (ARRAY['w9'::text, 'receipt'::text, 'move_out_photo'::text, 'owner_document'::text, 'work_order_photo'::text, 'property_document'::text, 'unit_document'::text, 'tenant_document'::text, 'lease_document'::text, 'vendor_document'::text, 'company_logo'::text, 'request_photo'::text, 'listing_photo'::text]))) NOT VALID;


--
-- Name: trust_documents trust_documents_lease_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_lease_check CHECK (((kind = ANY (ARRAY['move_out_photo'::text, 'lease_document'::text])) = (lease_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_documents trust_documents_listing_photo_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_listing_photo_check CHECK (((kind <> 'listing_photo'::text) OR (content_type = ANY (ARRAY['image/png'::text, 'image/jpeg'::text])))) NOT VALID;


--
-- Name: trust_documents trust_documents_logo_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_logo_check CHECK (((kind <> 'company_logo'::text) OR (content_type = ANY (ARRAY['image/png'::text, 'image/jpeg'::text])))) NOT VALID;


--
-- Name: trust_documents trust_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pkey PRIMARY KEY (id);


--
-- Name: trust_documents trust_documents_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_documents trust_documents_property_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_property_check CHECK (((kind = 'property_document'::text) = (property_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_documents trust_documents_request_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_request_check CHECK (((kind = 'request_photo'::text) = (request_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_documents trust_documents_tenant_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_tenant_check CHECK (((kind = 'tenant_document'::text) = (tenant_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_documents trust_documents_unit_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_unit_check CHECK (((kind = ANY (ARRAY['unit_document'::text, 'listing_photo'::text])) = (unit_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_documents trust_documents_vendor_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_vendor_check CHECK (((kind = ANY (ARRAY['w9'::text, 'vendor_document'::text])) = (vendor_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_documents trust_documents_work_order_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_documents
    ADD CONSTRAINT trust_documents_work_order_check CHECK (((kind = 'work_order_photo'::text) = (work_order_id IS NOT NULL))) NOT VALID;


--
-- Name: trust_email_deliveries trust_email_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_email_deliveries
    ADD CONSTRAINT trust_email_deliveries_pkey PRIMARY KEY (email_id, attempted_at);


--
-- Name: trust_email_settings trust_email_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_email_settings
    ADD CONSTRAINT trust_email_settings_pkey PRIMARY KEY (id);


--
-- Name: trust_emails trust_emails_kind_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_emails
    ADD CONSTRAINT trust_emails_kind_check CHECK ((kind = ANY (ARRAY['bill_approval'::text, 'invite'::text, 'password_reset'::text, 'message'::text, 'rent_reminder'::text, 'late_notice'::text, 'owner_statement'::text, 'work_order'::text, 'adverse_action'::text, 'emergency_request'::text, 'signing_request'::text]))) NOT VALID;


--
-- Name: trust_emails trust_emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pkey PRIMARY KEY (id);


--
-- Name: trust_emails trust_emails_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_emails trust_emails_recipient_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_emails
    ADD CONSTRAINT trust_emails_recipient_check CHECK ((
CASE kind
    WHEN 'invite'::text THEN ((num_nonnulls(tenant_id, owner_id, staff_id) = 1) AND (vendor_id IS NULL))
    WHEN 'password_reset'::text THEN ((num_nonnulls(tenant_id, owner_id) = 1) AND (vendor_id IS NULL) AND (staff_id IS NULL))
    WHEN 'message'::text THEN ((num_nonnulls(tenant_id, owner_id, vendor_id) = 1) AND (staff_id IS NULL))
    WHEN 'rent_reminder'::text THEN ((tenant_id IS NOT NULL) AND (lease_id IS NOT NULL) AND (period IS NOT NULL) AND (num_nonnulls(owner_id, vendor_id, staff_id) = 0))
    WHEN 'late_notice'::text THEN ((tenant_id IS NOT NULL) AND (lease_id IS NOT NULL) AND (charge_id IS NOT NULL) AND (num_nonnulls(owner_id, vendor_id, staff_id) = 0))
    WHEN 'owner_statement'::text THEN ((owner_id IS NOT NULL) AND (period IS NOT NULL) AND (num_nonnulls(tenant_id, vendor_id, staff_id) = 0))
    WHEN 'work_order'::text THEN ((vendor_id IS NOT NULL) AND (work_order_step_id IS NOT NULL) AND (num_nonnulls(tenant_id, owner_id, staff_id) = 0))
    WHEN 'adverse_action'::text THEN ((applicant_id IS NOT NULL) AND (application_id IS NOT NULL) AND (num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0))
    WHEN 'emergency_request'::text THEN ((staff_id IS NOT NULL) AND (request_id IS NOT NULL) AND (num_nonnulls(tenant_id, owner_id, vendor_id) = 0))
    WHEN 'signing_request'::text THEN ((offer_id IS NOT NULL) AND (signer_position IS NOT NULL) AND (num_nonnulls(tenant_id, owner_id, vendor_id, staff_id) = 0))
    ELSE ((tenant_id IS NULL) AND (vendor_id IS NULL) AND (staff_id IS NULL))
END AND ((kind = 'message'::text) = (sent_by IS NOT NULL)) AND ((kind = ANY (ARRAY['invite'::text, 'password_reset'::text, 'signing_request'::text])) = needs_link) AND ((kind = ANY (ARRAY['rent_reminder'::text, 'late_notice'::text])) = (lease_id IS NOT NULL)) AND ((kind = 'late_notice'::text) = (charge_id IS NOT NULL)) AND ((kind = ANY (ARRAY['rent_reminder'::text, 'owner_statement'::text])) = (period IS NOT NULL)) AND ((kind = 'owner_statement'::text) <= (period = (date_trunc('month'::text, (period)::timestamp with time zone))::date)) AND ((kind = 'work_order'::text) = (work_order_step_id IS NOT NULL)) AND ((kind = 'adverse_action'::text) = ((application_id IS NOT NULL) AND (applicant_id IS NOT NULL))) AND ((kind = 'emergency_request'::text) = (request_id IS NOT NULL)) AND ((kind = 'signing_request'::text) = ((offer_id IS NOT NULL) OR (signer_position IS NOT NULL))))) NOT VALID;


--
-- Name: trust_expense_accounts trust_expense_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_expense_accounts
    ADD CONSTRAINT trust_expense_accounts_pkey PRIMARY KEY (id);


--
-- Name: trust_expense_accounts trust_expense_accounts_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_expense_accounts
    ADD CONSTRAINT trust_expense_accounts_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_expense_accounts trust_expense_accounts_pmc_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_expense_accounts
    ADD CONSTRAINT trust_expense_accounts_pmc_id_name_key UNIQUE (pmc_id, name);


--
-- Name: trust_housing_authorities trust_housing_authorities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_housing_authorities
    ADD CONSTRAINT trust_housing_authorities_pkey PRIMARY KEY (id);


--
-- Name: trust_housing_authorities trust_housing_authorities_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_housing_authorities
    ADD CONSTRAINT trust_housing_authorities_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_idempotency_keys trust_idempotency_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_idempotency_keys
    ADD CONSTRAINT trust_idempotency_keys_pkey PRIMARY KEY (pmc_id, idempotency_key);


--
-- Name: trust_inspection_items trust_inspection_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspection_items
    ADD CONSTRAINT trust_inspection_items_pkey PRIMARY KEY (inspection_id, line);


--
-- Name: trust_inspections trust_inspections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspections
    ADD CONSTRAINT trust_inspections_pkey PRIMARY KEY (id);


--
-- Name: trust_inspections trust_inspections_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspections
    ADD CONSTRAINT trust_inspections_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_invites trust_invites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_invites
    ADD CONSTRAINT trust_invites_pkey PRIMARY KEY (id);


--
-- Name: trust_invites trust_invites_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_invites
    ADD CONSTRAINT trust_invites_token_hash_key UNIQUE (token_hash);


--
-- Name: trust_journal_entries trust_journal_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entries
    ADD CONSTRAINT trust_journal_entries_pkey PRIMARY KEY (id);


--
-- Name: trust_journal_entries trust_journal_entries_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entries
    ADD CONSTRAINT trust_journal_entries_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_journal_entry_decisions trust_journal_entry_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_decisions
    ADD CONSTRAINT trust_journal_entry_decisions_pkey PRIMARY KEY (entry_id);


--
-- Name: trust_journal_entry_lines trust_journal_entry_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_lines
    ADD CONSTRAINT trust_journal_entry_lines_pkey PRIMARY KEY (entry_id, line);


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
-- Name: trust_lead_closings trust_lead_closings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lead_closings
    ADD CONSTRAINT trust_lead_closings_pkey PRIMARY KEY (lead_id);


--
-- Name: trust_leads trust_leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leads
    ADD CONSTRAINT trust_leads_pkey PRIMARY KEY (id);


--
-- Name: trust_leads trust_leads_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leads
    ADD CONSTRAINT trust_leads_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_lease_break_fees trust_lease_break_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_break_fees
    ADD CONSTRAINT trust_lease_break_fees_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_break_fees trust_lease_break_fees_property_id_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_break_fees
    ADD CONSTRAINT trust_lease_break_fees_property_id_starts_on_key UNIQUE (property_id, starts_on);


--
-- Name: trust_lease_departures trust_lease_departures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_departures
    ADD CONSTRAINT trust_lease_departures_pkey PRIMARY KEY (lease_id, tenant_id);


--
-- Name: trust_lease_end_adjustments trust_lease_end_adjustments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_end_adjustments
    ADD CONSTRAINT trust_lease_end_adjustments_pkey PRIMARY KEY (adjustment_charge_id);


--
-- Name: trust_lease_guarantors trust_lease_guarantors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_guarantors
    ADD CONSTRAINT trust_lease_guarantors_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_guarantors trust_lease_guarantors_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_guarantors
    ADD CONSTRAINT trust_lease_guarantors_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_guarantor_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_guarantor_id_key UNIQUE (guarantor_id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_occupant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_occupant_id_key UNIQUE (occupant_id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pet_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pet_id_key UNIQUE (pet_id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_vehicle_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_vehicle_id_key UNIQUE (vehicle_id);


--
-- Name: trust_lease_occupants trust_lease_occupants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_occupants
    ADD CONSTRAINT trust_lease_occupants_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_occupants trust_lease_occupants_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_occupants
    ADD CONSTRAINT trust_lease_occupants_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_lease_offer_charges trust_lease_offer_charges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_charges
    ADD CONSTRAINT trust_lease_offer_charges_pkey PRIMARY KEY (offer_id, "position");


--
-- Name: trust_lease_offer_completions trust_lease_offer_completions_lease_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_completions
    ADD CONSTRAINT trust_lease_offer_completions_lease_id_key UNIQUE (lease_id);


--
-- Name: trust_lease_offer_completions trust_lease_offer_completions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_completions
    ADD CONSTRAINT trust_lease_offer_completions_pkey PRIMARY KEY (offer_id);


--
-- Name: trust_lease_offer_signers trust_lease_offer_signers_offer_id_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_signers
    ADD CONSTRAINT trust_lease_offer_signers_offer_id_email_key UNIQUE (offer_id, email);


--
-- Name: trust_lease_offer_signers trust_lease_offer_signers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_signers
    ADD CONSTRAINT trust_lease_offer_signers_pkey PRIMARY KEY (offer_id, "position");


--
-- Name: trust_lease_offer_voids trust_lease_offer_voids_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_voids
    ADD CONSTRAINT trust_lease_offer_voids_pkey PRIMARY KEY (offer_id);


--
-- Name: trust_lease_offers trust_lease_offers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_offers trust_lease_offers_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_lease_pets trust_lease_pets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_pets
    ADD CONSTRAINT trust_lease_pets_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_pets trust_lease_pets_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_pets
    ADD CONSTRAINT trust_lease_pets_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_lease_tenants trust_lease_tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_tenants
    ADD CONSTRAINT trust_lease_tenants_pkey PRIMARY KEY (lease_id, tenant_id);


--
-- Name: trust_lease_transfers trust_lease_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_transfers
    ADD CONSTRAINT trust_lease_transfers_pkey PRIMARY KEY (from_lease_id);


--
-- Name: trust_lease_transfers trust_lease_transfers_to_lease_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_transfers
    ADD CONSTRAINT trust_lease_transfers_to_lease_id_key UNIQUE (to_lease_id);


--
-- Name: trust_lease_utilities trust_lease_utilities_lease_id_utility_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_utilities
    ADD CONSTRAINT trust_lease_utilities_lease_id_utility_starts_on_key UNIQUE (lease_id, utility, starts_on);


--
-- Name: trust_lease_utilities trust_lease_utilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_utilities
    ADD CONSTRAINT trust_lease_utilities_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_vehicles trust_lease_vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_vehicles
    ADD CONSTRAINT trust_lease_vehicles_pkey PRIMARY KEY (id);


--
-- Name: trust_lease_vehicles trust_lease_vehicles_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_vehicles
    ADD CONSTRAINT trust_lease_vehicles_pmc_id_id_key UNIQUE (pmc_id, id);


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
-- Name: trust_ledger_accounts trust_ledger_accounts_deposit_kind; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_deposit_kind CHECK (((deposit_kind IS NULL) OR ((kind = 'tenant_deposit'::text) AND (deposit_kind = ANY (ARRAY['pet'::text, 'key'::text]))))) NOT VALID;


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pkey PRIMARY KEY (ledger_account_id);


--
-- Name: trust_listing_removals trust_listing_removals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listing_removals
    ADD CONSTRAINT trust_listing_removals_pkey PRIMARY KEY (listing_id);


--
-- Name: trust_listings trust_listings_link_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listings
    ADD CONSTRAINT trust_listings_link_id_key UNIQUE (link_id);


--
-- Name: trust_listings trust_listings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listings
    ADD CONSTRAINT trust_listings_pkey PRIMARY KEY (id);


--
-- Name: trust_listings trust_listings_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listings
    ADD CONSTRAINT trust_listings_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_login_secrets trust_login_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_login_secrets
    ADD CONSTRAINT trust_login_secrets_pkey PRIMARY KEY (login_id);


--
-- Name: trust_logins trust_logins_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pkey PRIMARY KEY (id);


--
-- Name: trust_logins trust_logins_pmc_id_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_email_key UNIQUE (pmc_id, email);


--
-- Name: trust_logins trust_logins_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_logins trust_logins_pmc_id_owner_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_owner_id_key UNIQUE (pmc_id, owner_id);


--
-- Name: trust_logins trust_logins_pmc_id_staff_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_staff_id_key UNIQUE (pmc_id, staff_id);


--
-- Name: trust_logins trust_logins_pmc_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_tenant_id_key UNIQUE (pmc_id, tenant_id);


--
-- Name: trust_maintenance_request_work_orders trust_maintenance_request_work_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_request_work_orders
    ADD CONSTRAINT trust_maintenance_request_work_orders_pkey PRIMARY KEY (request_id);


--
-- Name: trust_maintenance_request_work_orders trust_maintenance_request_work_orders_work_order_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_request_work_orders
    ADD CONSTRAINT trust_maintenance_request_work_orders_work_order_id_key UNIQUE (work_order_id);


--
-- Name: trust_maintenance_requests trust_maintenance_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_requests
    ADD CONSTRAINT trust_maintenance_requests_pkey PRIMARY KEY (id);


--
-- Name: trust_maintenance_requests trust_maintenance_requests_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_requests
    ADD CONSTRAINT trust_maintenance_requests_pmc_id_id_key UNIQUE (pmc_id, id);


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
-- Name: trust_management_fee_settings trust_management_fee_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fee_settings
    ADD CONSTRAINT trust_management_fee_settings_pkey PRIMARY KEY (id);


--
-- Name: trust_management_fees trust_management_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fees
    ADD CONSTRAINT trust_management_fees_pkey PRIMARY KEY (ledger_account_id, period_start);


--
-- Name: trust_notes trust_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pkey PRIMARY KEY (id);


--
-- Name: trust_nsf_fee_policies trust_nsf_fee_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fee_policies
    ADD CONSTRAINT trust_nsf_fee_policies_pkey PRIMARY KEY (id);


--
-- Name: trust_nsf_fee_policies trust_nsf_fee_policies_property_id_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fee_policies
    ADD CONSTRAINT trust_nsf_fee_policies_property_id_starts_on_key UNIQUE (property_id, starts_on);


--
-- Name: trust_nsf_fees trust_nsf_fees_fee_charge_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fees
    ADD CONSTRAINT trust_nsf_fees_fee_charge_id_key UNIQUE (fee_charge_id);


--
-- Name: trust_nsf_fees trust_nsf_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fees
    ADD CONSTRAINT trust_nsf_fees_pkey PRIMARY KEY (pmc_id, returned_key);


--
-- Name: trust_on_call_staff trust_on_call_staff_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_on_call_staff
    ADD CONSTRAINT trust_on_call_staff_pkey PRIMARY KEY (id);


--
-- Name: trust_opening_balances trust_opening_balances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_opening_balances
    ADD CONSTRAINT trust_opening_balances_pkey PRIMARY KEY (bank_account_id);


--
-- Name: trust_owner_contributions trust_owner_contributions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_contributions
    ADD CONSTRAINT trust_owner_contributions_pkey PRIMARY KEY (pmc_id, request_key);


--
-- Name: trust_owner_contributions trust_owner_contributions_transfer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_contributions
    ADD CONSTRAINT trust_owner_contributions_transfer_id_key UNIQUE (transfer_id);


--
-- Name: trust_owner_draws trust_owner_draws_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_draws
    ADD CONSTRAINT trust_owner_draws_pkey PRIMARY KEY (pmc_id, request_key);


--
-- Name: trust_owner_fees trust_owner_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_pkey PRIMARY KEY (id);


--
-- Name: trust_owner_fees trust_owner_fees_pmc_id_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_pmc_id_request_key_key UNIQUE (pmc_id, request_key);


--
-- Name: trust_owner_fees trust_owner_fees_transfer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_transfer_id_key UNIQUE (transfer_id);


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
-- Name: trust_ownership_share_lines trust_ownership_share_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_share_lines
    ADD CONSTRAINT trust_ownership_share_lines_pkey PRIMARY KEY (share_id, ledger_account_id);


--
-- Name: trust_ownership_shares trust_ownership_shares_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_shares
    ADD CONSTRAINT trust_ownership_shares_pkey PRIMARY KEY (id);


--
-- Name: trust_ownership_shares trust_ownership_shares_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_shares
    ADD CONSTRAINT trust_ownership_shares_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_payment_hold_allowances trust_payment_hold_allowances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_allowances
    ADD CONSTRAINT trust_payment_hold_allowances_pkey PRIMARY KEY (hold_id, transfer_id);


--
-- Name: trust_payment_hold_releases trust_payment_hold_releases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_releases
    ADD CONSTRAINT trust_payment_hold_releases_pkey PRIMARY KEY (hold_id);


--
-- Name: trust_payment_holds trust_payment_holds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_holds
    ADD CONSTRAINT trust_payment_holds_pkey PRIMARY KEY (id);


--
-- Name: trust_payment_holds trust_payment_holds_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_holds
    ADD CONSTRAINT trust_payment_holds_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_payment_reversals trust_payment_reversals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_reversals
    ADD CONSTRAINT trust_payment_reversals_pkey PRIMARY KEY (charge_id, transfer_id, reversal_id);


--
-- Name: trust_payout_methods trust_payout_methods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payout_methods
    ADD CONSTRAINT trust_payout_methods_pkey PRIMARY KEY (id);


--
-- Name: trust_payout_methods trust_payout_methods_transfer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payout_methods
    ADD CONSTRAINT trust_payout_methods_transfer_id_key UNIQUE (transfer_id);


--
-- Name: trust_pmc_fee_withdrawals trust_pmc_fee_withdrawals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_pmc_fee_withdrawals
    ADD CONSTRAINT trust_pmc_fee_withdrawals_pkey PRIMARY KEY (pmc_id, request_key);


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
-- Name: trust_property_group_members trust_property_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_group_members
    ADD CONSTRAINT trust_property_group_members_pkey PRIMARY KEY (id);


--
-- Name: trust_property_groups trust_property_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_groups
    ADD CONSTRAINT trust_property_groups_pkey PRIMARY KEY (id);


--
-- Name: trust_property_groups trust_property_groups_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_groups
    ADD CONSTRAINT trust_property_groups_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_reconciliation_preparations trust_reconciliation_preparations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliation_preparations
    ADD CONSTRAINT trust_reconciliation_preparations_pkey PRIMARY KEY (id);


--
-- Name: trust_reconciliations trust_reconciliations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliations
    ADD CONSTRAINT trust_reconciliations_pkey PRIMARY KEY (id);


--
-- Name: trust_recurring_bill_ends trust_recurring_bill_ends_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_ends
    ADD CONSTRAINT trust_recurring_bill_ends_pkey PRIMARY KEY (recurring_bill_id);


--
-- Name: trust_recurring_bill_expense_accounts trust_recurring_bill_expense_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_expense_accounts
    ADD CONSTRAINT trust_recurring_bill_expense_accounts_pkey PRIMARY KEY (id);


--
-- Name: trust_recurring_bills trust_recurring_bills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bills
    ADD CONSTRAINT trust_recurring_bills_pkey PRIMARY KEY (id);


--
-- Name: trust_recurring_bills trust_recurring_bills_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bills
    ADD CONSTRAINT trust_recurring_bills_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_recurring_bills trust_recurring_bills_vendor_id_reference_prefix_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bills
    ADD CONSTRAINT trust_recurring_bills_vendor_id_reference_prefix_key UNIQUE (vendor_id, reference_prefix);


--
-- Name: trust_recurring_charge_ends trust_recurring_charge_ends_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charge_ends
    ADD CONSTRAINT trust_recurring_charge_ends_pkey PRIMARY KEY (recurring_charge_id);


--
-- Name: trust_recurring_charges trust_recurring_charges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charges
    ADD CONSTRAINT trust_recurring_charges_pkey PRIMARY KEY (id);


--
-- Name: trust_recurring_charges trust_recurring_charges_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charges
    ADD CONSTRAINT trust_recurring_charges_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_recurring_charges trust_recurring_charges_pmc_id_lease_id_id_kind_amount_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charges
    ADD CONSTRAINT trust_recurring_charges_pmc_id_lease_id_id_kind_amount_key UNIQUE (pmc_id, lease_id, id, kind, amount);


--
-- Name: trust_recurring_work_order_ends trust_recurring_work_order_ends_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_order_ends
    ADD CONSTRAINT trust_recurring_work_order_ends_pkey PRIMARY KEY (recurring_work_order_id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pkey PRIMARY KEY (id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_rent_assistance trust_rent_assistance_lease_id_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_assistance
    ADD CONSTRAINT trust_rent_assistance_lease_id_starts_on_key UNIQUE (lease_id, starts_on);


--
-- Name: trust_rent_assistance trust_rent_assistance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_assistance
    ADD CONSTRAINT trust_rent_assistance_pkey PRIMARY KEY (id);


--
-- Name: trust_rent_changes trust_rent_changes_lease_id_effective_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_changes
    ADD CONSTRAINT trust_rent_changes_lease_id_effective_on_key UNIQUE (lease_id, effective_on);


--
-- Name: trust_rent_changes trust_rent_changes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_changes
    ADD CONSTRAINT trust_rent_changes_pkey PRIMARY KEY (id);


--
-- Name: trust_rent_due_days trust_rent_due_days_lease_id_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_due_days
    ADD CONSTRAINT trust_rent_due_days_lease_id_starts_on_key UNIQUE (lease_id, starts_on);


--
-- Name: trust_rent_due_days trust_rent_due_days_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_due_days
    ADD CONSTRAINT trust_rent_due_days_pkey PRIMARY KEY (id);


--
-- Name: trust_rent_runs trust_rent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_runs
    ADD CONSTRAINT trust_rent_runs_pkey PRIMARY KEY (id);


--
-- Name: trust_renters_insurance trust_renters_insurance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance
    ADD CONSTRAINT trust_renters_insurance_pkey PRIMARY KEY (id);


--
-- Name: trust_renters_insurance_terms trust_renters_insurance_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance_terms
    ADD CONSTRAINT trust_renters_insurance_terms_pkey PRIMARY KEY (id);


--
-- Name: trust_screening_criteria trust_screening_criteria_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screening_criteria
    ADD CONSTRAINT trust_screening_criteria_pkey PRIMARY KEY (id);


--
-- Name: trust_screening_criteria trust_screening_criteria_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screening_criteria
    ADD CONSTRAINT trust_screening_criteria_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_screenings trust_screenings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screenings
    ADD CONSTRAINT trust_screenings_pkey PRIMARY KEY (id);


--
-- Name: trust_screenings trust_screenings_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screenings
    ADD CONSTRAINT trust_screenings_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_sessions trust_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_sessions
    ADD CONSTRAINT trust_sessions_pkey PRIMARY KEY (id);


--
-- Name: trust_sessions trust_sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_sessions
    ADD CONSTRAINT trust_sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: trust_shared_bill_parts trust_shared_bill_parts_bill_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bill_parts
    ADD CONSTRAINT trust_shared_bill_parts_bill_id_key UNIQUE (bill_id);


--
-- Name: trust_shared_bill_parts trust_shared_bill_parts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bill_parts
    ADD CONSTRAINT trust_shared_bill_parts_pkey PRIMARY KEY (shared_bill_id, part);


--
-- Name: trust_shared_bills trust_shared_bills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bills
    ADD CONSTRAINT trust_shared_bills_pkey PRIMARY KEY (id);


--
-- Name: trust_shared_bills trust_shared_bills_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bills
    ADD CONSTRAINT trust_shared_bills_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_shared_bills trust_shared_bills_vendor_id_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bills
    ADD CONSTRAINT trust_shared_bills_vendor_id_reference_key UNIQUE (vendor_id, reference);


--
-- Name: trust_showing_outcomes trust_showing_outcomes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showing_outcomes
    ADD CONSTRAINT trust_showing_outcomes_pkey PRIMARY KEY (showing_id);


--
-- Name: trust_showings trust_showings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showings
    ADD CONSTRAINT trust_showings_pkey PRIMARY KEY (id);


--
-- Name: trust_showings trust_showings_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showings
    ADD CONSTRAINT trust_showings_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_signature_evidence trust_signature_evidence_link_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_evidence
    ADD CONSTRAINT trust_signature_evidence_link_id_key UNIQUE (link_id);


--
-- Name: trust_signature_evidence trust_signature_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_evidence
    ADD CONSTRAINT trust_signature_evidence_pkey PRIMARY KEY (offer_id, "position");


--
-- Name: trust_signature_requests trust_signature_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_requests
    ADD CONSTRAINT trust_signature_requests_pkey PRIMARY KEY (offer_id);


--
-- Name: trust_signatures trust_signatures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signatures
    ADD CONSTRAINT trust_signatures_pkey PRIMARY KEY (offer_id, "position");


--
-- Name: trust_signing_events trust_signing_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_events
    ADD CONSTRAINT trust_signing_events_pkey PRIMARY KEY (link_id, kind);


--
-- Name: trust_signing_links trust_signing_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_pkey PRIMARY KEY (id);


--
-- Name: trust_signing_links trust_signing_links_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_signing_links trust_signing_links_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_token_sha256_key UNIQUE (token_sha256);


--
-- Name: trust_staff_custom_roles trust_staff_custom_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_custom_roles
    ADD CONSTRAINT trust_staff_custom_roles_pkey PRIMARY KEY (id);


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
-- Name: trust_staff_roles trust_staff_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_roles
    ADD CONSTRAINT trust_staff_roles_pkey PRIMARY KEY (id);


--
-- Name: trust_staff_roles trust_staff_roles_staff_id_role_created_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_roles
    ADD CONSTRAINT trust_staff_roles_staff_id_role_created_at_key UNIQUE (staff_id, role, created_at);


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
-- Name: trust_unit_areas trust_unit_areas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_unit_areas
    ADD CONSTRAINT trust_unit_areas_pkey PRIMARY KEY (id);


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
-- Name: trust_utility_bills trust_utility_bills_bill_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_bill_id_key UNIQUE (bill_id);


--
-- Name: trust_utility_bills trust_utility_bills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_pkey PRIMARY KEY (id);


--
-- Name: trust_utility_bills trust_utility_bills_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_utility_plans trust_utility_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_plans
    ADD CONSTRAINT trust_utility_plans_pkey PRIMARY KEY (id);


--
-- Name: trust_utility_plans trust_utility_plans_property_id_utility_starts_on_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_plans
    ADD CONSTRAINT trust_utility_plans_property_id_utility_starts_on_key UNIQUE (property_id, utility, starts_on);


--
-- Name: trust_utility_shares trust_utility_shares_charge_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_shares
    ADD CONSTRAINT trust_utility_shares_charge_id_key UNIQUE (charge_id);


--
-- Name: trust_utility_shares trust_utility_shares_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_shares
    ADD CONSTRAINT trust_utility_shares_pkey PRIMARY KEY (utility_bill_id, lease_id);


--
-- Name: trust_vendor_insurance trust_vendor_insurance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_insurance
    ADD CONSTRAINT trust_vendor_insurance_pkey PRIMARY KEY (id);


--
-- Name: trust_vendor_link_revocations trust_vendor_link_revocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_link_revocations
    ADD CONSTRAINT trust_vendor_link_revocations_pkey PRIMARY KEY (link_id);


--
-- Name: trust_vendor_links trust_vendor_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_links
    ADD CONSTRAINT trust_vendor_links_pkey PRIMARY KEY (id);


--
-- Name: trust_vendor_links trust_vendor_links_pmc_id_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_links
    ADD CONSTRAINT trust_vendor_links_pmc_id_id_key UNIQUE (pmc_id, id);


--
-- Name: trust_vendor_links trust_vendor_links_token_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_links
    ADD CONSTRAINT trust_vendor_links_token_sha256_key UNIQUE (token_sha256);


--
-- Name: trust_vendor_payments_carried_over trust_vendor_payments_carried_over_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_payments_carried_over
    ADD CONSTRAINT trust_vendor_payments_carried_over_pkey PRIMARY KEY (vendor_id, year);


--
-- Name: trust_vendor_profiles trust_vendor_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_profiles
    ADD CONSTRAINT trust_vendor_profiles_pkey PRIMARY KEY (id);


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
-- Name: trust_work_order_details trust_work_order_details_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_details
    ADD CONSTRAINT trust_work_order_details_pkey PRIMARY KEY (id);


--
-- Name: trust_work_order_steps trust_work_order_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_pkey PRIMARY KEY (id);


--
-- Name: trust_work_order_steps trust_work_order_steps_scheduled_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_scheduled_check CHECK (((step = 'scheduled'::text) = (scheduled_for IS NOT NULL))) NOT VALID;


--
-- Name: trust_work_order_steps trust_work_order_steps_step_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_step_check CHECK ((step = ANY (ARRAY['assigned'::text, 'scheduled'::text, 'completed'::text, 'cancelled'::text]))) NOT VALID;


--
-- Name: trust_work_order_steps trust_work_order_steps_visit_window_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_visit_window_check CHECK (((visit_window IS NULL) OR ((step = 'scheduled'::text) AND (visit_window ~ '\S'::text) AND (length(visit_window) <= 60)))) NOT VALID;


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
-- Name: trust_work_orders trust_work_orders_recurring_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_recurring_check CHECK (((recurring_work_order_id IS NULL) = (recurring_on IS NULL))) NOT VALID;


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
-- Name: trust_account_signers_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_account_signers_account ON public.trust_account_signers USING btree (bank_account_id, staff_id, created_at);


--
-- Name: trust_account_signers_changed_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_account_signers_changed_by ON public.trust_account_signers USING btree (pmc_id, changed_by);


--
-- Name: trust_account_signers_staff; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_account_signers_staff ON public.trust_account_signers USING btree (pmc_id, staff_id);


--
-- Name: trust_application_links_unit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_application_links_unit_id ON public.trust_application_links USING btree (unit_id);


--
-- Name: trust_application_withdrawals_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_application_withdrawals_recorded_by ON public.trust_application_withdrawals USING btree (pmc_id, recorded_by);


--
-- Name: trust_applications_link_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_applications_link_id ON public.trust_applications USING btree (link_id);


--
-- Name: trust_archivals_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_archivals_owner ON public.trust_archivals USING btree (pmc_id, owner_id, created_at);


--
-- Name: trust_archivals_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_archivals_property ON public.trust_archivals USING btree (pmc_id, property_id, created_at);


--
-- Name: trust_archivals_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_archivals_recorded_by ON public.trust_archivals USING btree (pmc_id, recorded_by);


--
-- Name: trust_archivals_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_archivals_tenant ON public.trust_archivals USING btree (pmc_id, tenant_id, created_at);


--
-- Name: trust_archivals_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_archivals_unit ON public.trust_archivals USING btree (pmc_id, unit_id, created_at);


--
-- Name: trust_assistance_charges_adjusts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_assistance_charges_adjusts ON public.trust_assistance_charges USING btree (pmc_id, adjusts_id);


--
-- Name: trust_assistance_charges_authority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_assistance_charges_authority ON public.trust_assistance_charges USING btree (pmc_id, housing_authority_id);


--
-- Name: trust_assistance_charges_one_rent_per_month; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_assistance_charges_one_rent_per_month ON public.trust_assistance_charges USING btree (lease_id, date_trunc('month'::text, (due_on)::timestamp without time zone)) WHERE (source = 'rent'::text);


--
-- Name: trust_assistance_charges_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_assistance_charges_pmc ON public.trust_assistance_charges USING btree (pmc_id, lease_id, due_on);


--
-- Name: trust_assistance_charges_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_assistance_charges_recorded_by ON public.trust_assistance_charges USING btree (pmc_id, recorded_by);


--
-- Name: trust_assistance_payments_charge; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_assistance_payments_charge ON public.trust_assistance_payments USING btree (pmc_id, assistance_charge_id);


--
-- Name: trust_audit_log_pmc_id_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_audit_log_pmc_id_at ON public.trust_audit_log USING btree (pmc_id, at);


--
-- Name: trust_bank_deposit_items_deposit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_deposit_items_deposit ON public.trust_bank_deposit_items USING btree (pmc_id, deposit_id);


--
-- Name: trust_bank_deposits_bank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_deposits_bank ON public.trust_bank_deposits USING btree (bank_account_id, deposited_on);


--
-- Name: trust_bank_deposits_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_deposits_recorded_by ON public.trust_bank_deposits USING btree (pmc_id, recorded_by);


--
-- Name: trust_bank_match_cutover_items_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_match_cutover_items_item_id ON public.trust_bank_match_cutover_items USING btree (item_id);


--
-- Name: trust_bank_match_lines_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_match_lines_line_id ON public.trust_bank_match_lines USING btree (line_id);


--
-- Name: trust_bank_match_transfers_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_match_transfers_transfer_id ON public.trust_bank_match_transfers USING btree (transfer_id);


--
-- Name: trust_bank_matches_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bank_matches_bank_account_id ON public.trust_bank_matches USING btree (bank_account_id);


--
-- Name: trust_bill_expense_accounts_bill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_expense_accounts_bill ON public.trust_bill_expense_accounts USING btree (bill_id, id);


--
-- Name: trust_bill_markups_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_markups_recorded_by ON public.trust_bill_markups USING btree (pmc_id, recorded_by);


--
-- Name: trust_bill_payments_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_payments_transfer_id ON public.trust_bill_payments USING btree (transfer_id);


--
-- Name: trust_bill_splits_bill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_splits_bill ON public.trust_bill_splits USING btree (pmc_id, bill_id);


--
-- Name: trust_bill_splits_expense_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_splits_expense_account ON public.trust_bill_splits USING btree (pmc_id, expense_account_id);


--
-- Name: trust_bill_splits_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bill_splits_recorded_by ON public.trust_bill_splits USING btree (pmc_id, recorded_by);


--
-- Name: trust_bills_ledger_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bills_ledger_account_id ON public.trust_bills USING btree (ledger_account_id);


--
-- Name: trust_bills_work_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_bills_work_order_id ON public.trust_bills USING btree (work_order_id);


--
-- Name: trust_budgets_account_month; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_budgets_account_month ON public.trust_budgets USING btree (ledger_account_id, month);


--
-- Name: trust_calendar_feed_revocations_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_calendar_feed_revocations_recorded_by ON public.trust_calendar_feed_revocations USING btree (pmc_id, recorded_by);


--
-- Name: trust_calendar_feeds_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_calendar_feeds_group ON public.trust_calendar_feeds USING btree (pmc_id, group_id);


--
-- Name: trust_calendar_feeds_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_calendar_feeds_recorded_by ON public.trust_calendar_feeds USING btree (pmc_id, recorded_by);


--
-- Name: trust_calendar_feeds_staff; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_calendar_feeds_staff ON public.trust_calendar_feeds USING btree (pmc_id, staff_id);


--
-- Name: trust_calendar_feeds_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_calendar_feeds_tenant ON public.trust_calendar_feeds USING btree (pmc_id, tenant_id);


--
-- Name: trust_charge_payments_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charge_payments_transfer_id ON public.trust_charge_payments USING btree (transfer_id);


--
-- Name: trust_charge_waivers_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charge_waivers_recorded_by ON public.trust_charge_waivers USING btree (pmc_id, recorded_by);


--
-- Name: trust_charge_waivers_waived; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charge_waivers_waived ON public.trust_charge_waivers USING btree (pmc_id, waived_charge_id);


--
-- Name: trust_charges_lease_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charges_lease_id ON public.trust_charges USING btree (lease_id, due_on);


--
-- Name: trust_charges_one_per_recurring_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_charges_one_per_recurring_due_date ON public.trust_charges USING btree (recurring_charge_id, due_on) WHERE (recurring_charge_id IS NOT NULL);


--
-- Name: trust_charges_one_rent_per_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_charges_one_rent_per_due_date ON public.trust_charges USING btree (lease_id, due_on) WHERE (kind = 'rent'::text);


--
-- Name: trust_charges_work_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_charges_work_order_id ON public.trust_charges USING btree (work_order_id);


--
-- Name: trust_company_profiles_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_company_profiles_pmc ON public.trust_company_profiles USING btree (pmc_id, created_at);


--
-- Name: trust_company_profiles_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_company_profiles_recorded_by ON public.trust_company_profiles USING btree (pmc_id, recorded_by);


--
-- Name: trust_contacts_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_contacts_owner ON public.trust_contacts USING btree (pmc_id, owner_id, created_at);


--
-- Name: trust_contacts_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_contacts_recorded_by ON public.trust_contacts USING btree (pmc_id, recorded_by);


--
-- Name: trust_contacts_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_contacts_tenant ON public.trust_contacts USING btree (pmc_id, tenant_id, created_at);


--
-- Name: trust_contacts_vendor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_contacts_vendor ON public.trust_contacts USING btree (pmc_id, vendor_id, created_at);


--
-- Name: trust_custom_role_areas_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_custom_role_areas_role ON public.trust_custom_role_areas USING btree (role_id, area);


--
-- Name: trust_cutover_items_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_cutover_items_bank_account_id ON public.trust_cutover_items USING btree (bank_account_id);


--
-- Name: trust_documents_pmc_id_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_documents_pmc_id_kind ON public.trust_documents USING btree (pmc_id, kind);


--
-- Name: trust_documents_property_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_documents_property_id ON public.trust_documents USING btree (property_id);


--
-- Name: trust_documents_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_documents_request_id ON public.trust_documents USING btree (request_id);


--
-- Name: trust_documents_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_documents_tenant_id ON public.trust_documents USING btree (tenant_id);


--
-- Name: trust_documents_unit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_documents_unit_id ON public.trust_documents USING btree (unit_id);


--
-- Name: trust_documents_work_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_documents_work_order_id ON public.trust_documents USING btree (work_order_id);


--
-- Name: trust_email_deliveries_once; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_email_deliveries_once ON public.trust_email_deliveries USING btree (email_id) WHERE (error IS NULL);


--
-- Name: trust_email_settings_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_email_settings_pmc ON public.trust_email_settings USING btree (pmc_id, created_at);


--
-- Name: trust_email_settings_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_email_settings_recorded_by ON public.trust_email_settings USING btree (pmc_id, recorded_by);


--
-- Name: trust_emails_one_adverse_action; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_adverse_action ON public.trust_emails USING btree (applicant_id, application_id) WHERE (kind = 'adverse_action'::text);


--
-- Name: trust_emails_one_emergency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_emergency ON public.trust_emails USING btree (staff_id, request_id) WHERE (kind = 'emergency_request'::text);


--
-- Name: trust_emails_one_late_notice; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_late_notice ON public.trust_emails USING btree (tenant_id, charge_id) WHERE (kind = 'late_notice'::text);


--
-- Name: trust_emails_one_owner_statement; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_owner_statement ON public.trust_emails USING btree (owner_id, period) WHERE (kind = 'owner_statement'::text);


--
-- Name: trust_emails_one_per_bill_approval; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_per_bill_approval ON public.trust_emails USING btree (bill_id) WHERE (kind = 'bill_approval'::text);


--
-- Name: trust_emails_one_rent_reminder; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_rent_reminder ON public.trust_emails USING btree (tenant_id, lease_id, period) WHERE (kind = 'rent_reminder'::text);


--
-- Name: trust_emails_one_signing_request; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_signing_request ON public.trust_emails USING btree (offer_id, signer_position) WHERE (kind = 'signing_request'::text);


--
-- Name: trust_emails_one_work_order; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_emails_one_work_order ON public.trust_emails USING btree (work_order_step_id) WHERE (kind = 'work_order'::text);


--
-- Name: trust_emails_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_emails_owner_id ON public.trust_emails USING btree (owner_id) WHERE (owner_id IS NOT NULL);


--
-- Name: trust_emails_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_emails_tenant_id ON public.trust_emails USING btree (tenant_id) WHERE (tenant_id IS NOT NULL);


--
-- Name: trust_emails_vendor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_emails_vendor_id ON public.trust_emails USING btree (vendor_id) WHERE (vendor_id IS NOT NULL);


--
-- Name: trust_housing_authorities_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_housing_authorities_name ON public.trust_housing_authorities USING btree (pmc_id, lower(name));


--
-- Name: trust_housing_authorities_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_housing_authorities_recorded_by ON public.trust_housing_authorities USING btree (pmc_id, recorded_by);


--
-- Name: trust_inspection_items_room; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_inspection_items_room ON public.trust_inspection_items USING btree (inspection_id, lower(room));


--
-- Name: trust_late_fees_policy_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_late_fees_policy_id ON public.trust_late_fees USING btree (policy_id);


--
-- Name: trust_lead_closings_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lead_closings_recorded_by ON public.trust_lead_closings USING btree (pmc_id, recorded_by);


--
-- Name: trust_leads_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leads_pmc ON public.trust_leads USING btree (pmc_id, created_at);


--
-- Name: trust_leads_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leads_property ON public.trust_leads USING btree (pmc_id, property_id);


--
-- Name: trust_leads_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leads_recorded_by ON public.trust_leads USING btree (pmc_id, recorded_by);


--
-- Name: trust_leads_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_leads_unit ON public.trust_leads USING btree (pmc_id, unit_id);


--
-- Name: trust_lease_break_fees_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_break_fees_pmc ON public.trust_lease_break_fees USING btree (pmc_id, property_id);


--
-- Name: trust_lease_break_fees_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_break_fees_recorded_by ON public.trust_lease_break_fees USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_departures_deposit_to; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_departures_deposit_to ON public.trust_lease_departures USING btree (lease_id, deposit_to);


--
-- Name: trust_lease_departures_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_departures_recorded_by ON public.trust_lease_departures USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_departures_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_departures_tenant ON public.trust_lease_departures USING btree (tenant_id);


--
-- Name: trust_lease_end_adjustments_charge_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_end_adjustments_charge_id ON public.trust_lease_end_adjustments USING btree (charge_id);


--
-- Name: trust_lease_guarantors_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_guarantors_lease ON public.trust_lease_guarantors USING btree (pmc_id, lease_id);


--
-- Name: trust_lease_guarantors_offer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_guarantors_offer ON public.trust_lease_guarantors USING btree (pmc_id, offer_id);


--
-- Name: trust_lease_guarantors_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_guarantors_recorded_by ON public.trust_lease_guarantors USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_household_removals_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_household_removals_pmc ON public.trust_lease_household_removals USING btree (pmc_id);


--
-- Name: trust_lease_household_removals_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_household_removals_recorded_by ON public.trust_lease_household_removals USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_occupants_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_occupants_lease ON public.trust_lease_occupants USING btree (pmc_id, lease_id);


--
-- Name: trust_lease_occupants_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_occupants_recorded_by ON public.trust_lease_occupants USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_offer_voids_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_offer_voids_recorded_by ON public.trust_lease_offer_voids USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_offers_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_offers_application_id ON public.trust_lease_offers USING btree (application_id);


--
-- Name: trust_lease_offers_renews_lease_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_offers_renews_lease_id ON public.trust_lease_offers USING btree (renews_lease_id);


--
-- Name: trust_lease_pets_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_pets_lease ON public.trust_lease_pets USING btree (pmc_id, lease_id);


--
-- Name: trust_lease_pets_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_pets_recorded_by ON public.trust_lease_pets USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_tenants_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_tenants_tenant_id ON public.trust_lease_tenants USING btree (tenant_id);


--
-- Name: trust_lease_transfers_owner_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_transfers_owner_account ON public.trust_lease_transfers USING btree (owner_account);


--
-- Name: trust_lease_transfers_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_transfers_recorded_by ON public.trust_lease_transfers USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_utilities_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_utilities_pmc ON public.trust_lease_utilities USING btree (pmc_id, lease_id);


--
-- Name: trust_lease_utilities_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_utilities_recorded_by ON public.trust_lease_utilities USING btree (pmc_id, recorded_by);


--
-- Name: trust_lease_vehicles_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_vehicles_lease ON public.trust_lease_vehicles USING btree (pmc_id, lease_id);


--
-- Name: trust_lease_vehicles_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_lease_vehicles_recorded_by ON public.trust_lease_vehicles USING btree (pmc_id, recorded_by);


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

CREATE UNIQUE INDEX trust_ledger_accounts_one_tenant_deposit ON public.trust_ledger_accounts USING btree (bank_account_id, tenant_id, COALESCE(deposit_kind, 'security'::text)) WHERE (kind = 'tenant_deposit'::text);


--
-- Name: trust_ledger_accounts_one_vendor_payable; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_ledger_accounts_one_vendor_payable ON public.trust_ledger_accounts USING btree (bank_account_id, vendor_id) WHERE (kind = 'vendor_payable'::text);


--
-- Name: trust_ledger_accounts_pmc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ledger_accounts_pmc_id ON public.trust_ledger_accounts USING btree (pmc_id);


--
-- Name: trust_maintenance_requests_lease_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_maintenance_requests_lease_id ON public.trust_maintenance_requests USING btree (lease_id);


--
-- Name: trust_maintenance_requests_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_maintenance_requests_tenant_id ON public.trust_maintenance_requests USING btree (tenant_id);


--
-- Name: trust_management_fee_settings_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_management_fee_settings_pmc ON public.trust_management_fee_settings USING btree (pmc_id, created_at);


--
-- Name: trust_management_fee_settings_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_management_fee_settings_recorded_by ON public.trust_management_fee_settings USING btree (pmc_id, recorded_by);


--
-- Name: trust_management_fees_agreement_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_management_fees_agreement_id ON public.trust_management_fees USING btree (agreement_id);


--
-- Name: trust_management_fees_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_management_fees_transfer_id ON public.trust_management_fees USING btree (transfer_id);


--
-- Name: trust_notes_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_lease ON public.trust_notes USING btree (pmc_id, lease_id, created_at);


--
-- Name: trust_notes_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_owner ON public.trust_notes USING btree (pmc_id, owner_id, created_at);


--
-- Name: trust_notes_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_property ON public.trust_notes USING btree (pmc_id, property_id, created_at);


--
-- Name: trust_notes_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_recorded_by ON public.trust_notes USING btree (pmc_id, recorded_by);


--
-- Name: trust_notes_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_tenant ON public.trust_notes USING btree (pmc_id, tenant_id, created_at);


--
-- Name: trust_notes_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_unit ON public.trust_notes USING btree (pmc_id, unit_id, created_at);


--
-- Name: trust_notes_vendor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_notes_vendor ON public.trust_notes USING btree (pmc_id, vendor_id, created_at);


--
-- Name: trust_nsf_fees_policy_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_nsf_fees_policy_id ON public.trust_nsf_fees USING btree (policy_id);


--
-- Name: trust_on_call_staff_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_on_call_staff_recorded_by ON public.trust_on_call_staff USING btree (pmc_id, recorded_by);


--
-- Name: trust_on_call_staff_staff; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_on_call_staff_staff ON public.trust_on_call_staff USING btree (pmc_id, staff_id, created_at);


--
-- Name: trust_owner_draws_ledger_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_owner_draws_ledger_account_id ON public.trust_owner_draws USING btree (ledger_account_id);


--
-- Name: trust_owner_draws_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_owner_draws_transfer_id ON public.trust_owner_draws USING btree (transfer_id);


--
-- Name: trust_owner_fees_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_owner_fees_account ON public.trust_owner_fees USING btree (ledger_account_id);


--
-- Name: trust_owner_fees_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_owner_fees_recorded_by ON public.trust_owner_fees USING btree (pmc_id, recorded_by);


--
-- Name: trust_ownership_share_lines_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ownership_share_lines_account ON public.trust_ownership_share_lines USING btree (ledger_account_id);


--
-- Name: trust_ownership_share_lines_share; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ownership_share_lines_share ON public.trust_ownership_share_lines USING btree (pmc_id, share_id);


--
-- Name: trust_ownership_shares_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ownership_shares_property ON public.trust_ownership_shares USING btree (pmc_id, property_id, starts_on);


--
-- Name: trust_ownership_shares_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_ownership_shares_recorded_by ON public.trust_ownership_shares USING btree (pmc_id, recorded_by);


--
-- Name: trust_payment_holds_lease_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_payment_holds_lease_id ON public.trust_payment_holds USING btree (lease_id);


--
-- Name: trust_payment_reversals_reversal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_payment_reversals_reversal_id ON public.trust_payment_reversals USING btree (reversal_id);


--
-- Name: trust_payout_methods_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_payout_methods_recorded_by ON public.trust_payout_methods USING btree (pmc_id, recorded_by);


--
-- Name: trust_pmc_fee_withdrawals_ledger_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_pmc_fee_withdrawals_ledger_account_id ON public.trust_pmc_fee_withdrawals USING btree (ledger_account_id);


--
-- Name: trust_pmc_fee_withdrawals_transfer_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_pmc_fee_withdrawals_transfer_id ON public.trust_pmc_fee_withdrawals USING btree (transfer_id);


--
-- Name: trust_property_group_members_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_property_group_members_group ON public.trust_property_group_members USING btree (group_id, property_id, created_at);


--
-- Name: trust_property_group_members_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_property_group_members_property ON public.trust_property_group_members USING btree (pmc_id, property_id);


--
-- Name: trust_property_group_members_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_property_group_members_recorded_by ON public.trust_property_group_members USING btree (pmc_id, recorded_by);


--
-- Name: trust_property_groups_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_property_groups_name ON public.trust_property_groups USING btree (pmc_id, lower(display_name));


--
-- Name: trust_reconciliation_preparations_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_reconciliation_preparations_bank_account_id ON public.trust_reconciliation_preparations USING btree (bank_account_id, created_at);


--
-- Name: trust_reconciliations_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_reconciliations_bank_account_id ON public.trust_reconciliations USING btree (bank_account_id, period_end);


--
-- Name: trust_recurring_bill_expense_accounts_bill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_bill_expense_accounts_bill ON public.trust_recurring_bill_expense_accounts USING btree (recurring_bill_id, id);


--
-- Name: trust_recurring_charges_lease_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_charges_lease_id ON public.trust_recurring_charges USING btree (lease_id);


--
-- Name: trust_recurring_charges_lease_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_recurring_charges_lease_kind ON public.trust_recurring_charges USING btree (pmc_id, lease_id, id, kind);


--
-- Name: trust_recurring_work_order_ends_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_work_order_ends_recorded_by ON public.trust_recurring_work_order_ends USING btree (pmc_id, recorded_by);


--
-- Name: trust_recurring_work_orders_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_work_orders_pmc ON public.trust_recurring_work_orders USING btree (pmc_id);


--
-- Name: trust_recurring_work_orders_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_work_orders_property ON public.trust_recurring_work_orders USING btree (pmc_id, property_id);


--
-- Name: trust_recurring_work_orders_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_work_orders_recorded_by ON public.trust_recurring_work_orders USING btree (pmc_id, recorded_by);


--
-- Name: trust_recurring_work_orders_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_work_orders_unit ON public.trust_recurring_work_orders USING btree (pmc_id, unit_id);


--
-- Name: trust_recurring_work_orders_vendor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_recurring_work_orders_vendor ON public.trust_recurring_work_orders USING btree (pmc_id, vendor_id);


--
-- Name: trust_rent_assistance_authority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_assistance_authority ON public.trust_rent_assistance USING btree (pmc_id, housing_authority_id);


--
-- Name: trust_rent_assistance_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_assistance_pmc ON public.trust_rent_assistance USING btree (pmc_id, lease_id);


--
-- Name: trust_rent_assistance_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_assistance_recorded_by ON public.trust_rent_assistance USING btree (pmc_id, recorded_by);


--
-- Name: trust_rent_changes_pmc_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_changes_pmc_lease ON public.trust_rent_changes USING btree (pmc_id, lease_id);


--
-- Name: trust_rent_changes_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_changes_recorded_by ON public.trust_rent_changes USING btree (pmc_id, recorded_by);


--
-- Name: trust_rent_due_days_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_due_days_pmc ON public.trust_rent_due_days USING btree (pmc_id, lease_id);


--
-- Name: trust_rent_due_days_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_due_days_recorded_by ON public.trust_rent_due_days USING btree (pmc_id, recorded_by);


--
-- Name: trust_rent_runs_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_runs_pmc ON public.trust_rent_runs USING btree (pmc_id, created_at);


--
-- Name: trust_rent_runs_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_rent_runs_recorded_by ON public.trust_rent_runs USING btree (pmc_id, recorded_by);


--
-- Name: trust_renters_insurance_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_renters_insurance_lease ON public.trust_renters_insurance USING btree (pmc_id, lease_id);


--
-- Name: trust_renters_insurance_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_renters_insurance_recorded_by ON public.trust_renters_insurance USING btree (pmc_id, recorded_by);


--
-- Name: trust_renters_insurance_submitted_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_renters_insurance_submitted_by ON public.trust_renters_insurance USING btree (lease_id, submitted_by);


--
-- Name: trust_renters_insurance_terms_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_renters_insurance_terms_lease ON public.trust_renters_insurance_terms USING btree (pmc_id, lease_id, starts_on);


--
-- Name: trust_renters_insurance_terms_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_renters_insurance_terms_recorded_by ON public.trust_renters_insurance_terms USING btree (pmc_id, recorded_by);


--
-- Name: trust_screening_criteria_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_screening_criteria_pmc ON public.trust_screening_criteria USING btree (pmc_id, created_at);


--
-- Name: trust_screening_criteria_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_screening_criteria_recorded_by ON public.trust_screening_criteria USING btree (pmc_id, recorded_by);


--
-- Name: trust_screenings_application_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_screenings_application_id ON public.trust_screenings USING btree (application_id);


--
-- Name: trust_screenings_criteria; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_screenings_criteria ON public.trust_screenings USING btree (criteria_id);


--
-- Name: trust_sessions_login; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_sessions_login ON public.trust_sessions USING btree (login_id);


--
-- Name: trust_shared_bill_parts_bill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_shared_bill_parts_bill ON public.trust_shared_bill_parts USING btree (pmc_id, bill_id);


--
-- Name: trust_shared_bill_parts_shared; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_shared_bill_parts_shared ON public.trust_shared_bill_parts USING btree (pmc_id, shared_bill_id);


--
-- Name: trust_shared_bills_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_shared_bills_recorded_by ON public.trust_shared_bills USING btree (pmc_id, recorded_by);


--
-- Name: trust_shared_bills_vendor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_shared_bills_vendor ON public.trust_shared_bills USING btree (pmc_id, vendor_id);


--
-- Name: trust_showing_outcomes_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_showing_outcomes_recorded_by ON public.trust_showing_outcomes USING btree (pmc_id, recorded_by);


--
-- Name: trust_showings_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_showings_lead ON public.trust_showings USING btree (lead_id, starts_at);


--
-- Name: trust_showings_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_showings_recorded_by ON public.trust_showings USING btree (pmc_id, recorded_by);


--
-- Name: trust_showings_shown_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_showings_shown_by ON public.trust_showings USING btree (pmc_id, shown_by);


--
-- Name: trust_showings_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_showings_unit ON public.trust_showings USING btree (pmc_id, unit_id);


--
-- Name: trust_signing_links_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_signing_links_recorded_by ON public.trust_signing_links USING btree (pmc_id, recorded_by);


--
-- Name: trust_signing_links_signer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_signing_links_signer ON public.trust_signing_links USING btree (offer_id, "position");


--
-- Name: trust_staff_custom_roles_staff; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_staff_custom_roles_staff ON public.trust_staff_custom_roles USING btree (staff_id, role_id);


--
-- Name: trust_staff_roles_pmc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_staff_roles_pmc_id ON public.trust_staff_roles USING btree (pmc_id);


--
-- Name: trust_unit_areas_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_unit_areas_recorded_by ON public.trust_unit_areas USING btree (pmc_id, recorded_by);


--
-- Name: trust_unit_areas_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_unit_areas_unit ON public.trust_unit_areas USING btree (pmc_id, unit_id, created_at);


--
-- Name: trust_units_property_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_units_property_id ON public.trust_units USING btree (property_id);


--
-- Name: trust_utility_bills_bill; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_bills_bill ON public.trust_utility_bills USING btree (pmc_id, bill_id);


--
-- Name: trust_utility_bills_property; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_bills_property ON public.trust_utility_bills USING btree (pmc_id, property_id, utility, period_start);


--
-- Name: trust_utility_bills_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_bills_recorded_by ON public.trust_utility_bills USING btree (pmc_id, recorded_by);


--
-- Name: trust_utility_plans_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_plans_pmc ON public.trust_utility_plans USING btree (pmc_id, property_id);


--
-- Name: trust_utility_plans_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_plans_recorded_by ON public.trust_utility_plans USING btree (pmc_id, recorded_by);


--
-- Name: trust_utility_shares_charge; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_shares_charge ON public.trust_utility_shares USING btree (pmc_id, charge_id);


--
-- Name: trust_utility_shares_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_shares_lease ON public.trust_utility_shares USING btree (pmc_id, lease_id);


--
-- Name: trust_utility_shares_pmc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_utility_shares_pmc ON public.trust_utility_shares USING btree (pmc_id, utility_bill_id);


--
-- Name: trust_vendor_link_revocations_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_vendor_link_revocations_recorded_by ON public.trust_vendor_link_revocations USING btree (pmc_id, recorded_by);


--
-- Name: trust_vendor_links_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_vendor_links_recorded_by ON public.trust_vendor_links USING btree (pmc_id, recorded_by);


--
-- Name: trust_vendor_links_vendor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_vendor_links_vendor ON public.trust_vendor_links USING btree (pmc_id, vendor_id);


--
-- Name: trust_vendor_profiles_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_vendor_profiles_recorded_by ON public.trust_vendor_profiles USING btree (pmc_id, recorded_by);


--
-- Name: trust_vendor_profiles_vendor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_vendor_profiles_vendor ON public.trust_vendor_profiles USING btree (pmc_id, vendor_id, created_at);


--
-- Name: trust_work_order_details_recorded_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_work_order_details_recorded_by ON public.trust_work_order_details USING btree (pmc_id, recorded_by);


--
-- Name: trust_work_order_details_work_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_work_order_details_work_order ON public.trust_work_order_details USING btree (work_order_id, created_at);


--
-- Name: trust_work_order_steps_one_close; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_work_order_steps_one_close ON public.trust_work_order_steps USING btree (work_order_id) WHERE (step = ANY (ARRAY['completed'::text, 'cancelled'::text]));


--
-- Name: trust_work_order_steps_vendor_link; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_work_order_steps_vendor_link ON public.trust_work_order_steps USING btree (vendor_link_id);


--
-- Name: trust_work_orders_one_per_recurring_day; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX trust_work_orders_one_per_recurring_day ON public.trust_work_orders USING btree (recurring_work_order_id, recurring_on);


--
-- Name: trust_work_orders_property_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_work_orders_property_id ON public.trust_work_orders USING btree (property_id);


--
-- Name: trust_ledger_accounts trust_account_in_its_bank; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_account_in_its_bank BEFORE INSERT ON public.trust_ledger_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_check_account_bank_kind();


--
-- Name: trust_custom_role_areas trust_admin; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_admin BEFORE INSERT ON public.trust_custom_role_areas FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('admin', 'change a custom role');


--
-- Name: trust_custom_roles trust_admin; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_admin BEFORE INSERT ON public.trust_custom_roles FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('admin', 'change a custom role');


--
-- Name: trust_invites trust_admin; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_admin BEFORE INSERT ON public.trust_invites FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_invite();


--
-- Name: trust_logins trust_admin; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_admin BEFORE UPDATE OF disabled_at ON public.trust_logins FOR EACH ROW WHEN ((old.kind = 'staff'::text)) EXECUTE FUNCTION public.trust_check_staff_role('admin', 'disable a staff login');


--
-- Name: trust_staff_custom_roles trust_admin; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_admin BEFORE INSERT ON public.trust_staff_custom_roles FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('admin', 'change a custom role');


--
-- Name: trust_adverse_actions trust_adverse_action_belongs; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_adverse_action_belongs AFTER INSERT ON public.trust_adverse_actions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_adverse_action();


--
-- Name: trust_application_decisions trust_adverse_action_given; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_adverse_action_given AFTER INSERT ON public.trust_application_decisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_adverse_action();


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
-- Name: trust_account_signers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_account_signers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_adverse_actions trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_adverse_actions FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_applicants trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_applicants FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_application_decisions trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_application_decisions FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_application_link_closings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_application_link_closings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_application_links trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_application_links FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_application_withdrawals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_application_withdrawals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_applications trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_applications FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_archivals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_archivals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_assistance_charges trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_assistance_charges FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_assistance_payments trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_assistance_payments FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_audit_log trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_deposit_items trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_deposit_items FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_deposits trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_deposits FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_match_cutover_items trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_match_cutover_items FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_match_lines trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_match_lines FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_match_transfers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_match_transfers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_matches trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_matches FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_statement_lines trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_statement_lines FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_statements trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_statements FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bank_unmatches trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bank_unmatches FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_approvals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_approvals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_declines trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_declines FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_expense_accounts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_expense_accounts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_markup_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_markup_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_markups trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_markups FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_payments trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_payments FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bill_splits trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bill_splits FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_bills trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_bills FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_budgets trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_budgets FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_calendar_feed_revocations trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_calendar_feed_revocations FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_calendar_feeds trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_calendar_feeds FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_charge_payments trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_charge_payments FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_charge_waivers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_charge_waivers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_charges trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_charges FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_company_profiles trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_company_profiles FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_contacts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_contacts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_custom_role_areas trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_custom_role_areas FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_custom_roles trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_custom_roles FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_cutover_items trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_cutover_items FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_cutover_signoffs trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_cutover_signoffs FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_deposit_return_lines trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_deposit_return_lines FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_deposit_returns trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_deposit_returns FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_document_files trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_document_files FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_document_removals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_document_removals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_documents trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_documents FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_email_deliveries trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_email_deliveries FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_email_settings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_email_settings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_emails trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_emails FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_expense_accounts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_expense_accounts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_housing_authorities trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_housing_authorities FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_idempotency_keys trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_idempotency_keys FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_inspection_items trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_inspection_items FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_inspections trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_inspections FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_journal_entries trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_journal_entries FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_journal_entry_decisions trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_journal_entry_decisions FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_journal_entry_lines trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_journal_entry_lines FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_late_fee_policies trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_late_fee_policies FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_late_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_late_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lead_closings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lead_closings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_leads trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_leads FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_break_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_break_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_departures trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_departures FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_end_adjustments trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_end_adjustments FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_guarantors trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_guarantors FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_household_removals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_household_removals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_occupants trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_occupants FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_offer_charges trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_offer_charges FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_offer_completions trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_offer_completions FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_offer_signers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_offer_signers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_offer_voids trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_offer_voids FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_offers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_offers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_pets trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_pets FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_tenants trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_tenants FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_transfers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_transfers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_utilities trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_utilities FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_lease_vehicles trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_lease_vehicles FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_leasing_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_leasing_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_ledger_accounts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_ledger_accounts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_listing_removals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_listing_removals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_listings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_listings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_maintenance_request_work_orders trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_maintenance_request_work_orders FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_maintenance_requests trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_maintenance_requests FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_management_agreements trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_management_agreements FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_management_fee_settings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_management_fee_settings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_management_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_management_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_notes trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_notes FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_nsf_fee_policies trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_nsf_fee_policies FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_nsf_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_nsf_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_on_call_staff trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_on_call_staff FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_opening_balances trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_opening_balances FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_owner_contributions trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_owner_contributions FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_owner_draws trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_owner_draws FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_owner_fees trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_owner_fees FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_ownership_share_lines trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_ownership_share_lines FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_ownership_shares trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_ownership_shares FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_payment_hold_allowances trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_payment_hold_allowances FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_payment_hold_releases trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_payment_hold_releases FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_payment_holds trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_payment_holds FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_payment_reversals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_payment_reversals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_payout_methods trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_payout_methods FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_pmc_fee_withdrawals trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_pmc_fee_withdrawals FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_property_group_members trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_property_group_members FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_reconciliation_preparations trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_reconciliation_preparations FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_reconciliations trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_reconciliations FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_bill_ends trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_bill_ends FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_bill_expense_accounts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_bill_expense_accounts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_bills trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_bills FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_charge_ends trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_charge_ends FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_charges trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_charges FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_work_order_ends trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_work_order_ends FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_recurring_work_orders trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_recurring_work_orders FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_rent_assistance trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_rent_assistance FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_rent_changes trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_rent_changes FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_rent_due_days trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_rent_due_days FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_rent_runs trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_rent_runs FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_renters_insurance trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_renters_insurance FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_renters_insurance_terms trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_renters_insurance_terms FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_screening_criteria trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_screening_criteria FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_screenings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_screenings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_shared_bill_parts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_shared_bill_parts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_shared_bills trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_shared_bills FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_showing_outcomes trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_showing_outcomes FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_showings trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_showings FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_signature_evidence trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_signature_evidence FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_signature_requests trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_signature_requests FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_signatures trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_signatures FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_signing_events trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_signing_events FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_signing_links trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_signing_links FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_staff_custom_roles trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_staff_custom_roles FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_staff_roles trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_staff_roles FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_unit_areas trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_unit_areas FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_utility_bills trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_utility_bills FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_utility_plans trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_utility_plans FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_utility_shares trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_utility_shares FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_vendor_insurance trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_vendor_insurance FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_vendor_link_revocations trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_vendor_link_revocations FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_vendor_links trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_vendor_links FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_vendor_payments_carried_over trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_vendor_payments_carried_over FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_vendor_profiles trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_vendor_profiles FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_work_order_details trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_work_order_details FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_work_order_steps trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_work_order_steps FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_work_orders trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_work_orders FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_application_decisions trust_application_decision_screening; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_decision_screening BEFORE INSERT ON public.trust_application_decisions FOR EACH ROW EXECUTE FUNCTION public.trust_check_application_decision();


--
-- Name: trust_application_link_closings trust_application_link_lock; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_link_lock BEFORE INSERT ON public.trust_application_link_closings FOR EACH ROW EXECUTE FUNCTION public.trust_lock_application_link();


--
-- Name: trust_applications trust_application_link_open; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_link_open BEFORE INSERT ON public.trust_applications FOR EACH ROW EXECUTE FUNCTION public.trust_check_application();


--
-- Name: trust_application_decisions trust_application_not_withdrawn; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_not_withdrawn BEFORE INSERT ON public.trust_application_decisions FOR EACH ROW EXECUTE FUNCTION public.trust_check_application_not_withdrawn();


--
-- Name: trust_lease_offer_completions trust_application_not_withdrawn; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_not_withdrawn BEFORE INSERT ON public.trust_lease_offer_completions FOR EACH ROW EXECUTE FUNCTION public.trust_check_completion_not_withdrawn();


--
-- Name: trust_lease_offers trust_application_not_withdrawn; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_not_withdrawn BEFORE INSERT ON public.trust_lease_offers FOR EACH ROW EXECUTE FUNCTION public.trust_check_application_not_withdrawn();


--
-- Name: trust_screenings trust_application_not_withdrawn; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_not_withdrawn BEFORE INSERT ON public.trust_screenings FOR EACH ROW EXECUTE FUNCTION public.trust_check_application_not_withdrawn();


--
-- Name: trust_application_withdrawals trust_application_withdrawal_open; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_application_withdrawal_open BEFORE INSERT ON public.trust_application_withdrawals FOR EACH ROW EXECUTE FUNCTION public.trust_check_application_withdrawal();


--
-- Name: trust_cutover_signoffs trust_approve; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_approve BEFORE INSERT ON public.trust_cutover_signoffs FOR EACH ROW EXECUTE FUNCTION public.trust_check_cutover_signer();


--
-- Name: trust_journal_entry_decisions trust_approve; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_approve BEFORE INSERT ON public.trust_journal_entry_decisions FOR EACH ROW WHEN ((new.decision = 'posted'::text)) EXECUTE FUNCTION public.trust_check_staff_role('approve', 'post a journal entry');


--
-- Name: trust_reconciliations trust_approve; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_approve BEFORE INSERT ON public.trust_reconciliations FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('approve', 'approve a reconciliation');


--
-- Name: trust_archivals trust_archival; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_archival BEFORE INSERT ON public.trust_archivals FOR EACH ROW EXECUTE FUNCTION public.trust_check_archival();


--
-- Name: trust_assistance_charges trust_assistance_charge_adjusts; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_assistance_charge_adjusts BEFORE INSERT ON public.trust_assistance_charges FOR EACH ROW EXECUTE FUNCTION public.trust_check_assistance_charge();


--
-- Name: pgledger_transfers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_account_signers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_account_signers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_adverse_actions trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_adverse_actions FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_applicants trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_applicants FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_application_decisions trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_application_decisions FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_application_link_closings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_application_link_closings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_application_links trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_application_links FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_application_withdrawals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_application_withdrawals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_applications trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_applications FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_archivals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_archivals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_assistance_charges trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_assistance_charges FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_assistance_payments trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_assistance_payments FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_accounts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_deposit_items trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_deposit_items FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_deposits trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_deposits FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_match_cutover_items trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_match_cutover_items FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_match_lines trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_match_lines FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_match_transfers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_match_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_matches trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_matches FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_statement_lines trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_statement_lines FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_statements trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_statements FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_unmatches trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bank_unmatches FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_approvals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_approvals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_declines trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_declines FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_expense_accounts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_expense_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_markup_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_markup_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_markups trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_markups FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_payments trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_payments FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bill_splits trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bill_splits FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bills trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_bills FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_budgets trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_budgets FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_calendar_feed_revocations trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_calendar_feed_revocations FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_calendar_feeds trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_calendar_feeds FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_charge_payments trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_charge_payments FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_charge_waivers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_charge_waivers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_charges trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_charges FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_company_profiles trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_company_profiles FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_contacts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_contacts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_custom_role_areas trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_custom_role_areas FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_custom_roles trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_custom_roles FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_cutover_items trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_cutover_items FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_cutover_signoffs trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_cutover_signoffs FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_deposit_return_lines trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_deposit_return_lines FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_deposit_returns trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_deposit_returns FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_document_removals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_document_removals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_documents trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_documents FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_email_settings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_email_settings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_expense_accounts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_expense_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_housing_authorities trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_housing_authorities FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_inspection_items trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_inspection_items FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_inspections trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_inspections FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_invites trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_invites FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_journal_entries trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_journal_entries FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_journal_entry_decisions trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_journal_entry_decisions FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_journal_entry_lines trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_journal_entry_lines FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_late_fee_policies trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_late_fee_policies FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_late_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_late_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lead_closings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lead_closings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_leads trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_leads FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_break_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_break_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_departures trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_departures FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_end_adjustments trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_end_adjustments FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_guarantors trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_guarantors FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_household_removals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_household_removals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_occupants trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_occupants FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_offer_charges trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_offer_charges FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_offer_completions trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_offer_completions FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_offer_signers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_offer_signers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_offer_voids trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_offer_voids FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_offers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_offers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_pets trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_pets FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_tenants trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_tenants FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_transfers trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_utilities trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_utilities FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_lease_vehicles trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_lease_vehicles FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


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
-- Name: trust_listing_removals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_listing_removals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_listings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_listings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_logins trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_logins FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_maintenance_request_work_orders trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_maintenance_request_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_maintenance_requests trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_maintenance_requests FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_management_agreements trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_management_agreements FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_management_fee_settings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_management_fee_settings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_management_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_management_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_notes trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_notes FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_nsf_fee_policies trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_nsf_fee_policies FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_nsf_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_nsf_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_on_call_staff trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_on_call_staff FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_opening_balances trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_opening_balances FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_owner_contributions trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_owner_contributions FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_owner_draws trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_owner_draws FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_owner_fees trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_owner_fees FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_owners trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_owners FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_ownership_share_lines trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_ownership_share_lines FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_ownership_shares trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_ownership_shares FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_payment_hold_allowances trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_payment_hold_allowances FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_payment_hold_releases trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_payment_hold_releases FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_payment_holds trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_payment_holds FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_payment_reversals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_payment_reversals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_payout_methods trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_payout_methods FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_pmc_fee_withdrawals trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_pmc_fee_withdrawals FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_pmcs trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_pmcs FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_properties trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_properties FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_property_group_members trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_property_group_members FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_property_groups trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_property_groups FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_reconciliation_preparations trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_reconciliation_preparations FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_reconciliations trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_reconciliations FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_bill_ends trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_bill_ends FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_bill_expense_accounts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_bill_expense_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_bills trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_bills FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_charge_ends trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_charge_ends FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_charges trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_charges FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_work_order_ends trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_work_order_ends FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_recurring_work_orders trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_recurring_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_rent_assistance trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_rent_assistance FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_rent_changes trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_rent_changes FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_rent_due_days trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_rent_due_days FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_rent_runs trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_rent_runs FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_renters_insurance trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_renters_insurance FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_renters_insurance_terms trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_renters_insurance_terms FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_screening_criteria trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_screening_criteria FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_screenings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_screenings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_sessions trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_sessions FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_shared_bill_parts trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_shared_bill_parts FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_shared_bills trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_shared_bills FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_showing_outcomes trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_showing_outcomes FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_showings trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_showings FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_signature_evidence trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_signature_evidence FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_signature_requests trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_signature_requests FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_signatures trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_signatures FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_signing_events trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_signing_events FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_signing_links trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_signing_links FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_staff trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_staff FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_staff_custom_roles trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_staff_custom_roles FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_staff_roles trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_staff_roles FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_tenants trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_tenants FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_unit_areas trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_unit_areas FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_units trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_units FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_utility_bills trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_utility_bills FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_utility_plans trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_utility_plans FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_utility_shares trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_utility_shares FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendor_insurance trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendor_insurance FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendor_link_revocations trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendor_link_revocations FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendor_links trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendor_links FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendor_payments_carried_over trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendor_payments_carried_over FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendor_profiles trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendor_profiles FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_vendors trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_vendors FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_work_order_details trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_work_order_details FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_work_order_steps trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_work_order_steps FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_work_orders trust_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON public.trust_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_audit();


--
-- Name: trust_bank_deposit_items trust_bank_deposit_item_brought_money; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bank_deposit_item_brought_money BEFORE INSERT ON public.trust_bank_deposit_items FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_deposit_item();


--
-- Name: trust_bank_accounts trust_bank_kind_fixed; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bank_kind_fixed BEFORE UPDATE OF kind ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_changing_bank_kind();


--
-- Name: trust_bank_statements trust_bank_statement_follows; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bank_statement_follows BEFORE INSERT ON public.trust_bank_statements FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_statement();


--
-- Name: trust_bank_statement_lines trust_bank_statement_line; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bank_statement_line BEFORE INSERT ON public.trust_bank_statement_lines FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_statement_line();


--
-- Name: trust_bank_statements trust_bank_statement_total; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_bank_statement_total AFTER INSERT ON public.trust_bank_statements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_statement_total();


--
-- Name: pgledger_transfers trust_bank_tie_out; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_bank_tie_out AFTER INSERT ON public.pgledger_transfers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_tie_out();


--
-- Name: trust_bills trust_bill_account; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_account BEFORE INSERT ON public.trust_bills FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_account();


--
-- Name: trust_bill_approvals trust_bill_decision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_decision BEFORE INSERT ON public.trust_bill_approvals FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_decision();


--
-- Name: trust_bill_declines trust_bill_decision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_decision BEFORE INSERT ON public.trust_bill_declines FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_decision();


--
-- Name: trust_bill_markups trust_bill_markup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_markup BEFORE INSERT ON public.trust_bill_markups FOR EACH ROW EXECUTE FUNCTION public.trust_fill_bill_markup();


--
-- Name: trust_bills trust_bill_reference; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_reference BEFORE INSERT ON public.trust_bills FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_reference();


--
-- Name: trust_shared_bills trust_bill_reference; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_bill_reference BEFORE INSERT ON public.trust_shared_bills FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_reference();


--
-- Name: trust_bill_splits trust_bill_split; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_bill_split AFTER INSERT ON public.trust_bill_splits DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_bill_split();


--
-- Name: trust_budgets trust_budget_account; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_budget_account BEFORE INSERT ON public.trust_budgets FOR EACH ROW EXECUTE FUNCTION public.trust_check_budget();


--
-- Name: trust_custom_role_areas trust_changed_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_changed_by BEFORE INSERT ON public.trust_custom_role_areas FOR EACH ROW EXECUTE FUNCTION public.trust_name_custom_role_changer();


--
-- Name: trust_custom_roles trust_changed_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_changed_by BEFORE INSERT ON public.trust_custom_roles FOR EACH ROW EXECUTE FUNCTION public.trust_name_custom_role_changer();


--
-- Name: trust_staff_custom_roles trust_changed_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_changed_by BEFORE INSERT ON public.trust_staff_custom_roles FOR EACH ROW EXECUTE FUNCTION public.trust_name_custom_role_changer();


--
-- Name: trust_charge_waivers trust_charge_waiver_fits; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_charge_waiver_fits BEFORE INSERT ON public.trust_charge_waivers FOR EACH ROW EXECUTE FUNCTION public.trust_check_charge_waiver();


--
-- Name: trust_payment_hold_allowances trust_check_hold_allowance; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_check_hold_allowance BEFORE INSERT ON public.trust_payment_hold_allowances FOR EACH ROW EXECUTE FUNCTION public.trust_check_hold_allowance();


--
-- Name: trust_payment_hold_releases trust_check_hold_release; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_check_hold_release BEFORE INSERT ON public.trust_payment_hold_releases FOR EACH ROW EXECUTE FUNCTION public.trust_check_hold_release();


--
-- Name: trust_payment_holds trust_check_payment_hold; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_check_payment_hold BEFORE INSERT ON public.trust_payment_holds FOR EACH ROW EXECUTE FUNCTION public.trust_check_payment_hold();


--
-- Name: trust_recurring_bills trust_check_recurring_bill; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_check_recurring_bill BEFORE INSERT ON public.trust_recurring_bills FOR EACH ROW EXECUTE FUNCTION public.trust_check_recurring_bill();


--
-- Name: pgledger_transfers trust_closed_period; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_closed_period BEFORE INSERT ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_posting_into_closed_period();


--
-- Name: trust_bank_accounts trust_closed_through; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_closed_through BEFORE INSERT OR UPDATE OF closed_through ON public.trust_bank_accounts FOR EACH ROW EXECUTE FUNCTION public.trust_refuse_moving_closed_through();


--
-- Name: trust_cutover_items trust_cutover_entry; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_cutover_entry BEFORE INSERT ON public.trust_cutover_items FOR EACH ROW EXECUTE FUNCTION public.trust_check_cutover_entry();


--
-- Name: trust_cutover_signoffs trust_cutover_entry; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_cutover_entry BEFORE INSERT ON public.trust_cutover_signoffs FOR EACH ROW EXECUTE FUNCTION public.trust_check_cutover_entry();


--
-- Name: trust_application_decisions trust_decision_override; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_decision_override BEFORE INSERT ON public.trust_application_decisions FOR EACH ROW EXECUTE FUNCTION public.trust_check_decision_override();


--
-- Name: trust_bill_payments trust_disburse; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_disburse BEFORE INSERT ON public.trust_bill_payments FOR EACH ROW WHEN ((new.step = 'paid'::text)) EXECUTE FUNCTION public.trust_check_staff_role('disburse', 'pay out money');


--
-- Name: trust_deposit_returns trust_disburse; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_disburse BEFORE INSERT ON public.trust_deposit_returns FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('disburse', 'pay out money');


--
-- Name: trust_owner_draws trust_disburse; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_disburse BEFORE INSERT ON public.trust_owner_draws FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('disburse', 'pay out money');


--
-- Name: trust_owner_fees trust_disburse; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_disburse BEFORE INSERT ON public.trust_owner_fees FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('disburse', 'take a fee from an owner');


--
-- Name: trust_pmc_fee_withdrawals trust_disburse; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_disburse BEFORE INSERT ON public.trust_pmc_fee_withdrawals FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role('disburse', 'pay out money');


--
-- Name: trust_document_files trust_document_file; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_document_file BEFORE INSERT ON public.trust_document_files FOR EACH ROW EXECUTE FUNCTION public.trust_check_document_file();


--
-- Name: trust_documents trust_document_has_file; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_document_has_file AFTER INSERT ON public.trust_documents DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_document_has_file();


--
-- Name: trust_document_removals trust_document_staff; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_document_staff BEFORE INSERT ON public.trust_document_removals FOR EACH ROW EXECUTE FUNCTION public.trust_name_document_remover();


--
-- Name: trust_documents trust_document_staff; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_document_staff BEFORE INSERT ON public.trust_documents FOR EACH ROW EXECUTE FUNCTION public.trust_name_document_uploader();


--
-- Name: trust_property_group_members trust_group_member; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_group_member BEFORE INSERT ON public.trust_property_group_members FOR EACH ROW EXECUTE FUNCTION public.trust_check_group_member();


--
-- Name: trust_lead_closings trust_lead_open; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lead_open BEFORE INSERT ON public.trust_lead_closings FOR EACH ROW EXECUTE FUNCTION public.trust_check_lead_open();


--
-- Name: trust_showings trust_lead_open; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lead_open BEFORE INSERT ON public.trust_showings FOR EACH ROW EXECUTE FUNCTION public.trust_check_lead_open();


--
-- Name: trust_leads trust_lead_unit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lead_unit BEFORE INSERT ON public.trust_leads FOR EACH ROW EXECUTE FUNCTION public.trust_check_work_order();


--
-- Name: trust_lease_offer_charges trust_lease_offer_charge_terms; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_charge_terms BEFORE INSERT ON public.trust_lease_offer_charges FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer_charge();


--
-- Name: trust_lease_offer_completions trust_lease_offer_charges_open; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_charges_open AFTER INSERT ON public.trust_lease_offer_completions FOR EACH ROW EXECUTE FUNCTION public.trust_open_lease_offer_charges();


--
-- Name: trust_lease_offer_completions trust_lease_offer_not_void; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_not_void BEFORE INSERT ON public.trust_lease_offer_completions FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer_not_void();


--
-- Name: trust_signature_requests trust_lease_offer_not_void; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_not_void BEFORE INSERT ON public.trust_signature_requests FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer_not_void();


--
-- Name: trust_signatures trust_lease_offer_not_void; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_not_void BEFORE INSERT ON public.trust_signatures FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer_not_void();


--
-- Name: trust_signing_links trust_lease_offer_not_void; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_not_void BEFORE INSERT ON public.trust_signing_links FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer_not_void();


--
-- Name: trust_lease_offers trust_lease_offer_terms; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_terms BEFORE INSERT ON public.trust_lease_offers FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer();


--
-- Name: trust_lease_offer_voids trust_lease_offer_void_open; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_lease_offer_void_open BEFORE INSERT ON public.trust_lease_offer_voids FOR EACH ROW EXECUTE FUNCTION public.trust_check_lease_offer_void();


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
-- Name: trust_ownership_share_lines trust_ownership_share_line; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_ownership_share_line BEFORE INSERT ON public.trust_ownership_share_lines FOR EACH ROW EXECUTE FUNCTION public.trust_check_ownership_share_line();


--
-- Name: trust_ownership_share_lines trust_ownership_shares_whole; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_ownership_shares_whole AFTER INSERT ON public.trust_ownership_share_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_ownership_shares();


--
-- Name: trust_ownership_shares trust_ownership_shares_whole; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_ownership_shares_whole AFTER INSERT ON public.trust_ownership_shares DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_ownership_shares();


--
-- Name: trust_payout_methods trust_payout_method; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_payout_method BEFORE INSERT ON public.trust_payout_methods FOR EACH ROW EXECUTE FUNCTION public.trust_check_payout_method();


--
-- Name: trust_application_withdrawals trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_application_withdrawals FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_archivals trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_archivals FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_assistance_charges trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_assistance_charges FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_bank_deposits trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_bank_deposits FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_bill_markups trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_bill_markups FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_bill_splits trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_bill_splits FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_calendar_feed_revocations trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_calendar_feed_revocations FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_calendar_feeds trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_calendar_feeds FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_charge_waivers trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_charge_waivers FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_company_profiles trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_company_profiles FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_contacts trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_contacts FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_email_settings trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_email_settings FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_housing_authorities trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_housing_authorities FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_inspections trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_inspections FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lead_closings trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lead_closings FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_leads trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_leads FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_break_fees trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_break_fees FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_departures trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_departures FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_guarantors trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_guarantors FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_household_removals trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_household_removals FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_occupants trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_occupants FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_offer_voids trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_offer_voids FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_pets trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_pets FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_transfers trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_utilities trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_utilities FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_lease_vehicles trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_lease_vehicles FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_management_fee_settings trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_management_fee_settings FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_notes trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_notes FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_on_call_staff trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_on_call_staff FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_owner_fees trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_owner_fees FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_ownership_shares trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_ownership_shares FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_payout_methods trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_payout_methods FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_property_group_members trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_property_group_members FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_recurring_work_order_ends trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_recurring_work_order_ends FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_recurring_work_orders trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_recurring_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_rent_assistance trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_rent_assistance FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_rent_changes trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_rent_changes FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_rent_due_days trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_rent_due_days FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_rent_runs trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_rent_runs FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_renters_insurance trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_renters_insurance FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_renters_insurance_terms trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_renters_insurance_terms FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_screening_criteria trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_screening_criteria FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_shared_bills trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_shared_bills FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_showing_outcomes trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_showing_outcomes FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_showings trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_showings FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_signing_links trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_signing_links FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_unit_areas trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_unit_areas FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_utility_bills trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_utility_bills FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_utility_plans trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_utility_plans FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_vendor_insurance trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_vendor_insurance FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_vendor_link_revocations trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_vendor_link_revocations FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_vendor_links trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_vendor_links FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_vendor_profiles trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_vendor_profiles FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_work_order_details trust_recorded_by; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON public.trust_work_order_details FOR EACH ROW EXECUTE FUNCTION public.trust_name_recorder();


--
-- Name: trust_charges trust_recurring_charge_amount; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_recurring_charge_amount BEFORE INSERT ON public.trust_charges FOR EACH ROW WHEN ((new.recurring_charge_id IS NOT NULL)) EXECUTE FUNCTION public.trust_check_recurring_charge_amount();


--
-- Name: trust_rent_assistance trust_rent_assistance_in_time; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_rent_assistance_in_time BEFORE INSERT ON public.trust_rent_assistance FOR EACH ROW EXECUTE FUNCTION public.trust_check_rent_assistance();


--
-- Name: trust_charges trust_rent_assistance_split; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_rent_assistance_split BEFORE INSERT ON public.trust_charges FOR EACH ROW WHEN ((new.kind = 'rent'::text)) EXECUTE FUNCTION public.trust_split_rent_assistance();


--
-- Name: trust_rent_changes trust_rent_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_rent_change BEFORE INSERT ON public.trust_rent_changes FOR EACH ROW EXECUTE FUNCTION public.trust_check_rent_change();


--
-- Name: trust_rent_due_days trust_rent_due_day_in_time; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_rent_due_day_in_time BEFORE INSERT ON public.trust_rent_due_days FOR EACH ROW EXECUTE FUNCTION public.trust_check_rent_due_day();


--
-- Name: trust_maintenance_request_work_orders trust_request_work_order_unit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_request_work_order_unit BEFORE INSERT ON public.trust_maintenance_request_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_check_request_work_order();


--
-- Name: trust_shared_bill_parts trust_shared_bill_whole; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_shared_bill_whole AFTER INSERT ON public.trust_shared_bill_parts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_shared_bill();


--
-- Name: trust_shared_bills trust_shared_bill_whole; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_shared_bill_whole AFTER INSERT ON public.trust_shared_bills DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_shared_bill();


--
-- Name: trust_signature_evidence trust_signature_evidence_link; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signature_evidence_link BEFORE INSERT ON public.trust_signature_evidence FOR EACH ROW EXECUTE FUNCTION public.trust_check_signature_evidence();


--
-- Name: trust_signature_evidence trust_signature_evidence_signs; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signature_evidence_signs AFTER INSERT ON public.trust_signature_evidence FOR EACH ROW EXECUTE FUNCTION public.trust_record_evidenced_signature();


--
-- Name: trust_signatures trust_signature_evidenced; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signature_evidenced BEFORE INSERT ON public.trust_signatures FOR EACH ROW EXECUTE FUNCTION public.trust_check_signature_evidenced();


--
-- Name: trust_bill_payments trust_signer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signer BEFORE INSERT ON public.trust_bill_payments FOR EACH ROW WHEN ((new.step = 'paid'::text)) EXECUTE FUNCTION public.trust_check_bill_payment_signer();


--
-- Name: trust_deposit_returns trust_signer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signer BEFORE INSERT ON public.trust_deposit_returns FOR EACH ROW EXECUTE FUNCTION public.trust_check_deposit_return_signer();


--
-- Name: trust_journal_entry_decisions trust_signer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signer BEFORE INSERT ON public.trust_journal_entry_decisions FOR EACH ROW WHEN ((new.decision = 'posted'::text)) EXECUTE FUNCTION public.trust_check_journal_signer();


--
-- Name: trust_owner_draws trust_signer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signer BEFORE INSERT ON public.trust_owner_draws FOR EACH ROW EXECUTE FUNCTION public.trust_check_draw_signer();


--
-- Name: trust_pmc_fee_withdrawals trust_signer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signer BEFORE INSERT ON public.trust_pmc_fee_withdrawals FOR EACH ROW EXECUTE FUNCTION public.trust_check_draw_signer();


--
-- Name: trust_account_signers trust_signer_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signer_change BEFORE INSERT ON public.trust_account_signers FOR EACH ROW EXECUTE FUNCTION public.trust_check_signer_change();


--
-- Name: trust_account_signers trust_signers_have_a_broker; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_signers_have_a_broker AFTER INSERT ON public.trust_account_signers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_signers_have_a_broker();


--
-- Name: trust_signing_events trust_signing_event_order; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signing_event_order BEFORE INSERT ON public.trust_signing_events FOR EACH ROW EXECUTE FUNCTION public.trust_check_signing_event();


--
-- Name: trust_signing_links trust_signing_link_signer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_signing_link_signer BEFORE INSERT ON public.trust_signing_links FOR EACH ROW EXECUTE FUNCTION public.trust_check_signing_link();


--
-- Name: trust_staff_roles trust_staff_role_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_staff_role_change BEFORE INSERT ON public.trust_staff_roles FOR EACH ROW EXECUTE FUNCTION public.trust_check_staff_role_change();


--
-- Name: trust_charge_payments trust_tenant_payment_not_assistance; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_tenant_payment_not_assistance BEFORE INSERT ON public.trust_charge_payments FOR EACH ROW EXECUTE FUNCTION public.trust_check_tenant_payment_transfer();


--
-- Name: pgledger_transfers trust_transfer_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_transfer_scope BEFORE INSERT ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_check_transfer_scope();


--
-- Name: trust_work_order_steps trust_vendor_link_step; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_vendor_link_step BEFORE INSERT ON public.trust_work_order_steps FOR EACH ROW WHEN ((new.vendor_link_id IS NOT NULL)) EXECUTE FUNCTION public.trust_check_vendor_link_step();


--
-- Name: trust_charges trust_work_order_charge; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_work_order_charge BEFORE INSERT ON public.trust_charges FOR EACH ROW WHEN ((new.work_order_id IS NOT NULL)) EXECUTE FUNCTION public.trust_check_work_order_charge();


--
-- Name: trust_work_order_steps trust_work_order_step_order; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_work_order_step_order BEFORE INSERT ON public.trust_work_order_steps FOR EACH ROW EXECUTE FUNCTION public.trust_check_work_order_step();


--
-- Name: trust_work_order_steps trust_work_order_step_vendor; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_work_order_step_vendor BEFORE INSERT ON public.trust_work_order_steps FOR EACH ROW EXECUTE FUNCTION public.trust_check_work_order_step_vendor();


--
-- Name: trust_recurring_work_orders trust_work_order_unit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_work_order_unit BEFORE INSERT ON public.trust_recurring_work_orders FOR EACH ROW EXECUTE FUNCTION public.trust_check_work_order();


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
-- Name: trust_account_signers trust_account_signers_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_account_signers
    ADD CONSTRAINT trust_account_signers_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_account_signers trust_account_signers_pmc_id_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_account_signers
    ADD CONSTRAINT trust_account_signers_pmc_id_changed_by_fkey FOREIGN KEY (pmc_id, changed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_account_signers trust_account_signers_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_account_signers
    ADD CONSTRAINT trust_account_signers_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_account_signers trust_account_signers_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_account_signers
    ADD CONSTRAINT trust_account_signers_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_adverse_actions trust_adverse_actions_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_adverse_actions
    ADD CONSTRAINT trust_adverse_actions_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_application_decisions(pmc_id, application_id);


--
-- Name: trust_adverse_actions trust_adverse_actions_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_adverse_actions
    ADD CONSTRAINT trust_adverse_actions_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_adverse_actions trust_adverse_actions_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_adverse_actions
    ADD CONSTRAINT trust_adverse_actions_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_applicants trust_applicants_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applicants
    ADD CONSTRAINT trust_applicants_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_applications(pmc_id, id);


--
-- Name: trust_applicants trust_applicants_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applicants
    ADD CONSTRAINT trust_applicants_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_application_decisions trust_application_decisions_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_applications(pmc_id, id);


--
-- Name: trust_application_decisions trust_application_decisions_pmc_id_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_pmc_id_decided_by_fkey FOREIGN KEY (pmc_id, decided_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_application_decisions trust_application_decisions_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_application_decisions trust_application_decisions_pmc_id_screening_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_decisions
    ADD CONSTRAINT trust_application_decisions_pmc_id_screening_id_fkey FOREIGN KEY (pmc_id, screening_id) REFERENCES public.trust_screenings(pmc_id, id);


--
-- Name: trust_application_link_closings trust_application_link_closings_pmc_id_closed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_link_closings
    ADD CONSTRAINT trust_application_link_closings_pmc_id_closed_by_fkey FOREIGN KEY (pmc_id, closed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_application_link_closings trust_application_link_closings_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_link_closings
    ADD CONSTRAINT trust_application_link_closings_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_application_link_closings trust_application_link_closings_pmc_id_link_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_link_closings
    ADD CONSTRAINT trust_application_link_closings_pmc_id_link_id_fkey FOREIGN KEY (pmc_id, link_id) REFERENCES public.trust_application_links(pmc_id, id);


--
-- Name: trust_application_links trust_application_links_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_links
    ADD CONSTRAINT trust_application_links_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_application_links trust_application_links_pmc_id_opened_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_links
    ADD CONSTRAINT trust_application_links_pmc_id_opened_by_fkey FOREIGN KEY (pmc_id, opened_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_application_links trust_application_links_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_links
    ADD CONSTRAINT trust_application_links_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_application_withdrawals trust_application_withdrawals_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_withdrawals
    ADD CONSTRAINT trust_application_withdrawals_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_applications(pmc_id, id);


--
-- Name: trust_application_withdrawals trust_application_withdrawals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_withdrawals
    ADD CONSTRAINT trust_application_withdrawals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_application_withdrawals trust_application_withdrawals_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_application_withdrawals
    ADD CONSTRAINT trust_application_withdrawals_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_applications trust_applications_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applications
    ADD CONSTRAINT trust_applications_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_applications trust_applications_pmc_id_link_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_applications
    ADD CONSTRAINT trust_applications_pmc_id_link_id_fkey FOREIGN KEY (pmc_id, link_id) REFERENCES public.trust_application_links(pmc_id, id);


--
-- Name: trust_archivals trust_archivals_owner_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_owner_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_archivals trust_archivals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_archivals trust_archivals_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_archivals trust_archivals_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_archivals trust_archivals_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_archivals trust_archivals_tenant_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_archivals
    ADD CONSTRAINT trust_archivals_tenant_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pmc_id_adjusts_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pmc_id_adjusts_id_fkey FOREIGN KEY (pmc_id, adjusts_id) REFERENCES public.trust_assistance_charges(pmc_id, id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pmc_id_housing_authority_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pmc_id_housing_authority_id_fkey FOREIGN KEY (pmc_id, housing_authority_id) REFERENCES public.trust_housing_authorities(pmc_id, id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_assistance_charges trust_assistance_charges_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_charges
    ADD CONSTRAINT trust_assistance_charges_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_assistance_payments trust_assistance_payments_pmc_id_assistance_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_payments
    ADD CONSTRAINT trust_assistance_payments_pmc_id_assistance_charge_id_fkey FOREIGN KEY (pmc_id, assistance_charge_id) REFERENCES public.trust_assistance_charges(pmc_id, id);


--
-- Name: trust_assistance_payments trust_assistance_payments_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_payments
    ADD CONSTRAINT trust_assistance_payments_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_assistance_payments trust_assistance_payments_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_assistance_payments
    ADD CONSTRAINT trust_assistance_payments_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


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
-- Name: trust_bank_deposit_items trust_bank_deposit_items_pmc_id_deposit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposit_items
    ADD CONSTRAINT trust_bank_deposit_items_pmc_id_deposit_id_fkey FOREIGN KEY (pmc_id, deposit_id) REFERENCES public.trust_bank_deposits(pmc_id, id);


--
-- Name: trust_bank_deposit_items trust_bank_deposit_items_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposit_items
    ADD CONSTRAINT trust_bank_deposit_items_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_bank_deposits trust_bank_deposits_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposits
    ADD CONSTRAINT trust_bank_deposits_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_bank_deposits trust_bank_deposits_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposits
    ADD CONSTRAINT trust_bank_deposits_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_deposits trust_bank_deposits_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_deposits
    ADD CONSTRAINT trust_bank_deposits_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_bank_match_cutover_items trust_bank_match_cutover_items_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_cutover_items
    ADD CONSTRAINT trust_bank_match_cutover_items_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_match_cutover_items trust_bank_match_cutover_items_pmc_id_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_cutover_items
    ADD CONSTRAINT trust_bank_match_cutover_items_pmc_id_item_id_fkey FOREIGN KEY (pmc_id, item_id) REFERENCES public.trust_cutover_items(pmc_id, id);


--
-- Name: trust_bank_match_cutover_items trust_bank_match_cutover_items_pmc_id_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_cutover_items
    ADD CONSTRAINT trust_bank_match_cutover_items_pmc_id_match_id_fkey FOREIGN KEY (pmc_id, match_id) REFERENCES public.trust_bank_matches(pmc_id, id);


--
-- Name: trust_bank_match_lines trust_bank_match_lines_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_lines
    ADD CONSTRAINT trust_bank_match_lines_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_match_lines trust_bank_match_lines_pmc_id_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_lines
    ADD CONSTRAINT trust_bank_match_lines_pmc_id_line_id_fkey FOREIGN KEY (pmc_id, line_id) REFERENCES public.trust_bank_statement_lines(pmc_id, id);


--
-- Name: trust_bank_match_lines trust_bank_match_lines_pmc_id_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_lines
    ADD CONSTRAINT trust_bank_match_lines_pmc_id_match_id_fkey FOREIGN KEY (pmc_id, match_id) REFERENCES public.trust_bank_matches(pmc_id, id);


--
-- Name: trust_bank_match_transfers trust_bank_match_transfers_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_transfers
    ADD CONSTRAINT trust_bank_match_transfers_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_match_transfers trust_bank_match_transfers_pmc_id_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_transfers
    ADD CONSTRAINT trust_bank_match_transfers_pmc_id_match_id_fkey FOREIGN KEY (pmc_id, match_id) REFERENCES public.trust_bank_matches(pmc_id, id);


--
-- Name: trust_bank_match_transfers trust_bank_match_transfers_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_match_transfers
    ADD CONSTRAINT trust_bank_match_transfers_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_bank_matches trust_bank_matches_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_matches
    ADD CONSTRAINT trust_bank_matches_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_bank_matches trust_bank_matches_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_matches
    ADD CONSTRAINT trust_bank_matches_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_matches trust_bank_matches_pmc_id_matched_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_matches
    ADD CONSTRAINT trust_bank_matches_pmc_id_matched_by_fkey FOREIGN KEY (pmc_id, matched_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_bank_statement_lines trust_bank_statement_lines_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statement_lines
    ADD CONSTRAINT trust_bank_statement_lines_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_statement_lines trust_bank_statement_lines_pmc_id_statement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statement_lines
    ADD CONSTRAINT trust_bank_statement_lines_pmc_id_statement_id_fkey FOREIGN KEY (pmc_id, statement_id) REFERENCES public.trust_bank_statements(pmc_id, id);


--
-- Name: trust_bank_statements trust_bank_statements_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_bank_statements trust_bank_statements_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_statements trust_bank_statements_pmc_id_imported_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_statements
    ADD CONSTRAINT trust_bank_statements_pmc_id_imported_by_fkey FOREIGN KEY (pmc_id, imported_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_bank_unmatches trust_bank_unmatches_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_unmatches
    ADD CONSTRAINT trust_bank_unmatches_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bank_unmatches trust_bank_unmatches_pmc_id_match_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_unmatches
    ADD CONSTRAINT trust_bank_unmatches_pmc_id_match_id_fkey FOREIGN KEY (pmc_id, match_id) REFERENCES public.trust_bank_matches(pmc_id, id);


--
-- Name: trust_bank_unmatches trust_bank_unmatches_pmc_id_unmatched_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_unmatches
    ADD CONSTRAINT trust_bank_unmatches_pmc_id_unmatched_by_fkey FOREIGN KEY (pmc_id, unmatched_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_bill_approvals trust_bill_approvals_owner_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_approvals
    ADD CONSTRAINT trust_bill_approvals_owner_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


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
-- Name: trust_bill_declines trust_bill_declines_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_declines
    ADD CONSTRAINT trust_bill_declines_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_bill_declines trust_bill_declines_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_declines
    ADD CONSTRAINT trust_bill_declines_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_declines trust_bill_declines_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_declines
    ADD CONSTRAINT trust_bill_declines_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_bill_expense_accounts trust_bill_expense_accounts_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_expense_accounts
    ADD CONSTRAINT trust_bill_expense_accounts_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_bill_expense_accounts trust_bill_expense_accounts_pmc_id_expense_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_expense_accounts
    ADD CONSTRAINT trust_bill_expense_accounts_pmc_id_expense_account_id_fkey FOREIGN KEY (pmc_id, expense_account_id) REFERENCES public.trust_expense_accounts(pmc_id, id);


--
-- Name: trust_bill_expense_accounts trust_bill_expense_accounts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_expense_accounts
    ADD CONSTRAINT trust_bill_expense_accounts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_markup_fees trust_bill_markup_fees_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markup_fees
    ADD CONSTRAINT trust_bill_markup_fees_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bill_markups(pmc_id, bill_id);


--
-- Name: trust_bill_markup_fees trust_bill_markup_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markup_fees
    ADD CONSTRAINT trust_bill_markup_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_markup_fees trust_bill_markup_fees_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markup_fees
    ADD CONSTRAINT trust_bill_markup_fees_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_bill_markups trust_bill_markups_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markups
    ADD CONSTRAINT trust_bill_markups_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_bill_markups trust_bill_markups_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markups
    ADD CONSTRAINT trust_bill_markups_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_markups trust_bill_markups_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_markups
    ADD CONSTRAINT trust_bill_markups_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_bill_splits trust_bill_splits_categorized_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_categorized_id_fkey FOREIGN KEY (categorized_id) REFERENCES public.trust_bill_expense_accounts(id);


--
-- Name: trust_bill_splits trust_bill_splits_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_bill_splits trust_bill_splits_pmc_id_expense_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_pmc_id_expense_account_id_fkey FOREIGN KEY (pmc_id, expense_account_id) REFERENCES public.trust_expense_accounts(pmc_id, id);


--
-- Name: trust_bill_splits trust_bill_splits_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_bill_splits trust_bill_splits_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bill_splits
    ADD CONSTRAINT trust_bill_splits_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_budgets trust_budgets_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_budgets
    ADD CONSTRAINT trust_budgets_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_budgets trust_budgets_pmc_id_expense_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_budgets
    ADD CONSTRAINT trust_budgets_pmc_id_expense_account_id_fkey FOREIGN KEY (pmc_id, expense_account_id) REFERENCES public.trust_expense_accounts(pmc_id, id);


--
-- Name: trust_budgets trust_budgets_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_budgets
    ADD CONSTRAINT trust_budgets_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_calendar_feed_revocations trust_calendar_feed_revocations_pmc_id_feed_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feed_revocations
    ADD CONSTRAINT trust_calendar_feed_revocations_pmc_id_feed_id_fkey FOREIGN KEY (pmc_id, feed_id) REFERENCES public.trust_calendar_feeds(pmc_id, id);


--
-- Name: trust_calendar_feed_revocations trust_calendar_feed_revocations_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feed_revocations
    ADD CONSTRAINT trust_calendar_feed_revocations_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pmc_id_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pmc_id_group_id_fkey FOREIGN KEY (pmc_id, group_id) REFERENCES public.trust_property_groups(pmc_id, id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_calendar_feeds trust_calendar_feeds_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_calendar_feeds
    ADD CONSTRAINT trust_calendar_feeds_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


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
-- Name: trust_charge_waivers trust_charge_waivers_pmc_id_credit_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_waivers
    ADD CONSTRAINT trust_charge_waivers_pmc_id_credit_charge_id_fkey FOREIGN KEY (pmc_id, credit_charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_charge_waivers trust_charge_waivers_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_waivers
    ADD CONSTRAINT trust_charge_waivers_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_charge_waivers trust_charge_waivers_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_waivers
    ADD CONSTRAINT trust_charge_waivers_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_charge_waivers trust_charge_waivers_pmc_id_waived_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charge_waivers
    ADD CONSTRAINT trust_charge_waivers_pmc_id_waived_charge_id_fkey FOREIGN KEY (pmc_id, waived_charge_id) REFERENCES public.trust_charges(pmc_id, id);


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
-- Name: trust_charges trust_charges_pmc_id_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charges
    ADD CONSTRAINT trust_charges_pmc_id_work_order_id_fkey FOREIGN KEY (pmc_id, work_order_id) REFERENCES public.trust_work_orders(pmc_id, id) NOT VALID;


--
-- Name: trust_charges trust_charges_recurring_charge_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_charges
    ADD CONSTRAINT trust_charges_recurring_charge_fkey FOREIGN KEY (pmc_id, lease_id, recurring_charge_id, kind) REFERENCES public.trust_recurring_charges(pmc_id, lease_id, id, kind);


--
-- Name: trust_company_profiles trust_company_profiles_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_company_profiles
    ADD CONSTRAINT trust_company_profiles_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_company_profiles trust_company_profiles_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_company_profiles
    ADD CONSTRAINT trust_company_profiles_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_contacts trust_contacts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_contacts
    ADD CONSTRAINT trust_contacts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_contacts trust_contacts_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_contacts
    ADD CONSTRAINT trust_contacts_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_contacts trust_contacts_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_contacts
    ADD CONSTRAINT trust_contacts_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_contacts trust_contacts_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_contacts
    ADD CONSTRAINT trust_contacts_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_contacts trust_contacts_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_contacts
    ADD CONSTRAINT trust_contacts_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_custom_role_areas trust_custom_role_areas_pmc_id_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_role_areas
    ADD CONSTRAINT trust_custom_role_areas_pmc_id_changed_by_fkey FOREIGN KEY (pmc_id, changed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_custom_role_areas trust_custom_role_areas_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_role_areas
    ADD CONSTRAINT trust_custom_role_areas_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_custom_role_areas trust_custom_role_areas_pmc_id_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_role_areas
    ADD CONSTRAINT trust_custom_role_areas_pmc_id_role_id_fkey FOREIGN KEY (pmc_id, role_id) REFERENCES public.trust_custom_roles(pmc_id, id);


--
-- Name: trust_custom_roles trust_custom_roles_pmc_id_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_roles
    ADD CONSTRAINT trust_custom_roles_pmc_id_changed_by_fkey FOREIGN KEY (pmc_id, changed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_custom_roles trust_custom_roles_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_custom_roles
    ADD CONSTRAINT trust_custom_roles_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_cutover_items trust_cutover_items_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_items
    ADD CONSTRAINT trust_cutover_items_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_cutover_items trust_cutover_items_pmc_id_entered_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_items
    ADD CONSTRAINT trust_cutover_items_pmc_id_entered_by_fkey FOREIGN KEY (pmc_id, entered_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_cutover_items trust_cutover_items_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_items
    ADD CONSTRAINT trust_cutover_items_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_cutover_signoffs trust_cutover_signoffs_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_signoffs
    ADD CONSTRAINT trust_cutover_signoffs_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_cutover_signoffs trust_cutover_signoffs_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_signoffs
    ADD CONSTRAINT trust_cutover_signoffs_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_cutover_signoffs trust_cutover_signoffs_pmc_id_signed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_cutover_signoffs
    ADD CONSTRAINT trust_cutover_signoffs_pmc_id_signed_by_fkey FOREIGN KEY (pmc_id, signed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_deposit_return_lines trust_deposit_return_lines_pmc_id_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_return_lines
    ADD CONSTRAINT trust_deposit_return_lines_pmc_id_charge_id_fkey FOREIGN KEY (pmc_id, charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_deposit_return_lines trust_deposit_return_lines_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_return_lines
    ADD CONSTRAINT trust_deposit_return_lines_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_deposit_return_lines trust_deposit_return_lines_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_return_lines
    ADD CONSTRAINT trust_deposit_return_lines_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_deposit_returns(pmc_id, lease_id);


--
-- Name: trust_deposit_return_lines trust_deposit_return_lines_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_return_lines
    ADD CONSTRAINT trust_deposit_return_lines_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_deposit_returns trust_deposit_returns_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_returns
    ADD CONSTRAINT trust_deposit_returns_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_deposit_returns trust_deposit_returns_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_returns
    ADD CONSTRAINT trust_deposit_returns_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_deposit_returns trust_deposit_returns_pmc_id_returned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_deposit_returns
    ADD CONSTRAINT trust_deposit_returns_pmc_id_returned_by_fkey FOREIGN KEY (pmc_id, returned_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_document_files trust_document_files_pmc_id_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_files
    ADD CONSTRAINT trust_document_files_pmc_id_document_id_fkey FOREIGN KEY (pmc_id, document_id) REFERENCES public.trust_documents(pmc_id, id);


--
-- Name: trust_document_files trust_document_files_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_files
    ADD CONSTRAINT trust_document_files_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_document_removals trust_document_removals_pmc_id_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_removals
    ADD CONSTRAINT trust_document_removals_pmc_id_document_id_fkey FOREIGN KEY (pmc_id, document_id) REFERENCES public.trust_documents(pmc_id, id);


--
-- Name: trust_document_removals trust_document_removals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_removals
    ADD CONSTRAINT trust_document_removals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_document_removals trust_document_removals_pmc_id_removed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_document_removals
    ADD CONSTRAINT trust_document_removals_pmc_id_removed_by_fkey FOREIGN KEY (pmc_id, removed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_documents trust_documents_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_documents trust_documents_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_documents trust_documents_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_documents trust_documents_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_documents trust_documents_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id) NOT VALID;


--
-- Name: trust_documents trust_documents_pmc_id_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_request_id_fkey FOREIGN KEY (pmc_id, request_id) REFERENCES public.trust_maintenance_requests(pmc_id, id) NOT VALID;


--
-- Name: trust_documents trust_documents_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id) NOT VALID;


--
-- Name: trust_documents trust_documents_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id) NOT VALID;


--
-- Name: trust_documents trust_documents_pmc_id_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_uploaded_by_fkey FOREIGN KEY (pmc_id, uploaded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_documents trust_documents_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_documents trust_documents_pmc_id_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_documents
    ADD CONSTRAINT trust_documents_pmc_id_work_order_id_fkey FOREIGN KEY (pmc_id, work_order_id) REFERENCES public.trust_work_orders(pmc_id, id) NOT VALID;


--
-- Name: trust_email_deliveries trust_email_deliveries_pmc_id_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_email_deliveries
    ADD CONSTRAINT trust_email_deliveries_pmc_id_email_id_fkey FOREIGN KEY (pmc_id, email_id) REFERENCES public.trust_emails(pmc_id, id);


--
-- Name: trust_email_deliveries trust_email_deliveries_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_email_deliveries
    ADD CONSTRAINT trust_email_deliveries_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_email_settings trust_email_settings_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_email_settings
    ADD CONSTRAINT trust_email_settings_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_email_settings trust_email_settings_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_email_settings
    ADD CONSTRAINT trust_email_settings_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_emails trust_emails_applicant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_applicant_id_fkey FOREIGN KEY (applicant_id) REFERENCES public.trust_applicants(id) NOT VALID;


--
-- Name: trust_emails trust_emails_offer_id_signer_position_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_offer_id_signer_position_fkey FOREIGN KEY (offer_id, signer_position) REFERENCES public.trust_lease_offer_signers(offer_id, "position") NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_applications(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_emails trust_emails_pmc_id_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_charge_id_fkey FOREIGN KEY (pmc_id, charge_id) REFERENCES public.trust_charges(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_emails trust_emails_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_request_id_fkey FOREIGN KEY (pmc_id, request_id) REFERENCES public.trust_maintenance_requests(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_sent_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_sent_by_fkey FOREIGN KEY (pmc_id, sent_by) REFERENCES public.trust_staff(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id) NOT VALID;


--
-- Name: trust_emails trust_emails_work_order_step_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_emails
    ADD CONSTRAINT trust_emails_work_order_step_id_fkey FOREIGN KEY (work_order_step_id) REFERENCES public.trust_work_order_steps(id) NOT VALID;


--
-- Name: trust_expense_accounts trust_expense_accounts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_expense_accounts
    ADD CONSTRAINT trust_expense_accounts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_housing_authorities trust_housing_authorities_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_housing_authorities
    ADD CONSTRAINT trust_housing_authorities_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_housing_authorities trust_housing_authorities_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_housing_authorities
    ADD CONSTRAINT trust_housing_authorities_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_idempotency_keys trust_idempotency_keys_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_idempotency_keys
    ADD CONSTRAINT trust_idempotency_keys_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_inspection_items trust_inspection_items_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspection_items
    ADD CONSTRAINT trust_inspection_items_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_inspection_items trust_inspection_items_pmc_id_inspection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspection_items
    ADD CONSTRAINT trust_inspection_items_pmc_id_inspection_id_fkey FOREIGN KEY (pmc_id, inspection_id) REFERENCES public.trust_inspections(pmc_id, id);


--
-- Name: trust_inspections trust_inspections_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspections
    ADD CONSTRAINT trust_inspections_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_inspections trust_inspections_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspections
    ADD CONSTRAINT trust_inspections_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_inspections trust_inspections_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_inspections
    ADD CONSTRAINT trust_inspections_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_invites trust_invites_pmc_id_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_invites
    ADD CONSTRAINT trust_invites_pmc_id_created_by_fkey FOREIGN KEY (pmc_id, created_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_invites trust_invites_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_invites
    ADD CONSTRAINT trust_invites_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_invites trust_invites_pmc_id_login_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_invites
    ADD CONSTRAINT trust_invites_pmc_id_login_id_fkey FOREIGN KEY (pmc_id, login_id) REFERENCES public.trust_logins(pmc_id, id);


--
-- Name: trust_journal_entries trust_journal_entries_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entries
    ADD CONSTRAINT trust_journal_entries_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_journal_entries trust_journal_entries_pmc_id_proposed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entries
    ADD CONSTRAINT trust_journal_entries_pmc_id_proposed_by_fkey FOREIGN KEY (pmc_id, proposed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_journal_entry_decisions trust_journal_entry_decisions_pmc_id_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_decisions
    ADD CONSTRAINT trust_journal_entry_decisions_pmc_id_decided_by_fkey FOREIGN KEY (pmc_id, decided_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_journal_entry_decisions trust_journal_entry_decisions_pmc_id_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_decisions
    ADD CONSTRAINT trust_journal_entry_decisions_pmc_id_entry_id_fkey FOREIGN KEY (pmc_id, entry_id) REFERENCES public.trust_journal_entries(pmc_id, id);


--
-- Name: trust_journal_entry_decisions trust_journal_entry_decisions_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_decisions
    ADD CONSTRAINT trust_journal_entry_decisions_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_journal_entry_lines trust_journal_entry_lines_from_account_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_lines
    ADD CONSTRAINT trust_journal_entry_lines_from_account_fkey FOREIGN KEY (from_account) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_journal_entry_lines trust_journal_entry_lines_pmc_id_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_lines
    ADD CONSTRAINT trust_journal_entry_lines_pmc_id_entry_id_fkey FOREIGN KEY (pmc_id, entry_id) REFERENCES public.trust_journal_entries(pmc_id, id);


--
-- Name: trust_journal_entry_lines trust_journal_entry_lines_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_lines
    ADD CONSTRAINT trust_journal_entry_lines_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_journal_entry_lines trust_journal_entry_lines_to_account_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_journal_entry_lines
    ADD CONSTRAINT trust_journal_entry_lines_to_account_fkey FOREIGN KEY (to_account) REFERENCES public.trust_ledger_accounts(ledger_account_id);


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
-- Name: trust_lead_closings trust_lead_closings_pmc_id_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lead_closings
    ADD CONSTRAINT trust_lead_closings_pmc_id_lead_id_fkey FOREIGN KEY (pmc_id, lead_id) REFERENCES public.trust_leads(pmc_id, id);


--
-- Name: trust_lead_closings trust_lead_closings_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lead_closings
    ADD CONSTRAINT trust_lead_closings_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_leads trust_leads_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leads
    ADD CONSTRAINT trust_leads_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_leads trust_leads_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leads
    ADD CONSTRAINT trust_leads_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_leads trust_leads_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leads
    ADD CONSTRAINT trust_leads_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_leads trust_leads_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_leads
    ADD CONSTRAINT trust_leads_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_lease_break_fees trust_lease_break_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_break_fees
    ADD CONSTRAINT trust_lease_break_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_break_fees trust_lease_break_fees_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_break_fees
    ADD CONSTRAINT trust_lease_break_fees_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_lease_break_fees trust_lease_break_fees_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_break_fees
    ADD CONSTRAINT trust_lease_break_fees_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_departures trust_lease_departures_lease_id_deposit_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_departures
    ADD CONSTRAINT trust_lease_departures_lease_id_deposit_to_fkey FOREIGN KEY (lease_id, deposit_to) REFERENCES public.trust_lease_tenants(lease_id, tenant_id);


--
-- Name: trust_lease_departures trust_lease_departures_lease_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_departures
    ADD CONSTRAINT trust_lease_departures_lease_id_tenant_id_fkey FOREIGN KEY (lease_id, tenant_id) REFERENCES public.trust_lease_tenants(lease_id, tenant_id);


--
-- Name: trust_lease_departures trust_lease_departures_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_departures
    ADD CONSTRAINT trust_lease_departures_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_departures trust_lease_departures_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_departures
    ADD CONSTRAINT trust_lease_departures_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_departures trust_lease_departures_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_departures
    ADD CONSTRAINT trust_lease_departures_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_end_adjustments trust_lease_end_adjustments_pmc_id_adjustment_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_end_adjustments
    ADD CONSTRAINT trust_lease_end_adjustments_pmc_id_adjustment_charge_id_fkey FOREIGN KEY (pmc_id, adjustment_charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_lease_end_adjustments trust_lease_end_adjustments_pmc_id_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_end_adjustments
    ADD CONSTRAINT trust_lease_end_adjustments_pmc_id_charge_id_fkey FOREIGN KEY (pmc_id, charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_lease_end_adjustments trust_lease_end_adjustments_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_end_adjustments
    ADD CONSTRAINT trust_lease_end_adjustments_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_guarantors trust_lease_guarantors_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_guarantors
    ADD CONSTRAINT trust_lease_guarantors_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_guarantors trust_lease_guarantors_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_guarantors
    ADD CONSTRAINT trust_lease_guarantors_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_guarantors trust_lease_guarantors_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_guarantors
    ADD CONSTRAINT trust_lease_guarantors_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_lease_guarantors trust_lease_guarantors_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_guarantors
    ADD CONSTRAINT trust_lease_guarantors_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pmc_id_guarantor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pmc_id_guarantor_id_fkey FOREIGN KEY (pmc_id, guarantor_id) REFERENCES public.trust_lease_guarantors(pmc_id, id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pmc_id_occupant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pmc_id_occupant_id_fkey FOREIGN KEY (pmc_id, occupant_id) REFERENCES public.trust_lease_occupants(pmc_id, id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pmc_id_pet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pmc_id_pet_id_fkey FOREIGN KEY (pmc_id, pet_id) REFERENCES public.trust_lease_pets(pmc_id, id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_household_removals trust_lease_household_removals_pmc_id_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_household_removals
    ADD CONSTRAINT trust_lease_household_removals_pmc_id_vehicle_id_fkey FOREIGN KEY (pmc_id, vehicle_id) REFERENCES public.trust_lease_vehicles(pmc_id, id);


--
-- Name: trust_lease_occupants trust_lease_occupants_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_occupants
    ADD CONSTRAINT trust_lease_occupants_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_occupants trust_lease_occupants_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_occupants
    ADD CONSTRAINT trust_lease_occupants_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_occupants trust_lease_occupants_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_occupants
    ADD CONSTRAINT trust_lease_occupants_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_offer_charges trust_lease_offer_charges_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_charges
    ADD CONSTRAINT trust_lease_offer_charges_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_offer_charges trust_lease_offer_charges_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_charges
    ADD CONSTRAINT trust_lease_offer_charges_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_lease_offer_completions trust_lease_offer_completions_pmc_id_completed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_completions
    ADD CONSTRAINT trust_lease_offer_completions_pmc_id_completed_by_fkey FOREIGN KEY (pmc_id, completed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_offer_completions trust_lease_offer_completions_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_completions
    ADD CONSTRAINT trust_lease_offer_completions_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_offer_completions trust_lease_offer_completions_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_completions
    ADD CONSTRAINT trust_lease_offer_completions_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_offer_completions trust_lease_offer_completions_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_completions
    ADD CONSTRAINT trust_lease_offer_completions_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_lease_offer_signers trust_lease_offer_signers_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_signers
    ADD CONSTRAINT trust_lease_offer_signers_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_offer_signers trust_lease_offer_signers_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_signers
    ADD CONSTRAINT trust_lease_offer_signers_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_lease_offer_voids trust_lease_offer_voids_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_voids
    ADD CONSTRAINT trust_lease_offer_voids_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_offer_voids trust_lease_offer_voids_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_voids
    ADD CONSTRAINT trust_lease_offer_voids_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_lease_offer_voids trust_lease_offer_voids_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offer_voids
    ADD CONSTRAINT trust_lease_offer_voids_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_offers trust_lease_offers_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_applications(pmc_id, id);


--
-- Name: trust_lease_offers trust_lease_offers_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_offers trust_lease_offers_pmc_id_offered_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pmc_id_offered_by_fkey FOREIGN KEY (pmc_id, offered_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_offers trust_lease_offers_pmc_id_renews_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pmc_id_renews_lease_id_fkey FOREIGN KEY (pmc_id, renews_lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_offers trust_lease_offers_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_offers
    ADD CONSTRAINT trust_lease_offers_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_lease_pets trust_lease_pets_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_pets
    ADD CONSTRAINT trust_lease_pets_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_pets trust_lease_pets_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_pets
    ADD CONSTRAINT trust_lease_pets_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_pets trust_lease_pets_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_pets
    ADD CONSTRAINT trust_lease_pets_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_lease_transfers trust_lease_transfers_owner_account_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_transfers
    ADD CONSTRAINT trust_lease_transfers_owner_account_fkey FOREIGN KEY (owner_account) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_lease_transfers trust_lease_transfers_pmc_id_from_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_transfers
    ADD CONSTRAINT trust_lease_transfers_pmc_id_from_lease_id_fkey FOREIGN KEY (pmc_id, from_lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_transfers trust_lease_transfers_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_transfers
    ADD CONSTRAINT trust_lease_transfers_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_transfers trust_lease_transfers_pmc_id_to_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_transfers
    ADD CONSTRAINT trust_lease_transfers_pmc_id_to_lease_id_fkey FOREIGN KEY (pmc_id, to_lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_utilities trust_lease_utilities_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_utilities
    ADD CONSTRAINT trust_lease_utilities_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_utilities trust_lease_utilities_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_utilities
    ADD CONSTRAINT trust_lease_utilities_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_utilities trust_lease_utilities_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_utilities
    ADD CONSTRAINT trust_lease_utilities_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_lease_vehicles trust_lease_vehicles_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_vehicles
    ADD CONSTRAINT trust_lease_vehicles_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_lease_vehicles trust_lease_vehicles_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_vehicles
    ADD CONSTRAINT trust_lease_vehicles_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_lease_vehicles trust_lease_vehicles_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_lease_vehicles
    ADD CONSTRAINT trust_lease_vehicles_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_listing_removals trust_listing_removals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listing_removals
    ADD CONSTRAINT trust_listing_removals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_listing_removals trust_listing_removals_pmc_id_listing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listing_removals
    ADD CONSTRAINT trust_listing_removals_pmc_id_listing_id_fkey FOREIGN KEY (pmc_id, listing_id) REFERENCES public.trust_listings(pmc_id, id);


--
-- Name: trust_listing_removals trust_listing_removals_pmc_id_removed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listing_removals
    ADD CONSTRAINT trust_listing_removals_pmc_id_removed_by_fkey FOREIGN KEY (pmc_id, removed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_listings trust_listings_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listings
    ADD CONSTRAINT trust_listings_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_listings trust_listings_pmc_id_link_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listings
    ADD CONSTRAINT trust_listings_pmc_id_link_id_fkey FOREIGN KEY (pmc_id, link_id) REFERENCES public.trust_application_links(pmc_id, id);


--
-- Name: trust_listings trust_listings_pmc_id_listed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_listings
    ADD CONSTRAINT trust_listings_pmc_id_listed_by_fkey FOREIGN KEY (pmc_id, listed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_login_secrets trust_login_secrets_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_login_secrets
    ADD CONSTRAINT trust_login_secrets_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_login_secrets trust_login_secrets_pmc_id_login_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_login_secrets
    ADD CONSTRAINT trust_login_secrets_pmc_id_login_id_fkey FOREIGN KEY (pmc_id, login_id) REFERENCES public.trust_logins(pmc_id, id);


--
-- Name: trust_logins trust_logins_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_logins trust_logins_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_logins trust_logins_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_logins trust_logins_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_logins
    ADD CONSTRAINT trust_logins_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_maintenance_request_work_orders trust_maintenance_request_work_orders_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_request_work_orders
    ADD CONSTRAINT trust_maintenance_request_work_orders_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_maintenance_request_work_orders trust_maintenance_request_work_orders_pmc_id_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_request_work_orders
    ADD CONSTRAINT trust_maintenance_request_work_orders_pmc_id_request_id_fkey FOREIGN KEY (pmc_id, request_id) REFERENCES public.trust_maintenance_requests(pmc_id, id);


--
-- Name: trust_maintenance_request_work_orders trust_maintenance_request_work_orders_pmc_id_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_request_work_orders
    ADD CONSTRAINT trust_maintenance_request_work_orders_pmc_id_work_order_id_fkey FOREIGN KEY (pmc_id, work_order_id) REFERENCES public.trust_work_orders(pmc_id, id);


--
-- Name: trust_maintenance_requests trust_maintenance_requests_lease_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_requests
    ADD CONSTRAINT trust_maintenance_requests_lease_id_tenant_id_fkey FOREIGN KEY (lease_id, tenant_id) REFERENCES public.trust_lease_tenants(lease_id, tenant_id);


--
-- Name: trust_maintenance_requests trust_maintenance_requests_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_requests
    ADD CONSTRAINT trust_maintenance_requests_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_maintenance_requests trust_maintenance_requests_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_requests
    ADD CONSTRAINT trust_maintenance_requests_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_maintenance_requests trust_maintenance_requests_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_maintenance_requests
    ADD CONSTRAINT trust_maintenance_requests_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


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
-- Name: trust_management_fee_settings trust_management_fee_settings_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fee_settings
    ADD CONSTRAINT trust_management_fee_settings_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_management_fee_settings trust_management_fee_settings_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_management_fee_settings
    ADD CONSTRAINT trust_management_fee_settings_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_notes trust_notes_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_notes trust_notes_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_notes trust_notes_pmc_id_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_owner_id_fkey FOREIGN KEY (pmc_id, owner_id) REFERENCES public.trust_owners(pmc_id, id);


--
-- Name: trust_notes trust_notes_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_notes trust_notes_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_notes trust_notes_pmc_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_tenant_id_fkey FOREIGN KEY (pmc_id, tenant_id) REFERENCES public.trust_tenants(pmc_id, id);


--
-- Name: trust_notes trust_notes_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_notes trust_notes_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_notes
    ADD CONSTRAINT trust_notes_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_nsf_fee_policies trust_nsf_fee_policies_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fee_policies
    ADD CONSTRAINT trust_nsf_fee_policies_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_nsf_fee_policies trust_nsf_fee_policies_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fee_policies
    ADD CONSTRAINT trust_nsf_fee_policies_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_nsf_fees trust_nsf_fees_pmc_id_fee_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fees
    ADD CONSTRAINT trust_nsf_fees_pmc_id_fee_charge_id_fkey FOREIGN KEY (pmc_id, fee_charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_nsf_fees trust_nsf_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fees
    ADD CONSTRAINT trust_nsf_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_nsf_fees trust_nsf_fees_pmc_id_returned_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fees
    ADD CONSTRAINT trust_nsf_fees_pmc_id_returned_key_fkey FOREIGN KEY (pmc_id, returned_key) REFERENCES public.trust_idempotency_keys(pmc_id, idempotency_key);


--
-- Name: trust_nsf_fees trust_nsf_fees_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_nsf_fees
    ADD CONSTRAINT trust_nsf_fees_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.trust_nsf_fee_policies(id);


--
-- Name: trust_on_call_staff trust_on_call_staff_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_on_call_staff
    ADD CONSTRAINT trust_on_call_staff_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_on_call_staff trust_on_call_staff_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_on_call_staff
    ADD CONSTRAINT trust_on_call_staff_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_on_call_staff trust_on_call_staff_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_on_call_staff
    ADD CONSTRAINT trust_on_call_staff_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_owner_contributions trust_owner_contributions_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_contributions
    ADD CONSTRAINT trust_owner_contributions_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_owner_contributions trust_owner_contributions_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_contributions
    ADD CONSTRAINT trust_owner_contributions_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_owner_contributions trust_owner_contributions_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_contributions
    ADD CONSTRAINT trust_owner_contributions_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


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
-- Name: trust_owner_fees trust_owner_fees_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_owner_fees trust_owner_fees_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_owner_fees trust_owner_fees_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_owner_fees trust_owner_fees_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owner_fees
    ADD CONSTRAINT trust_owner_fees_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_owners trust_owners_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owners
    ADD CONSTRAINT trust_owners_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_ownership_share_lines trust_ownership_share_lines_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_share_lines
    ADD CONSTRAINT trust_ownership_share_lines_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_ownership_share_lines trust_ownership_share_lines_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_share_lines
    ADD CONSTRAINT trust_ownership_share_lines_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_ownership_share_lines trust_ownership_share_lines_pmc_id_share_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_share_lines
    ADD CONSTRAINT trust_ownership_share_lines_pmc_id_share_id_fkey FOREIGN KEY (pmc_id, share_id) REFERENCES public.trust_ownership_shares(pmc_id, id);


--
-- Name: trust_ownership_shares trust_ownership_shares_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_shares
    ADD CONSTRAINT trust_ownership_shares_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_ownership_shares trust_ownership_shares_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_shares
    ADD CONSTRAINT trust_ownership_shares_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_ownership_shares trust_ownership_shares_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ownership_shares
    ADD CONSTRAINT trust_ownership_shares_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_payment_hold_allowances trust_payment_hold_allowances_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_allowances
    ADD CONSTRAINT trust_payment_hold_allowances_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_payment_hold_allowances trust_payment_hold_allowances_pmc_id_hold_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_allowances
    ADD CONSTRAINT trust_payment_hold_allowances_pmc_id_hold_id_fkey FOREIGN KEY (pmc_id, hold_id) REFERENCES public.trust_payment_holds(pmc_id, id);


--
-- Name: trust_payment_hold_allowances trust_payment_hold_allowances_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_allowances
    ADD CONSTRAINT trust_payment_hold_allowances_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_payment_hold_releases trust_payment_hold_releases_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_releases
    ADD CONSTRAINT trust_payment_hold_releases_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_payment_hold_releases trust_payment_hold_releases_pmc_id_hold_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_hold_releases
    ADD CONSTRAINT trust_payment_hold_releases_pmc_id_hold_id_fkey FOREIGN KEY (pmc_id, hold_id) REFERENCES public.trust_payment_holds(pmc_id, id);


--
-- Name: trust_payment_holds trust_payment_holds_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_holds
    ADD CONSTRAINT trust_payment_holds_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_payment_holds trust_payment_holds_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payment_holds
    ADD CONSTRAINT trust_payment_holds_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


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
-- Name: trust_payout_methods trust_payout_methods_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payout_methods
    ADD CONSTRAINT trust_payout_methods_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_payout_methods trust_payout_methods_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payout_methods
    ADD CONSTRAINT trust_payout_methods_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_payout_methods trust_payout_methods_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_payout_methods
    ADD CONSTRAINT trust_payout_methods_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_pmc_fee_withdrawals trust_pmc_fee_withdrawals_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_pmc_fee_withdrawals
    ADD CONSTRAINT trust_pmc_fee_withdrawals_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_pmc_fee_withdrawals trust_pmc_fee_withdrawals_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_pmc_fee_withdrawals
    ADD CONSTRAINT trust_pmc_fee_withdrawals_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_pmc_fee_withdrawals trust_pmc_fee_withdrawals_transfer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_pmc_fee_withdrawals
    ADD CONSTRAINT trust_pmc_fee_withdrawals_transfer_id_fkey FOREIGN KEY (transfer_id) REFERENCES public.pgledger_transfers(id);


--
-- Name: trust_properties trust_properties_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_properties
    ADD CONSTRAINT trust_properties_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_property_group_members trust_property_group_members_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_group_members
    ADD CONSTRAINT trust_property_group_members_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_property_group_members trust_property_group_members_pmc_id_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_group_members
    ADD CONSTRAINT trust_property_group_members_pmc_id_group_id_fkey FOREIGN KEY (pmc_id, group_id) REFERENCES public.trust_property_groups(pmc_id, id);


--
-- Name: trust_property_group_members trust_property_group_members_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_group_members
    ADD CONSTRAINT trust_property_group_members_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_property_group_members trust_property_group_members_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_group_members
    ADD CONSTRAINT trust_property_group_members_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_property_groups trust_property_groups_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_property_groups
    ADD CONSTRAINT trust_property_groups_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_reconciliation_preparations trust_reconciliation_preparations_pmc_id_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliation_preparations
    ADD CONSTRAINT trust_reconciliation_preparations_pmc_id_bank_account_id_fkey FOREIGN KEY (pmc_id, bank_account_id) REFERENCES public.trust_bank_accounts(pmc_id, id);


--
-- Name: trust_reconciliation_preparations trust_reconciliation_preparations_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliation_preparations
    ADD CONSTRAINT trust_reconciliation_preparations_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_reconciliation_preparations trust_reconciliation_preparations_pmc_id_prepared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_reconciliation_preparations
    ADD CONSTRAINT trust_reconciliation_preparations_pmc_id_prepared_by_fkey FOREIGN KEY (pmc_id, prepared_by) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_recurring_bill_ends trust_recurring_bill_ends_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_ends
    ADD CONSTRAINT trust_recurring_bill_ends_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_recurring_bill_ends trust_recurring_bill_ends_pmc_id_recurring_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_ends
    ADD CONSTRAINT trust_recurring_bill_ends_pmc_id_recurring_bill_id_fkey FOREIGN KEY (pmc_id, recurring_bill_id) REFERENCES public.trust_recurring_bills(pmc_id, id);


--
-- Name: trust_recurring_bill_expense_accounts trust_recurring_bill_expense_acc_pmc_id_expense_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_expense_accounts
    ADD CONSTRAINT trust_recurring_bill_expense_acc_pmc_id_expense_account_id_fkey FOREIGN KEY (pmc_id, expense_account_id) REFERENCES public.trust_expense_accounts(pmc_id, id);


--
-- Name: trust_recurring_bill_expense_accounts trust_recurring_bill_expense_acco_pmc_id_recurring_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_expense_accounts
    ADD CONSTRAINT trust_recurring_bill_expense_acco_pmc_id_recurring_bill_id_fkey FOREIGN KEY (pmc_id, recurring_bill_id) REFERENCES public.trust_recurring_bills(pmc_id, id);


--
-- Name: trust_recurring_bill_expense_accounts trust_recurring_bill_expense_accounts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bill_expense_accounts
    ADD CONSTRAINT trust_recurring_bill_expense_accounts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_recurring_bills trust_recurring_bills_ledger_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bills
    ADD CONSTRAINT trust_recurring_bills_ledger_account_id_fkey FOREIGN KEY (ledger_account_id) REFERENCES public.trust_ledger_accounts(ledger_account_id);


--
-- Name: trust_recurring_bills trust_recurring_bills_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bills
    ADD CONSTRAINT trust_recurring_bills_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_recurring_bills trust_recurring_bills_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_bills
    ADD CONSTRAINT trust_recurring_bills_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_recurring_charge_ends trust_recurring_charge_ends_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charge_ends
    ADD CONSTRAINT trust_recurring_charge_ends_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_recurring_charge_ends trust_recurring_charge_ends_pmc_id_recurring_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charge_ends
    ADD CONSTRAINT trust_recurring_charge_ends_pmc_id_recurring_charge_id_fkey FOREIGN KEY (pmc_id, recurring_charge_id) REFERENCES public.trust_recurring_charges(pmc_id, id);


--
-- Name: trust_recurring_charges trust_recurring_charges_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charges
    ADD CONSTRAINT trust_recurring_charges_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_recurring_charges trust_recurring_charges_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_charges
    ADD CONSTRAINT trust_recurring_charges_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_recurring_work_order_ends trust_recurring_work_order_en_pmc_id_recurring_work_order__fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_order_ends
    ADD CONSTRAINT trust_recurring_work_order_en_pmc_id_recurring_work_order__fkey FOREIGN KEY (pmc_id, recurring_work_order_id) REFERENCES public.trust_recurring_work_orders(pmc_id, id);


--
-- Name: trust_recurring_work_order_ends trust_recurring_work_order_ends_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_order_ends
    ADD CONSTRAINT trust_recurring_work_order_ends_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_recurring_work_orders trust_recurring_work_orders_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_recurring_work_orders
    ADD CONSTRAINT trust_recurring_work_orders_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_rent_assistance trust_rent_assistance_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_assistance
    ADD CONSTRAINT trust_rent_assistance_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_rent_assistance trust_rent_assistance_pmc_id_housing_authority_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_assistance
    ADD CONSTRAINT trust_rent_assistance_pmc_id_housing_authority_id_fkey FOREIGN KEY (pmc_id, housing_authority_id) REFERENCES public.trust_housing_authorities(pmc_id, id);


--
-- Name: trust_rent_assistance trust_rent_assistance_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_assistance
    ADD CONSTRAINT trust_rent_assistance_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_rent_assistance trust_rent_assistance_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_assistance
    ADD CONSTRAINT trust_rent_assistance_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_rent_changes trust_rent_changes_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_changes
    ADD CONSTRAINT trust_rent_changes_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_rent_changes trust_rent_changes_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_changes
    ADD CONSTRAINT trust_rent_changes_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_rent_changes trust_rent_changes_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_changes
    ADD CONSTRAINT trust_rent_changes_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_rent_due_days trust_rent_due_days_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_due_days
    ADD CONSTRAINT trust_rent_due_days_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_rent_due_days trust_rent_due_days_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_due_days
    ADD CONSTRAINT trust_rent_due_days_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_rent_due_days trust_rent_due_days_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_due_days
    ADD CONSTRAINT trust_rent_due_days_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_rent_runs trust_rent_runs_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_runs
    ADD CONSTRAINT trust_rent_runs_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_rent_runs trust_rent_runs_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_rent_runs
    ADD CONSTRAINT trust_rent_runs_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_renters_insurance trust_renters_insurance_lease_id_submitted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance
    ADD CONSTRAINT trust_renters_insurance_lease_id_submitted_by_fkey FOREIGN KEY (lease_id, submitted_by) REFERENCES public.trust_lease_tenants(lease_id, tenant_id);


--
-- Name: trust_renters_insurance trust_renters_insurance_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance
    ADD CONSTRAINT trust_renters_insurance_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_renters_insurance trust_renters_insurance_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance
    ADD CONSTRAINT trust_renters_insurance_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_renters_insurance trust_renters_insurance_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance
    ADD CONSTRAINT trust_renters_insurance_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_renters_insurance_terms trust_renters_insurance_terms_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance_terms
    ADD CONSTRAINT trust_renters_insurance_terms_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_renters_insurance_terms trust_renters_insurance_terms_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance_terms
    ADD CONSTRAINT trust_renters_insurance_terms_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_renters_insurance_terms trust_renters_insurance_terms_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_renters_insurance_terms
    ADD CONSTRAINT trust_renters_insurance_terms_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_screening_criteria trust_screening_criteria_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screening_criteria
    ADD CONSTRAINT trust_screening_criteria_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_screening_criteria trust_screening_criteria_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screening_criteria
    ADD CONSTRAINT trust_screening_criteria_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_screenings trust_screenings_pmc_id_application_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screenings
    ADD CONSTRAINT trust_screenings_pmc_id_application_id_fkey FOREIGN KEY (pmc_id, application_id) REFERENCES public.trust_applications(pmc_id, id);


--
-- Name: trust_screenings trust_screenings_pmc_id_criteria_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screenings
    ADD CONSTRAINT trust_screenings_pmc_id_criteria_id_fkey FOREIGN KEY (pmc_id, criteria_id) REFERENCES public.trust_screening_criteria(pmc_id, id) NOT VALID;


--
-- Name: trust_screenings trust_screenings_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screenings
    ADD CONSTRAINT trust_screenings_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_screenings trust_screenings_pmc_id_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_screenings
    ADD CONSTRAINT trust_screenings_pmc_id_requested_by_fkey FOREIGN KEY (pmc_id, requested_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_sessions trust_sessions_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_sessions
    ADD CONSTRAINT trust_sessions_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_sessions trust_sessions_pmc_id_login_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_sessions
    ADD CONSTRAINT trust_sessions_pmc_id_login_id_fkey FOREIGN KEY (pmc_id, login_id) REFERENCES public.trust_logins(pmc_id, id);


--
-- Name: trust_shared_bill_parts trust_shared_bill_parts_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bill_parts
    ADD CONSTRAINT trust_shared_bill_parts_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_shared_bill_parts trust_shared_bill_parts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bill_parts
    ADD CONSTRAINT trust_shared_bill_parts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_shared_bill_parts trust_shared_bill_parts_pmc_id_shared_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bill_parts
    ADD CONSTRAINT trust_shared_bill_parts_pmc_id_shared_bill_id_fkey FOREIGN KEY (pmc_id, shared_bill_id) REFERENCES public.trust_shared_bills(pmc_id, id);


--
-- Name: trust_shared_bills trust_shared_bills_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bills
    ADD CONSTRAINT trust_shared_bills_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_shared_bills trust_shared_bills_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bills
    ADD CONSTRAINT trust_shared_bills_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_shared_bills trust_shared_bills_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_shared_bills
    ADD CONSTRAINT trust_shared_bills_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_showing_outcomes trust_showing_outcomes_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showing_outcomes
    ADD CONSTRAINT trust_showing_outcomes_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_showing_outcomes trust_showing_outcomes_pmc_id_showing_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showing_outcomes
    ADD CONSTRAINT trust_showing_outcomes_pmc_id_showing_id_fkey FOREIGN KEY (pmc_id, showing_id) REFERENCES public.trust_showings(pmc_id, id);


--
-- Name: trust_showings trust_showings_pmc_id_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showings
    ADD CONSTRAINT trust_showings_pmc_id_lead_id_fkey FOREIGN KEY (pmc_id, lead_id) REFERENCES public.trust_leads(pmc_id, id);


--
-- Name: trust_showings trust_showings_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showings
    ADD CONSTRAINT trust_showings_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_showings trust_showings_pmc_id_shown_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showings
    ADD CONSTRAINT trust_showings_pmc_id_shown_by_fkey FOREIGN KEY (pmc_id, shown_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_showings trust_showings_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_showings
    ADD CONSTRAINT trust_showings_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_signature_evidence trust_signature_evidence_offer_id_position_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_evidence
    ADD CONSTRAINT trust_signature_evidence_offer_id_position_fkey FOREIGN KEY (offer_id, "position") REFERENCES public.trust_lease_offer_signers(offer_id, "position");


--
-- Name: trust_signature_evidence trust_signature_evidence_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_evidence
    ADD CONSTRAINT trust_signature_evidence_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_signature_evidence trust_signature_evidence_pmc_id_link_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_evidence
    ADD CONSTRAINT trust_signature_evidence_pmc_id_link_id_fkey FOREIGN KEY (pmc_id, link_id) REFERENCES public.trust_signing_links(pmc_id, id);


--
-- Name: trust_signature_evidence trust_signature_evidence_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_evidence
    ADD CONSTRAINT trust_signature_evidence_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_signature_requests trust_signature_requests_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_requests
    ADD CONSTRAINT trust_signature_requests_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_signature_requests trust_signature_requests_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_requests
    ADD CONSTRAINT trust_signature_requests_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_signature_requests trust_signature_requests_pmc_id_sent_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signature_requests
    ADD CONSTRAINT trust_signature_requests_pmc_id_sent_by_fkey FOREIGN KEY (pmc_id, sent_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_signatures trust_signatures_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signatures
    ADD CONSTRAINT trust_signatures_offer_id_fkey FOREIGN KEY (offer_id) REFERENCES public.trust_signature_requests(offer_id);


--
-- Name: trust_signatures trust_signatures_offer_id_position_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signatures
    ADD CONSTRAINT trust_signatures_offer_id_position_fkey FOREIGN KEY (offer_id, "position") REFERENCES public.trust_lease_offer_signers(offer_id, "position");


--
-- Name: trust_signatures trust_signatures_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signatures
    ADD CONSTRAINT trust_signatures_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_signatures trust_signatures_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signatures
    ADD CONSTRAINT trust_signatures_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_signing_events trust_signing_events_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_events
    ADD CONSTRAINT trust_signing_events_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_signing_events trust_signing_events_pmc_id_link_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_events
    ADD CONSTRAINT trust_signing_events_pmc_id_link_id_fkey FOREIGN KEY (pmc_id, link_id) REFERENCES public.trust_signing_links(pmc_id, id);


--
-- Name: trust_signing_links trust_signing_links_offer_id_position_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_offer_id_position_fkey FOREIGN KEY (offer_id, "position") REFERENCES public.trust_lease_offer_signers(offer_id, "position");


--
-- Name: trust_signing_links trust_signing_links_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_signing_links trust_signing_links_pmc_id_offer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_pmc_id_offer_id_fkey FOREIGN KEY (pmc_id, offer_id) REFERENCES public.trust_lease_offers(pmc_id, id);


--
-- Name: trust_signing_links trust_signing_links_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_signing_links
    ADD CONSTRAINT trust_signing_links_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_staff_custom_roles trust_staff_custom_roles_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_custom_roles
    ADD CONSTRAINT trust_staff_custom_roles_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_staff_custom_roles trust_staff_custom_roles_pmc_id_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_custom_roles
    ADD CONSTRAINT trust_staff_custom_roles_pmc_id_role_id_fkey FOREIGN KEY (pmc_id, role_id) REFERENCES public.trust_custom_roles(pmc_id, id);


--
-- Name: trust_staff_custom_roles trust_staff_custom_roles_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_custom_roles
    ADD CONSTRAINT trust_staff_custom_roles_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_staff trust_staff_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff
    ADD CONSTRAINT trust_staff_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_staff_roles trust_staff_roles_pmc_id_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_roles
    ADD CONSTRAINT trust_staff_roles_pmc_id_changed_by_fkey FOREIGN KEY (pmc_id, changed_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_staff_roles trust_staff_roles_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_roles
    ADD CONSTRAINT trust_staff_roles_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_staff_roles trust_staff_roles_pmc_id_staff_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_staff_roles
    ADD CONSTRAINT trust_staff_roles_pmc_id_staff_id_fkey FOREIGN KEY (pmc_id, staff_id) REFERENCES public.trust_staff(pmc_id, id);


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
-- Name: trust_unit_areas trust_unit_areas_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_unit_areas
    ADD CONSTRAINT trust_unit_areas_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_unit_areas trust_unit_areas_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_unit_areas
    ADD CONSTRAINT trust_unit_areas_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_unit_areas trust_unit_areas_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_unit_areas
    ADD CONSTRAINT trust_unit_areas_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


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
-- Name: trust_utility_bills trust_utility_bills_pmc_id_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_pmc_id_bill_id_fkey FOREIGN KEY (pmc_id, bill_id) REFERENCES public.trust_bills(pmc_id, id);


--
-- Name: trust_utility_bills trust_utility_bills_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_utility_bills trust_utility_bills_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_utility_bills trust_utility_bills_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_bills
    ADD CONSTRAINT trust_utility_bills_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_utility_plans trust_utility_plans_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_plans
    ADD CONSTRAINT trust_utility_plans_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_utility_plans trust_utility_plans_pmc_id_property_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_plans
    ADD CONSTRAINT trust_utility_plans_pmc_id_property_id_fkey FOREIGN KEY (pmc_id, property_id) REFERENCES public.trust_properties(pmc_id, id);


--
-- Name: trust_utility_plans trust_utility_plans_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_plans
    ADD CONSTRAINT trust_utility_plans_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_utility_shares trust_utility_shares_pmc_id_charge_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_shares
    ADD CONSTRAINT trust_utility_shares_pmc_id_charge_id_fkey FOREIGN KEY (pmc_id, charge_id) REFERENCES public.trust_charges(pmc_id, id);


--
-- Name: trust_utility_shares trust_utility_shares_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_shares
    ADD CONSTRAINT trust_utility_shares_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_utility_shares trust_utility_shares_pmc_id_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_shares
    ADD CONSTRAINT trust_utility_shares_pmc_id_lease_id_fkey FOREIGN KEY (pmc_id, lease_id) REFERENCES public.trust_leases(pmc_id, id);


--
-- Name: trust_utility_shares trust_utility_shares_pmc_id_utility_bill_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_utility_shares
    ADD CONSTRAINT trust_utility_shares_pmc_id_utility_bill_id_fkey FOREIGN KEY (pmc_id, utility_bill_id) REFERENCES public.trust_utility_bills(pmc_id, id);


--
-- Name: trust_vendor_insurance trust_vendor_insurance_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_insurance
    ADD CONSTRAINT trust_vendor_insurance_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_vendor_insurance trust_vendor_insurance_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_insurance
    ADD CONSTRAINT trust_vendor_insurance_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_vendor_insurance trust_vendor_insurance_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_insurance
    ADD CONSTRAINT trust_vendor_insurance_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_vendor_link_revocations trust_vendor_link_revocations_pmc_id_link_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_link_revocations
    ADD CONSTRAINT trust_vendor_link_revocations_pmc_id_link_id_fkey FOREIGN KEY (pmc_id, link_id) REFERENCES public.trust_vendor_links(pmc_id, id);


--
-- Name: trust_vendor_link_revocations trust_vendor_link_revocations_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_link_revocations
    ADD CONSTRAINT trust_vendor_link_revocations_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_vendor_links trust_vendor_links_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_links
    ADD CONSTRAINT trust_vendor_links_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_vendor_links trust_vendor_links_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_links
    ADD CONSTRAINT trust_vendor_links_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_vendor_links trust_vendor_links_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_links
    ADD CONSTRAINT trust_vendor_links_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_vendor_payments_carried_over trust_vendor_payments_carried_over_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_payments_carried_over
    ADD CONSTRAINT trust_vendor_payments_carried_over_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_vendor_payments_carried_over trust_vendor_payments_carried_over_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_payments_carried_over
    ADD CONSTRAINT trust_vendor_payments_carried_over_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_vendor_profiles trust_vendor_profiles_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_profiles
    ADD CONSTRAINT trust_vendor_profiles_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_vendor_profiles trust_vendor_profiles_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_profiles
    ADD CONSTRAINT trust_vendor_profiles_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_vendor_profiles trust_vendor_profiles_pmc_id_vendor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendor_profiles
    ADD CONSTRAINT trust_vendor_profiles_pmc_id_vendor_id_fkey FOREIGN KEY (pmc_id, vendor_id) REFERENCES public.trust_vendors(pmc_id, id);


--
-- Name: trust_vendors trust_vendors_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendors
    ADD CONSTRAINT trust_vendors_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_work_order_details trust_work_order_details_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_details
    ADD CONSTRAINT trust_work_order_details_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_work_order_details trust_work_order_details_pmc_id_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_details
    ADD CONSTRAINT trust_work_order_details_pmc_id_recorded_by_fkey FOREIGN KEY (pmc_id, recorded_by) REFERENCES public.trust_staff(pmc_id, id);


--
-- Name: trust_work_order_details trust_work_order_details_pmc_id_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_details
    ADD CONSTRAINT trust_work_order_details_pmc_id_work_order_id_fkey FOREIGN KEY (pmc_id, work_order_id) REFERENCES public.trust_work_orders(pmc_id, id);


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
-- Name: trust_work_order_steps trust_work_order_steps_pmc_id_vendor_link_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_order_steps
    ADD CONSTRAINT trust_work_order_steps_pmc_id_vendor_link_fkey FOREIGN KEY (pmc_id, vendor_link_id) REFERENCES public.trust_vendor_links(pmc_id, id) NOT VALID;


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
-- Name: trust_work_orders trust_work_orders_pmc_id_recurring_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pmc_id_recurring_fkey FOREIGN KEY (pmc_id, recurring_work_order_id) REFERENCES public.trust_recurring_work_orders(pmc_id, id) NOT VALID;


--
-- Name: trust_work_orders trust_work_orders_pmc_id_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_work_orders
    ADD CONSTRAINT trust_work_orders_pmc_id_unit_id_fkey FOREIGN KEY (pmc_id, unit_id) REFERENCES public.trust_units(pmc_id, id);


--
-- Name: trust_audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.trust_audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: trust_audit_log trust_audit_log_ai; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY trust_audit_log_ai ON public.trust_audit_log FOR SELECT TO trust_ai_agent USING (has_table_privilege((to_regclass(('public.'::text || quote_ident(table_name))))::oid, 'SELECT'::text));


--
-- Name: trust_audit_log trust_audit_log_app; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY trust_audit_log_app ON public.trust_audit_log FOR SELECT TO trust_app USING (true);


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
    ('20261010000024'),
    ('20261010000025'),
    ('20261010000026'),
    ('20261010000027'),
    ('20261011000028'),
    ('20261011000029'),
    ('20261011000030'),
    ('20261011000031'),
    ('20261012000032'),
    ('20261012000033'),
    ('20261012000034'),
    ('20261012000035'),
    ('20261012000036'),
    ('20261013000037'),
    ('20261013000038'),
    ('20261013000039'),
    ('20261013000040'),
    ('20261013000041'),
    ('20261013000042'),
    ('20261013000043'),
    ('20261013000044'),
    ('20261013000045'),
    ('20261013000046'),
    ('20261013000047'),
    ('20261013000048'),
    ('20261013000049'),
    ('20261013000050'),
    ('20261013000051'),
    ('20261015000052'),
    ('20261015000053'),
    ('20261015000054'),
    ('20261015000055'),
    ('20261015000056'),
    ('20261015000057'),
    ('20261015000058'),
    ('20261015000059'),
    ('20261015000060'),
    ('20261015000061'),
    ('20261015000062'),
    ('20261016000063'),
    ('20261016000064'),
    ('20261016000065'),
    ('20261016000066'),
    ('20261016000067'),
    ('20261016000068'),
    ('20261016000069'),
    ('20261016000070'),
    ('20261016000071'),
    ('20261016000072'),
    ('20261016000073'),
    ('20261016000074'),
    ('20261017000075'),
    ('20261017000076'),
    ('20261017000077'),
    ('20261018000078'),
    ('20261018000079'),
    ('20261018000080'),
    ('20261018000081'),
    ('20261018000082'),
    ('20261018000083'),
    ('20261018000084'),
    ('20261018000085'),
    ('20261018000086'),
    ('20261018000087'),
    ('20261018000088'),
    ('20261018000089'),
    ('20261018000090'),
    ('20261018000091'),
    ('20261018000092'),
    ('20261018000093'),
    ('20261019000094'),
    ('20261020000095'),
    ('20261020000096'),
    ('20261020000097'),
    ('20261020000098'),
    ('20261020000099');
