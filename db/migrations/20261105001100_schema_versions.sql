-- migrate:up
-- The migrations a database holds, for the app to compare with its own (docs/edge-cases.md
-- H-041, src/pms/schema.py).
--
-- Nothing compared schema_migrations, dbmate's record of the migrations applied, to the code: an
-- older PMS started on a database a newer one migrated (an older image tag, or a newer backup
-- restored under one) and posted money on a schema it was never tested on. The app's role reads
-- no table it wasn't granted, and schema_migrations is dbmate's, so the versions come through
-- this function, which runs as the owner and returns them alone.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_schema_versions() RETURNS SETOF text
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
    SELECT m.version::text FROM schema_migrations AS m ORDER BY m.version
$$;

REVOKE ALL ON FUNCTION trust_schema_versions() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_schema_versions() TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_schema_versions();
