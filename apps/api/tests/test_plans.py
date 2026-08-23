from pathlib import Path
import subprocess
from uuid import UUID, uuid4

import pytest

from holden_reel.errors import DomainError
from holden_reel.media import MediaAsset
from holden_reel.plans import AudioBed, PlanValidator, ReelPlan, Shot


PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
AUDIO_ID = UUID("00000000-0000-0000-0000-000000000010")
VIDEO_ID = UUID("00000000-0000-0000-0000-000000000011")
IMAGE_ID = UUID("00000000-0000-0000-0000-000000000012")
PORTRAIT_ID = UUID("00000000-0000-0000-0000-000000000013")


@pytest.fixture
def assets() -> dict[UUID, MediaAsset]:
    return {
        AUDIO_ID: _asset(AUDIO_ID, "audio", duration_ms=20_000),
        VIDEO_ID: _asset(VIDEO_ID, "video", duration_ms=10_000, width=320, height=240),
        IMAGE_ID: _asset(IMAGE_ID, "image", duration_ms=None, width=320, height=240),
        PORTRAIT_ID: _asset(PORTRAIT_ID, "video", duration_ms=10_000, width=240, height=320),
    }


@pytest.fixture
def valid_plan() -> ReelPlan:
    return ReelPlan(
        id=uuid4(),
        project_id=PROJECT_ID,
        version=1,
        duration_ms=15_000,
        audio=AudioBed(asset_id=AUDIO_ID, source_start_ms=0, source_end_ms=15_000),
        shots=[
            Shot(
                asset_id=VIDEO_ID,
                source_start_ms=0,
                source_end_ms=7_500,
                output_start_ms=0,
                output_end_ms=7_500,
            ),
            Shot(
                asset_id=VIDEO_ID,
                source_start_ms=0,
                source_end_ms=7_500,
                output_start_ms=7_500,
                output_end_ms=15_000,
            ),
        ],
        rationale="Deterministic source order.",
    )


def _asset(
    asset_id: UUID,
    kind: str,
    *,
    duration_ms: int | None,
    width: int | None = None,
    height: int | None = None,
    available: bool = True,
) -> MediaAsset:
    return MediaAsset(
        id=asset_id,
        project_id=PROJECT_ID,
        path=Path(f"/catalog/{asset_id}"),
        kind=kind,
        duration_ms=duration_ms,
        width=width,
        height=height,
        codec="fixture",
        available=available,
        fingerprint="fixture",
    )


def test_plan_rejects_gap_between_shots(valid_plan, assets):
    """Would fail if output timeline continuity were not enforced."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[1].output_start_ms += 100

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.code == "invalid_reel_plan"
    assert error.value.details["violations"] == ["shots must cover output without gaps"]


def test_plan_rejects_overlapping_shots(valid_plan, assets):
    """Would fail if two source choices rendered into the same output interval."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[1].output_start_ms -= 100

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == ["shots must cover output without gaps"]


def test_plan_rejects_duration_outside_supported_reel_lengths(valid_plan, assets):
    """Would fail if a renderer could receive a reel length it cannot target."""
    broken = valid_plan.model_copy(update={"duration_ms": 12_000})

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == ["duration must be 15000 or 30000 milliseconds"]


def test_plan_rejects_offline_referenced_asset(valid_plan, assets):
    """Would fail if plans could render source media that is no longer available."""
    assets[VIDEO_ID] = _asset(VIDEO_ID, "video", duration_ms=10_000, available=False)

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(valid_plan, assets)

    assert error.value.details["violations"] == ["referenced assets must be available"]


def test_plan_rejects_video_source_range_beyond_media_duration(valid_plan, assets):
    """Would fail if a shot asked FFmpeg for video frames past the catalogued source."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[0].source_end_ms = 10_001

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == ["video source ranges must fit asset duration"]


def test_plan_rejects_negative_timeline_or_source_time(valid_plan, assets):
    """Would fail if invalid negative seeks reached rendering."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[0].source_start_ms = -1

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == ["times must be non-negative"]


def test_plan_rejects_audio_that_does_not_cover_output_duration(valid_plan, assets):
    """Would fail if the exported reel could end in silence."""
    broken = valid_plan.model_copy(deep=True)
    broken.audio.source_end_ms = 14_999

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == ["audio must cover output duration"]


def test_plan_requires_still_shots_to_use_null_source_ranges(valid_plan, assets):
    """Would fail if a still image were treated as seekable video."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[0] = Shot(
        asset_id=IMAGE_ID,
        source_start_ms=0,
        source_end_ms=7_500,
        output_start_ms=0,
        output_end_ms=7_500,
        still_motion="slow_zoom",
    )

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == ["still shots must use null source ranges"]


def test_plan_accepts_landscape_and_portrait_assets_with_cover_fit(valid_plan, assets):
    """Would fail if source orientation leaked into the fixed portrait output contract."""
    plan = valid_plan.model_copy(deep=True)
    plan.shots[0].asset_id = PORTRAIT_ID

    PlanValidator().validate(plan, assets)


def test_compose_persists_deterministic_shot_timing_and_sequential_versions(client, media_fixture):
    """Would fail if composition chose shots nondeterministically or did not persist versions."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    assets = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root)},
    ).json()["assets"]
    by_name = {Path(asset["path"]).name: asset for asset in assets}
    payload = {
        "duration_ms": 15_000,
        "audio_asset_id": by_name["song.wav"]["id"],
        "audio_start_ms": 0,
        "visual_asset_ids": [by_name["red.mp4"]["id"], by_name["blue.mp4"]["id"]],
    }

    first = client.post(f"/api/projects/{project['id']}/plans/compose", json=payload)
    second = client.post(f"/api/projects/{project['id']}/plans/compose", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    first_plan = first.json()
    second_plan = second.json()
    assert first_plan["id"] != second_plan["id"]
    assert (first_plan["version"], second_plan["version"]) == (1, 2)
    assert [(shot["asset_id"], shot["source_start_ms"], shot["source_end_ms"], shot["output_start_ms"], shot["output_end_ms"]) for shot in first_plan["shots"]] == [
        (by_name["red.mp4"]["id"], 0, 3000, 0, 3000),
        (by_name["blue.mp4"]["id"], 0, 3000, 3000, 6000),
        (by_name["red.mp4"]["id"], 0, 3000, 6000, 9000),
        (by_name["blue.mp4"]["id"], 0, 3000, 9000, 12000),
        (by_name["red.mp4"]["id"], 0, 3000, 12000, 15000),
    ]
    assert second_plan["shots"] == first_plan["shots"]
    assert client.get(f"/api/plans/{first_plan['id']}").json() == first_plan


def test_compose_rejects_sources_that_cannot_fill_a_shot(client, media_fixture):
    """Would fail if composition emitted a video shot shorter than its output interval."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    short_video = media_fixture.root / "short.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(short_video),
        ],
        check=True,
        capture_output=True,
        shell=False,
    )
    assets = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(short_video)},
    ).json()["assets"]
    short_asset = assets[0]
    audio = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.paths["song.wav"])},
    ).json()["assets"][0]

    response = client.post(
        f"/api/projects/{project['id']}/plans/compose",
        json={
            "duration_ms": 15_000,
            "audio_asset_id": audio["id"],
            "audio_start_ms": 0,
            "visual_asset_ids": [short_asset["id"]],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_usable_media"
