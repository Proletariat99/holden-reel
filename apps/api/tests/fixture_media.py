import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


FFMPEG_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class FixtureMedia:
    root: Path
    paths: dict[str, Path]


def _run_ffmpeg(arguments: list[str]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required to generate media fixtures but was not found")
    try:
        subprocess.run(
            [ffmpeg, "-y", *arguments],
            check=True,
            capture_output=True,
            shell=False,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"FFmpeg fixture generation timed out after {FFMPEG_TIMEOUT_SECONDS} seconds"
        ) from None


def generate_fixture_media(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "red.mp4": root / "red.mp4",
        "blue.mp4": root / "blue.mp4",
        "off-center.mp4": root / "off-center.mp4",
        "left-red.mp4": root / "left-red.mp4",
        "right-blue.mp4": root / "right-blue.mp4",
        "still.jpg": root / "still.jpg",
        "song.wav": root / "song.wav",
    }
    try:
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=red:s=320x240:r=30:d=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(paths["red.mp4"]),
            ]
        )
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=blue:s=240x320:r=30:d=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(paths["blue.mp4"]),
            ]
        )
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=green:s=640x360:r=30:d=4",
                "-vf", "drawbox=x=20:y=70:w=150:h=220:color=red:t=fill",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(paths["off-center.mp4"]),
            ]
        )
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(paths["left-red.mp4"]),
            ]
        )
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(paths["right-blue.mp4"]),
            ]
        )
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=yellow:s=320x240",
                "-frames:v", "1", str(paths["still.jpg"]),
            ]
        )
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "sine=frequency=440:duration=18",
                "-c:a", "pcm_s16le", str(paths["song.wav"]),
            ]
        )
    except BaseException:
        for path in paths.values():
            path.unlink(missing_ok=True)
        raise
    return {name: path.resolve() for name, path in paths.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generated = generate_fixture_media(arguments.output)
    print(json.dumps({name: str(path) for name, path in generated.items()}))
