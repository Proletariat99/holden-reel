from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .db import Database
from .errors import DomainError
from .projects import ProjectService


SUPPORTED_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".webm", ".jpg", ".jpeg", ".png", ".wav", ".mp3", ".m4a", ".aac"
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class ProbeResult:
    kind: str
    duration_ms: int | None
    width: int | None
    height: int | None
    codec: str | None


@dataclass(frozen=True)
class MediaAsset:
    id: UUID
    project_id: UUID
    path: Path
    kind: str
    duration_ms: int | None
    width: int | None
    height: int | None
    codec: str | None
    available: bool
    fingerprint: str


class FFprobe:
    def __init__(self, ffprobe_bin: str):
        self.ffprobe_bin = ffprobe_bin

    def probe(self, path: Path) -> ProbeResult:
        try:
            completed = subprocess.run(
                [self.ffprobe_bin, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"Media inspection timed out after 30 seconds: {path.name}") from error
        if completed.returncode != 0:
            return ProbeResult("unsupported_media", None, None, None, None)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ProbeResult("unsupported_media", None, None, None, None)

        streams = payload.get("streams", [])
        video_streams = [
            stream for stream in streams
            if stream.get("codec_type") == "video" and not _is_attached_picture(stream)
        ]
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if path.suffix.casefold() in IMAGE_SUFFIXES and video_streams:
            return _probe_result("image", video_streams[0], None)
        format_duration = payload.get("format", {}).get("duration")
        timed_video = next((stream for stream in video_streams if not _is_single_frame(stream)), None)
        if timed_video is not None:
            return _probe_result("video", timed_video, timed_video.get("duration") or format_duration)
        image_stream = next(
            (stream for stream in video_streams if _is_single_frame(stream) or path.suffix.casefold() in IMAGE_SUFFIXES),
            None,
        )
        if image_stream is not None:
            return _probe_result("image", image_stream, None)
        if audio_streams:
            audio_stream = audio_streams[0]
            duration = audio_stream.get("duration") or payload.get("format", {}).get("duration")
            return _probe_result("audio", audio_stream, duration)
        return ProbeResult("unsupported_media", None, None, None, None)


def _duration_ms(value: object) -> int | None:
    if value is None:
        return None
    try:
        duration = float(str(value))
    except ValueError:
        return None
    return round(duration * 1000) if duration > 0 else None


def _is_single_frame(stream: dict) -> bool:
    return str(stream.get("nb_frames", "")) == "1"


def _is_attached_picture(stream: dict) -> bool:
    return bool(stream.get("disposition", {}).get("attached_pic"))


def _probe_result(kind: str, stream: dict, duration: object) -> ProbeResult:
    return ProbeResult(
        kind=kind,
        duration_ms=_duration_ms(duration),
        width=stream.get("width"),
        height=stream.get("height"),
        codec=stream.get("codec_name"),
    )


class MediaRepository:
    def __init__(self, database: Database):
        self.database = database

    def save(self, asset: MediaAsset, size_bytes: int, modified_ns: int) -> MediaAsset:
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM media_assets WHERE project_id = ? AND path = ?",
                (str(asset.project_id), str(asset.path)),
            ).fetchone()
            if existing is not None:
                asset = MediaAsset(id=UUID(existing["id"]), **_asset_fields(asset))
                connection.execute(
                    """
                    UPDATE media_assets SET kind = ?, duration_ms = ?, width = ?, height = ?, codec = ?,
                    available = ?, size_bytes = ?, modified_ns = ?, fingerprint = ? WHERE id = ?
                    """,
                    (asset.kind, asset.duration_ms, asset.width, asset.height, asset.codec,
                     int(asset.available), size_bytes, modified_ns, asset.fingerprint, str(asset.id)),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO media_assets (
                      id, project_id, path, kind, duration_ms, width, height, codec,
                      available, size_bytes, modified_ns, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(asset.id), str(asset.project_id), str(asset.path), asset.kind,
                     asset.duration_ms, asset.width, asset.height, asset.codec, int(asset.available),
                     size_bytes, modified_ns, asset.fingerprint),
                )
        return asset

    def list_for_project(self, project_id: UUID) -> list[MediaAsset]:
        rows = self.database.fetch_all(
            "SELECT * FROM media_assets WHERE project_id = ? ORDER BY path COLLATE NOCASE",
            (str(project_id),),
        )
        return [self._to_asset(row) for row in rows]

    def set_available(self, asset_id: UUID, available: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE media_assets SET available = ? WHERE id = ?", (int(available), str(asset_id))
            )

    @staticmethod
    def _to_asset(row) -> MediaAsset:
        return MediaAsset(
            id=UUID(row["id"]), project_id=UUID(row["project_id"]), path=Path(row["path"]),
            kind=row["kind"], duration_ms=row["duration_ms"], width=row["width"],
            height=row["height"], codec=row["codec"], available=bool(row["available"]),
            fingerprint=row["fingerprint"],
        )


def _asset_fields(asset: MediaAsset) -> dict:
    return {
        "project_id": asset.project_id, "path": asset.path, "kind": asset.kind,
        "duration_ms": asset.duration_ms, "width": asset.width, "height": asset.height,
        "codec": asset.codec, "available": asset.available, "fingerprint": asset.fingerprint,
    }


class MediaService:
    def __init__(self, repository: MediaRepository, projects: ProjectService, ffprobe: FFprobe):
        self.repository = repository
        self.projects = projects
        self.ffprobe = ffprobe

    def import_path(self, project_id: UUID, path: Path) -> list[MediaAsset]:
        self.projects.get(project_id)
        if not path.is_absolute():
            raise DomainError("media_path_must_be_absolute", "Media path must be absolute", status_code=422)
        resolved_path = path.resolve()
        if not resolved_path.exists():
            raise DomainError("media_path_not_found", "Media path was not found", status_code=404)
        assets: list[MediaAsset] = []
        for candidate in self._candidates(resolved_path):
            probe = self.ffprobe.probe(candidate)
            if probe.kind == "unsupported_media":
                if resolved_path.is_dir():
                    continue
                raise DomainError("unsupported_media", "Media file is unsupported", status_code=422)
            stat = candidate.stat()
            asset = MediaAsset(
                id=uuid4(), project_id=project_id, path=candidate, kind=probe.kind,
                duration_ms=probe.duration_ms, width=probe.width, height=probe.height, codec=probe.codec,
                available=True, fingerprint=_fingerprint(candidate, stat.st_size, stat.st_mtime_ns),
            )
            assets.append(self.repository.save(asset, stat.st_size, stat.st_mtime_ns))
        return assets

    def list(self, project_id: UUID) -> list[MediaAsset]:
        self.projects.get(project_id)
        refreshed: list[MediaAsset] = []
        for asset in self.repository.list_for_project(project_id):
            available = asset.path.exists()
            if available != asset.available:
                self.repository.set_available(asset.id, available)
                asset = MediaAsset(id=asset.id, **(_asset_fields(asset) | {"available": available}))
            refreshed.append(asset)
        return refreshed

    @staticmethod
    def _candidates(path: Path) -> list[Path]:
        if path.is_file():
            return [path]
        return sorted(
            (candidate.resolve() for candidate in path.iterdir()
             if candidate.is_file() and candidate.suffix.casefold() in SUPPORTED_SUFFIXES),
            key=lambda candidate: str(candidate).casefold(),
        )


def _fingerprint(path: Path, size_bytes: int, modified_ns: int) -> str:
    source = f"{path.resolve()}\0{size_bytes}\0{modified_ns}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()
