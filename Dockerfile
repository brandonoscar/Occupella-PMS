# The self-hosted build: what a PMC runs next to its own plain Postgres 16.
# One image: it applies the migrations (dbmate, the default command), runs the smoke check
# (selfhost/smoke.py), exports reports (python -m pms.exports), makes the first staff invite
# (python -m pms.admin) and serves the API and the portal on port 8000 (python -m pms.api).

# The portal (frontend/), built in the pinned Node image; only its static files go on.
FROM mirror.gcr.io/library/node:26.11.1-slim@sha256:193fe51b64e77981119c98c2002c9e32a70e2f006fb4d25068ce0558998917f0 AS portal
WORKDIR /portal
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# dbmate v2.37.0, built from its source with the Go it asks for (1.27.2, pinned), every module
# checked against Go's checksum database. The v2.36.0 release binary carried Go 1.27.1 and an
# x/net and x/crypto with known critical and high vulnerabilities (Grype, selfhost.yml). Without
# cgo: the image uses only dbmate's Postgres driver, which is pure Go. It is built in a module of
# its own that also asks for klauspost/compress 1.18.7: v2.37.0 pins 1.18.5, which has a high
# vulnerability (GHSA-259r-337f-4rfw), and Go picks the higher of the two.
FROM mirror.gcr.io/library/golang:1.27.2-bookworm@sha256:5cf287a799e6b94384bad13d16b14904c531f51ba65792237e122ce42b392f61 AS dbmate
ENV CGO_ENABLED=0 GOFLAGS=-trimpath GOTOOLCHAIN=local
WORKDIR /build
RUN go mod init build \
    && go get github.com/amacneil/dbmate/v2@v2.37.0 github.com/klauspost/compress@v1.18.7 \
    && go build -o /go/bin/dbmate github.com/amacneil/dbmate/v2

FROM mirror.gcr.io/library/python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60

COPY --from=dbmate /go/bin/dbmate /usr/local/bin/dbmate
RUN useradd --create-home --uid 10001 pms

WORKDIR /app
COPY requirements.txt .
# setuptools and wheel come with the base image and only install packages; the app runs without
# them (the smoke check passes), and they carried a vendored jaraco.context and a wheel with known
# high vulnerabilities (Grype).
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall --yes setuptools wheel
COPY db/migrations db/migrations
COPY selfhost selfhost
COPY src src
COPY --from=portal /portal/dist frontend/dist

USER pms
EXPOSE 8000
ENV DBMATE_MIGRATIONS_DIR=/app/db/migrations \
    DBMATE_NO_DUMP_SCHEMA=true \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PMS_STATIC_DIR=/app/frontend/dist
CMD ["dbmate", "--wait", "up"]
