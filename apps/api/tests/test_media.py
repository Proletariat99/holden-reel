import hashlib
import os
from pathlib import Path
import subprocess
import pytest

from holden_reel.media import FFprobe


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
