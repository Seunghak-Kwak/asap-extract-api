FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv: fast python package manager
COPY --from=ghcr.io/astral-sh/uv:0.5.6 /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first for layer cache
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Source
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini ./

EXPOSE 8000
