# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A high-volume data extraction API. Clients submit an extraction request, the server runs the query asynchronously against a remote SingleStore warehouse, writes the result to disk as a file, and returns a download URL backed by Nginx.

The whole system serves **one hard requirement**: a single extract may contain millions of rows and must not fully load into memory at any point — not in the worker, not in the response, not in transit. Every architectural choice (keyset pagination, file-on-disk results, Nginx delivery via `X-Accel-Redirect`) traces back to this.

## Code philosophy: keep it boring

This service is operationally ambitious (millions of rows, async jobs, two heterogeneous databases) but the code must be the opposite — small, obvious, and easy to delete. "Production-grade" here means **fewer moving parts that work**, not more parts arranged cleverly.

Concrete rules when writing or reviewing code in this repo:

- **No premature abstraction.** A direct function call beats an interface with one implementation. We add a seam only when the second implementation actually exists.
- **No layers for the sake of layers.** No service/repository/manager triplets wrapping a single SQL query. The router calls a function; the function does the thing.
- **No dependency-injection framework.** FastAPI's `Depends` is sufficient. Don't introduce `dependency-injector`, `kink`, or a custom container.
- **No generic "framework code" inside this repo.** If a helper is generic enough to live in a separate library, it isn't needed yet.
- **One module, one responsibility, no clever re-exports.** Importing from `app.extract.cursor` is fine; importing from `app` because everything is re-exported is not.
- **Async all the way.** No sync DB calls hiding inside async handlers. No `asyncio.run` inside request paths.
- **Fail loudly at the boundary, trust internally.** Validate at the API edge (pydantic) and at the SingleStore query builder; don't re-validate the same shape five layers deep.

If a PR adds an abstraction, it must justify the abstraction with a *second* concrete caller in the same PR. Otherwise inline it.

## Stack

- **Runtime**: Python 3.12+, FastAPI (ASGI, async)
- **Job queue**: Arq (asyncio-native) with Redis broker — same event-loop model as the API, so the worker is just "the same code, different entrypoint"
- **Source DB**: **Remote SingleStore** (MySQL wire protocol, read-only). Accessed with `aiomysql`. **Never run migrations against this.**
- **App metadata DB**: **PostgreSQL** (jobs, api_keys, audit, idempotency). Local to the deployment, owned by this service.
- **DB access**: SQLAlchemy 2.0 async + `asyncpg` for Postgres. For SingleStore, `aiomysql` with **raw cursors and keyset pagination** — see "Extract streaming" below. No ORM on the extract path.
- **Migrations**: Alembic against the Postgres metadata schema only.
- **Result storage**: Host filesystem (`/var/lib/extracts/<job_id>/...`), bind-mounted into the app/worker/nginx containers. Served by Nginx via `X-Accel-Redirect`.
- **Auth**: API key. Hashed at rest with Argon2id. Sent as `Authorization: Bearer <key>`. Keys carry a public prefix (`ek_live_…`) so logs are useful without leaking the secret.
- **Observability**: `structlog` JSON logs with `request_id` / `job_id` correlation; Prometheus metrics on `/metrics`.
- **Packaging**: `uv` (lockfile `uv.lock`).
- **Lint/format**: `ruff`. **Type check**: `mypy --strict` on `app/`.
- **Tests**: `pytest`, `pytest-asyncio`, `httpx.AsyncClient`. Integration tests hit the real Postgres + Redis from Compose. For SingleStore, use a recorded-result fixture at the connection layer — never mock cursors in extract tests.

## Architecture

### Two asynchronies, do not confuse them

The word "async" means two different things here. Treat them separately:

1. **HTTP-level async (client ↔ API).** `POST /v1/extracts` returns immediately with `{job_id, status: "queued"}`. The HTTP connection closes. The client polls `GET /v1/extracts/{id}` (or receives a webhook) and then downloads via `GET /v1/extracts/{id}/download`. **The client never holds a connection open while data is being extracted.**

2. **Worker-level streaming (worker ↔ SingleStore ↔ disk).** Inside the worker, the result is paginated out of SingleStore in small batches (default 10k rows), each batch appended to a partial file on disk. Memory holds at most one batch at a time; the disk file grows monotonically.

These are independent. The HTTP layer is async to free the client; the worker layer streams to bound memory.

### Two-tier data plane

```
  ┌─────────────────┐    read-only          ┌──────────────────────┐
  │   FastAPI app   │ ────────────────────► │  Remote SingleStore  │
  │   Arq worker    │   keyset pagination   │  (source data)       │
  └────────┬────────┘                       └──────────────────────┘
           │
           │ jobs, api_keys, idempotency, audit
           ▼
  ┌─────────────────┐
  │   Postgres      │  (Compose service, owned by us)
  └─────────────────┘
```

Source DB and metadata DB are **never** in the same connection pool, the same SQLAlchemy session, or the same transaction. They are different systems with different reliability assumptions (remote/lossy/read-only vs. local/owned/transactional).

### Request → result lifecycle

```
POST /v1/extracts          ──► validate, write job row in PG (status=queued), enqueue Arq task,
                               return {job_id} immediately
GET  /v1/extracts/{id}     ──► poll status (queued|running|succeeded|failed|expired|cancelled)
GET  /v1/extracts/{id}/download
                           ──► FastAPI authenticates, then X-Accel-Redirect to Nginx
                               (Nginx serves the file; Python never reads the bytes)
DELETE /v1/extracts/{id}   ──► sets cancel_requested = true; worker exits at next batch boundary
```

### Worker flow (the part that has to be right)

1. Claim a job from Postgres with `SELECT ... FOR UPDATE SKIP LOCKED`, flip status to `running`.
2. Open an `aiomysql` connection to SingleStore for the extract query.
3. **Stream rows via keyset pagination** (`WHERE (sort_key) > :cursor ORDER BY sort_key LIMIT :batch`). Default batch 10k.
4. Append each batch to `<job_id>.part` on the shared volume. Never accumulate the full result in memory.
5. On success: `fsync` + atomic rename to `<job_id>.<ext>`, set `status=succeeded`, record `row_count`, `bytes`, `sha256`.
6. On failure: keep `.part` for forensics, mark `status=failed` with error class + message. A periodic sweeper deletes orphaned `.part` files past the retention window.
7. **Cancellation:** check `cancel_requested` on the job row between batches. If set, close the cursor, delete the partial file, mark `status=cancelled`. A job the user gave up on must not keep burning a SingleStore connection.

### Extract streaming: keyset, not `SSCursor`

SingleStore speaks the MySQL wire protocol but is a distributed engine. `aiomysql`'s `SSCursor` (server-side cursor) was designed against single-node MySQL; its streaming semantics are not guaranteed across SingleStore aggregator/leaf boundaries. **Use keyset pagination**:

- Every extract query must have a stable, indexed sort key (often `(partition_col, id)`).
- The worker keeps only the last-seen key, not a server-side cursor. Transient disconnects are cheap to retry; SingleStore stays stateless on our behalf.
- `LIMIT/OFFSET` is forbidden in extract loops: cost grows linearly with offset on a distributed scan.

### Why each choice (one sentence each)

- **Arq + Redis**: same asyncio model as FastAPI, so the worker is just "the same code, different entrypoint."
- **Postgres for metadata**: jobs/auth/audit need transactions, constraints, and migrations — Postgres is the right hammer.
- **SingleStore source**: out of our control; we treat it as a remote, read-only system and never store state there.
- **File on disk + Nginx**: zero-copy delivery; streaming bytes through Python would tie up an event loop for the whole download.
- **API key, not OAuth**: clients are systems, not humans.
- **Docker Compose**: single-server deployment but reproducible local ↔ server environments. The result directory uses a **bind mount** (not a named volume) so filesystem semantics stay native.

## Layout (target)

Keep this flat. Resist nesting unless three modules already live at a level.

```
app/
  api/            # FastAPI routers; thin — validation + auth + enqueue/lookup only
  workers/        # Arq task definitions; the extract pipeline lives here
  extract/        # Pure extract logic: keyset paginator, batching, writers (csv/parquet)
  storage/        # On-disk layout, atomic rename, retention sweeper, X-Accel path mapping
  auth/           # API key issue/verify (Argon2id), rate limiting
  db/
    meta/         # SQLAlchemy models + Alembic env (Postgres)
    source/       # aiomysql connection factory + query helpers (SingleStore)
  observability/  # structlog config, Prometheus metrics, request_id middleware
  config.py       # pydantic-settings; all env vars declared here
alembic/          # Postgres metadata migrations ONLY
tests/
  unit/
  integration/    # requires `docker compose up` (postgres + redis + nginx)
deploy/
  docker-compose.yml
  nginx/          # site config with `internal` location for X-Accel-Redirect
  Dockerfile
```

## Common commands

```bash
# Bootstrap
uv sync
cp .env.example .env                     # fill SINGLESTORE_* and secrets
docker compose -f deploy/docker-compose.yml up -d postgres redis nginx

# Run (two processes — both needed end-to-end)
uv run uvicorn app.main:app --reload
uv run arq app.workers.WorkerSettings

# DB (Postgres metadata only — never against SingleStore)
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "<msg>"

# Quality gates
uv run ruff format .
uv run ruff check . --fix
uv run mypy app
uv run pytest                            # all tests
uv run pytest tests/unit -q              # unit only (fast)
uv run pytest tests/integration -q       # needs compose up
uv run pytest tests/integration/test_extract_streaming.py::test_million_rows -s

# Full local stack (app + worker + deps)
docker compose -f deploy/docker-compose.yml up --build
```

## Non-negotiables

These are the rules that, if broken, undo the design. Treat them as compile-time errors of judgment.

- **Never call `fetchall`, `list(cursor)`, or any equivalent on SingleStore queries.** If you think you need all rows in memory, you've misunderstood the problem.
- **Never build the response body in memory.** Results go to disk first, then to the client via Nginx (`X-Accel-Redirect`).
- **No `LIMIT/OFFSET` in extract loops on SingleStore.** Keyset pagination over an indexed sort key, always.
- **Never share a connection pool, session, or transaction between Postgres and SingleStore.** They are separate systems.
- **Every batch boundary checks `cancel_requested`.** No exceptions.
- **Atomic file moves only.** Partial files must never become "succeeded" downloads.
- **No DB mocks in worker/extract integration tests.** Postgres comes from Compose; SingleStore uses a recorded-fixture connection, never a mocked cursor.
- **No new abstraction without a second concrete caller in the same change.** Inline first, extract later.
