from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


FocusMethod = Literal["face", "person", "motion", "contrast", "center"]

FOCUS_ANALYZER_VERSION = 1
MAX_VIDEO_FRAMES = 9
MAX_FRAME_EDGE = 640
FOCUS_TIMEOUT_SECONDS = 10
MIN_FOCUS_CONFIDENCE = 0.10


@dataclass(frozen=True)
class FocusResult:
    x: float
    y: float
    confidence: float
    method: FocusMethod
    analyzer_version: int = FOCUS_ANALYZER_VERSION


def center_focus() -> FocusResult:
    return FocusResult(0.5, 0.5, 0.0, "center")


class FocusAnalyzer:
    def analyze(self, path: Path, kind: Literal["image", "video"]) -> FocusResult:
        if kind not in {"image", "video"}:
            return center_focus()
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "holden_reel.focus_worker",
                    "--path",
                    str(path),
                    "--kind",
                    kind,
                ],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=FOCUS_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return center_focus()
        if completed.returncode != 0:
            return center_focus()
        return _parse_result(completed.stdout)


def _parse_result(output: str) -> FocusResult:
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return center_focus()
    if not isinstance(payload, dict):
        return center_focus()
    x = payload.get("x")
    y = payload.get("y")
    confidence = payload.get("confidence")
    method = payload.get("method")
    version = payload.get("analyzer_version")
    values = (x, y, confidence)
    if (
        not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values)
        or not all(0.0 <= value <= 1.0 for value in values)
        or not isinstance(method, str)
        or method not in {"face", "person", "motion", "contrast", "center"}
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != FOCUS_ANALYZER_VERSION
    ):
        return center_focus()
    return FocusResult(float(x), float(y), float(confidence), method, version)
