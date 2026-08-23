from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import subprocess
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest

from holden_reel.artifacts import ArtifactStore
from holden_reel.config import Settings
from holden_reel.media import MediaAsset
from holden_reel.plans import AudioBed, ReelPlan, Shot
from holden_reel.renderer import (
    FINAL,
    PREVIEW,
    FFmpegCompiler,
    RenderCancelled,
    RenderResult,
    Renderer,
)


PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
AUDIO_ID = UUID("00000000-0000-0000-0000-000000000010")
RED_ID = UUID("00000000-0000-0000-0000-000000000011")
BLUE_ID = UUID("00000000-0000-0000-0000-000000000012")
STILL_ID = UUID("00000000-0000-0000-0000-000000000013")


class StaticMedia:
    def __init__(self, assets: dict[UUID, MediaAsset]):
        self.assets = assets

    def list(self, project_id: UUID) -> list[MediaAsset]:
        assert project_id == PROJECT_ID
        return list(self.assets.values())


@pytest.fixture
def command_assets() -> dict[UUID, MediaAsset]:
    root = Path("/source media")
    return {
        AUDIO_ID: _asset(AUDIO_ID, root / "theme song.wav", "audio", 18_000),
        RED_ID: _asset(RED_ID, root / "red clip.mp4", "video", 4_000),
        BLUE_ID: _asset(BLUE_ID, root / "blue clip.mp4", "video", 4_000),
        STILL_ID: _asset(STILL_ID, root / "still frame.jpg", "image", None),
    }


@pytest.fixture
def valid_plan() -> ReelPlan:
    return ReelPlan(
        id=uuid4(),
        project_id=PROJECT_ID,
        version=1,
        duration_ms=15_000,
        audio=AudioBed(
            asset_id=AUDIO_ID,
            source_start_ms=1_000,
            source_end_ms=16_000,
            gain_db=-2.5,
        ),
        shots=[
            _video_shot(RED_ID, 0, 3_000),
            _video_shot(BLUE_ID, 3_000, 6_000),
            Shot(
                asset_id=STILL_ID,
                source_start_ms=None,
                source_end_ms=None,
                output_start_ms=6_000,
                output_end_ms=9_000,
                still_motion="slow_zoom",
            ),
            _video_shot(RED_ID, 9_000, 12_000),
            _video_shot(BLUE_ID, 12_000, 15_000),
        ],
        rationale="Fixed render fixture order.",
    )


def test_artifact_store_builds_only_safe_uncreated_paths(tmp_path):
    """Would fail if generated artifacts could escape or be pre-created."""
    store = ArtifactStore(tmp_path)
    artifact_id = UUID("00000000-0000-0000-0000-000000000020")

    preview = store.path_for(PROJECT_ID, "preview", artifact_id, ".mp4")
    final = store.path_for(PROJECT_ID, "final", artifact_id, ".mp4")

    assert preview == tmp_path / "projects" / str(PROJECT_ID) / "previews" / f"{artifact_id}.mp4"
    assert final == tmp_path / "projects" / str(PROJECT_ID) / "exports" / f"{artifact_id}.mp4"
    assert preview.parent.is_dir()
    assert final.parent.is_dir()
    assert not preview.exists()
    assert not final.exists()


@pytest.mark.parametrize(
    ("kind", "artifact_id", "suffix"),
    [
        ("preview", "../escape", ".mp4"),
        ("../previews", UUID("00000000-0000-0000-0000-000000000020"), ".mp4"),
        ("preview", UUID("00000000-0000-0000-0000-000000000020"), ".mov"),
    ],
)
def test_artifact_store_rejects_path_escape(tmp_path, kind, artifact_id, suffix):
    """Would fail if attacker-controlled path components reached the filesystem."""
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path).path_for(PROJECT_ID, kind, artifact_id, suffix)


def test_artifact_store_rejects_symlink_escape(tmp_path):
    """Would fail if a pre-existing symlink redirected artifacts outside data_dir."""
    data_dir = tmp_path / "data"
    external = tmp_path / "external"
    (data_dir / "projects").mkdir(parents=True)
    external.mkdir()
    (data_dir / "projects" / str(PROJECT_ID)).symlink_to(
        external, target_is_directory=True
    )

    with pytest.raises(ValueError, match="escapes"):
        ArtifactStore(data_dir).path_for(PROJECT_ID, "preview", uuid4(), ".mp4")

    assert list(external.iterdir()) == []


def test_compiler_uses_argument_array_and_exact_preview_filters(
    valid_plan, command_assets
):
    """Would fail if shell parsing or a missing normalization filter made renders unsafe."""
    output = Path("/render output/preview reel.mp4")

    command = FFmpegCompiler("ffmpeg").compile(
        valid_plan, command_assets, PREVIEW, output
    )
    joined = " ".join(command)

    assert isinstance(command, list)
    assert command[0] == "ffmpeg"
    assert str(command_assets[RED_ID].path) in command
    assert str(command_assets[AUDIO_ID].path) in command
    assert "scale=540:960:force_original_aspect_ratio=increase" in joined
    assert "crop=540:960" in joined
    assert "fps=30" in joined
    assert "setsar=1" in joined
    assert "format=yuv420p" in joined
    assert "atrim=start=1:end=16" in joined
    assert "volume=-2.5dB" in joined
    assert "apad=pad_dur=0.021334" in joined
    assert command[-1] == str(output)


def test_compiler_loops_images_and_uses_exact_final_profile(valid_plan, command_assets):
    """Would fail if stills ended early or final output used proxy dimensions/quality."""
    command = FFmpegCompiler("ffmpeg").compile(
        valid_plan, command_assets, FINAL, Path("/render/final.mp4")
    )
    image_index = command.index(str(command_assets[STILL_ID].path))
    joined = " ".join(command)

    assert command[image_index - 5 : image_index] == [
        "-loop",
        "1",
        "-t",
        "3",
        "-i",
    ]
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in joined
    assert "crop=1080:1920" in joined
    assert "zoompan=" in joined
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-crf") + 1] == "18"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-t", image_index) + 1] == "15"
    assert "-shortest" in command


def test_compiler_rejects_output_equal_to_any_source(valid_plan, command_assets):
    """Would fail if FFmpeg could overwrite an original source asset."""
    with pytest.raises(ValueError, match="source"):
        FFmpegCompiler("ffmpeg").compile(
            valid_plan, command_assets, PREVIEW, command_assets[RED_ID].path
        )


def test_renderer_preserves_source_equal_to_derived_partial_path(
    tmp_path, valid_plan, command_assets
):
    """Would fail if stale-partial cleanup could unlink an original source file."""
    output = tmp_path / "output.mp4"
    partial = Path(f"{output}.partial.mp4")
    partial.write_bytes(b"original-source")
    command_assets[RED_ID] = _asset(RED_ID, partial, "video", 4_000)
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(ValueError, match="source"):
        renderer.render(valid_plan, PREVIEW, output, lambda _: None, lambda: False)

    assert partial.read_bytes() == b"original-source"


def test_renderer_rejects_output_outside_configured_data_root(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if a caller could make Renderer create files outside data_dir."""
    data_root = tmp_path / "data"
    external_parent = tmp_path / "external" / "nested"
    output = external_parent / "output.mp4"
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("FFmpeg must not start for an unsafe path"),
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=data_root, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(ValueError, match="configured data directory"):
        renderer.render(valid_plan, PREVIEW, output, lambda _: None, lambda: False)

    assert not external_parent.exists()


def test_renderer_rejects_output_through_symlink_parent(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if a symlink parent redirected renderer output outside data_dir."""
    data_root = tmp_path / "data"
    external = tmp_path / "external"
    data_root.mkdir()
    external.mkdir()
    (data_root / "redirect").symlink_to(external, target_is_directory=True)
    output = data_root / "redirect" / "nested" / "output.mp4"
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("FFmpeg must not start for an unsafe path"),
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=data_root, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(ValueError, match="configured data directory"):
        renderer.render(valid_plan, PREVIEW, output, lambda _: None, lambda: False)

    assert list(external.iterdir()) == []


def test_renderer_rejects_derived_partial_symlink_escape(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if the derived partial path bypassed canonical containment."""
    data_root = tmp_path / "data"
    external = tmp_path / "external"
    data_root.mkdir()
    external.mkdir()
    output = data_root / "output.mp4"
    partial = Path(f"{output}.partial.mp4")
    partial.symlink_to(external / "partial.mp4")
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("FFmpeg must not start for an unsafe partial"),
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=data_root, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(ValueError, match="configured data directory"):
        renderer.render(valid_plan, PREVIEW, output, lambda _: None, lambda: False)

    assert partial.is_symlink()
    assert list(external.iterdir()) == []


@pytest.mark.parametrize(
    "profile",
    [
        replace(PREVIEW, width=541),
        replace(PREVIEW, fps=24),
        replace(PREVIEW, video_codec="mpeg4"),
        replace(PREVIEW, audio_codec="mp3"),
        replace(PREVIEW, crf=27),
    ],
    ids=["dimensions", "fps", "video-codec", "audio-codec", "crf"],
)
def test_renderer_rejects_altered_profile_before_spawning(
    tmp_path, monkeypatch, valid_plan, command_assets, profile
):
    """Would fail if caller-defined encoding settings reached FFmpeg."""
    output = tmp_path / "data" / "output.mp4"
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("FFmpeg must not start for an altered profile"),
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path / "data", ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(ValueError, match="exact PREVIEW or FINAL"):
        renderer.render(valid_plan, profile, output, lambda _: None, lambda: False)

    assert not output.parent.exists()


def test_verify_rejects_altered_profile_before_probing(
    tmp_path, monkeypatch, command_assets
):
    """Would fail if verification trusted caller-defined technical expectations."""
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.run",
        lambda *args, **kwargs: pytest.fail("FFprobe must not run for an altered profile"),
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(ValueError, match="exact PREVIEW or FINAL"):
        renderer.verify(tmp_path / "output.mp4", 15_000, replace(FINAL, crf=17))


def test_renderer_cancels_while_ffmpeg_emits_no_progress(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if cancellation waited for FFmpeg to emit a progress record."""
    output = tmp_path / "output.mp4"
    partial = Path(f"{output}.partial.mp4")
    process = _NoProgressProcess(partial)
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen", lambda *args, **kwargs: process
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )
    errors: list[BaseException] = []
    finished = Event()

    def run_render() -> None:
        try:
            renderer.render(valid_plan, PREVIEW, output, lambda _: None, lambda: True)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    render_thread = Thread(target=run_render, name="test-no-progress-render")
    render_thread.start()
    try:
        assert finished.wait(1), "renderer did not poll cancellation without progress"
    finally:
        if not finished.is_set():
            process.force_stop()
            assert finished.wait(1), "test cleanup could not stop renderer"
        render_thread.join(timeout=1)

    assert not render_thread.is_alive()
    assert process.output.ended.is_set()
    assert process.terminated
    assert process.killed
    assert process.wait_timeouts == [5]
    assert len(errors) == 1
    assert isinstance(errors[0], RenderCancelled)
    assert not partial.exists()


def test_renderer_cancels_after_output_eof_while_process_remains_running(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if post-EOF process waiting stopped polling cancellation."""
    output = tmp_path / "output.mp4"
    partial = Path(f"{output}.partial.mp4")
    process = _EofRunningProcess(partial)
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen", lambda *args, **kwargs: process
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )
    errors: list[BaseException] = []
    finished = Event()

    def run_render() -> None:
        try:
            renderer.render(
                valid_plan,
                PREVIEW,
                output,
                lambda _: None,
                process.short_wait_started.is_set,
            )
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    render_thread = Thread(target=run_render, name="test-eof-running-render")
    render_thread.start()
    try:
        assert finished.wait(1), "renderer blocked after output EOF"
    finally:
        if not finished.is_set():
            process.force_stop()
            assert finished.wait(1), "test cleanup could not stop renderer"
        render_thread.join(timeout=1)

    assert not render_thread.is_alive()
    assert process.output_ended.is_set()
    assert process.poll_calls >= 1
    assert process.short_wait_timeouts == [0.05]
    assert process.terminated
    assert process.killed
    assert process.termination_wait_timeouts == [5]
    assert len(errors) == 1
    assert isinstance(errors[0], RenderCancelled)
    assert not partial.exists()


def test_renderer_reports_progress_and_escalates_cancel_to_kill(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if cancellation left FFmpeg or its partial output behind."""
    output = tmp_path / "output.mp4"
    partial = Path(f"{output}.partial.mp4")
    process = _HungProcess(partial)
    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return process

    monkeypatch.setattr("holden_reel.renderer.subprocess.Popen", fake_popen)
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )
    progress = []
    cancellation_checks = iter([False, True])

    with pytest.raises(RenderCancelled):
        renderer.render(
            valid_plan,
            PREVIEW,
            output,
            progress.append,
            lambda: next(cancellation_checks),
        )

    assert progress == [0.0, 0.5]
    assert process.terminated
    assert process.killed
    assert process.wait_timeouts == [5]
    assert not partial.exists()
    assert not output.exists()
    assert popen_calls[0][1]["shell"] is False


def test_renderer_failure_removes_only_partial_output(
    tmp_path, monkeypatch, valid_plan, command_assets
):
    """Would fail if a render error removed an existing completed artifact."""
    output = tmp_path / "output.mp4"
    output.write_bytes(b"previous-good-render")
    partial = Path(f"{output}.partial.mp4")
    process = _FailedProcess(partial)
    monkeypatch.setattr(
        "holden_reel.renderer.subprocess.Popen", lambda *args, **kwargs: process
    )
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"),
    )

    with pytest.raises(RuntimeError, match="FFmpeg render failed"):
        renderer.render(valid_plan, PREVIEW, output, lambda _: None, lambda: False)

    assert output.read_bytes() == b"previous-good-render"
    assert not partial.exists()


def test_real_render_is_verified_atomic_and_preserves_sources(
    tmp_path, media_fixture, ffmpeg_bins, valid_plan
):
    """Would fail on bad media output, non-atomic publication, or source mutation."""
    ffmpeg, ffprobe = ffmpeg_bins
    assets = _fixture_assets(media_fixture.paths)
    before_hashes = {name: _sha256(path) for name, path in media_fixture.paths.items()}
    output = ArtifactStore(tmp_path).path_for(
        PROJECT_ID, "preview", uuid4(), ".mp4"
    )
    output.write_bytes(b"replace-me-only-after-verification")
    renderer = Renderer(
        StaticMedia(assets),
        Settings(data_dir=tmp_path, ffmpeg_bin=ffmpeg, ffprobe_bin=ffprobe),
    )
    progress = []

    result = renderer.render(valid_plan, PREVIEW, output, progress.append, lambda: False)

    assert result == RenderResult(
        path=output,
        width=540,
        height=960,
        video_codec="h264",
        audio_codec="aac",
        duration_ms=result.duration_ms,
        size_bytes=result.size_bytes,
    )
    assert abs(result.duration_ms - 15_000) <= 100
    assert result.size_bytes > 0
    assert progress[0] == 0.0
    assert progress[-1] == 1.0
    assert progress == sorted(progress)
    assert not Path(f"{output}.partial.mp4").exists()
    assert {name: _sha256(path) for name, path in media_fixture.paths.items()} == before_hashes


def test_verify_rejects_wrong_frame_rate(tmp_path, ffmpeg_bins, command_assets):
    """Would fail if technical verification ignored the profile frame rate."""
    ffmpeg, ffprobe = ffmpeg_bins
    renderer = Renderer(
        StaticMedia(command_assets),
        Settings(data_dir=tmp_path, ffmpeg_bin=ffmpeg, ffprobe_bin=ffprobe),
    )
    output = tmp_path / "24-fps.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=540x960:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=True,
        capture_output=True,
        shell=False,
    )

    with pytest.raises(RuntimeError, match="frame rate"):
        renderer.verify(output, 1_000, PREVIEW)


def _asset(
    asset_id: UUID,
    path: Path,
    kind: str,
    duration_ms: int | None,
    width: int | None = 320,
    height: int | None = 240,
) -> MediaAsset:
    return MediaAsset(
        id=asset_id,
        project_id=PROJECT_ID,
        path=path,
        kind=kind,
        duration_ms=duration_ms,
        width=width if kind != "audio" else None,
        height=height if kind != "audio" else None,
        codec="fixture",
        available=True,
        fingerprint="fixture",
    )


def _video_shot(asset_id: UUID, output_start_ms: int, output_end_ms: int) -> Shot:
    duration = output_end_ms - output_start_ms
    return Shot(
        asset_id=asset_id,
        source_start_ms=0,
        source_end_ms=duration,
        output_start_ms=output_start_ms,
        output_end_ms=output_end_ms,
    )


def _fixture_assets(paths: dict[str, Path]) -> dict[UUID, MediaAsset]:
    return {
        AUDIO_ID: _asset(AUDIO_ID, paths["song.wav"], "audio", 18_000),
        RED_ID: _asset(RED_ID, paths["red.mp4"], "video", 4_000),
        BLUE_ID: _asset(BLUE_ID, paths["blue.mp4"], "video", 4_000, 240, 320),
        STILL_ID: _asset(STILL_ID, paths["still.jpg"], "image", None),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _HungProcess:
    def __init__(self, partial: Path):
        partial.write_bytes(b"incomplete")
        self.stdout = iter(["out_time_ms=7500000\n"])
        self.stderr = _TextStream("")
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.wait_timeouts = []

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.terminated and not self.killed:
            self.wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode


class _FailedProcess:
    def __init__(self, partial: Path):
        partial.write_bytes(b"incomplete")
        self.stdout = iter([])
        self.stderr = _TextStream("encoder failed")
        self.returncode = 1

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _NoProgressProcess:
    def __init__(self, partial: Path):
        partial.write_bytes(b"incomplete")
        self.output = _BlockingOutput()
        self.stdout = self.output
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.wait_timeouts = []

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.output.release.set()

    def wait(self, timeout=None):
        if self.terminated and not self.killed:
            self.wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode

    def force_stop(self):
        self.returncode = -9
        self.output.release.set()


class _EofRunningProcess:
    def __init__(self, partial: Path):
        partial.write_bytes(b"incomplete")
        self.output_ended = Event()
        self.stdout = _ImmediateEofOutput(self.output_ended)
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.poll_calls = 0
        self.short_wait_started = Event()
        self.short_wait_timeouts = []
        self.termination_wait_timeouts = []
        self.release_unbounded_wait = Event()

    def poll(self):
        self.poll_calls += 1
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.release_unbounded_wait.set()

    def wait(self, timeout=None):
        if self.killed:
            return self.returncode
        if self.terminated:
            self.termination_wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        if timeout is None:
            self.release_unbounded_wait.wait()
            return self.returncode
        self.short_wait_timeouts.append(timeout)
        self.short_wait_started.set()
        raise subprocess.TimeoutExpired("ffmpeg", timeout)

    def force_stop(self):
        self.killed = True
        self.returncode = -9
        self.release_unbounded_wait.set()


class _ImmediateEofOutput:
    def __init__(self, ended: Event):
        self.ended = ended

    def __iter__(self):
        self.ended.set()
        return iter(())


class _BlockingOutput:
    def __init__(self):
        self.release = Event()
        self.ended = Event()

    def __iter__(self):
        return self

    def __next__(self):
        self.release.wait()
        self.ended.set()
        raise StopIteration


class _TextStream:
    def __init__(self, value: str):
        self.value = value

    def read(self) -> str:
        return self.value
