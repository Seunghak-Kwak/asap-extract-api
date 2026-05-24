#!/usr/bin/env bash
# Build a self-contained tarball for air-gapped deployment.
#
#   ./scripts/bundle.sh              # prod bundle (no mysql)
#   ./scripts/bundle.sh --with-mysql # dev parity bundle (includes mysql:8.0)
#
# Output: dist/asap-extract-api-bundle-<UTC datetime>.tar.gz

set -euo pipefail

WITH_MYSQL=false
PLATFORM="${PLATFORM:-}"  # set e.g. PLATFORM=linux/amd64 for cross-arch builds

while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-mysql) WITH_MYSQL=true; shift ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

COMPOSE="docker compose -f deploy/docker-compose.yml"

echo "==> building app + worker images"
if [[ -n "$PLATFORM" ]]; then
    # buildx for cross-platform; loaded into the local daemon
    docker buildx build --platform "$PLATFORM" --load \
        -f deploy/Dockerfile -t extract-api-app:latest .
    docker tag extract-api-app:latest extract-api-worker:latest
else
    $COMPOSE build app worker
fi

BASE_IMAGES=(postgres:16-alpine redis:7-alpine nginx:1.27-alpine)
$WITH_MYSQL && BASE_IMAGES+=(mysql:8.0)

echo "==> pulling base images"
for img in "${BASE_IMAGES[@]}"; do
    if [[ -n "$PLATFORM" ]]; then
        docker pull --platform "$PLATFORM" "$img"
    else
        docker pull "$img"
    fi
done

mkdir -p dist
STAGE="dist/asap-extract-api"
rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "==> saving images"
docker save -o "$STAGE/images.tar" \
    extract-api-app:latest \
    extract-api-worker:latest \
    "${BASE_IMAGES[@]}"

echo "==> staging source"
# Use git ls-files so we ship exactly what's tracked + the new images.tar.
# Excludes .git, .venv, data/, dist/, .env automatically (not tracked).
while IFS= read -r -d '' f; do
    mkdir -p "$STAGE/$(dirname "$f")"
    cp "$f" "$STAGE/$f"
done < <(git ls-files -z)

DATE=$(date -u +%Y%m%dT%H%M%SZ)
OUT="dist/asap-extract-api-bundle-${DATE}.tar.gz"

echo "==> taring → $OUT"
tar -C dist -czf "$OUT" asap-extract-api

rm -rf "$STAGE"
SIZE=$(du -h "$OUT" | awk '{print $1}')

echo
echo "Done.  $OUT  ($SIZE)"
echo
echo "On the air-gapped host:"
echo "  tar -xzf $(basename "$OUT")"
echo "  cd asap-extract-api"
echo "  ./scripts/unbundle.sh"
