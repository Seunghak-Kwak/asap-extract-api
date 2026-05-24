"""End-to-end integration test against the running Docker Compose stack.

Run with:
    docker compose -f deploy/docker-compose.yml up -d --build
    BOOTSTRAP_API_KEY=ek_live_devtest_devtestdevtestdevtestdevtestde \
        API_BASE=http://localhost:8080 uv run pytest tests/integration -s
"""

import os
import time

import httpx
import pytest

API_BASE = os.environ.get("API_BASE", "http://localhost:8080")
API_KEY = os.environ.get(
    "BOOTSTRAP_API_KEY", "ek_live_devtest_devtestdevtestdevtestdevtestde"
)
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def _wait_until_succeeded(client: httpx.Client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/v1/extracts/{job_id}", headers=AUTH)
        r.raise_for_status()
        last = r.json()
        if last["status"] in ("succeeded", "failed", "cancelled"):
            return last
        time.sleep(0.5)
    pytest.fail(f"job {job_id} did not finish in {timeout}s; last={last}")


@pytest.mark.integration
def test_extract_events_end_to_end() -> None:
    with httpx.Client(base_url=API_BASE, timeout=30.0) as client:
        # 1. Submit
        r = client.post(
            "/v1/extracts",
            headers=AUTH,
            json={
                "dataset": "events",
                "filters": {
                    "from": "2026-01-01T00:00:00Z",
                    "to": "2026-02-01T00:00:00Z",
                },
            },
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        # 2. Poll
        final = _wait_until_succeeded(client, job_id)
        assert final["status"] == "succeeded"
        assert final["row_count"] == 100000  # seeded
        assert final["bytes"] > 0
        assert final["file_sha256"]

        # 3. Download (nginx serves via X-Accel-Redirect)
        r = client.get(f"/v1/extracts/{job_id}/download", headers=AUTH)
        assert r.status_code == 200
        text = r.text
        lines = text.splitlines()
        assert lines[0] == "id,occurred_at,category,user_id,payload"
        assert len(lines) - 1 == 100000


@pytest.mark.integration
def test_filter_validation_rejected_at_api() -> None:
    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        r = client.post(
            "/v1/extracts",
            headers=AUTH,
            json={"dataset": "events", "filters": {"from": "2026-01-01T00:00:00Z"}},
        )
        assert r.status_code == 422
        assert "to" in r.text


@pytest.mark.integration
def test_unknown_field_rejected() -> None:
    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        r = client.post(
            "/v1/extracts",
            headers=AUTH,
            json={
                "dataset": "events",
                "filters": {
                    "from": "2026-01-01T00:00:00Z",
                    "to": "2026-01-02T00:00:00Z",
                    "ssn": "123",  # not whitelisted
                },
            },
        )
        assert r.status_code == 422


@pytest.mark.integration
def test_unauthorized_without_key() -> None:
    with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
        r = client.post(
            "/v1/extracts",
            json={
                "dataset": "events",
                "filters": {
                    "from": "2026-01-01T00:00:00Z",
                    "to": "2026-01-02T00:00:00Z",
                },
            },
        )
        assert r.status_code == 401
