from pathlib import Path
import subprocess
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from holden_reel.errors import DomainError
from holden_reel.focus import FocusResult
from holden_reel.media import MediaAsset
from holden_reel.plans import (
    AudioBed,
    ComposePlanRequest,
    PlanService,
    PlanValidator,
    ReelPlan,
    Shot,
)


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


def test_old_plan_json_defaults_to_cut_and_center_focus(valid_plan):
    """Would fail if persisted plans from before focus and transitions could not reload."""
    old_plan_json_without_new_fields = valid_plan.model_dump_json(
        exclude={
            "transition_style": True,
            "shots": {"__all__": {"focus_x", "focus_y", "focus_method"}},
        }
    )

    old = ReelPlan.model_validate_json(old_plan_json_without_new_fields)

    assert old.transition_style == "cut"
    assert [(shot.focus_x, shot.focus_y, shot.focus_method) for shot in old.shots] == [
        (0.5, 0.5, "center"),
        (0.5, 0.5, "center"),
    ]


def test_plan_accepts_exact_dissolve_overlap(valid_plan, assets):
    """Would fail if a dissolve plan could not overlap adjacent shots by exactly 200 ms."""
    dissolve = valid_plan.model_copy(update={"transition_style": "dissolve"}, deep=True)
    dissolve.shots[0].asset_id = IMAGE_ID
    dissolve.shots[0].source_start_ms = None
    dissolve.shots[0].source_end_ms = None
    dissolve.shots[0].still_motion = "slow_zoom"
    dissolve.shots[1].asset_id = IMAGE_ID
    dissolve.shots[1].source_start_ms = None
    dissolve.shots[1].source_end_ms = None
    dissolve.shots[1].still_motion = "slow_zoom"
    dissolve.shots[1].output_start_ms = dissolve.shots[0].output_end_ms - 200

    PlanValidator().validate(dissolve, assets)


def test_cut_plan_rejects_any_overlap(valid_plan, assets):
    """Would fail if cut plans admitted ambiguous overlapping output intervals."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[1].output_start_ms -= 1

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert "shot boundaries must match transition overlap" in error.value.details["violations"]


@pytest.mark.parametrize("overlap_ms", [199, 201])
def test_dissolve_plan_rejects_inexact_overlap(valid_plan, assets, overlap_ms):
    """Would fail if dissolve timing drifted from its exact 200 ms contract."""
    broken = valid_plan.model_copy(update={"transition_style": "dissolve"}, deep=True)
    broken.shots[1].output_start_ms = broken.shots[0].output_end_ms - overlap_ms

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert "shot boundaries must match transition overlap" in error.value.details["violations"]


def test_plan_requires_first_shot_to_start_at_zero(valid_plan, assets):
    """Would fail if a reel could begin without a visual at output time zero."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[0].output_start_ms = 1

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert "shot boundaries must match transition overlap" in error.value.details["violations"]


def test_plan_requires_final_shot_to_end_at_reel_duration(valid_plan, assets):
    """Would fail if the visual timeline could end before the requested reel."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[-1].output_end_ms -= 1
    broken.shots[-1].source_end_ms -= 1

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert "shots must end at output duration" in error.value.details["violations"]


@pytest.mark.parametrize(("field", "value"), [("focus_x", -0.01), ("focus_x", 1.01), ("focus_y", -0.01), ("focus_y", 1.01)])
def test_shot_rejects_focus_coordinates_outside_unit_interval(field, value):
    """Would fail if a persisted crop target could fall outside the source frame."""
    values = {
        "asset_id": VIDEO_ID,
        "source_start_ms": 0,
        "source_end_ms": 1_000,
        "output_start_ms": 0,
        "output_end_ms": 1_000,
        field: value,
    }

    with pytest.raises(ValidationError):
        Shot(**values)


def test_dissolve_plan_rejects_shot_not_longer_than_overlap(valid_plan, assets):
    """Would fail if a dissolve consumed an entire shot before it could be displayed."""
    broken = valid_plan.model_copy(update={"transition_style": "dissolve"}, deep=True)
    broken.shots[0].output_end_ms = 200
    broken.shots[0].source_end_ms = 200
    broken.shots[1].output_start_ms = 0

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert "shots must be longer than the transition overlap" in error.value.details["violations"]


def _asset(
    asset_id: UUID,
    kind: str,
    *,
    duration_ms: int | None,
    width: int | None = None,
    height: int | None = None,
    available: bool = True,
    has_audio: bool | None = None,
    audio_duration_ms: int | None = None,
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
        has_audio=kind == "audio" if has_audio is None else has_audio,
        audio_duration_ms=duration_ms if kind == "audio" else audio_duration_ms,
    )


def test_plan_accepts_embedded_video_audio_as_its_soundtrack(valid_plan, assets):
    """Would fail if a video with a usable audio stream could not supply the audio bed."""
    assets[VIDEO_ID] = _asset(
        VIDEO_ID,
        "video",
        duration_ms=20_000,
        width=320,
        height=240,
        has_audio=True,
        audio_duration_ms=20_000,
    )
    plan = valid_plan.model_copy(deep=True)
    plan.audio.asset_id = VIDEO_ID

    PlanValidator().validate(plan, assets)


def test_compose_request_defaults_to_cut():
    """Would fail if callers that predate transitions changed rendering semantics."""
    request = ComposePlanRequest(
        duration_ms=15_000,
        audio_asset_id=AUDIO_ID,
        audio_start_ms=0,
        visual_asset_ids=[VIDEO_ID],
    )

    assert request.transition_style == "cut"


def test_composed_shots_fall_back_to_center_for_legacy_assets_without_focus():
    """Would fail if legacy catalog rows with null focus could not produce frozen shots."""
    legacy_image = _asset(IMAGE_ID, "image", duration_ms=None, width=320, height=240)

    shots = PlanService._compose_shots([legacy_image], 15_000, 5, 0)

    assert [(shot.focus_x, shot.focus_y, shot.focus_method) for shot in shots] == [
        (0.5, 0.5, "center"),
    ] * 5


def test_plan_rejects_gap_between_shots(valid_plan, assets):
    """Would fail if output timeline continuity were not enforced."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[1].output_start_ms += 100

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.code == "invalid_reel_plan"
    assert error.value.details["violations"] == [
        "shot boundaries must match transition overlap"
    ]


def test_plan_rejects_overlapping_shots(valid_plan, assets):
    """Would fail if two source choices rendered into the same output interval."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[1].output_start_ms -= 100

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == [
        "shot boundaries must match transition overlap"
    ]


def test_plan_rejects_duration_outside_supported_reel_lengths(valid_plan, assets):
    """Would fail if a renderer could receive a reel length it cannot target."""
    broken = valid_plan.model_copy(update={"duration_ms": 12_000})

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == [
        "duration must be 15000 or 30000 milliseconds",
        "shots must end at output duration",
    ]


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


def test_plan_rejects_video_source_range_shorter_than_output(valid_plan, assets):
    """Would fail if rendering had to invent a frame to fill a video shot's output range."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[0].source_end_ms = 7_499

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == [
        "video source range duration must equal output duration"
    ]


def test_plan_rejects_video_source_range_longer_than_output(valid_plan, assets):
    """Would fail if rendering had to make an implicit trim inside a video shot."""
    broken = valid_plan.model_copy(deep=True)
    broken.shots[0].source_end_ms = 7_501

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.details["violations"] == [
        "video source range duration must equal output duration"
    ]


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
    assert first_plan["transition_style"] == "cut"
    assert [
        (shot["output_start_ms"], shot["output_end_ms"])
        for shot in first_plan["shots"]
    ] == [
        (0, 3000),
        (3000, 6000),
        (6000, 9000),
        (9000, 12000),
        (12000, 15000),
    ]
    assert [(shot["asset_id"], shot["source_start_ms"], shot["source_end_ms"], shot["output_start_ms"], shot["output_end_ms"]) for shot in first_plan["shots"]] == [
        (by_name["red.mp4"]["id"], 0, 3000, 0, 3000),
        (by_name["blue.mp4"]["id"], 0, 3000, 3000, 6000),
        (by_name["red.mp4"]["id"], 0, 3000, 6000, 9000),
        (by_name["blue.mp4"]["id"], 0, 3000, 9000, 12000),
        (by_name["red.mp4"]["id"], 0, 3000, 12000, 15000),
    ]
    assert second_plan["shots"] == first_plan["shots"]
    assert client.get(f"/api/plans/{first_plan['id']}").json() == first_plan


def test_compose_persists_dissolve_timing_and_each_assets_frozen_focus(
    client, media_fixture, focus_analyzer
):
    """Would fail if dissolve timing or selected focus changed after plan persistence."""
    project = client.post("/api/projects", json={"name": "Focused dissolve"}).json()
    focus_analyzer.result = FocusResult(0.1, 0.2, 0.7, "face")
    red = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.paths["red.mp4"])},
    ).json()["assets"][0]
    focus_analyzer.result = FocusResult(0.8, 0.9, 0.6, "motion")
    blue = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.paths["blue.mp4"])},
    ).json()["assets"][0]
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
            "visual_asset_ids": [red["id"], blue["id"]],
            "transition_style": "dissolve",
        },
    )

    assert response.status_code == 201
    plan = response.json()
    assert plan["transition_style"] == "dissolve"
    assert [(shot["output_start_ms"], shot["output_end_ms"]) for shot in plan["shots"]] == [
        (0, 3160),
        (2960, 6120),
        (5920, 9080),
        (8880, 12040),
        (11840, 15000),
    ]
    assert [
        (shot["asset_id"], shot["focus_x"], shot["focus_y"], shot["focus_method"])
        for shot in plan["shots"]
    ] == [
        (red["id"], 0.1, 0.2, "face"),
        (blue["id"], 0.8, 0.9, "motion"),
        (red["id"], 0.1, 0.2, "face"),
        (blue["id"], 0.8, 0.9, "motion"),
        (red["id"], 0.1, 0.2, "face"),
    ]
    assert client.get(f"/api/plans/{plan['id']}").json() == plan


@pytest.mark.parametrize(
    ("transition_style", "visual_count"), [("cut", 26), ("dissolve", 38)]
)
def test_compose_rejects_more_shots_than_minimum_duration_allows(
    client, media_fixture, transition_style, visual_count
):
    """Would fail if deterministic allocation emitted shots shorter than 600 ms."""
    project = client.post("/api/projects", json={"name": "Too many shots"}).json()
    image = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.paths["still.jpg"])},
    ).json()["assets"][0]
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
            "visual_asset_ids": [image["id"]] * visual_count,
            "transition_style": transition_style,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_usable_media"


def test_compose_rejects_video_that_cannot_fill_complete_dissolve_shot(
    client, media_fixture
):
    """Would fail if a video's usable duration excluded its overlapped shot portion."""
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    short_video = media_fixture.root / "short.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=3",
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
            "transition_style": "dissolve",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_usable_media"


def test_compose_rejects_huge_unsupported_duration_before_composition(client):
    """Would fail if an unsupported duration could enter the shot-composition loop."""
    class CompositionMustNotRun:
        def compose(self, *_):
            raise AssertionError("PlanService.compose must not run for an invalid request")

    client.app.state.plan_service = CompositionMustNotRun()

    response = client.post(
        f"/api/projects/{uuid4()}/plans/compose",
        json={
            "duration_ms": 1_000_000_000,
            "audio_asset_id": str(uuid4()),
            "audio_start_ms": 0,
            "visual_asset_ids": [str(uuid4())],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["message"] == "Request validation failed"
    assert response.json()["error"]["details"]["errors"][0]["loc"] == ["body", "duration_ms"]
