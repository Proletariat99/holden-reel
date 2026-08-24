import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
from uuid import uuid4

import pytest

from holden_reel.db import open_database
from holden_reel.focus import FOCUS_ANALYZER_VERSION, center_focus
from holden_reel.media import FFprobe


FOCUS_COLUMNS = {
    "focus_x",
    "focus_y",
    "focus_confidence",
    "focus_method",
    "focus_analyzer_version",
    "focus_fingerprint",
}


def _create_version_5_database(database_path: Path):
    project_id = uuid4()
    asset_id = uuid4()
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);
        INSERT INTO schema_migrations (version) VALUES (1), (2), (3), (4), (5);
        CREATE TABLE projects (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE media_assets (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL REFERENCES projects(id),
          path TEXT NOT NULL,
          kind TEXT NOT NULL,
          duration_ms INTEGER,
          width INTEGER,
          height INTEGER,
          codec TEXT,
          available INTEGER NOT NULL,
          size_bytes INTEGER NOT NULL,
          modified_ns INTEGER NOT NULL,
          fingerprint TEXT NOT NULL,
          has_audio INTEGER NOT NULL DEFAULT 0,
          audio_duration_ms INTEGER,
          UNIQUE(project_id, path)
        );
        """
    )
    connection.execute(
        "INSERT INTO projects VALUES (?, 'Existing', 'created', 'updated')",
        (str(project_id),),
    )
    connection.execute(
        """
        INSERT INTO media_assets VALUES (
          ?, ?, '/existing/clip.mp4', 'video', 4000, 320, 240, 'h264',
          1, 1234, 5678, 'old-fingerprint', 1, 4000
        )
        """,
        (str(asset_id), str(project_id)),
    )
    connection.commit()
    connection.close()
    return project_id, asset_id


def test_migration_6_adds_nullable_focus_columns_without_altering_old_rows(tmp_path):
    """Would fail if migration 6 omitted metadata or rewrote a version-5 asset."""
    database_path = tmp_path / "version-5.sqlite3"
    project_id, asset_id = _create_version_5_database(database_path)

    migrated = open_database(database_path)
    columns = {
        row[1] for row in migrated.execute("PRAGMA table_info(media_assets)").fetchall()
    }
    row = migrated.execute(
        "SELECT * FROM media_assets WHERE id = ?", (str(asset_id),)
    ).fetchone()
    versions = migrated.execute(
        "SELECT version FROM schema_migrations WHERE version = 6"
    ).fetchall()

    assert FOCUS_COLUMNS <= columns
    assert tuple(row[:14]) == (
        str(asset_id),
        str(project_id),
        "/existing/clip.mp4",
        "video",
        4000,
        320,
        240,
        "h264",
        1,
        1234,
        5678,
        "old-fingerprint",
        1,
        4000,
    )
    assert tuple(row[14:]) == (None, None, None, None, None, None)
    assert [version["version"] for version in versions] == [6]
    migrated.close()


def test_migration_6_rolls_back_partial_failure_and_succeeds_on_reopen(
    tmp_path, monkeypatch
):
    """Would fail if interrupted DDL stranded partial columns without version 6."""
    database_path = tmp_path / "interrupted-version-5.sqlite3"
    _create_version_5_database(database_path)
    real_connect = sqlite3.connect
    failure_pending = True
    opened_connections = []

    class FailingMigrationConnection(sqlite3.Connection):
        def execute(self, query, parameters=()):
            nonlocal failure_pending
            if failure_pending and query == (
                "ALTER TABLE media_assets ADD COLUMN focus_y REAL"
            ):
                failure_pending = False
                raise sqlite3.OperationalError("injected migration failure")
            return super().execute(query, parameters)

    def connect_with_one_failure(*args, **kwargs):
        kwargs["factory"] = FailingMigrationConnection
        connection = real_connect(*args, **kwargs)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr("holden_reel.db.sqlite3.connect", connect_with_one_failure)

    with pytest.raises(sqlite3.OperationalError, match="injected migration failure"):
        open_database(database_path)
    opened_connections[-1].close()

    with real_connect(database_path) as inspection:
        columns_after_failure = {
            row[1]
            for row in inspection.execute("PRAGMA table_info(media_assets)").fetchall()
        }
        version_after_failure = inspection.execute(
            "SELECT version FROM schema_migrations WHERE version = 6"
        ).fetchall()

    assert FOCUS_COLUMNS.isdisjoint(columns_after_failure)
    assert version_after_failure == []

    reopened = open_database(database_path)
    columns_after_reopen = {
        row[1] for row in reopened.execute("PRAGMA table_info(media_assets)").fetchall()
    }
    versions_after_reopen = reopened.execute(
        "SELECT version FROM schema_migrations WHERE version = 6"
    ).fetchall()

    assert FOCUS_COLUMNS <= columns_after_reopen
    assert [version["version"] for version in versions_after_reopen] == [6]
    reopened.close()


def test_repository_finds_imported_asset_by_resolved_path(media_service_harness, tmp_path):
    """Would fail if focus caching could not load the existing path-keyed asset."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    imported = media_service_harness.service.import_path(
        media_service_harness.project_id, source.resolve()
    )[0]

    found = media_service_harness.repository.find_by_path(
        media_service_harness.project_id, source
    )

    assert found == imported


def test_import_reuses_focus_until_the_source_fingerprint_changes(
    media_service_harness, tmp_path
):
    """Would fail if cache keys were ignored or fresh source metadata reused stale focus."""
    source = (tmp_path / "clip.mp4").resolve()
    source.write_bytes(b"video")

    first = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]
    second = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]

    assert media_service_harness.analyzer.calls == [(source.resolve(), "video")]
    assert second.focus_fingerprint == first.fingerprint
    assert second.id == first.id

    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    third = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]

    assert len(media_service_harness.analyzer.calls) == 2
    assert third.focus_fingerprint == third.fingerprint
    assert third.fingerprint != first.fingerprint
    assert third.id == first.id


def test_missing_and_stale_focus_versions_trigger_analysis(media_service_harness, tmp_path):
    """Would fail if analyzer-version changes could reuse incompatible cached focus."""
    source = (tmp_path / "clip.mp4").resolve()
    source.write_bytes(b"video")
    media_service_harness.service.import_path(media_service_harness.project_id, source)

    with media_service_harness.database.transaction() as connection:
        connection.execute(
            "UPDATE media_assets SET focus_analyzer_version = NULL WHERE path = ?",
            (str(source),),
        )
    missing = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]

    with media_service_harness.database.transaction() as connection:
        connection.execute(
            "UPDATE media_assets SET focus_analyzer_version = ? WHERE path = ?",
            (FOCUS_ANALYZER_VERSION - 1, str(source)),
        )
    stale = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]

    assert len(media_service_harness.analyzer.calls) == 3
    assert missing.focus_analyzer_version == FOCUS_ANALYZER_VERSION
    assert stale.focus_analyzer_version == FOCUS_ANALYZER_VERSION


def test_center_fallback_is_persisted_as_a_valid_cached_result(
    media_service_harness, tmp_path
):
    """Would fail if a safe center result were treated as missing and repeatedly analyzed."""
    source = (tmp_path / "still.jpg").resolve()
    source.write_bytes(b"image")
    media_service_harness.analyzer.result = center_focus()

    first = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]
    second = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]

    assert len(media_service_harness.analyzer.calls) == 1
    assert (
        first.focus_x,
        first.focus_y,
        first.focus_confidence,
        first.focus_method,
        first.focus_analyzer_version,
        first.focus_fingerprint,
    ) == (0.5, 0.5, 0.0, "center", FOCUS_ANALYZER_VERSION, first.fingerprint)
    assert second == first


def test_audio_assets_leave_focus_metadata_null_without_analysis(
    media_service_harness, tmp_path
):
    """Would fail if nonvisual audio invoked focus analysis or persisted visual metadata."""
    source = (tmp_path / "song.wav").resolve()
    source.write_bytes(b"audio")

    asset = media_service_harness.service.import_path(
        media_service_harness.project_id, source
    )[0]

    assert media_service_harness.analyzer.calls == []
    assert (
        asset.focus_x,
        asset.focus_y,
        asset.focus_confidence,
        asset.focus_method,
        asset.focus_analyzer_version,
        asset.focus_fingerprint,
    ) == (None, None, None, None, None, None)


def test_ffprobe_uses_a_bounded_timeout(monkeypatch, tmp_path):
    """Would fail if a corrupt source could block an import forever."""
    calls = []
    monkeypatch.setattr("holden_reel.media.subprocess.run", lambda *a, **kw: calls.append(kw) or subprocess.CompletedProcess(a[0], 1, "", ""))
    FFprobe("ffprobe").probe(tmp_path / "clip.mp4")
    assert calls[0]["timeout"] == 30


def test_ffprobe_timeout_is_actionable(monkeypatch, tmp_path):
    """Would fail if an import timeout surfaced as an opaque subprocess exception."""
    monkeypatch.setattr(
        "holden_reel.media.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffprobe", 30)),
    )
    with pytest.raises(RuntimeError, match="timed out after 30 seconds"):
        FFprobe("ffprobe").probe(tmp_path / "clip.mp4")


def test_ffprobe_classifies_generated_webm_and_covered_audio(tmp_path, ffmpeg_bins):
    """Would fail if video audio or container duration metadata were lost during probing."""
    ffmpeg, ffprobe = ffmpeg_bins
    webm = tmp_path / "clip.webm"
    mp3 = tmp_path / "covered.mp3"
    m4a = tmp_path / "covered.m4a"
    cover = tmp_path / "cover.png"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "color=red:s=64x64:d=1.25",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.25",
            "-c:v", "libvpx-vp9", "-c:a", "libopus", "-shortest", str(webm),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=blue:s=64x64", "-frames:v", "1", str(cover)], check=True, capture_output=True, timeout=20)
    for output, codec in ((mp3, "libmp3lame"), (m4a, "aac")):
        subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:a", codec, "-c:v", "png", "-disposition:v", "attached_pic", str(output)], check=True, capture_output=True, timeout=20)
    probe = FFprobe(ffprobe)
    webm_result = probe.probe(webm)
    assert webm_result.kind == "video"
    assert webm_result.duration_ms is not None and abs(webm_result.duration_ms - 1250) < 100
    assert webm_result.has_audio is True
    assert webm_result.audio_duration_ms is not None
    assert abs(webm_result.audio_duration_ms - 1250) < 100
    for path in (mp3, m4a):
        result = probe.probe(path)
        assert result.kind == "audio"
        assert result.has_audio is True
        assert result.duration_ms is not None and abs(result.duration_ms - 2000) < 100
        assert result.audio_duration_ms == result.duration_ms


def test_ffprobe_extracts_stream_metadata(media_fixture):
    """Would fail if stream probing misclassified source media or lost metadata."""
    probe = FFprobe("ffprobe")

    video = probe.probe(media_fixture.paths["red.mp4"])
    image = probe.probe(media_fixture.paths["still.jpg"])
    audio = probe.probe(media_fixture.paths["song.wav"])

    assert (video.kind, video.duration_ms, video.width, video.height, video.codec) == (
        "video", 4000, 320, 240, "h264"
    )
    assert (image.kind, image.duration_ms, image.width, image.height) == (
        "image", None, 320, 240
    )
    assert (audio.kind, audio.duration_ms, audio.codec) == ("audio", 18000, "pcm_s16le")


def test_import_folder_catalogs_supported_media_without_copying(client, media_fixture):
    """Would fail if import copied media rather than catalogued resolved sources."""
    original_stats = {
        name: (path.stat().st_size, path.stat().st_mtime_ns)
        for name, path in media_fixture.paths.items()
    }
    project = client.post("/api/projects", json={"name": "Fixture"}).json()

    response = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root)},
    )

    assert response.status_code == 201
    assets = response.json()["assets"]
    assert {asset["kind"] for asset in assets} == {"video", "image", "audio"}
    assert len(assets) == 4
    assert all(Path(asset["path"]).is_relative_to(media_fixture.root) for asset in assets)
    assert [Path(asset["path"]).name for asset in assets] == [
        "blue.mp4",
        "red.mp4",
        "song.wav",
        "still.jpg",
    ]
    assert {
        name: (path.stat().st_size, path.stat().st_mtime_ns)
        for name, path in media_fixture.paths.items()
    } == original_stats


def test_import_and_list_json_include_normalized_focus_metadata(
    client, media_fixture, focus_analyzer
):
    """Would fail if API serialization dropped persisted normalized focus metadata."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    source = media_fixture.paths["red.mp4"]

    imported = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(source)},
    )
    listed = client.get(f"/api/projects/{project['id']}/media")

    assert imported.status_code == 201
    assert listed.status_code == 200
    expected_focus = {
        "focus_x": 0.25,
        "focus_y": 0.75,
        "focus_confidence": 0.8,
        "focus_method": "face",
        "focus_analyzer_version": FOCUS_ANALYZER_VERSION,
    }
    imported_asset = imported.json()["assets"][0]
    listed_asset = listed.json()["assets"][0]
    assert {key: imported_asset[key] for key in expected_focus} == expected_focus
    assert {key: listed_asset[key] for key in expected_focus} == expected_focus
    assert imported_asset["focus_fingerprint"] == imported_asset["fingerprint"]
    assert listed_asset["focus_fingerprint"] == listed_asset["fingerprint"]
    assert all(0.0 <= imported_asset[key] <= 1.0 for key in (
        "focus_x", "focus_y", "focus_confidence"
    ))
    assert focus_analyzer.calls == [(source.resolve(), "video")]


def test_reimport_refreshes_path_size_mtime_fingerprint(client, media_fixture):
    """Would fail if fingerprints omitted source metadata or used file contents."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    source = media_fixture.paths["red.mp4"]
    url = f"/api/projects/{project['id']}/media/import"

    initial = client.post(url, json={"path": str(source)})
    initial_stat = source.stat()
    expected_initial = hashlib.sha256(
        f"{source.resolve()}\0{initial_stat.st_size}\0{initial_stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()

    assert initial.status_code == 201
    initial_asset = initial.json()["assets"][0]
    assert initial_asset["fingerprint"] == expected_initial

    os.utime(
        source,
        ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns + 1_000_000_000),
    )
    updated = client.post(url, json={"path": str(source)})
    updated_stat = source.stat()
    expected_updated = hashlib.sha256(
        f"{source.resolve()}\0{updated_stat.st_size}\0{updated_stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()

    assert updated.status_code == 201
    updated_asset = updated.json()["assets"][0]
    assert updated_asset["id"] == initial_asset["id"]
    assert updated_asset["fingerprint"] == expected_updated
    assert updated_asset["fingerprint"] != initial_asset["fingerprint"]


def test_import_missing_path_returns_not_found_error(client, tmp_path):
    """Would fail if a nonexistent source path were accepted or misclassified."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()

    response = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str((tmp_path / "missing").resolve())},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_path_not_found"


def test_import_rejects_relative_source_path(client, media_fixture, monkeypatch):
    """Would fail if the API accepted a path that cannot be stored canonically."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    monkeypatch.chdir(media_fixture.root.parent)

    response = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": media_fixture.root.name},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "media_path_must_be_absolute"


def test_import_selected_unsupported_file_returns_domain_error(client, media_fixture):
    """Would fail if a selected unsupported file were silently accepted."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()

    response = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root / "unsupported.txt")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_media"


def test_import_directory_ignores_unsupported_files(client, media_fixture):
    """Would fail if directory import attempted unsupported text files."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()

    response = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root)},
    )

    assert response.status_code == 201
    assert {Path(asset["path"]).name for asset in response.json()["assets"]} == {
        "red.mp4",
        "blue.mp4",
        "still.jpg",
        "song.wav",
    }


def test_reimporting_a_folder_returns_existing_assets_once(client, media_fixture):
    """Would fail if a repeated import created duplicate catalog records."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    url = f"/api/projects/{project['id']}/media/import"

    first = client.post(url, json={"path": str(media_fixture.root)})
    second = client.post(url, json={"path": str(media_fixture.root)})
    listed = client.get(f"/api/projects/{project['id']}/media")

    assert first.status_code == 201
    assert second.status_code == 201
    assert len(second.json()["assets"]) == 4
    assert len(listed.json()["assets"]) == 4
    assert {asset["id"] for asset in second.json()["assets"]} == {
        asset["id"] for asset in first.json()["assets"]
    }


def test_listing_marks_renamed_source_unavailable(client, media_fixture):
    """Would fail if listing returned stale availability after a source vanishes."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root)},
    )
    media_fixture.paths["red.mp4"].rename(media_fixture.root / "red-renamed.mp4")

    response = client.get(f"/api/projects/{project['id']}/media")

    assert response.status_code == 200
    by_name = {Path(asset["path"]).name: asset for asset in response.json()["assets"]}
    assert by_name["red.mp4"]["available"] is False
