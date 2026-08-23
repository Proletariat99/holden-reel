import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class FixtureMedia:
    root: Path
    paths: dict[str, Path]


def _run_ffmpeg(arguments: list[str]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required to generate media fixtures but was not found")
    subprocess.run([ffmpeg, "-y", *arguments], check=True, capture_output=True, shell=False)


def generate_fixture_media(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "red.mp4": root / "red.mp4",
        "blue.mp4": root / "blue.mp4",
        "still.jpg": root / "still.jpg",
        "song.wav": root / "song.wav",
    }
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
    return {name: path.resolve() for name, path in paths.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generated = generate_fixture_media(arguments.output)
    print(json.dumps({name: str(path) for name, path in generated.items()}))
