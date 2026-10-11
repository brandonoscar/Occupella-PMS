# The self-hosted build: what a PMC runs next to its own plain Postgres 16.
# One image: it applies the migrations (dbmate, the default command), runs the smoke check
# (selfhost/smoke.py), exports reports (python -m pms.exports), makes the first staff invite
# (python -m pms.admin) and serves the API on port 8000 (python -m pms.api).
FROM mirror.gcr.io/library/python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

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
COPY src src

USER pms
EXPOSE 8000
ENV DBMATE_MIGRATIONS_DIR=/app/db/migrations \
    DBMATE_NO_DUMP_SCHEMA=true \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src
CMD ["dbmate", "--wait", "up"]
