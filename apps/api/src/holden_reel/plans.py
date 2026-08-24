from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel

from .db import Database
from .errors import DomainError
from .media import MediaAsset, MediaService
from .projects import ProjectService


class Shot(BaseModel):
    asset_id: UUID
    source_start_ms: int | None
    source_end_ms: int | None
    output_start_ms: int
    output_end_ms: int
    fit: Literal["cover"] = "cover"
    still_motion: Literal["slow_zoom"] | None = None


class AudioBed(BaseModel):
    asset_id: UUID
    source_start_ms: int
    source_end_ms: int
    gain_db: float = 0.0


class ReelPlan(BaseModel):
    schema_version: Literal[1] = 1
    id: UUID
    project_id: UUID
    version: int
    duration_ms: Literal[15000, 30000]
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    fps: Literal[30] = 30
    safe_area: Literal["instagram_reels_v1"] = "instagram_reels_v1"
    audio: AudioBed
    shots: list[Shot]
    rationale: str


class ComposePlanRequest(BaseModel):
    duration_ms: Literal[15000, 30000]
    audio_asset_id: UUID
    audio_start_ms: int
    visual_asset_ids: list[UUID]


class PlanValidator:
    def validate(
        self, plan: ReelPlan, assets: Mapping[UUID, MediaAsset] | Sequence[MediaAsset]
    ) -> None:
        by_id = dict(assets) if isinstance(assets, Mapping) else {asset.id: asset for asset in assets}
        violations: list[str] = []

        duration_is_supported = plan.duration_ms in {15_000, 30_000}
        if not duration_is_supported:
            violations.append("duration must be 15000 or 30000 milliseconds")

        referenced_assets = [plan.audio.asset_id, *(shot.asset_id for shot in plan.shots)]
        referenced = [by_id.get(asset_id) for asset_id in referenced_assets]
        if any(asset is None for asset in referenced):
            violations.append("referenced assets must exist")
        elif any(not asset.available for asset in referenced if asset is not None):
            violations.append("referenced assets must be available")

        audio_asset = by_id.get(plan.audio.asset_id)
        audio_duration = _audio_duration(audio_asset)
        if audio_asset is not None and audio_duration is None:
            violations.append("audio bed must reference media with audio")
        times_are_nonnegative = not (
            plan.audio.source_start_ms < 0
            or plan.audio.source_end_ms < 0
            or any(
                shot.output_start_ms < 0
                or shot.output_end_ms < 0
                or (shot.source_start_ms is not None and shot.source_start_ms < 0)
                or (shot.source_end_ms is not None and shot.source_end_ms < 0)
                for shot in plan.shots
            )
        )
        if not times_are_nonnegative:
            violations.append("times must be non-negative")
        if plan.audio.source_end_ms <= plan.audio.source_start_ms:
            violations.append("audio source range must have positive duration")
        if (
            audio_asset is not None
            and audio_duration is not None
            and plan.audio.source_end_ms > audio_duration
        ):
            violations.append("audio source range must fit asset duration")
        if plan.audio.source_end_ms - plan.audio.source_start_ms < plan.duration_ms:
            violations.append("audio must cover output duration")

        expected_start = 0
        gap_found = False
        for shot in plan.shots:
            if shot.output_end_ms <= shot.output_start_ms:
                violations.append("shots must have positive output duration")
                break
            if shot.output_start_ms != expected_start:
                gap_found = True
            expected_start = shot.output_end_ms
        if gap_found or (duration_is_supported and expected_start != plan.duration_ms):
            violations.append("shots must cover output without gaps")

        for shot in plan.shots:
            asset = by_id.get(shot.asset_id)
            if asset is None:
                continue
            if asset.kind == "video":
                if shot.source_start_ms is None or shot.source_end_ms is None:
                    violations.append("video shots must include source ranges")
                elif shot.source_end_ms <= shot.source_start_ms:
                    violations.append("video source ranges must have positive duration")
                elif asset.duration_ms is None or shot.source_end_ms > asset.duration_ms:
                    violations.append("video source ranges must fit asset duration")
                elif (
                    times_are_nonnegative
                    and not gap_found
                    and (
                        shot.source_end_ms - shot.source_start_ms
                        != shot.output_end_ms - shot.output_start_ms
                    )
                ):
                    violations.append("video source range duration must equal output duration")
            elif asset.kind == "image":
                if shot.source_start_ms is not None or shot.source_end_ms is not None:
                    violations.append("still shots must use null source ranges")
                if shot.still_motion != "slow_zoom":
                    violations.append("still shots must use slow_zoom motion")
            else:
                violations.append("shots must reference video or image assets")

        if violations:
            raise DomainError(
                "invalid_reel_plan",
                "Reel plan failed validation",
                status_code=422,
                details={"violations": list(dict.fromkeys(violations))},
            )


class PlanRepository:
    def __init__(self, database: Database):
        self.database = database

    def insert(self, plan: ReelPlan) -> ReelPlan:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM reel_plans WHERE project_id = ?",
                (str(plan.project_id),),
            ).fetchone()
            persisted = plan.model_copy(update={"version": row["version"]})
            connection.execute(
                """
                INSERT INTO reel_plans (id, project_id, version, plan_json, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    str(persisted.id),
                    str(persisted.project_id),
                    persisted.version,
                    persisted.model_dump_json(),
                ),
            )
        return persisted

    def get(self, plan_id: UUID) -> ReelPlan | None:
        row = self.database.fetch_one("SELECT plan_json FROM reel_plans WHERE id = ?", (str(plan_id),))
        return ReelPlan.model_validate_json(row["plan_json"]) if row is not None else None


class PlanService:
    def __init__(
        self,
        repository: PlanRepository,
        projects: ProjectService,
        media: MediaService,
        validator: PlanValidator | None = None,
    ):
        self.repository = repository
        self.projects = projects
        self.media = media
        self.validator = validator or PlanValidator()

    def compose(self, project_id: UUID, request: ComposePlanRequest) -> ReelPlan:
        self.projects.get(project_id)
        assets = self.media.list(project_id)
        by_id = {asset.id: asset for asset in assets}
        audio = by_id.get(request.audio_asset_id)
        audio_duration = _audio_duration(audio)
        if (
            audio is None
            or not audio.available
            or audio_duration is None
            or request.audio_start_ms < 0
            or request.audio_start_ms + request.duration_ms > audio_duration
        ):
            raise _insufficient_media()

        visuals = [
            by_id[asset_id]
            for asset_id in request.visual_asset_ids
            if asset_id in by_id and by_id[asset_id].available and by_id[asset_id].kind in {"video", "image"}
        ]
        if not visuals:
            raise _insufficient_media()

        interval_ms = max(1, min(3_000, request.duration_ms // len(visuals)))
        shots = self._compose_shots(visuals, request.duration_ms, interval_ms)
        plan = ReelPlan.model_construct(
            id=uuid4(),
            project_id=project_id,
            version=0,
            duration_ms=request.duration_ms,
            audio=AudioBed(
                asset_id=audio.id,
                source_start_ms=request.audio_start_ms,
                source_end_ms=request.audio_start_ms + request.duration_ms,
            ),
            shots=shots,
            rationale="Deterministic visual rotation using supplied source order.",
        )
        self.validator.validate(plan, by_id)
        return self.repository.insert(plan)

    def get(self, plan_id: UUID) -> ReelPlan:
        plan = self.repository.get(plan_id)
        if plan is None:
            raise DomainError("plan_not_found", "Reel plan was not found", status_code=404)
        return plan

    @staticmethod
    def _compose_shots(
        visuals: list[MediaAsset], duration_ms: int, interval_ms: int
    ) -> list[Shot]:
        shots: list[Shot] = []
        cursor = 0
        output_start_ms = 0
        while output_start_ms < duration_ms:
            output_end_ms = min(output_start_ms + interval_ms, duration_ms)
            shot_duration_ms = output_end_ms - output_start_ms
            for _ in range(len(visuals)):
                asset = visuals[cursor]
                cursor = (cursor + 1) % len(visuals)
                if asset.kind == "image":
                    shots.append(
                        Shot(
                            asset_id=asset.id,
                            source_start_ms=None,
                            source_end_ms=None,
                            output_start_ms=output_start_ms,
                            output_end_ms=output_end_ms,
                            still_motion="slow_zoom",
                        )
                    )
                    break
                if asset.duration_ms is not None and asset.duration_ms >= shot_duration_ms:
                    shots.append(
                        Shot(
                            asset_id=asset.id,
                            source_start_ms=0,
                            source_end_ms=shot_duration_ms,
                            output_start_ms=output_start_ms,
                            output_end_ms=output_end_ms,
                        )
                    )
                    break
            else:
                raise _insufficient_media()
            output_start_ms = output_end_ms
        return shots


def _insufficient_media() -> DomainError:
    return DomainError(
        "insufficient_usable_media",
        "Usable media cannot cover the requested reel",
        status_code=422,
    )


def _audio_duration(asset: MediaAsset | None) -> int | None:
    if asset is None:
        return None
    if asset.kind == "audio":
        return asset.audio_duration_ms or asset.duration_ms
    if asset.kind == "video" and asset.has_audio:
        return asset.audio_duration_ms
    return None
