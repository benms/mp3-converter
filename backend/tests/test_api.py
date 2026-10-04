import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import ConversionError, Stage

URL = "https://youtu.be/BaW_jenozKc"


class FixtureConverter:
    def __init__(self, delay=0, error=None):
        self.delay = delay
        self.error = error

    async def convert(self, url, bitrate, directory, update):
        update(stage=Stage.DOWNLOADING, title="A test / song", duration=2, progress=45)
        (directory / "source.part").write_bytes(b"partial")
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        (directory / "source.part").unlink()
        result = directory / "audio.mp3"
        result.write_bytes(b"ID3fixture")
        return result


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, allowed_origins=["https://sounddrop.vercel.app"])


@pytest.fixture(autouse=True)
def available_tools(monkeypatch):
    monkeypatch.setattr("app.main.tools_ready", lambda: {"ffmpeg": True, "deno": True})


def wait_for(client, job_id, expected):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["stage"] == expected:
            return status
        time.sleep(0.02)
    pytest.fail(f"Expected stage {expected}, got {status}")


def test_conversion_download_and_cors(settings):
    app = create_app(settings, FixtureConverter())
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["limits"] == {"max_duration_seconds": 3600, "file_ttl_seconds": 3600}
        origin = {"Origin": "https://sounddrop.vercel.app"}
        response = client.post("/api/jobs", json={"url": URL, "bitrate": 192}, headers=origin)
        assert response.status_code == 202
        assert response.headers["access-control-allow-origin"] == origin["Origin"]
        job_id = response.json()["id"]
        assert len(job_id) == 43
        status = wait_for(client, job_id, "ready")
        assert status["title"] == "A test / song"
        assert status["expires_at"]
        assert "directory" not in status and "url" not in status
        download = client.get(f"/api/jobs/{job_id}/download", headers=origin)
        assert download.content == b"ID3fixture"
        assert download.headers["content-type"] == "audio/mpeg"
        assert download.headers["cache-control"] == "no-store"
        assert (
            download.headers["content-disposition"]
            == "attachment; filename*=utf-8''A%20test%20-%20song.mp3"
        )
        denied = client.options(
            "/api/jobs",
            headers={
                "Origin": "https://someone-else.vercel.app",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers
        assert client.get("/api/jobs/not-a-job").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"url": "https://example.com/video"},
        {"url": URL, "bitrate": 64},
        {"url": URL, "unexpected": True},
        {"url": "file:///etc/passwd"},
    ],
)
def test_invalid_requests(settings, payload):
    with TestClient(create_app(settings, FixtureConverter())) as client:
        assert client.post("/api/jobs", json=payload).status_code == 422


def test_rate_limit_uses_client_address(settings):
    with TestClient(create_app(replace(settings, hourly_limit=1), FixtureConverter())) as client:
        assert client.post("/api/jobs", json={"url": URL}).status_code == 202
        response = client.post(
            "/api/jobs", json={"url": URL}, headers={"X-Forwarded-For": "1.2.3.4"}
        )
        assert response.status_code == 429
        assert response.headers["retry-after"] == "3600"


def test_bounded_queue_and_unfinished_download(settings):
    app = create_app(replace(settings, max_queue=1), FixtureConverter(delay=30))
    with TestClient(app) as client:
        first = client.post("/api/jobs", json={"url": URL}).json()
        wait_for(client, first["id"], "downloading")
        assert client.get(f"/api/jobs/{first['id']}/download").status_code == 409
        second = client.post("/api/jobs", json={"url": URL})
        assert second.status_code == 202
        assert second.json()["queue_position"] == 1
        third = client.post("/api/jobs", json={"url": URL})
        assert third.status_code == 503
        assert third.json()["detail"]["code"] == "queue_full"


@pytest.mark.parametrize(
    "error",
    [
        ConversionError("upstream_restricted", "Upstream unavailable"),
        RuntimeError("sensitive detail"),
    ],
)
def test_failure_removes_partial_files(settings, error):
    app = create_app(settings, FixtureConverter(error=error))
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", json={"url": URL}).json()["id"]
        status = wait_for(client, job_id, "failed")
        assert status["error"]
        assert "sensitive detail" not in str(status)
        assert not (settings.data_dir / job_id).exists()


def test_processing_timeout(settings):
    app = create_app(replace(settings, timeout=0.05), FixtureConverter(delay=30))
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", json={"url": URL}).json()["id"]
        assert wait_for(client, job_id, "failed")["error"]["code"] == "timeout"
        assert not (settings.data_dir / job_id).exists()


def test_expiry_preserves_an_active_download_then_cleans_up(settings):
    app = create_app(settings, FixtureConverter())
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", json={"url": URL}).json()["id"]
        wait_for(client, job_id, "ready")
        job = app.state.manager.jobs[job_id]
        job.update(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        job.readers = 1
        app.state.manager.cleanup()
        assert job.file.exists()
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        job.readers = 0
        app.state.manager.cleanup()
        assert not job.directory.exists()
        assert job_id not in app.state.manager.jobs


def test_restart_loses_jobs_and_removes_old_files(settings):
    with TestClient(create_app(settings, FixtureConverter())) as client:
        job_id = client.post("/api/jobs", json={"url": URL}).json()["id"]
        wait_for(client, job_id, "ready")
    assert (settings.data_dir / job_id).exists()
    with TestClient(create_app(settings, FixtureConverter())) as client:
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        assert not (settings.data_dir / job_id).exists()


def test_storage_admission_and_tools_unavailable(settings, monkeypatch):
    with TestClient(
        create_app(replace(settings, max_storage_bytes=100), FixtureConverter())
    ) as client:
        response = client.post("/api/jobs", json={"url": URL})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "storage_full"
    monkeypatch.setattr("app.main.tools_ready", lambda: {"ffmpeg": False})
    with TestClient(create_app(settings, FixtureConverter())) as client:
        assert client.get("/api/health").status_code == 503
        assert client.post("/api/jobs", json={"url": URL}).status_code == 503
