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
-- Name: trust_approve_reconciliation(uuid, uuid, timestamp with time zone, timestamp with time zone, numeric, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_approve_reconciliation(p_pmc_id uuid, p_bank_account_id uuid, p_period_start timestamp with time zone, p_period_end timestamp with time zone, p_statement_balance numeric, p_prepared_by text, p_approved_by text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_closed_through timestamptz;
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
    SELECT b.closed_through INTO v_closed_through
    FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_period_start <> v_closed_through THEN
        RAISE EXCEPTION 'trust: the next period of trust bank account % starts at %',
            p_bank_account_id, v_closed_through
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Periods follow one another: no gap, no overlap.';
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
-- Name: trust_refuse_posting_into_closed_period(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trust_refuse_posting_into_closed_period() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_bank uuid;
    v_closed_through timestamptz;
BEGIN
    -- FOR SHARE waits for an approval in progress on either account and then reads the date
    -- it set. At REPEATABLE READ or SERIALIZABLE, an approval that committed after this
    -- transaction's snapshot makes the lock fail with a serialization error instead.
    FOR v_bank, v_closed_through IN
        SELECT b.id, b.closed_through
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
    END LOOP;
    RETURN NEW;
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
-- Name: trust_bank_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trust_bank_accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pmc_id uuid NOT NULL,
    kind text NOT NULL,
    display_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_through timestamp with time zone,
    CONSTRAINT trust_bank_accounts_display_name_check CHECK ((display_name <> ''::text)),
    CONSTRAINT trust_bank_accounts_kind_check CHECK ((kind = ANY (ARRAY['operating'::text, 'security_deposit'::text])))
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
-- Name: trust_idempotency_keys trust_idempotency_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_idempotency_keys
    ADD CONSTRAINT trust_idempotency_keys_pkey PRIMARY KEY (pmc_id, idempotency_key);


--
-- Name: trust_ledger_accounts trust_ledger_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_ledger_accounts
    ADD CONSTRAINT trust_ledger_accounts_pkey PRIMARY KEY (ledger_account_id);


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
-- Name: trust_reconciliations_bank_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX trust_reconciliations_bank_account_id ON public.trust_reconciliations USING btree (bank_account_id, period_end);


--
-- Name: pgledger_entries trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.pgledger_entries FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: pgledger_transfers trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.pgledger_transfers FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_idempotency_keys trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_idempotency_keys FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_ledger_accounts trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_ledger_accounts FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: trust_reconciliations trust_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_append_only BEFORE DELETE OR UPDATE OR TRUNCATE ON public.trust_reconciliations FOR EACH STATEMENT EXECUTE FUNCTION public.trust_refuse_ledger_rewrite();


--
-- Name: pgledger_transfers trust_bank_tie_out; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER trust_bank_tie_out AFTER INSERT ON public.pgledger_transfers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.trust_check_bank_tie_out();


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
-- Name: pgledger_transfers trust_transfer_scope; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trust_transfer_scope BEFORE INSERT ON public.pgledger_transfers FOR EACH ROW EXECUTE FUNCTION public.trust_check_transfer_scope();


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
-- Name: trust_bank_accounts trust_bank_accounts_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_bank_accounts
    ADD CONSTRAINT trust_bank_accounts_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


--
-- Name: trust_idempotency_keys trust_idempotency_keys_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_idempotency_keys
    ADD CONSTRAINT trust_idempotency_keys_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


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
-- Name: trust_owners trust_owners_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_owners
    ADD CONSTRAINT trust_owners_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


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
-- Name: trust_vendors trust_vendors_pmc_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trust_vendors
    ADD CONSTRAINT trust_vendors_pmc_id_fkey FOREIGN KEY (pmc_id) REFERENCES public.trust_pmcs(pmc_id);


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
    ('20261009000007');
