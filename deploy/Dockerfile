FROM python:3.12-slim AS base

# --- Proxy plumbing (no-op when not behind a corporate proxy) -------------
# Predefined build args; passed in automatically by docker-compose.yml's
# `build.args` from the shell's HTTP_PROXY / HTTPS_PROXY / NO_PROXY.
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# apt over HTTP first (no TLS) so we can install ca-certificates before
# any HTTPS reach (PyPI, ghcr).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Trust corporate / MITM CA bundles -----------------------------------
# Drop your *.crt files in deploy/extra-ca/ before building. The directory
# always exists (via .gitkeep), so this COPY succeeds even without certs;
# update-ca-certificates is a no-op when there are no new files.
COPY deploy/extra-ca/ /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# uv: fast python package manager
COPY --from=ghcr.io/astral-sh/uv:0.5.6 /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first for layer cache
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Source (app/ includes app/static/admin.html)
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini ./

EXPOSE 8000
