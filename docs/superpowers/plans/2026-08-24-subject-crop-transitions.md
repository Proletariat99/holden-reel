# Subject-aware Crop and Simple Transitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the most interesting detected subject visible in a stable, edge-to-edge 9:16 crop and let users choose either Clean cut or a fixed 200 ms Quick dissolve for the whole reel.

**Architecture:** Media import invokes a bounded local OpenCV subprocess and caches one normalized focus point per visual asset. Composition copies that focus point and the reel-level transition choice into the persisted plan; validation owns timeline semantics, while the FFmpeg compiler only executes the frozen plan with focus-aware cover crops and either concat or `xfade`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, `opencv-python-headless`, NumPy, FFmpeg/FFprobe 7+, React 19, TypeScript 7, Vitest, Playwright

**Spec:** `docs/superpowers/specs/2026-08-24-subject-crop-transitions-design.md`

## Global Constraints

- All analysis remains local; do not call cloud APIs, fetch model weights, or modify source media.
- Use bundled classical OpenCV face/person detectors plus local motion and contrast analysis.
- Analyze at most 9 evenly spaced video frames, downscaled so the longest edge is at most 640 pixels.
- Terminate each focus-analysis subprocess after 10 seconds and return a center fallback on every decode, timeout, detector, or payload failure.
- Store exactly one fixed focus point per asset; do not track or pan during a shot.
- Fill the 9:16 canvas with `force_original_aspect_ratio=increase` plus a clamped crop; never add padding or black bars.
- Support exactly `cut` and `dissolve`; dissolve duration is always 200 ms and audio stays continuous.
- Preserve exact 15,000 ms and 30,000 ms output durations.
- Older plans default to focus `(0.5, 0.5)` and transition style `cut`.
- Keep the existing Python 3.12, Node.js 22, pnpm 10, FFmpeg 7+, and FFprobe 7+ floors.
- Commit implementation directly to `main`, per the user's explicit preference; do not create a feature branch or worktree.
- Preserve the user-owned untracked file `docs/instagram-strategy-holden-reed.md` and never stage it.

## File Map

- Create `apps/api/src/holden_reel/focus.py`: public focus types, subprocess boundary, payload validation, fallback behavior.
- Create `apps/api/src/holden_reel/focus_worker.py`: bounded OpenCV decoding, bundled detectors, candidate ranking, motion/contrast fallback, JSON CLI response.
- Create `apps/api/tests/test_focus.py`: pure scoring, real local decode, timeout, malformed-result, and fallback coverage.
- Modify `apps/api/src/holden_reel/db.py`: schema migration 6 for cached focus metadata.
- Modify `apps/api/src/holden_reel/media.py`: persist/reuse/invalidate focus results and expose them through existing API models.
- Modify `apps/api/src/holden_reel/main.py`: inject the focus analyzer into `MediaService`.
- Modify `apps/api/src/holden_reel/plans.py`: backward-compatible focus and transition fields, deterministic overlap composition, validation.
- Modify `apps/api/src/holden_reel/renderer.py`: focus-aware crop expressions and cut/dissolve graph compilation.
- Modify `apps/web/src/types.ts`: shared focus and transition fields.
- Modify `apps/web/src/features/draft/DraftWorkspace.tsx`: reel-level transition control, restoration, invalidation, and summary.
- Modify `apps/api/tests/fixture_media.py`: deterministic off-center and transition acceptance media.
- Modify `apps/api/tests/test_media.py`, `test_plans.py`, `test_renderer.py`, and `test_vertical_slice.py`: backend integration and real-media assertions.
- Modify `apps/web/src/features/draft/DraftWorkspace.test.tsx` and `apps/web/e2e/vertical-slice.spec.ts`: user-flow coverage.
- Modify `README.md` and `docs/development.md`: explain local analysis, choices, and manual verification.

---

### Task 1: Local Focus Analysis Boundary

**Files:**
- Create: `apps/api/src/holden_reel/focus.py`
- Create: `apps/api/src/holden_reel/focus_worker.py`
- Create: `apps/api/tests/test_focus.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`

**Interfaces:**
- Produces: `FocusMethod = Literal["face", "person", "motion", "contrast", "center"]`
- Produces: `FOCUS_ANALYZER_VERSION = 1`, `MAX_VIDEO_FRAMES = 9`, `MAX_FRAME_EDGE = 640`, `FOCUS_TIMEOUT_SECONDS = 10`, and `MIN_FOCUS_CONFIDENCE = 0.10`
- Produces: immutable `FocusResult(x: float, y: float, confidence: float, method: FocusMethod, analyzer_version: int)`
- Produces: `center_focus() -> FocusResult`
- Produces: `FocusAnalyzer.analyze(path: Path, kind: Literal["image", "video"]) -> FocusResult`
- Produces: worker functions `analyze_image(path: Path) -> FocusResult`, `analyze_video(path: Path) -> FocusResult`, and `choose_focus(frames: Sequence[np.ndarray], detector: SubjectDetector) -> FocusResult`

- [ ] **Step 1: Add and lock the local computer-vision dependency**

Run:

```bash
cd apps/api
uv add 'opencv-python-headless>=4.10,<5'
uv lock
```

Expected: `pyproject.toml` contains the bounded dependency and `uv.lock` resolves it for Python 3.12 without adding any model-download package.

- [ ] **Step 2: Write failing tests for ranking, stable aggregation, and fallbacks**

Create tests around literal detector candidates and synthetic NumPy frames:

```python
def test_faces_outrank_people_and_image_fallback():
    detector = StubDetector(
        faces=[Candidate(0.18, 0.42, 0.12, "face")],
        people=[Candidate(0.82, 0.50, 0.70, "person")],
    )
    result = choose_focus([solid_frame()], detector)
    assert result.method == "face"
    assert result.x == pytest.approx(0.18, abs=0.02)


def test_people_outrank_motion_and_contrast():
    frames = [frame_with_box(x=20), frame_with_box(x=80)]
    result = choose_focus(frames, StubDetector(people=[Candidate(0.25, 0.5, 0.6, "person")]))
    assert result.method == "person"
    assert result.x == pytest.approx(0.25, abs=0.02)


def test_video_candidates_reduce_to_one_robust_fixed_point():
    detector = PerFrameDetector(xs=[0.20, 0.22, 0.21, 0.95])
    result = choose_focus([solid_frame() for _ in range(4)], detector)
    assert result.method == "face"
    assert result.x == pytest.approx(0.215, abs=0.03)


def test_low_signal_and_detector_failure_return_center():
    assert choose_focus([solid_frame()], EmptyDetector()) == center_focus()
    assert choose_focus([solid_frame()], RaisingDetector()) == center_focus()
```

Also test that `FocusAnalyzer` calls `[sys.executable, "-m", "holden_reel.focus_worker", ...]` with `shell=False`, `timeout=10`, and returns `center_focus()` for `TimeoutExpired`, nonzero exit, invalid JSON, an out-of-range coordinate, or an unknown method.

- [ ] **Step 3: Run the focus tests and confirm the red state**

Run:

```bash
cd apps/api
uv run pytest tests/test_focus.py -q
```

Expected: collection fails because `holden_reel.focus` and `holden_reel.focus_worker` do not exist.

- [ ] **Step 4: Implement the public subprocess boundary**

In `focus.py`, define the immutable payload and strict fallback:

```python
FocusMethod = Literal["face", "person", "motion", "contrast", "center"]
FOCUS_ANALYZER_VERSION = 1
FOCUS_TIMEOUT_SECONDS = 10

@dataclass(frozen=True)
class FocusResult:
    x: float
    y: float
    confidence: float
    method: FocusMethod
    analyzer_version: int = FOCUS_ANALYZER_VERSION

def center_focus() -> FocusResult:
    return FocusResult(0.5, 0.5, 0.0, "center")
```

`FocusAnalyzer.analyze()` must use `subprocess.run(..., capture_output=True, text=True, shell=False, timeout=FOCUS_TIMEOUT_SECONDS)`, accept only `image` and `video`, parse one JSON object, require finite `x`, `y`, and `confidence` values in `0.0..1.0`, require the current analyzer version, and convert every failure to `center_focus()`.

- [ ] **Step 5: Implement the bounded OpenCV worker**

In `focus_worker.py`:

- Load the face cascade from `cv2.data.haarcascades + "haarcascade_frontalface_default.xml"` and use `cv2.HOGDescriptor_getDefaultPeopleDetector()` for people.
- Resize decoded frames with unchanged aspect ratio so `max(height, width) <= 640`.
- For images, decode once with `cv2.imread`.
- For videos with a positive frame count, sample indices `round(i * (frame_count - 1) / 8)` for `i in range(9)`. If OpenCV reports no usable frame count, decode only the first frame and use detection/contrast fallback; never scan an unbounded stream.
- Convert each bounding box to normalized center coordinates and weight it by normalized area times detector confidence.
- Evaluate all face candidates first; use people only when no face candidates exist.
- Aggregate detected candidates with a coordinate-wise weighted median; clamp the result to `0.0..1.0`.
- If detections are absent in video, calculate `absdiff` between adjacent grayscale frames, combine that map with Sobel edge magnitude, and use its weighted centroid as method `motion`.
- If motion energy is below `0.01` of full-scale pixels, use the Sobel/Laplacian edge centroid as method `contrast`.
- Normalize the selected method's signal strength to confidence `0.0..1.0`; if final confidence is below `MIN_FOCUS_CONFIDENCE`, return `center_focus()`.
- If total usable weight is below `1e-6`, return `center_focus()` before computing a centroid.
- Catch decode and OpenCV exceptions at the worker CLI boundary, print the center result as JSON, and exit successfully so import is never blocked.

The CLI must accept exactly `--path <absolute path>` and `--kind image|video`, reject a relative path, and print `asdict(result)` as one JSON line.

- [ ] **Step 6: Run focused tests and a real worker smoke test**

Run:

```bash
cd apps/api
uv run pytest tests/test_focus.py -q
uv run python -m holden_reel.focus_worker --path "$PWD/tests/assets-does-not-exist.jpg" --kind image
```

Expected: tests pass; the missing-file smoke command prints a valid center JSON result with analyzer version 1 and does not hang.

- [ ] **Step 7: Commit the focus boundary**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock apps/api/src/holden_reel/focus.py apps/api/src/holden_reel/focus_worker.py apps/api/tests/test_focus.py
git commit -m "feat: add bounded local focus analysis"
```

---

### Task 2: Persist and Cache Asset Focus

**Files:**
- Modify: `apps/api/src/holden_reel/db.py`
- Modify: `apps/api/src/holden_reel/media.py`
- Modify: `apps/api/src/holden_reel/main.py`
- Modify: `apps/api/tests/test_media.py`
- Modify: `apps/api/tests/conftest.py`

**Interfaces:**
- Consumes: `FocusAnalyzer.analyze(path, kind)`, `FocusResult`, `FOCUS_ANALYZER_VERSION`, and `center_focus()` from Task 1.
- Produces: nullable `MediaAsset.focus_x`, `focus_y`, `focus_confidence`, `focus_method`, `focus_analyzer_version`, and `focus_fingerprint`.
- Produces: `MediaRepository.find_by_path(project_id: UUID, path: Path) -> MediaAsset | None`.
- Changes: `MediaService(repository, projects, ffprobe, focus_analyzer)` constructor.

- [ ] **Step 1: Write failing migration and cache tests**

Add tests that open an existing version-5 database and assert migration 6 adds all six columns without altering old rows. Add an injected `RecordingFocusAnalyzer` and prove:

```python
first = service.import_path(project_id, source)[0]
second = service.import_path(project_id, source)[0]
assert analyzer.calls == [(source.resolve(), "video")]
assert second.focus_fingerprint == first.fingerprint

os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
third = service.import_path(project_id, source)[0]
assert len(analyzer.calls) == 2
assert third.focus_fingerprint == third.fingerprint
```

Also assert audio assets keep all focus fields `None`, missing/stale analyzer versions trigger analysis, a center fallback persists as a valid cached result, and import/list API JSON includes normalized focus metadata.

- [ ] **Step 2: Run the focused tests and confirm missing fields fail**

Run:

```bash
cd apps/api
uv run pytest tests/test_media.py -q
```

Expected: failures mention absent focus columns, fields, repository lookup, and constructor argument.

- [ ] **Step 3: Add SQLite migration 6**

Append a guarded migration in `open_database()`:

```sql
ALTER TABLE media_assets ADD COLUMN focus_x REAL;
ALTER TABLE media_assets ADD COLUMN focus_y REAL;
ALTER TABLE media_assets ADD COLUMN focus_confidence REAL;
ALTER TABLE media_assets ADD COLUMN focus_method TEXT;
ALTER TABLE media_assets ADD COLUMN focus_analyzer_version INTEGER;
ALTER TABLE media_assets ADD COLUMN focus_fingerprint TEXT;
```

Insert migration version 6 only after all statements succeed. Do not backfill old rows; null values intentionally cause analysis on re-import.

- [ ] **Step 4: Extend repository serialization and lookup**

Add the six optional fields to `MediaAsset`, `_asset_fields()`, insert/update SQL, and `_to_asset()`. Implement `find_by_path()` with the existing database wrapper and the `(project_id, resolved path)` unique key. Keep the API response model as `MediaAsset`, so FastAPI exposes the new fields without a parallel DTO.

- [ ] **Step 5: Reuse focus only when both cache keys match**

In `MediaService.import_path()`, calculate the new fingerprint before analysis, load the existing row, and use this exact condition:

```python
cache_is_current = (
    existing is not None
    and existing.focus_fingerprint == fingerprint
    and existing.focus_analyzer_version == FOCUS_ANALYZER_VERSION
    and existing.focus_x is not None
    and existing.focus_y is not None
    and existing.focus_method is not None
)
```

For `image` and `video`, copy current cached values when true; otherwise call the analyzer and persist its result with `focus_fingerprint=fingerprint`. For `audio`, persist all focus fields as `None` and never invoke the analyzer. Preserve the existing asset UUID on update.

- [ ] **Step 6: Wire the production analyzer**

Construct `FocusAnalyzer()` in `create_app()` and pass it as the fourth `MediaService` dependency. Update direct `MediaService` construction in fixtures to inject a deterministic fake when a test is not specifically testing OpenCV.

- [ ] **Step 7: Run media and API tests**

Run:

```bash
cd apps/api
uv run pytest tests/test_media.py tests/test_health.py tests/test_projects.py -q
```

Expected: all pass, repeated import performs no second analysis, and changed metadata triggers exactly one reanalysis.

- [ ] **Step 8: Commit cached focus metadata**

```bash
git add apps/api/src/holden_reel/db.py apps/api/src/holden_reel/media.py apps/api/src/holden_reel/main.py apps/api/tests/test_media.py apps/api/tests/conftest.py
git commit -m "feat: cache subject focus during media import"
```

---

### Task 3: Freeze Focus and Transition Timing in Reel Plans

**Files:**
- Modify: `apps/api/src/holden_reel/plans.py`
- Modify: `apps/api/tests/test_plans.py`

**Interfaces:**
- Consumes: media focus fields from Task 2.
- Produces: `TransitionStyle = Literal["cut", "dissolve"]`, `DISSOLVE_DURATION_MS = 200`, and `MIN_SHOT_DURATION_MS = 600`.
- Changes: `Shot` adds `focus_x: float = 0.5`, `focus_y: float = 0.5`, and `focus_method: FocusMethod = "center"`.
- Changes: `ReelPlan` and `ComposePlanRequest` add `transition_style: TransitionStyle = "cut"`.
- Produces: `transition_overlap_ms(style: TransitionStyle) -> int` returning exactly `0` or `200`.

- [ ] **Step 1: Write failing backward-compatibility and validation tests**

Cover these exact cases:

```python
old = ReelPlan.model_validate_json(old_plan_json_without_new_fields)
assert old.transition_style == "cut"
assert [(shot.focus_x, shot.focus_y, shot.focus_method) for shot in old.shots] == [
    (0.5, 0.5, "center"),
    (0.5, 0.5, "center"),
]

dissolve = valid_plan.model_copy(update={"transition_style": "dissolve"}, deep=True)
dissolve.shots[1].output_start_ms = dissolve.shots[0].output_end_ms - 200
PlanValidator().validate(dissolve, assets)
```

Assert cut rejects any overlap, dissolve rejects 199 ms and 201 ms overlaps, first shot must start at 0, final shot must end at reel duration, focus coordinates outside `0.0..1.0` fail Pydantic validation, and each shot duration must exceed the 200 ms dissolve.

- [ ] **Step 2: Run plan tests and confirm the red state**

Run:

```bash
cd apps/api
uv run pytest tests/test_plans.py -q
```

Expected: failures show missing transition/focus fields and current gap-only validation.

- [ ] **Step 3: Add backward-compatible plan fields and overlap validation**

Use Pydantic bounds for focus coordinates and validate timeline boundaries by transition style:

```python
overlap_ms = transition_overlap_ms(plan.transition_style)
expected_start = 0
for index, shot in enumerate(plan.shots):
    if shot.output_start_ms != expected_start:
        violations.append("shot boundaries must match transition overlap")
    if shot.output_end_ms - shot.output_start_ms <= overlap_ms:
        violations.append("shots must be longer than the transition overlap")
    expected_start = shot.output_end_ms - overlap_ms
if plan.shots and plan.shots[-1].output_end_ms != plan.duration_ms:
    violations.append("shots must end at output duration")
```

Keep the existing video source rule: source duration equals the full shot interval, including its overlapped portion.

- [ ] **Step 4: Write failing composition tests with exact integer timelines**

For 15 seconds and five effective shots, assert:

```python
assert cut_intervals == [
    (0, 3000), (3000, 6000), (6000, 9000), (9000, 12000), (12000, 15000)
]
assert dissolve_intervals == [
    (0, 3160), (2960, 6120), (5920, 9080), (8880, 12040), (11840, 15000)
]
```

Assert composed shots copy each selected asset's focus coordinates/method, the request defaults to `cut`, and persisted/reloaded plans retain `dissolve` and focus data.

- [ ] **Step 5: Implement deterministic shot allocation**

Set `shot_count = max(len(visuals), ceil(duration_ms / 3000))`. Reject when `shot_count * MIN_SHOT_DURATION_MS > duration_ms + (shot_count - 1) * overlap_ms`. Distribute total segment time exactly:

```python
segment_total_ms = duration_ms + (shot_count - 1) * overlap_ms
base_ms, remainder = divmod(segment_total_ms, shot_count)
durations = [base_ms + (1 if index < remainder else 0) for index in range(shot_count)]
```

Start the first shot at 0 and each later shot at `previous_end - overlap_ms`; cycle supplied visuals in order. A video is usable only when its duration covers that complete assigned shot duration. Copy `asset.focus_x if asset.focus_x is not None else 0.5`, `asset.focus_y if asset.focus_y is not None else 0.5`, and `asset.focus_method if asset.focus_method is not None else "center"` into every shot. Explicitly set transition fields when using `model_construct()`.

- [ ] **Step 6: Run plan tests**

Run:

```bash
cd apps/api
uv run pytest tests/test_plans.py -q
```

Expected: all plan tests pass for cut, dissolve, old JSON, exact duration, and frozen focus.

- [ ] **Step 7: Commit deterministic plan semantics**

```bash
git add apps/api/src/holden_reel/plans.py apps/api/tests/test_plans.py
git commit -m "feat: plan focused crops and reel transitions"
```

---

### Task 4: Compile Focus-aware Crops and Both Transitions

**Files:**
- Modify: `apps/api/src/holden_reel/renderer.py`
- Modify: `apps/api/tests/test_renderer.py`

**Interfaces:**
- Consumes: `Shot.focus_x`, `Shot.focus_y`, `ReelPlan.transition_style`, and `DISSOLVE_DURATION_MS` from Task 3.
- Produces: `_focus_crop(profile: RenderProfile, shot: Shot) -> str`.
- Produces: `_visual_join_filter(plan: ReelPlan, labels: Sequence[str]) -> list[str]` returning concat or the complete `xfade` chain.

- [ ] **Step 1: Write failing compiler tests for clamped crop expressions**

Assert both image and video filters contain scale-to-cover and these clamped origins, with no `pad=`:

```text
x='min(max(iw*0.150000-1080/2,0),iw-1080)'
y='min(max(ih*0.700000-1920/2,0),ih-1920)'
```

For preview, assert the same normalized point is compiled against `540x960`. For stills, ensure the slow zoom remains centered on the already focused/cropped frame and does not re-center the original source.

- [ ] **Step 2: Write failing graph tests for cut and dissolve**

Assert a cut plan emits one `concat=n=<count>:v=1:a=0[vout]` and no `xfade`. Assert a three-shot dissolve emits:

```text
[v0][v1]xfade=transition=fade:duration=0.2:offset=2.96[x1]
[x1][v2]xfade=transition=fade:duration=0.2:offset=5.92[vout]
```

Use each next shot's `output_start_ms / 1000` as the global xfade offset. Assert audio still has one continuous `atrim` and no `afade` or `acrossfade`.

- [ ] **Step 3: Run renderer tests and confirm the red state**

Run:

```bash
cd apps/api
uv run pytest tests/test_renderer.py -q
```

Expected: focus expressions and `xfade` are absent and the assertions fail.

- [ ] **Step 4: Implement the focus crop helper**

Return this filter fragment, formatting coordinates to six decimals:

```python
def _focus_crop(profile: RenderProfile, shot: Shot) -> str:
    return (
        f"crop={profile.width}:{profile.height}:"
        f"x='min(max(iw*{shot.focus_x:.6f}-{profile.width}/2,0),iw-{profile.width})':"
        f"y='min(max(ih*{shot.focus_y:.6f}-{profile.height}/2,0),ih-{profile.height})'"
    )
```

Place it immediately after scale in video and image pipelines. For images, crop first, then run the existing `zoompan` on the portrait-sized result so zoom motion cannot discard the chosen focus.

- [ ] **Step 5: Implement the visual join helper**

For `cut`, return the existing concat expression. For `dissolve`, chain labels in order, name intermediate outputs `[x1]`, `[x2]`, and name only the final output `[vout]`. Format 200 ms as `0.2` via `_seconds()` and format each plan offset the same way. A one-shot dissolve plan aliases its only label to `[vout]` with `null` rather than invoking `xfade`.

- [ ] **Step 6: Run renderer and job tests**

Run:

```bash
cd apps/api
uv run pytest tests/test_renderer.py tests/test_jobs.py -q
```

Expected: compiler strings, render execution, cancellation, progress, and verification tests all pass.

- [ ] **Step 7: Commit renderer behavior**

```bash
git add apps/api/src/holden_reel/renderer.py apps/api/tests/test_renderer.py
git commit -m "feat: render focused crops and quick dissolves"
```

---

### Task 5: Add the Reel-level Transition Control

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/features/draft/DraftWorkspace.tsx`
- Modify: `apps/web/src/features/draft/DraftWorkspace.test.tsx`
- Modify: `apps/web/src/app.css`

**Interfaces:**
- Consumes: API transition and focus JSON fields from Tasks 2 and 3.
- Produces: `TransitionStyle = "cut" | "dissolve"` in TypeScript.
- Changes: `ComposePlanRequest.transition_style`, `ReelPlan.transition_style`, and `ReelShot.focus_x`, `focus_y`, `focus_method`.

- [ ] **Step 1: Extend TypeScript fixtures and write failing UI tests**

Set every `ReelPlan` fixture to `transition_style: "cut"` and every shot fixture to center focus. Add tests proving:

```typescript
expect(screen.getByRole("radio", { name: /clean cut/i })).toBeChecked();
await user.click(screen.getByRole("radio", { name: /quick dissolve/i }));
await user.click(screen.getByRole("button", { name: /generate draft/i }));
expect(api.composePlan).toHaveBeenCalledWith("p1", expect.objectContaining({
  transition_style: "dissolve",
}));
```

Add `transition` to the existing invalidation table, assert both transition radios are disabled during preview and final jobs, assert restoring a dissolve plan selects Quick dissolve, and assert the plan summary displays `Quick dissolve · 200 ms` or `Clean cut`.

- [ ] **Step 2: Run the draft tests and confirm the red state**

Run:

```bash
pnpm --dir apps/web test --run src/features/draft/DraftWorkspace.test.tsx
```

Expected: TypeScript/runtime assertions fail because the transition type and controls do not exist.

- [ ] **Step 3: Add shared TypeScript fields**

Define:

```typescript
export type FocusMethod = "face" | "person" | "motion" | "contrast" | "center";
export type TransitionStyle = "cut" | "dissolve";
```

Add optional focus fields to `MediaAsset`, required focus fields to `ReelShot`, and required `transition_style` to both `ComposePlanRequest` and `ReelPlan`.

- [ ] **Step 4: Add accessible transition choices**

Initialize `transitionStyle` to `"cut"`. Reset it to cut when the project/selection resets; restore it from `restoredPlan.transition_style`; include it in compose payloads. Beside Reel length, add a fieldset with exactly:

```tsx
<fieldset className="choice-group">
  <legend>Transition</legend>
  <label className="inline-choice">
    <input type="radio" name="transition" checked={transitionStyle === "cut"} disabled={contentLocked}
      onChange={() => { if (!contentLocked) { invalidatePlan(); setTransitionStyle("cut"); } }} />
    Clean cut
  </label>
  <label className="inline-choice">
    <input type="radio" name="transition" checked={transitionStyle === "dissolve"} disabled={contentLocked}
      onChange={() => { if (!contentLocked) { invalidatePlan(); setTransitionStyle("dissolve"); } }} />
    Quick dissolve
  </label>
</fieldset>
```

Use existing choice styling; add only the minimum CSS needed to keep both settings compact at narrow widths. In `PlanSummary`, render the persisted choice, not component state.

- [ ] **Step 5: Run unit tests and strict build**

Run:

```bash
pnpm --dir apps/web test --run src/features/draft/DraftWorkspace.test.tsx
pnpm --dir apps/web build
```

Expected: tests and TypeScript/Vite build pass.

- [ ] **Step 6: Commit the UI choice**

```bash
git add apps/web/src/types.ts apps/web/src/features/draft/DraftWorkspace.tsx apps/web/src/features/draft/DraftWorkspace.test.tsx apps/web/src/app.css
git commit -m "feat: choose clean cuts or quick dissolves"
```

---

### Task 6: Prove the Behavior with Real Generated Media

**Files:**
- Modify: `apps/api/tests/fixture_media.py`
- Modify: `apps/api/tests/test_fixture_media.py`
- Modify: `apps/api/tests/test_vertical_slice.py`
- Modify: `apps/web/e2e/vertical-slice.spec.ts`

**Interfaces:**
- Consumes: the complete import, compose, render, and UI behavior from Tasks 1–5.
- Produces: fixture keys `off-center.mp4`, `left-red.mp4`, and `right-blue.mp4` with deterministic colors and 18-second audio coverage.

- [ ] **Step 1: Add failing real-media acceptance assertions**

Extend fixture generation with a landscape `640x360` source whose background is green and whose high-contrast red subject box occupies the far-left quarter. Add two solid transition clips, one red and one blue. Update the vertical slice to compose a 15-second dissolve plan and assert:

- imported off-center media reports focus `x < 0.35` with a method other than `center`;
- the persisted shot carries the same focus coordinates;
- extracted frames from the portrait output contain red subject pixels, proving a center crop did not discard the subject;
- every sampled corner has real image content rather than padding;
- a frame 100 ms into a dissolve has red and blue channels both above 80 and green below 80;
- FFprobe reports exactly `15.000000` seconds, H.264 video, AAC audio, and `1080x1920` dimensions;
- SHA-256 hashes for every source are unchanged.

Use FFmpeg to extract exact PNG frames and OpenCV to inspect BGR pixels. Define red/blue thresholds explicitly as dominant channel `> 140` and the other two channels `< 100`.

- [ ] **Step 2: Run acceptance tests and confirm the red state**

Run:

```bash
cd apps/api
uv run pytest tests/test_fixture_media.py tests/test_vertical_slice.py -q
```

Expected: fixture keys and real focus/dissolve assertions fail before fixture and workflow updates.

- [ ] **Step 3: Generate deterministic fixture sources**

Use FFmpeg lavfi commands with fixed sizes, rates, durations, and `yuv420p`. For the off-center clip use a green source plus `drawbox=x=20:y=70:w=150:h=220:color=red:t=fill`; its strong static edges deliberately exercise the contrast fallback. Keep each video at least 4 seconds so the deterministic composer can reuse it safely.

- [ ] **Step 4: Update the HTTP acceptance workflow**

Select the new sources, send `"transition_style": "dissolve"`, wait through the existing bounded job helper, extract frames with a 30-second subprocess timeout, and apply the exact pixel assertions from Step 1. Retain all existing container, codec, duration, download, and source-integrity assertions.

- [ ] **Step 5: Exercise Quick dissolve in Playwright**

After entering the draft workspace, select the Quick dissolve radio and assert it is checked before generating. After preview succeeds, assert the plan summary says `Quick dissolve · 200 ms`, then complete playback, seeking, final export, and download checks unchanged.

- [ ] **Step 6: Run backend acceptance and Playwright**

Run:

```bash
cd apps/api
uv run pytest tests/test_fixture_media.py tests/test_vertical_slice.py -q
cd ../..
pnpm --dir apps/web test:e2e -- --workers=1
```

Expected: real off-center subject, real blended boundary, exact duration, source integrity, and complete browser workflow all pass.

- [ ] **Step 7: Commit real-media coverage**

```bash
git add apps/api/tests/fixture_media.py apps/api/tests/test_fixture_media.py apps/api/tests/test_vertical_slice.py apps/web/e2e/vertical-slice.spec.ts
git commit -m "test: verify focused dissolve renders end to end"
```

---

### Task 7: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/development.md`

**Interfaces:**
- Consumes: final user-visible behavior and verified commands from Tasks 1–6.
- Produces: installation, privacy, cache, transition, and manual acceptance guidance.

- [ ] **Step 1: Update user documentation**

In `README.md`, replace the claim that every draft is a plain deterministic rotation with a concise description of local subject-aware fixed crops and the two transition choices. State that analysis uses bundled OpenCV logic, sends nothing off-machine, and may add a short delay on first import while unchanged files reuse cached focus.

In `docs/development.md`, add:

- `opencv-python-headless` is installed by the existing locked `uv sync` command;
- focus analysis uses at most 9 low-resolution frames and a 10-second subprocess timeout per visual;
- re-import after changing a source or analyzer version refreshes focus;
- Clean cut is default and Quick dissolve is a fixed 200 ms visual-only overlap;
- manual acceptance: import a Holden Reed `.mov`, compare the subject-aware portrait framing, and render both styles;
- no original media is copied or modified.

- [ ] **Step 2: Run the complete verification matrix**

Run:

```bash
cd apps/api
uv sync --python 3.12 --frozen
uv run pytest -q
cd ../..
pnpm --dir apps/web test --run
pnpm --dir apps/web build
pnpm --dir apps/web test:e2e -- --workers=1
```

Expected: every API test, web unit test, strict TypeScript/Vite build, and Playwright test passes with no skipped feature coverage.

- [ ] **Step 3: Scan for forbidden behavior and placeholders**

Run:

```bash
rg -n 'TO''DO|T''BD|implement'" later"'|pad=|acrossfade|afade|https?://' apps/api/src/holden_reel/focus.py apps/api/src/holden_reel/focus_worker.py apps/api/src/holden_reel/renderer.py docs/superpowers/plans/2026-08-24-subject-crop-transitions.md
git status --short
```

Expected: no implementation placeholders, no renderer padding/audio fades, no runtime model URL, and the only unrelated untracked path remains `docs/instagram-strategy-holden-reed.md`.

- [ ] **Step 4: Perform manual acceptance with user media when available**

Run `make dev`, re-import one existing Holden Reed `.mov`, generate Clean cut and Quick dissolve previews, and verify the chosen performer remains visible in a stable crop with no black space. Record only pass/fail observations; do not add, hash, copy, or commit the user's media.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/development.md
git commit -m "docs: explain focused crops and transitions"
```

- [ ] **Step 6: Verify final repository state**

Run:

```bash
git log --oneline -8
git status --short
```

Expected: the feature commits are on `main`; implementation files are clean; `docs/instagram-strategy-holden-reed.md` remains untracked and unstaged.
