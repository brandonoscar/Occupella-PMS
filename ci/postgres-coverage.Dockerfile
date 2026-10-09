# Postgres 16 with plpgsql_check, for the coverage job only. The profiler has to be loaded at
# server start so every test connection adds to one shared count. The schema never depends on
# this extension; tests/conftest.py adds it to the throwaway test database.
#
# plpgsql_check is built from source at a pinned commit (tag v2.7.2), not taken from apt: its
# profiler output changed in 2.10 (no parent_note), and tools/sql_coverage.py counts branches
# from that column. tools/sql_coverage.py refuses any other extension version.
FROM postgres:16@sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d

ARG PLPGSQL_CHECK_TAG=v2.7.2
ARG PLPGSQL_CHECK_COMMIT=35de2769e3b4c26d4b324e48093c55afffec5ad0
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates git build-essential postgresql-server-dev-16 libicu-dev; \
    git clone --quiet --depth 1 --branch "$PLPGSQL_CHECK_TAG" \
        https://github.com/okbob/plpgsql_check /tmp/plpgsql_check; \
    test "$(git -C /tmp/plpgsql_check rev-parse HEAD)" = "$PLPGSQL_CHECK_COMMIT"; \
    make -C /tmp/plpgsql_check USE_PGXS=1 with_llvm=no; \
    make -C /tmp/plpgsql_check USE_PGXS=1 with_llvm=no install; \
    apt-get purge -y --auto-remove git build-essential postgresql-server-dev-16 libicu-dev; \
    rm -rf /tmp/plpgsql_check /var/lib/apt/lists/*

CMD ["postgres", "-c", "shared_preload_libraries=plpgsql_check"]
