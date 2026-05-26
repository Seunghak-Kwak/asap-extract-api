# Extra CA bundles

Drop your corporate CA certificate(s) here as `*.crt` files **before**
running `docker compose build`. Files are picked up by
`update-ca-certificates` inside the build image and become trusted for
all subsequent HTTPS traffic (apt, PyPI, ghcr, etc.).

The directory is empty by default — the `.gitkeep` exists so the
`COPY` in `Dockerfile` succeeds in environments without proxy MITM.

Example:
```bash
cp /path/to/corp-root.crt deploy/extra-ca/
docker compose -f deploy/docker-compose.yml build app worker
```

Files in this directory (except `.gitkeep` and this README) are
git-ignored to keep certificates out of the repo.
