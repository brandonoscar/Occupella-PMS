# Postgres 16 with plpgsql_check, for the coverage and mutation jobs only. The profiler has to be
# loaded at server start so every test connection adds to one shared count. The schema never
# depends on this extension; tests/conftest.py adds it to the throwaway test database.
FROM postgres:16@sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-plpgsql-check \
    && rm -rf /var/lib/apt/lists/*
CMD ["postgres", "-c", "shared_preload_libraries=plpgsql_check"]
