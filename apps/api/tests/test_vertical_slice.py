from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time

import cv2
from fastapi.testclient import TestClient
import pytest

from holden_reel.focus import FocusAnalyzer


RENDER_TIMEOUT_SECONDS = 120.0
FFPROBE_TIMEOUT_SECONDS = 30
FRAME_EXTRACTION_TIMEOUT_SECONDS = 30
DOMINANT_COLOR_CHANNEL_MIN = 140
OTHER_COLOR_CHANNEL_MAX = 100


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


def test_acceptance_frame_extraction_timeout_has_a_deterministic_failure(
    tmp_path: Path,
):
    """Would fail if exact rendered-frame extraction could hang during acceptance."""
    observed_timeouts: list[float | None] = []
    artifact = tmp_path / "final.mp4"
    artifact.write_bytes(b"incomplete acceptance artifact")
    frame = tmp_path / "frame.png"

    def time_out(command, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        frame.write_bytes(b"partial frame")
        raise subprocess.TimeoutExpired(command, 30)

    with pytest.raises(
        AssertionError,
        match="^FFmpeg frame extraction timed out after 30 seconds$",
    ):
        _extract_frame("ffmpeg", artifact, 1_000, frame, runner=time_out)

    assert observed_timeouts == [30]
    assert not frame.exists()


def test_complete_http_workflow_exports_verified_final_without_modifying_sources(
    client: TestClient, media_fixture, ffmpeg_bins, tmp_path: Path
):
    """Would fail if real focus, dissolve, final profile, or source integrity regressed."""
    ffmpeg, ffprobe = ffmpeg_bins
    client.app.state.media_service.focus_analyzer = FocusAnalyzer()
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
    assets_by_name = {Path(asset["path"]).name: asset for asset in assets}
    audio = next(asset for asset in assets if asset["kind"] == "audio")
    off_center = assets_by_name["off-center.mp4"]
    left_red = assets_by_name["left-red.mp4"]
    right_blue = assets_by_name["right-blue.mp4"]
    assert off_center["focus_x"] < 0.35
    assert off_center["focus_method"] != "center"
    visuals = [
        off_center,
        left_red,
        right_blue,
    ]

    composed = client.post(
        f"/api/projects/{project['id']}/plans/compose",
        json={
            "duration_ms": 15_000,
            "audio_asset_id": audio["id"],
            "audio_start_ms": 0,
            "visual_asset_ids": [asset["id"] for asset in visuals],
            "transition_style": "dissolve",
        },
    )
    assert composed.status_code == 201
    plan = composed.json()
    assert plan["duration_ms"] == 15_000
    assert plan["transition_style"] == "dissolve"

    persisted_response = client.get(f"/api/plans/{plan['id']}")
    assert persisted_response.status_code == 200
    persisted = persisted_response.json()
    off_center_shots = [
        shot for shot in persisted["shots"] if shot["asset_id"] == off_center["id"]
    ]
    assert off_center_shots
    assert all(
        (shot["focus_x"], shot["focus_y"], shot["focus_method"])
        == (
            off_center["focus_x"],
            off_center["focus_y"],
            off_center["focus_method"],
        )
        for shot in off_center_shots
    )

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

    focus_frame_path = tmp_path / "off-center-focus.png"
    _extract_frame(ffmpeg, artifact, 1_000, focus_frame_path)
    focus_frame = _read_frame(focus_frame_path)
    red_pixels = _red_pixels(focus_frame)
    assert red_pixels.any()
    corners = (
        focus_frame[0, 0],
        focus_frame[0, -1],
        focus_frame[-1, 0],
        focus_frame[-1, -1],
    )
    assert all(int(pixel.max()) > 20 for pixel in corners)

    red_to_blue = next(
        (before, after)
        for before, after in zip(persisted["shots"], persisted["shots"][1:])
        if before["asset_id"] == left_red["id"]
        and after["asset_id"] == right_blue["id"]
    )
    red_frame_path = tmp_path / "solid-red.png"
    _extract_frame(
        ffmpeg,
        artifact,
        red_to_blue[0]["output_start_ms"] + 400,
        red_frame_path,
    )
    assert _red_pixels(_read_frame(red_frame_path)).any()

    blue_frame_path = tmp_path / "solid-blue.png"
    _extract_frame(
        ffmpeg,
        artifact,
        red_to_blue[1]["output_start_ms"] + 400,
        blue_frame_path,
    )
    assert _blue_pixels(_read_frame(blue_frame_path)).any()

    dissolve_frame_path = tmp_path / "red-blue-dissolve.png"
    _extract_frame(
        ffmpeg,
        artifact,
        red_to_blue[1]["output_start_ms"] + 100,
        dissolve_frame_path,
    )
    dissolve_frame = _read_frame(dissolve_frame_path)
    blue, green, red = (
        int(channel)
        for channel in dissolve_frame[
            dissolve_frame.shape[0] // 2, dissolve_frame.shape[1] // 2
        ]
    )
    assert red > 80
    assert blue > 80
    assert green < 80

    final_source_hashes = {
        name: _sha256(path) for name, path in media_fixture.paths.items()
    }
    assert final_source_hashes == source_hashes


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


def _extract_frame(
    ffmpeg: str,
    artifact: Path,
    timestamp_ms: int,
    destination: Path,
    runner=subprocess.run,
) -> None:
    try:
        runner(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(artifact),
                "-ss",
                f"{timestamp_ms / 1_000:.3f}",
                "-frames:v",
                "1",
                str(destination),
            ],
            check=True,
            capture_output=True,
            shell=False,
            timeout=FRAME_EXTRACTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        destination.unlink(missing_ok=True)
        raise AssertionError(
            "FFmpeg frame extraction timed out after "
            f"{FRAME_EXTRACTION_TIMEOUT_SECONDS} seconds"
        ) from None


def _read_frame(path: Path):
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None, f"OpenCV could not decode extracted frame {path}"
    return frame


def _red_pixels(frame):
    return (
        (frame[:, :, 2] > DOMINANT_COLOR_CHANNEL_MIN)
        & (frame[:, :, 1] < OTHER_COLOR_CHANNEL_MAX)
        & (frame[:, :, 0] < OTHER_COLOR_CHANNEL_MAX)
    )


def _blue_pixels(frame):
    return (
        (frame[:, :, 0] > DOMINANT_COLOR_CHANNEL_MIN)
        & (frame[:, :, 1] < OTHER_COLOR_CHANNEL_MAX)
        & (frame[:, :, 2] < OTHER_COLOR_CHANNEL_MAX)
    )


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
