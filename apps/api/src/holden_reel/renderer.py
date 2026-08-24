from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import time
from threading import Thread
from typing import Protocol
from uuid import UUID

from .config import Settings
from .media import MediaAsset
from .plans import ReelPlan, Shot


@dataclass(frozen=True)
class RenderProfile:
    name: str
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str
    crf: int = 23


PREVIEW = RenderProfile("preview", 540, 960, 30, "libx264", "aac", 28)
FINAL = RenderProfile("final", 1080, 1920, 30, "libx264", "aac", 18)


@dataclass(frozen=True)
class RenderResult:
    path: Path
    width: int
    height: int
    video_codec: str
    audio_codec: str
    duration_ms: int
    size_bytes: int


class RenderCancelled(RuntimeError):
    pass


class MediaLookup(Protocol):
    def list(self, project_id: UUID) -> Sequence[MediaAsset]: ...


class FFmpegCompiler:
    def __init__(self, ffmpeg_bin: str):
        self.ffmpeg_bin = ffmpeg_bin

    def compile(
        self,
        plan: ReelPlan,
        assets: Mapping[UUID, MediaAsset] | Sequence[MediaAsset],
        profile: RenderProfile,
        output_path: Path,
    ) -> list[str]:
        by_id = _assets_by_id(assets)
        _assert_output_not_source(output_path, by_id.values())

        command = [self.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y"]
        filters: list[str] = []
        visual_labels: list[str] = []

        for index, shot in enumerate(plan.shots):
            asset = _required_asset(by_id, shot.asset_id)
            duration = _seconds(shot.output_end_ms - shot.output_start_ms)
            if asset.kind == "image":
                command.extend(["-loop", "1", "-t", duration, "-i", str(asset.path)])
                filters.append(_image_filter(index, shot, profile))
            elif asset.kind == "video":
                command.extend(["-i", str(asset.path)])
                filters.append(_video_filter(index, shot, profile))
            else:
                raise ValueError("visual shot must reference video or image media")
            visual_labels.append(f"[v{index}]")

        audio = _required_asset(by_id, plan.audio.asset_id)
        if audio.kind != "audio" and not (audio.kind == "video" and audio.has_audio):
            raise ValueError("audio bed must reference media with audio")
        audio_index = len(plan.shots)
        command.extend(["-i", str(audio.path)])
        filters.append(
            _audio_filter(
                audio_index,
                plan.audio.source_start_ms,
                plan.audio.source_end_ms,
                plan.audio.gain_db,
                plan.duration_ms,
            )
        )
        filters.append(
            f"{''.join(visual_labels)}concat=n={len(visual_labels)}:v=1:a=0[vout]"
        )

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                profile.video_codec,
                "-crf",
                str(profile.crf),
                "-r",
                str(profile.fps),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                profile.audio_codec,
                "-movflags",
                "+faststart",
                "-t",
                _seconds(plan.duration_ms),
                "-shortest",
                "-progress",
                "pipe:1",
                "-nostats",
                str(output_path),
            ]
        )
        return command


class Renderer:
    def __init__(self, media: MediaLookup, settings: Settings):
        self.media = media
        self.settings = settings
        self.data_root = settings.data_dir.resolve()
        self.compiler = FFmpegCompiler(settings.ffmpeg_bin)

    def render(
        self,
        plan: ReelPlan,
        profile: RenderProfile,
        output_path: Path,
        on_progress: Callable[[float], None],
        is_cancelled: Callable[[], bool],
    ) -> RenderResult:
        _require_supported_profile(profile)
        _require_path_within_data_root(output_path, self.data_root)
        partial_path = Path(f"{output_path}.partial.mp4")
        _require_path_within_data_root(partial_path, self.data_root)
        assets = {asset.id: asset for asset in self.media.list(plan.project_id)}
        _assert_output_not_source(output_path, assets.values())
        _assert_output_not_source(partial_path, assets.values())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.unlink(missing_ok=True)
        command = self.compiler.compile(plan, assets, profile, partial_path)
        process: subprocess.Popen[str] | None = None
        output_reader: Thread | None = None
        last_progress = 0.0
        diagnostics: list[str] = []
        on_progress(last_progress)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False,
            )
            if process.stdout is None:
                raise RuntimeError("FFmpeg progress pipe was unavailable")
            output_messages: Queue[str | BaseException | None] = Queue()
            output_reader = Thread(
                target=_drain_process_output,
                args=(process.stdout, output_messages),
                name="holden-reel-ffmpeg-output",
            )
            output_reader.start()
            while True:
                if is_cancelled():
                    _stop_process(process)
                    raise RenderCancelled("Render was cancelled")
                try:
                    message = output_messages.get(timeout=0.05)
                except Empty:
                    continue
                if message is None:
                    break
                if isinstance(message, BaseException):
                    raise RuntimeError("Failed to read FFmpeg output") from message
                line = message
                progress = _parse_progress(line, plan.duration_ms)
                if progress is None:
                    if stripped := line.strip():
                        diagnostics.append(stripped)
                        diagnostics = diagnostics[-20:]
                    continue
                last_progress = max(last_progress, progress)
                on_progress(last_progress)
                if is_cancelled():
                    _stop_process(process)
                    raise RenderCancelled("Render was cancelled")

            returncode = _wait_for_process(process, is_cancelled)
            if returncode != 0:
                error = "\n".join(diagnostics)
                detail = f": {error}" if error else ""
                raise RuntimeError(f"FFmpeg render failed with exit code {returncode}{detail}")

            result = self.verify(partial_path, plan.duration_ms, profile, is_cancelled=is_cancelled)
            partial_path.replace(output_path)
            on_progress(1.0)
            return replace(result, path=output_path)
        except BaseException:
            if process is not None and process.returncode is None:
                _stop_process(process)
            partial_path.unlink(missing_ok=True)
            raise
        finally:
            if output_reader is not None:
                output_reader.join()

    def verify(
        self,
        output_path: Path,
        expected_duration_ms: int,
        profile: RenderProfile,
        *,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> RenderResult:
        _require_supported_profile(profile)
        _require_path_within_data_root(output_path, self.data_root)
        process = subprocess.Popen(
            [
                self.settings.ffprobe_bin,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(output_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        deadline = time.monotonic() + 30
        while True:
            if is_cancelled():
                _stop_process(process)
                raise RenderCancelled("Render was cancelled during output verification")
            try:
                stdout, stderr = process.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    _stop_process(process)
                    raise RuntimeError("FFprobe verification timed out after 30 seconds")
        if process.returncode != 0:
            raise RuntimeError(f"FFprobe verification failed: {stderr.strip()}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("FFprobe returned invalid JSON") from error

        streams = payload.get("streams", [])
        video = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (stream for stream in streams if stream.get("codec_type") == "audio"),
            None,
        )
        if video is None or audio is None:
            raise RuntimeError("Rendered output must contain video and audio streams")
        duration_ms = _probe_duration_ms(payload, video, audio)
        result = RenderResult(
            path=output_path,
            width=int(video.get("width", 0)),
            height=int(video.get("height", 0)),
            video_codec=str(video.get("codec_name", "")),
            audio_codec=str(audio.get("codec_name", "")),
            duration_ms=duration_ms,
            size_bytes=output_path.stat().st_size,
        )
        _verify_result(result, expected_duration_ms, profile, _probe_fps(video))
        return result


def _assets_by_id(
    assets: Mapping[UUID, MediaAsset] | Sequence[MediaAsset],
) -> dict[UUID, MediaAsset]:
    return dict(assets) if isinstance(assets, Mapping) else {asset.id: asset for asset in assets}


def _required_asset(assets: Mapping[UUID, MediaAsset], asset_id: UUID) -> MediaAsset:
    try:
        return assets[asset_id]
    except KeyError as error:
        raise ValueError(f"missing media asset {asset_id}") from error


def _assert_output_not_source(
    output_path: Path, assets: Iterable[MediaAsset]
) -> None:
    output = output_path.resolve()
    if any(asset.path.resolve() == output for asset in assets):
        raise ValueError("render output must not equal a source path")


def _require_supported_profile(profile: RenderProfile) -> None:
    if profile not in (PREVIEW, FINAL):
        raise ValueError("render profile must equal the exact PREVIEW or FINAL configuration")


def _require_path_within_data_root(path: Path, data_root: Path) -> None:
    resolved = path.resolve()
    if resolved == data_root or not resolved.is_relative_to(data_root):
        raise ValueError("render output must remain within the configured data directory")


def _video_filter(index: int, shot: Shot, profile: RenderProfile) -> str:
    if shot.source_start_ms is None or shot.source_end_ms is None:
        raise ValueError("video shots require source ranges")
    return (
        f"[{index}:v]trim=start={_seconds(shot.source_start_ms)}:"
        f"end={_seconds(shot.source_end_ms)},setpts=PTS-STARTPTS,"
        f"fps={profile.fps},"
        f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=increase,"
        f"crop={profile.width}:{profile.height},setsar=1,format=yuv420p[v{index}]"
    )


def _image_filter(index: int, shot: Shot, profile: RenderProfile) -> str:
    duration_ms = shot.output_end_ms - shot.output_start_ms
    return (
        f"[{index}:v]fps={profile.fps},"
        f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=increase,"
        f"crop={profile.width}:{profile.height},setsar=1,"
        "zoompan=z='min(zoom+0.0005,1.08)':"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={profile.width}x{profile.height}:fps={profile.fps},"
        f"trim=duration={_seconds(duration_ms)},setpts=PTS-STARTPTS,"
        f"format=yuv420p[v{index}]"
    )


def _audio_filter(
    input_index: int,
    source_start_ms: int,
    source_end_ms: int,
    gain_db: float,
    duration_ms: int,
) -> str:
    return (
        f"[{input_index}:a]atrim=start={_seconds(source_start_ms)}:"
        f"end={_seconds(source_end_ms)},asetpts=PTS-STARTPTS,"
        f"volume={gain_db:g}dB,apad=pad_dur=0.021334,"
        f"atrim=duration={_seconds(duration_ms)}[aout]"
    )


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.6f}".rstrip("0").rstrip(".")


def _parse_progress(line: str, duration_ms: int) -> float | None:
    key, separator, raw_value = line.strip().partition("=")
    if separator == "" or key != "out_time_ms":
        return None
    try:
        out_time_microseconds = int(raw_value)
    except ValueError:
        return None
    return min(1.0, max(0.0, out_time_microseconds / (duration_ms * 1000)))


def _drain_process_output(
    output: Iterable[str], messages: Queue[str | BaseException | None]
) -> None:
    try:
        for line in output:
            messages.put(line)
    except BaseException as error:
        messages.put(error)
    finally:
        messages.put(None)


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("FFmpeg did not exit after being killed") from error


def _wait_for_process(
    process: subprocess.Popen[str], is_cancelled: Callable[[], bool]
) -> int:
    while True:
        if is_cancelled():
            _stop_process(process)
            raise RenderCancelled("Render was cancelled")
        returncode = process.poll()
        if returncode is not None:
            return returncode
        try:
            return process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            continue


def _probe_duration_ms(payload: dict, video: dict, audio: dict) -> int:
    candidates = [
        payload.get("format", {}).get("duration"),
        video.get("duration"),
        audio.get("duration"),
    ]
    for value in candidates:
        try:
            if value is not None:
                return round(float(value) * 1000)
        except (TypeError, ValueError):
            continue
    raise RuntimeError("Rendered output has no measurable duration")


def _verify_result(
    result: RenderResult,
    expected_duration_ms: int,
    profile: RenderProfile,
    actual_fps: float,
) -> None:
    if (result.width, result.height) != (profile.width, profile.height):
        raise RuntimeError("Rendered output dimensions do not match profile")
    expected_video_codec = "h264" if profile.video_codec == "libx264" else profile.video_codec
    if result.video_codec != expected_video_codec:
        raise RuntimeError("Rendered output video codec does not match profile")
    if result.audio_codec != profile.audio_codec:
        raise RuntimeError("Rendered output audio codec does not match profile")
    if abs(actual_fps - profile.fps) > 0.01:
        raise RuntimeError("Rendered output frame rate does not match profile")
    if abs(result.duration_ms - expected_duration_ms) > 100:
        raise RuntimeError("Rendered output duration is outside tolerance")
    if result.size_bytes <= 0:
        raise RuntimeError("Rendered output is empty")


def _probe_fps(video: dict) -> float:
    value = video.get("avg_frame_rate") or video.get("r_frame_rate")
    try:
        fps = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError) as error:
        raise RuntimeError("Rendered output has no measurable frame rate") from error
    if fps <= 0:
        raise RuntimeError("Rendered output has no measurable frame rate")
    return fps
