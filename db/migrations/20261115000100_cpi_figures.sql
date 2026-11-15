-- migrate:up
-- The Consumer Price Index figures a rent cap counts (docs/law.md, "A year's rent increase
-- capped by inflation"; src/pms/cpi.py, src/pms/rent_cap.py): where California, Oregon or
-- Washington caps a year's increase by inflation, the warning staff see before a rent change or a
-- renewal says the most it may be, worked out from these.
--
--   trust_cpi_fetches  each time the nightly run kept a series' figures in a company's books: where
--                      from ('bls', the Bureau of Labor Statistics' Public Data API, or 'fake' for
--                      development and CI), the series, and the months it answered for.
--   trust_cpi_figures  each month's figure of a series that fetch found new or changed, to three
--                      places as BLS publishes it. A series' month reads as its latest fetch over
--                      every month a cap compares saw it, so a series rebased between two fetches
--                      never mixes bases.
--
-- Public figures, not a person's or the company's own: each company keeps those it was shown, the
-- AI role reads them, and the nightly run names nobody. Both append-only.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_cpi_fetches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    source text NOT NULL CHECK (source IN ('bls', 'fake')),
    -- A BLS series id, as "CUURS49ASA0".
    series_id text NOT NULL CHECK (series_id ~ '^[A-Z0-9]{8,20}$'),
    first_month date NOT NULL CHECK (extract(day FROM first_month) = 1),
    last_month date NOT NULL CHECK (extract(day FROM last_month) = 1),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (last_month >= first_month),
    UNIQUE (pmc_id, id, series_id)
);
CREATE INDEX trust_cpi_fetches_series
ON trust_cpi_fetches (pmc_id, series_id, created_at);

CREATE TABLE trust_cpi_figures (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL,
    fetch_id uuid NOT NULL,
    series_id text NOT NULL,
    month date NOT NULL CHECK (extract(day FROM month) = 1),
    value numeric NOT NULL CHECK (value > 0 AND value = round(value, 3)),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (fetch_id, month),
    FOREIGN KEY (pmc_id, fetch_id, series_id)
        REFERENCES trust_cpi_fetches (pmc_id, id, series_id)
);
CREATE INDEX trust_cpi_figures_series
ON trust_cpi_figures (pmc_id, series_id, month);
CREATE INDEX trust_cpi_figures_fetch
ON trust_cpi_figures (pmc_id, fetch_id, series_id);

-- A figure is of a month its fetch answered for.
CREATE FUNCTION trust_check_cpi_figure()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_cpi_fetches AS f
        WHERE f.id = NEW.fetch_id AND NEW.month BETWEEN f.first_month AND f.last_month
    ) THEN
        RAISE EXCEPTION 'trust: a CPI figure is of a month its fetch answered for'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_check_cpi_figure BEFORE INSERT ON trust_cpi_figures
FOR EACH ROW EXECUTE FUNCTION trust_check_cpi_figure();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_cpi_fetches
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_cpi_figures
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_cpi_fetches
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_cpi_figures
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_cpi_fetches FROM PUBLIC;
REVOKE ALL ON trust_cpi_figures FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_cpi_figure() FROM PUBLIC;
GRANT SELECT, INSERT ON trust_cpi_fetches TO trust_app;
GRANT SELECT, INSERT ON trust_cpi_figures TO trust_app;
GRANT SELECT ON trust_cpi_fetches TO trust_ai_agent;
GRANT SELECT ON trust_cpi_figures TO trust_ai_agent;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Public figures any fetch brings back; nothing else reads them.
-- squawk-ignore ban-drop-table
DROP TABLE trust_cpi_figures;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_cpi_figure();
-- squawk-ignore ban-drop-table
DROP TABLE trust_cpi_fetches;
