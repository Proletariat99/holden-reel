import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from holden_reel.focus import FocusAnalyzer, center_focus
from holden_reel.focus_worker import Candidate, choose_focus


def solid_frame() -> np.ndarray:
    return np.zeros((120, 160, 3), dtype=np.uint8)


def frame_with_box(*, x: int) -> np.ndarray:
    frame = solid_frame()
    frame[35:85, x:x + 30] = 255
    return frame


class StubDetector:
    def __init__(self, *, faces=(), people=()):
        self.faces = list(faces)
        self.people = list(people)

    def detect_faces(self, frame: np.ndarray) -> list[Candidate]:
        return self.faces

    def detect_people(self, frame: np.ndarray) -> list[Candidate]:
        return self.people


class PerFrameDetector:
    def __init__(self, *, xs: list[float]):
        self.xs = iter(xs)

    def detect_faces(self, frame: np.ndarray) -> list[Candidate]:
        return [Candidate(next(self.xs), 0.5, 0.8, "face")]

    def detect_people(self, frame: np.ndarray) -> list[Candidate]:
        return []


class EmptyDetector(StubDetector):
    pass


class RaisingDetector(StubDetector):
    def detect_faces(self, frame: np.ndarray) -> list[Candidate]:
        raise RuntimeError("detector unavailable")


def test_faces_outrank_people_and_image_fallback():
    """Would fail if face candidates did not take priority over people."""
    detector = StubDetector(
        faces=[Candidate(0.18, 0.42, 0.12, "face")],
        people=[Candidate(0.82, 0.50, 0.70, "person")],
    )
    result = choose_focus([solid_frame()], detector)
    assert result.method == "face"
    assert result.x == pytest.approx(0.18, abs=0.02)


def test_people_outrank_motion_and_contrast():
    """Would fail if visual fallbacks took priority over person detections."""
    frames = [frame_with_box(x=20), frame_with_box(x=80)]
    result = choose_focus(frames, StubDetector(people=[Candidate(0.25, 0.5, 0.6, "person")]))
    assert result.method == "person"
    assert result.x == pytest.approx(0.25, abs=0.02)


def test_video_candidates_reduce_to_one_robust_fixed_point():
    """Would fail if an outlier frame pulled the fixed crop away from its subject."""
    detector = PerFrameDetector(xs=[0.20, 0.22, 0.21, 0.95])
    result = choose_focus([solid_frame() for _ in range(4)], detector)
    assert result.method == "face"
    assert result.x == pytest.approx(0.215, abs=0.03)


def test_low_signal_and_detector_failure_return_center():
    """Would fail if unreadable or low-signal media could create an arbitrary crop."""
    assert choose_focus([solid_frame()], EmptyDetector()) == center_focus()
    assert choose_focus([solid_frame()], RaisingDetector()) == center_focus()


def test_analyzer_invokes_bounded_worker_and_parses_result(monkeypatch, tmp_path):
    """Would fail if imports could use a shell, omit a timeout, or reject valid worker JSON."""
    path = tmp_path / "still.jpg"
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args[0], 0, json.dumps({
                "x": 0.3, "y": 0.7, "confidence": 0.8, "method": "face", "analyzer_version": 1,
            }), "",
        )

    monkeypatch.setattr("holden_reel.focus.subprocess.run", run)
    result = FocusAnalyzer().analyze(path, "image")

    assert result.x == pytest.approx(0.3)
    assert result.method == "face"
    assert calls == [(
        ([sys.executable, "-m", "holden_reel.focus_worker", "--path", str(path), "--kind", "image"],),
        {"check": False, "capture_output": True, "text": True, "shell": False, "timeout": 10},
    )]


@pytest.mark.parametrize(
    "completed",
    [
        subprocess.CompletedProcess([], 1, "", "worker failed"),
        subprocess.CompletedProcess([], 0, "not json", ""),
        subprocess.CompletedProcess([], 0, '{"x": 1.1, "y": 0.5, "confidence": 0.4, "method": "face", "analyzer_version": 1}', ""),
        subprocess.CompletedProcess([], 0, '{"x": 0.5, "y": 0.5, "confidence": 0.4, "method": "unknown", "analyzer_version": 1}', ""),
    ],
)
def test_analyzer_invalid_worker_result_returns_center(monkeypatch, tmp_path, completed):
    """Would fail if corrupt or incompatible worker output could escape into import state."""
    monkeypatch.setattr("holden_reel.focus.subprocess.run", lambda *args, **kwargs: completed)
    assert FocusAnalyzer().analyze(tmp_path / "clip.mp4", "video") == center_focus()


def test_analyzer_timeout_returns_center(monkeypatch, tmp_path):
    """Would fail if worker timeout could block or fail media import."""
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr("holden_reel.focus.subprocess.run", timeout)
    assert FocusAnalyzer().analyze(tmp_path / "clip.mp4", "video") == center_focus()
