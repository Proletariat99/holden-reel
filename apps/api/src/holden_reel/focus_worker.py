from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np

from .focus import MAX_FRAME_EDGE, MAX_VIDEO_FRAMES, MIN_FOCUS_CONFIDENCE, FocusResult, center_focus


@dataclass(frozen=True)
class Candidate:
    x: float
    y: float
    weight: float
    method: Literal["face", "person"]


class SubjectDetector(Protocol):
    def detect_faces(self, frame: np.ndarray) -> list[Candidate]: ...

    def detect_people(self, frame: np.ndarray) -> list[Candidate]: ...


class OpenCVSubjectDetector:
    def __init__(self) -> None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        self.people_detector = cv2.HOGDescriptor()
        self.people_detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect_faces(self, frame: np.ndarray) -> list[Candidate]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        return _boxes_to_candidates(boxes, frame.shape, "face", np.ones(len(boxes)))

    def detect_people(self, frame: np.ndarray) -> list[Candidate]:
        boxes, weights = self.people_detector.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        confidences = np.asarray(weights, dtype=float).reshape(-1)
        # HOG scores are unbounded margins; the sigmoid maps them into detector confidence.
        confidences = 1.0 / (1.0 + np.exp(-np.clip(confidences, -20.0, 20.0)))
        return _boxes_to_candidates(boxes, frame.shape, "person", confidences)


def _boxes_to_candidates(
    boxes: Sequence[Sequence[int]], shape: tuple[int, ...], method: Literal["face", "person"], confidences: np.ndarray
) -> list[Candidate]:
    height, width = shape[:2]
    image_area = height * width
    if image_area <= 0:
        return []
    candidates: list[Candidate] = []
    for (x, y, box_width, box_height), confidence in zip(boxes, confidences, strict=False):
        area_weight = (float(box_width) * float(box_height) / image_area) * float(confidence)
        if area_weight > 0:
            candidates.append(Candidate(
                x=min(max((float(x) + float(box_width) / 2) / width, 0.0), 1.0),
                y=min(max((float(y) + float(box_height) / 2) / height, 0.0), 1.0),
                weight=area_weight,
                method=method,
            ))
    return candidates


def choose_focus(frames: Sequence[np.ndarray], detector: SubjectDetector) -> FocusResult:
    try:
        face_candidates: list[Candidate] = []
        person_candidates: list[Candidate] = []
        for frame in frames:
            face_candidates.extend(detector.detect_faces(frame))
        if face_candidates:
            return _aggregate_candidates(face_candidates, "face", len(frames))
        for frame in frames:
            person_candidates.extend(detector.detect_people(frame))
        if person_candidates:
            return _aggregate_candidates(person_candidates, "person", len(frames))
        return _visual_fallback(frames)
    except Exception:
        return center_focus()


def _aggregate_candidates(candidates: Sequence[Candidate], method: Literal["face", "person"], frame_count: int) -> FocusResult:
    weights = np.asarray([candidate.weight for candidate in candidates], dtype=float)
    if not np.all(np.isfinite(weights)) or float(weights.sum()) < 1e-6:
        return center_focus()
    x = _weighted_median([candidate.x for candidate in candidates], weights)
    y = _weighted_median([candidate.y for candidate in candidates], weights)
    confidence = min(max(float(weights.sum()) / max(frame_count, 1), 0.0), 1.0)
    if confidence < MIN_FOCUS_CONFIDENCE:
        return center_focus()
    return FocusResult(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0), confidence, method)


def _weighted_median(values: Sequence[float], weights: np.ndarray) -> float:
    ordered = sorted(zip(values, weights, strict=True), key=lambda pair: pair[0])
    threshold = float(weights.sum()) / 2
    total = 0.0
    for value, weight in ordered:
        total += float(weight)
        if total >= threshold:
            return float(value)
    return float(ordered[-1][0])


def _visual_fallback(frames: Sequence[np.ndarray]) -> FocusResult:
    gray_frames = [_gray(frame) for frame in frames]
    if not gray_frames:
        return center_focus()
    if len(gray_frames) >= 2:
        differences = [cv2.absdiff(before, after) for before, after in zip(gray_frames, gray_frames[1:], strict=False)]
        diff_map = np.mean(np.asarray(differences, dtype=np.float32), axis=0)
        motion_energy = float(diff_map.mean()) / 255.0
        if motion_energy >= 0.01:
            edge_map = _edge_magnitude(gray_frames[-1])
            return _centroid_result(diff_map + edge_map, min(motion_energy, 1.0), "motion")
    contrast_map = _edge_magnitude(gray_frames[-1])
    contrast_energy = min(float(contrast_map.mean()) / 255.0, 1.0)
    return _centroid_result(contrast_map, contrast_energy, "contrast")


def _gray(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame


def _edge_magnitude(frame: np.ndarray) -> np.ndarray:
    gray = frame.astype(np.float32)
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    return np.abs(sobel_x) + np.abs(sobel_y) + np.abs(laplacian)


def _centroid_result(signal: np.ndarray, confidence: float, method: Literal["motion", "contrast"]) -> FocusResult:
    usable = np.asarray(signal, dtype=np.float64)
    usable[~np.isfinite(usable)] = 0.0
    usable = np.maximum(usable, 0.0)
    total_weight = float(usable.sum())
    if total_weight < 1e-6 or confidence < MIN_FOCUS_CONFIDENCE:
        return center_focus()
    y_indices, x_indices = np.indices(usable.shape)
    x = float((usable * x_indices).sum() / total_weight) / max(usable.shape[1] - 1, 1)
    y = float((usable * y_indices).sum() / total_weight) / max(usable.shape[0] - 1, 1)
    return FocusResult(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0), min(max(confidence, 0.0), 1.0), method)


def _resize(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    edge = max(height, width)
    if edge <= MAX_FRAME_EDGE:
        return frame
    scale = MAX_FRAME_EDGE / edge
    return cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)


def analyze_image(path: Path) -> FocusResult:
    frame = cv2.imread(str(path))
    if frame is None:
        return center_focus()
    return choose_focus([_resize(frame)], OpenCVSubjectDetector())


def analyze_video(path: Path) -> FocusResult:
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return center_focus()
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        frames: list[np.ndarray] = []
        if math.isfinite(frame_count) and frame_count > 0:
            for index in (round(i * (frame_count - 1) / (MAX_VIDEO_FRAMES - 1)) for i in range(MAX_VIDEO_FRAMES)):
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if ok and frame is not None:
                    frames.append(_resize(frame))
        else:
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(_resize(frame))
        return choose_focus(frames, OpenCVSubjectDetector()) if frames else center_focus()
    finally:
        capture.release()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=("image", "video"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.path.is_absolute():
        _parser().error("--path must be absolute")
    try:
        result = analyze_image(args.path) if args.kind == "image" else analyze_video(args.path)
    except (cv2.error, OSError, ValueError):
        result = center_focus()
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
