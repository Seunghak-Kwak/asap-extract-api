#!/usr/bin/env bash
# Load bundled images and bring the stack up — runs in the extracted bundle dir.
#
#   tar -xzf asap-extract-api-bundle-*.tar.gz
#   cd asap-extract-api
#   ./scripts/unbundle.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f images.tar ]]; then
    echo "images.tar not found — are you in the extracted bundle directory?" >&2
    exit 1
fi

echo "==> loading docker images"
docker load -i images.tar
rm images.tar

if [[ ! -f .env ]]; then
    echo "==> .env not present, copying from .env.example"
    cp .env.example .env
    echo
    echo ".env was just created — edit it before starting:"
    echo "  SOURCE_HOST / SOURCE_PORT / SOURCE_USER / SOURCE_PASSWORD / SOURCE_DB"
    echo "  BOOTSTRAP_API_KEY        # consider removing in prod"
    echo
    echo "Then start the stack with:"
    echo "  docker compose -f deploy/docker-compose.yml up -d --no-build"
    exit 0
fi

echo "==> .env present, bringing stack up"
docker compose -f deploy/docker-compose.yml up -d --no-build

echo
echo "==> status"
docker compose -f deploy/docker-compose.yml ps
echo
echo "Logs:    docker compose -f deploy/docker-compose.yml logs -f app worker"
echo "Health:  curl http://localhost:8080/healthz"
