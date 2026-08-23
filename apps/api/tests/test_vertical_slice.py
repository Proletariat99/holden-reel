from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time

from fastapi.testclient import TestClient
import pytest


RENDER_TIMEOUT_SECONDS = 120.0
FFPROBE_TIMEOUT_SECONDS = 30


def test_acceptance_ffprobe_timeout_has_a_deterministic_failure(tmp_path: Path):
    """Would fail if final artifact verification could hang without a clear bound."""
    observed_timeouts: list[float | None] = []
    artifact = tmp_path / "final.mp4"
    artifact.write_bytes(b"incomplete acceptance artifact")

    def time_out(command, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(command, 30)

    with pytest.raises(
        AssertionError,
        match="^FFprobe acceptance verification timed out after 30 seconds$",
    ):
        _probe_artifact("ffprobe", artifact, runner=time_out)

    assert observed_timeouts == [30]
    assert not artifact.exists()


def test_complete_http_workflow_exports_verified_final_without_modifying_sources(
    client: TestClient, media_fixture, ffmpeg_bins, tmp_path: Path
):
    """Would fail if the real HTTP workflow, final profile, or source integrity regressed."""
    _, ffprobe = ffmpeg_bins
    source_hashes = {
        name: _sha256(path) for name, path in media_fixture.paths.items()
    }

    created = client.post("/api/projects", json={"name": "Golden Reel"})
    assert created.status_code == 201
    project = created.json()

    imported = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root)},
    )
    assert imported.status_code == 201
    assets = imported.json()["assets"]
    audio = next(asset for asset in assets if asset["kind"] == "audio")
    visuals = [
        asset for asset in assets if asset["kind"] in {"video", "image"}
    ]

    composed = client.post(
        f"/api/projects/{project['id']}/plans/compose",
        json={
            "duration_ms": 15_000,
            "audio_asset_id": audio["id"],
            "audio_start_ms": 0,
            "visual_asset_ids": [asset["id"] for asset in visuals],
        },
    )
    assert composed.status_code == 201
    plan = composed.json()
    assert plan["duration_ms"] == 15_000

    submitted = client.post(
        f"/api/plans/{plan['id']}/renders", json={"profile": "final"}
    )
    assert submitted.status_code == 202
    job = _wait_for_terminal_job(client, submitted.json()["id"])
    assert job["status"] == "succeeded", job

    downloaded = client.get(f"/api/jobs/{job['id']}/artifact")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "video/mp4"
    assert len(downloaded.content) > 0
    artifact = tmp_path / "downloaded-final.mp4"
    artifact.write_bytes(downloaded.content)

    metadata = _probe_artifact(ffprobe, artifact)
    video = next(
        stream for stream in metadata["streams"] if stream["codec_type"] == "video"
    )
    audio_stream = next(
        stream for stream in metadata["streams"] if stream["codec_type"] == "audio"
    )
    assert metadata["format"]["format_name"] == "mov,mp4,m4a,3gp,3g2,mj2"
    assert video["codec_name"] == "h264"
    assert (video["width"], video["height"]) == (1080, 1920)
    assert audio_stream["codec_name"] == "aac"
    assert metadata["format"]["duration"] == "15.000000"
    assert {
        name: _sha256(path) for name, path in media_fixture.paths.items()
    } == source_hashes


def _probe_artifact(ffprobe: str, artifact: Path, runner=subprocess.run) -> dict:
    try:
        probe = runner(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(artifact),
            ],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        artifact.unlink(missing_ok=True)
        raise AssertionError(
            "FFprobe acceptance verification timed out after "
            f"{FFPROBE_TIMEOUT_SECONDS} seconds"
        ) from None
    return json.loads(probe.stdout)


def _wait_for_terminal_job(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + RENDER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(
        f"render job {job_id} did not finish within {RENDER_TIMEOUT_SECONDS} seconds"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
