# The self-hosted build: what a PMC runs next to its own plain Postgres 16.
# Phase 0 has no app server yet, so the image applies the migrations (dbmate) and runs the
# smoke check (selfhost/smoke.py). The app joins this image when it exists.
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

# dbmate v2.36.0, checked against the sha256 recorded when it was pinned (dbmate publishes none).
ADD --checksum=sha256:47e284b3d8cbad1ba5f090495aa05afd1bbd5f35e2ed5577aad06da74ce780ce \
    https://github.com/amacneil/dbmate/releases/download/v2.36.0/dbmate-linux-amd64 \
    /usr/local/bin/dbmate
RUN chmod 0755 /usr/local/bin/dbmate \
    && useradd --create-home --uid 10001 pms

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY db/migrations db/migrations
COPY selfhost selfhost

USER pms
ENV DBMATE_MIGRATIONS_DIR=/app/db/migrations \
    DBMATE_NO_DUMP_SCHEMA=true \
    PYTHONDONTWRITEBYTECODE=1
CMD ["dbmate", "--wait", "up"]
