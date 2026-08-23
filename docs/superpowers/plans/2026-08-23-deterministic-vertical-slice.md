# Deterministic Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, working Holden Reel path that creates a project from a small folder, catalogs mixed media, creates a deterministic 15- or 30-second reel plan, renders a proxy preview, and exports a validated 1080×1920 MP4 without remote AI.

**Architecture:** A Vite React/TypeScript client talks to a FastAPI service bound to `127.0.0.1`. The service persists metadata and job state in SQLite, references source media in place, represents edits as versioned Pydantic reel plans, and delegates all media inspection and rendering to narrow FFprobe/FFmpeg adapters. Rendering runs through a persisted in-process job runner so the UI can poll progress and recover job results after a page refresh.

**Tech Stack:** Node.js 22, pnpm 10, React 19, TypeScript 5, Vite, Vitest, Testing Library, Playwright, Python 3.12, uv, FastAPI, Pydantic 2, platformdirs, pytest, HTTPX, SQLite, FFmpeg/FFprobe 7 or newer.

**Spec:** `docs/superpowers/specs/2026-08-23-holden-reel-mvp-design.md`

## Global Constraints

- Bind the development API to `127.0.0.1`; this milestone has no authentication and must not expose the service on the LAN by default.
- Original media is referenced in place, never copied, modified, overwritten, or deleted.
- Binary media is not stored in SQLite.
- Generated artifacts live under the configured application data directory and can be removed without affecting source media.
- The only supported output is vertical 1080×1920 MP4 with H.264 video and AAC audio.
- Reel duration is exactly 15 or 30 seconds at the plan/API boundary.
- A reel plan is the only creative input to the renderer; the renderer must not make shot-selection decisions.
- Remote AI, authentication, posting integrations, a multitrack timeline, and large-library optimization are outside this plan.
- Tests use generated fixture media and temporary application-data directories; they never depend on the user's band media.
- Use `subprocess` argument arrays with `shell=False` for FFmpeg and FFprobe. Never interpolate media paths into a shell command.
- API errors use the shape `{"error":{"code":string,"message":string,"details":object}}`.

---

## Planned file structure

```text
.
├── .env.example                       # Local configuration contract
├── .node-version                      # Node major used by contributors
├── .python-version                    # Python version used by uv
├── Makefile                           # Thin, discoverable developer commands
├── package.json                       # Root pnpm scripts and JS tooling
├── pnpm-workspace.yaml                # Frontend workspace declaration
├── apps
│   ├── api
│   │   ├── pyproject.toml             # Python package and test configuration
│   │   ├── uv.lock                    # Reproducible Python dependency lock
│   │   ├── src/holden_reel
│   │   │   ├── main.py                # FastAPI composition root and lifespan
│   │   │   ├── config.py              # Validated runtime settings
│   │   │   ├── errors.py              # Domain-to-HTTP error mapping
│   │   │   ├── db.py                  # SQLite connection and migrations
│   │   │   ├── api.py                 # HTTP routes and response contracts
│   │   │   ├── projects.py            # Project repository and service
│   │   │   ├── media.py               # Media models, catalog, and FFprobe adapter
│   │   │   ├── plans.py               # Reel-plan schema, validation, and composer
│   │   │   ├── artifacts.py           # Safe generated-artifact paths
│   │   │   ├── renderer.py            # FFmpeg command compiler and runner
│   │   │   └── jobs.py                # Persisted in-process job runner
│   │   └── tests
│   │       ├── conftest.py             # Isolated app/client and media fixtures
│   │       ├── fixture_media.py        # Deterministic FFmpeg fixture generator
│   │       ├── test_health.py
│   │       ├── test_projects.py
│   │       ├── test_media.py
│   │       ├── test_plans.py
│   │       ├── test_renderer.py
│   │       ├── test_jobs.py
│   │       └── test_vertical_slice.py
│   └── web
│       ├── index.html
│       ├── package.json
│       ├── tsconfig.json
│       ├── vite.config.ts
│       ├── playwright.config.ts
│       ├── src
│       │   ├── main.tsx                # React entry point
│       │   ├── App.tsx                 # Small route/state composition root
│       │   ├── app.css                 # MVP visual system and responsive layout
│       │   ├── api.ts                  # Typed fetch client and error decoding
│       │   ├── types.ts                # HTTP contract types mirrored from API
│       │   ├── useJob.ts               # Polling/cancellation hook
│       │   └── features
│       │       ├── projects/ProjectStart.tsx
│       │       ├── import/MediaImport.tsx
│       │       └── draft/DraftWorkspace.tsx
│       ├── src/test/setup.ts
│       ├── src/**/*.test.tsx
│       └── e2e/vertical-slice.spec.ts
└── docs/development.md                 # Exact local setup and verification flow
```

Keep these files focused during implementation. If a Python module exceeds
roughly 300 lines, split repository, service, and model definitions into a
same-named package without changing the public interfaces in this plan.

---

### Task 1: Bootstrap the local API and web workspace

**Files:**
- Create: `.node-version`
- Create: `.python-version`
- Create: `.env.example`
- Create: `Makefile`
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/holden_reel/__init__.py`
- Create: `apps/api/src/holden_reel/config.py`
- Create: `apps/api/src/holden_reel/main.py`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/web/package.json`
- Create: `apps/web/index.html`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/App.tsx`
- Create: `apps/web/src/app.css`
- Create: `apps/web/src/test/setup.ts`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: no application interfaces.
- Produces: `create_app(settings: Settings | None = None) -> FastAPI`, `Settings(data_dir: Path, ffmpeg_bin: str, ffprobe_bin: str)`, and `GET /api/health -> {"status":"ok","version":string}`.

- [ ] **Step 1: Write the failing API health test**

```python
from fastapi.testclient import TestClient

from holden_reel.config import Settings
from holden_reel.main import create_app


def test_health_reports_ready(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"
```

- [ ] **Step 2: Add the Python project metadata and verify the test fails**

Create `apps/api/pyproject.toml` with package name `holden-reel-api`, Python
`>=3.12`, runtime dependencies `fastapi`, `platformdirs`, `pydantic-settings`,
and `uvicorn[standard]`, plus development dependencies `httpx`, `pytest`, and
`pytest-asyncio`. Configure pytest with `pythonpath = ["src"]` and
`testpaths = ["tests"]`.

Run: `cd apps/api && uv run pytest tests/test_health.py -q`

Expected: FAIL because `holden_reel.main` does not exist.

- [ ] **Step 3: Implement settings and the health endpoint**

```python
# config.py
from pathlib import Path
from platformdirs import user_data_path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOLDEN_REEL_")
    data_dir: Path = user_data_path("holden-reel", appauthor=False)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
```

```python
# main.py
from fastapi import FastAPI
from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Holden Reel", version="0.1.0")
    app.state.settings = settings or Settings()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    return app


app = create_app()
```

- [ ] **Step 4: Scaffold the Vite client and root commands**

Use a pnpm workspace with root scripts:

```json
{
  "private": true,
  "scripts": {
    "dev": "concurrently -k -n api,web -c magenta,cyan \"pnpm dev:api\" \"pnpm dev:web\"",
    "dev:web": "pnpm --dir apps/web dev",
    "dev:api": "cd apps/api && uv run uvicorn holden_reel.main:app --reload --host 127.0.0.1 --port 8000",
    "test": "pnpm typecheck && pnpm test:web && pnpm test:api",
    "typecheck": "pnpm --dir apps/web exec tsc --noEmit",
    "test:web": "pnpm --dir apps/web test --run",
    "test:api": "cd apps/api && uv run pytest -q"
  }
}
```

Run `pnpm add -Dw concurrently` so pnpm records the resolved version in both
`package.json` and `pnpm-lock.yaml`.

Configure Vite to proxy `/api` to `http://127.0.0.1:8000`. `App.tsx` renders
the heading “Holden Reel” and the subheading “Make reels from the comfort of
your own holden.” Add a Testing Library smoke test that asserts both strings.
Create a `Makefile` whose `dev` and `test` targets run `pnpm dev` and
`pnpm test`; Task 9 adds the end-to-end target.

- [ ] **Step 5: Add configuration and ignore rules**

Set `.node-version` to `22`, `.python-version` to `3.12`, and document
`HOLDEN_REEL_DATA_DIR`, `HOLDEN_REEL_FFMPEG_BIN`, and
`HOLDEN_REEL_FFPROBE_BIN` in `.env.example`. Ignore `.env`, `.venv`,
`__pycache__`, `.pytest_cache`, `node_modules`, `dist`, `playwright-report`,
`test-results`, and `.DS_Store`. Do not add the existing untracked `.DS_Store`.

- [ ] **Step 6: Install, lock, and run both smoke tests**

Run: `pnpm install && cd apps/api && uv lock && uv run pytest tests/test_health.py -q && cd ../web && pnpm test --run`

Expected: API test passes; web smoke test passes; `pnpm-lock.yaml` and
`apps/api/uv.lock` are created.

- [ ] **Step 7: Commit**

```bash
git add .gitignore .env.example .node-version .python-version Makefile package.json pnpm-lock.yaml pnpm-workspace.yaml apps/api apps/web
git commit -m "build: scaffold local web application"
```

---

### Task 2: Persist projects and expose project APIs

**Files:**
- Create: `apps/api/src/holden_reel/db.py`
- Create: `apps/api/src/holden_reel/errors.py`
- Create: `apps/api/src/holden_reel/projects.py`
- Create: `apps/api/src/holden_reel/api.py`
- Create: `apps/api/tests/conftest.py`
- Create: `apps/api/tests/test_projects.py`
- Modify: `apps/api/src/holden_reel/main.py`

**Interfaces:**
- Consumes: `Settings.data_dir` from Task 1.
- Produces: `Project(id: UUID, name: str, created_at: datetime, updated_at: datetime)`, `ProjectService.create(name: str) -> Project`, `ProjectService.list() -> list[Project]`, `ProjectService.get(project_id: UUID) -> Project`, `POST /api/projects`, `GET /api/projects`, and `GET /api/projects/{project_id}`.

- [ ] **Step 1: Write failing project API tests**

```python
def test_create_and_reopen_project(client):
    created = client.post("/api/projects", json={"name": "August rehearsal"})
    assert created.status_code == 201
    project = created.json()

    reopened = client.get(f"/api/projects/{project['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["name"] == "August rehearsal"


def test_blank_project_name_uses_error_contract(client):
    response = client.post("/api/projects", json={"name": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_project_name"
```

`client` must construct `Settings(data_dir=tmp_path)` so tests cannot touch the
real application directory.

- [ ] **Step 2: Run the tests and confirm the missing route failure**

Run: `cd apps/api && uv run pytest tests/test_projects.py -q`

Expected: FAIL with 404 responses for `/api/projects`.

- [ ] **Step 3: Implement SQLite initialization and the project repository**

`open_database(path: Path) -> sqlite3.Connection` enables foreign keys, uses
row objects, and creates a `schema_migrations` table. Migration 1 creates:

```sql
CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Use explicit repository methods `insert`, `list_all`, and `get`. Serialize
timestamps as UTC ISO 8601 strings. `ProjectService.create` strips the name and
raises `DomainError("invalid_project_name", ..., status_code=422)` when empty.

- [ ] **Step 4: Add the API error envelope and routes**

```python
class DomainError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
```

Register one exception handler for `DomainError` and one for FastAPI's
`RequestValidationError`; both emit the global error shape. Validation failures
use code `invalid_request`, message `Request validation failed`, and put
Pydantic's serializable error list in `details.errors`. Build an
`APIRouter(prefix="/api")`, inject `ProjectService` from `app.state`, and return
201 for project creation and 404 code `project_not_found` for unknown IDs.

- [ ] **Step 5: Run project and health tests**

Run: `cd apps/api && uv run pytest tests/test_health.py tests/test_projects.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/holden_reel apps/api/tests
git commit -m "feat: persist local projects"
```

---

### Task 3: Catalog source media in place with FFprobe

**Files:**
- Create: `apps/api/src/holden_reel/media.py`
- Create: `apps/api/tests/fixture_media.py`
- Create: `apps/api/tests/test_media.py`
- Modify: `apps/api/src/holden_reel/db.py`
- Modify: `apps/api/src/holden_reel/api.py`
- Modify: `apps/api/tests/conftest.py`

**Interfaces:**
- Consumes: project IDs from Task 2 and `Settings.ffprobe_bin` from Task 1.
- Produces: `MediaAsset(id, project_id, path, kind, duration_ms, width, height, codec, available, fingerprint)`, `FFprobe.probe(path: Path) -> ProbeResult`, `MediaService.import_path(project_id: UUID, path: Path) -> list[MediaAsset]`, `POST /api/projects/{project_id}/media/import`, and `GET /api/projects/{project_id}/media`.

- [ ] **Step 1: Generate deterministic test media**

Implement `generate_fixture_media(root: Path) -> dict[str, Path]` using argument
arrays to create:

- `red.mp4`: 4 seconds, 320×240, 30 fps, red color source, H.264
- `blue.mp4`: 4 seconds, 240×320, 30 fps, blue color source, H.264
- `still.jpg`: 320×240 yellow frame
- `song.wav`: 18 seconds, 440 Hz sine wave, PCM

Skip with a clear pytest message if FFmpeg is unavailable.
Add a `__main__` entry that accepts `--output PATH`, generates the same files,
and prints one JSON object mapping fixture names to absolute paths. This is the
single fixture-generation entry point used later by Playwright.

- [ ] **Step 2: Write failing probe and import tests**

```python
def test_import_folder_catalogs_supported_media_without_copying(client, media_fixture):
    project = client.post("/api/projects", json={"name": "Fixture"}).json()
    response = client.post(
        f"/api/projects/{project['id']}/media/import",
        json={"path": str(media_fixture.root)},
    )

    assert response.status_code == 201
    assets = response.json()["assets"]
    assert {asset["kind"] for asset in assets} == {"video", "image", "audio"}
    assert all(Path(asset["path"]).is_relative_to(media_fixture.root) for asset in assets)
```

Also test one missing path (`media_path_not_found`, 404), one unsupported text
file (ignored when importing a directory), re-import idempotence, and
`available=false` after a fixture file is renamed.

- [ ] **Step 3: Run media tests and verify route failures**

Run: `cd apps/api && uv run pytest tests/test_media.py -q`

Expected: FAIL because the import route and probe adapter do not exist.

- [ ] **Step 4: Add media persistence and fingerprinting**

Migration 2 creates `media_assets` with a unique `(project_id, path)` pair,
technical metadata columns, `size_bytes`, `modified_ns`, and `fingerprint`.
For this milestone, fingerprint the UTF-8 resolved path, file size, and nanosecond
mtime with SHA-256. Do not hash whole media files.

- [ ] **Step 5: Implement safe FFprobe inspection**

Run:

```python
[
    ffprobe_bin, "-v", "error", "-show_format", "-show_streams",
    "-of", "json", str(path),
]
```

Parse JSON into a typed `ProbeResult`. Classify by usable stream: video when a
video stream has duration, image when a single-frame video stream or supported
image suffix has no timed duration, and audio when it has audio but no timed
video. Return `unsupported_media` for a selected unsupported file and skip it
inside directory imports. Sort directory candidates by case-folded path for
deterministic IDs/order.

- [ ] **Step 6: Implement import/list routes and availability refresh**

Accept only absolute paths. Resolve them before storing. Directory traversal is
non-recursive in this milestone and considers `.mp4`, `.mov`, `.m4v`, `.webm`,
`.jpg`, `.jpeg`, `.png`, `.wav`, `.mp3`, `.m4a`, and `.aac`. Listing assets
checks `Path.exists()` and returns the current `available` value without
deleting stale rows.

- [ ] **Step 7: Run media tests and the API suite**

Run: `cd apps/api && uv run pytest -q`

Expected: all API tests pass; source fixture sizes and mtimes are unchanged by
import.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/holden_reel apps/api/tests
git commit -m "feat: catalog local media in place"
```

---

### Task 4: Define, validate, and persist deterministic reel plans

**Files:**
- Create: `apps/api/src/holden_reel/plans.py`
- Create: `apps/api/tests/test_plans.py`
- Modify: `apps/api/src/holden_reel/db.py`
- Modify: `apps/api/src/holden_reel/api.py`

**Interfaces:**
- Consumes: `MediaAsset` lookup from Task 3.
- Produces: `ReelPlan`, `Shot`, `AudioBed`, `PlanValidator.validate(plan, assets) -> None`, `PlanService.compose(project_id, request) -> ReelPlan`, `PlanService.get(plan_id) -> ReelPlan`, `POST /api/projects/{project_id}/plans/compose`, and `GET /api/plans/{plan_id}`.

- [ ] **Step 1: Write failing plan-validation tests**

```python
def test_plan_rejects_gap_between_shots(valid_plan, assets):
    broken = valid_plan.model_copy(deep=True)
    broken.shots[1].output_start_ms += 100

    with pytest.raises(DomainError) as error:
        PlanValidator().validate(broken, assets)

    assert error.value.code == "invalid_reel_plan"
    assert error.value.details["violations"] == ["shots must cover output without gaps"]
```

Add exact tests for duration not in `{15000, 30000}`, offline assets, source
range beyond video duration, negative time, missing audio coverage, overlap,
still-image shots without source ranges, and portrait/landscape assets.

- [ ] **Step 2: Run the validation tests and verify failure**

Run: `cd apps/api && uv run pytest tests/test_plans.py -q`

Expected: FAIL because `holden_reel.plans` does not exist.

- [ ] **Step 3: Define the versioned plan schema**

```python
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
```

- [ ] **Step 4: Implement the deterministic composer**

`ComposePlanRequest` contains `duration_ms`, `audio_asset_id`,
`audio_start_ms`, and ordered `visual_asset_ids`. Require at least one usable
visual and enough source audio. Divide the output at `min(3000, duration / N)`
milliseconds, cycle through visuals when necessary, and make the last shot end
exactly at `duration_ms`. Video sources start at zero in Milestone 1 and clamp
each shot to the source duration; stills use `slow_zoom` with null source
ranges. If a video is too short, move to the next visual; fail with
`insufficient_usable_media` if coverage cannot be completed.

- [ ] **Step 5: Persist plan JSON and expose compose/get routes**

Migration 3 creates `reel_plans(id, project_id, version, plan_json, created_at)`
and a unique `(project_id, version)` constraint. Store canonical JSON from
`model_dump_json()`. Validate before insertion. Return 201 from compose.

- [ ] **Step 6: Run the plan tests and API suite**

Run: `cd apps/api && uv run pytest tests/test_plans.py -q && uv run pytest -q`

Expected: all tests pass; two identical compose requests produce equivalent
shot timing but separate plan IDs and sequential versions.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/holden_reel apps/api/tests/test_plans.py
git commit -m "feat: compose validated reel plans"
```

---

### Task 5: Compile reel plans into safe FFmpeg renders

**Files:**
- Create: `apps/api/src/holden_reel/artifacts.py`
- Create: `apps/api/src/holden_reel/renderer.py`
- Create: `apps/api/tests/test_renderer.py`
- Modify: `apps/api/tests/conftest.py`

**Interfaces:**
- Consumes: `ReelPlan`, media lookup, `Settings.data_dir`, and FFmpeg/FFprobe binary settings.
- Produces: `ArtifactStore.path_for(project_id, kind, artifact_id, suffix) -> Path`, `RenderProfile(name, width, height, fps, video_codec, audio_codec)`, `Renderer.render(plan, profile, output_path, on_progress, is_cancelled) -> RenderResult`, and `Renderer.verify(output_path, expected_duration_ms, profile) -> RenderResult`.

- [ ] **Step 1: Write failing artifact-path and command tests**

```python
def test_artifact_store_rejects_path_escape(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        store.path_for(PROJECT_ID, "preview", "../escape", ".mp4")


def test_compiler_uses_argument_array_and_vertical_cover(valid_plan, assets):
    command = FFmpegCompiler("ffmpeg").compile(valid_plan, assets, PREVIEW, Path("out.mp4"))
    assert isinstance(command, list)
    assert command[0] == "ffmpeg"
    assert "scale=540:960:force_original_aspect_ratio=increase" in " ".join(command)
    assert "crop=540:960" in " ".join(command)
```

Add tests that paths containing spaces remain one argument, image inputs use
looping, final profile uses 1080×1920, audio is trimmed to the plan range, and
the output path is never equal to a source path.

- [ ] **Step 2: Run renderer unit tests and verify failure**

Run: `cd apps/api && uv run pytest tests/test_renderer.py -q`

Expected: FAIL because artifact and renderer modules do not exist.

- [ ] **Step 3: Implement safe artifact paths and render profiles**

Define `PREVIEW` as 540×960, 30 fps, H.264/AAC, CRF 28 and `FINAL` as
1080×1920, 30 fps, H.264/AAC, CRF 18. Build paths under
`data_dir/projects/{project_id}/{previews|exports}/`. Accept only UUID artifact
IDs and fixed suffixes. Create parent directories, but do not create the output
file before FFmpeg runs.

- [ ] **Step 4: Implement the FFmpeg compiler and runner**

For every visual input, trim video or loop an image, normalize to the profile
FPS and pixel format, scale-to-cover, center crop, and set sample aspect ratio
to 1. Concatenate normalized shot streams in plan order. Trim the selected
audio interval, reset timestamps, pad only when floating-point rounding leaves
less than one audio frame, and use `-shortest` plus an explicit output duration.

Run FFmpeg with `-progress pipe:1 -nostats`, parse `out_time_ms`, and report a
float from 0 through 1. Check the injected `is_cancelled() -> bool` callback at
each progress update; terminate FFmpeg, wait up to five seconds, then kill it if
needed. Write to `{output}.partial.mp4`; on success, verify it and replace the
final path with `Path.replace`. On cancellation or failure, delete only the
partial output.

- [ ] **Step 5: Write and pass real render verification tests**

Render a 15-second plan from the generated red video, blue video, still, and
song. Use FFprobe JSON to assert:

```python
assert result.width == 540
assert result.height == 960
assert result.video_codec == "h264"
assert result.audio_codec == "aac"
assert abs(result.duration_ms - 15_000) <= 100
assert result.size_bytes > 0
```

Also assert all fixture source hashes are identical before and after rendering.

Run: `cd apps/api && uv run pytest tests/test_renderer.py -q`

Expected: all renderer tests pass and create output only below the temporary
test data directory.

- [ ] **Step 6: Run the complete API suite**

Run: `cd apps/api && uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/holden_reel/artifacts.py apps/api/src/holden_reel/renderer.py apps/api/tests
git commit -m "feat: render validated reel plans"
```

---

### Task 6: Add persisted preview/export jobs and HTTP endpoints

**Files:**
- Create: `apps/api/src/holden_reel/jobs.py`
- Create: `apps/api/tests/test_jobs.py`
- Modify: `apps/api/src/holden_reel/db.py`
- Modify: `apps/api/src/holden_reel/api.py`
- Modify: `apps/api/src/holden_reel/main.py`

**Interfaces:**
- Consumes: `PlanService.get`, `Renderer.render`, `ArtifactStore`, and database lifecycle.
- Produces: `Job(id, project_id, kind, status, progress, plan_id, artifact_path, error, created_at, updated_at)`, `JobService.submit_render(plan_id, profile) -> Job`, `JobService.get(job_id) -> Job`, `JobService.cancel(job_id) -> Job`, `POST /api/plans/{plan_id}/renders`, `GET /api/jobs/{job_id}`, and `POST /api/jobs/{job_id}/cancel`.

- [ ] **Step 1: Write failing job lifecycle tests with a fake renderer**

```python
def test_render_job_reaches_succeeded(job_service, fake_renderer, plan):
    job = job_service.submit_render(plan.id, profile="preview")
    finished = wait_for_terminal_job(job_service, job.id)

    assert finished.status == "succeeded"
    assert finished.progress == 1.0
    assert finished.artifact_path.endswith(".mp4")
```

Add tests for queued → running → succeeded, renderer exception → failed with
code `render_failed`, cancellation → cancelled, unknown job → 404, and startup
recovery changing stale `running` rows to `failed` with code
`application_restarted`.

- [ ] **Step 2: Run job tests and verify failure**

Run: `cd apps/api && uv run pytest tests/test_jobs.py -q`

Expected: FAIL because `holden_reel.jobs` does not exist.

- [ ] **Step 3: Add job persistence and a single-worker executor**

Migration 4 creates a `jobs` table with the fields in the interface. Use one
`ThreadPoolExecutor(max_workers=1)`. Persist every state transition and throttle
progress writes to at most four per second. A `threading.Event` keyed by job ID
drives the renderer's `is_cancelled` callback. `JobService.close()` sets all
cancellation events and calls `executor.shutdown(wait=False,
cancel_futures=True)`; FastAPI lifespan calls it on shutdown.

- [ ] **Step 4: Add render/job routes**

`POST /api/plans/{plan_id}/renders` accepts `{"profile":"preview"}` or
`{"profile":"final"}`, returns 202, and exposes the job URL in the response.
`GET /api/jobs/{job_id}` returns current state. Cancellation returns the updated
job; cancelling a terminal job is idempotent.

- [ ] **Step 5: Pass fake-renderer job and API tests**

Run: `cd apps/api && uv run pytest tests/test_jobs.py -q`

Expected: all job tests pass without invoking real FFmpeg.

- [ ] **Step 6: Add one real preview-job integration test**

Import generated fixture media, compose a plan, submit a preview render through
HTTP, poll with a two-minute test timeout, and verify the returned artifact path
exists below `tmp_path` and passes `Renderer.verify`.

Run: `cd apps/api && uv run pytest tests/test_jobs.py -q`

Expected: fake and real job tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/holden_reel apps/api/tests/test_jobs.py
git commit -m "feat: run persisted render jobs"
```

---

### Task 7: Build the project and local-folder import interface

**Files:**
- Create: `apps/web/src/types.ts`
- Create: `apps/web/src/api.ts`
- Create: `apps/web/src/features/projects/ProjectStart.tsx`
- Create: `apps/web/src/features/projects/ProjectStart.test.tsx`
- Create: `apps/web/src/features/import/MediaImport.tsx`
- Create: `apps/web/src/features/import/MediaImport.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/app.css`

**Interfaces:**
- Consumes: project and media HTTP contracts from Tasks 2 and 3.
- Produces: `ApiClient`, `ProjectStart({api,onOpen})`, and `MediaImport({api,project,onReady})`; `onReady` returns `{assets: MediaAsset[], audioAssetId: string, visualAssetIds: string[]}`.

- [ ] **Step 1: Write the failing project-start component test**

```tsx
it("creates a named project and advances", async () => {
  const api = fakeApi({ createProject: { id: "p1", name: "Rehearsal" } });
  const onOpen = vi.fn();
  render(<ProjectStart api={api} onOpen={onOpen} />);

  await userEvent.type(screen.getByLabelText(/project name/i), "Rehearsal");
  await userEvent.click(screen.getByRole("button", { name: /create project/i }));

  expect(api.createProject).toHaveBeenCalledWith("Rehearsal");
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "p1" }));
});
```

- [ ] **Step 2: Write the failing import component tests**

Test an absolute folder path submission, visible import error, media cards with
kind/dimensions/duration, audio selection, visual selection, offline badge, and
the Continue button disabled until one audio and one visual asset are selected.

Run: `pnpm --dir apps/web test --run`

Expected: FAIL because the components and client do not exist.

- [ ] **Step 3: Implement the typed API client**

Define exact TypeScript types matching the API JSON field names. `request<T>`
sets JSON headers, parses successful JSON, and throws:

```ts
export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown>,
  ) { super(message); }
}
```

Implement `createProject`, `listProjects`, `getProject`, `importMedia`, and
`listMedia` methods. Do not use `any`.

- [ ] **Step 4: Implement the two guided screens**

`ProjectStart` shows recent projects plus a create form. `MediaImport` explains
that files remain in place, uses a labeled absolute-path input for this local
milestone, renders accessible media cards, and clearly separates audio from
visual selection. Use native controls before custom interactions.

- [ ] **Step 5: Add the visual foundation**

Define CSS custom properties for one dark neutral background, a warm surface,
an electric accent, readable foreground/muted colors, 8/12/16/24/32 spacing,
12px radii, and a single fast transition. Ensure focus rings are visible,
buttons have 44px minimum targets, and the layout works at 375px and 1280px.
Respect `prefers-reduced-motion`.

- [ ] **Step 6: Run component tests and type checking**

Run: `pnpm --dir apps/web test --run && pnpm --dir apps/web exec tsc --noEmit`

Expected: all tests pass and TypeScript reports no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src
git commit -m "feat: add project media import flow"
```

---

### Task 8: Build the deterministic draft, preview, and export workspace

**Files:**
- Create: `apps/web/src/useJob.ts`
- Create: `apps/web/src/useJob.test.tsx`
- Create: `apps/web/src/features/draft/DraftWorkspace.tsx`
- Create: `apps/web/src/features/draft/DraftWorkspace.test.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/app.css`
- Modify: `apps/api/src/holden_reel/api.py`

**Interfaces:**
- Consumes: compose/get plan routes from Task 4 and render/job routes from Task 6.
- Produces: API client methods `composePlan`, `startRender`, `getJob`, and `cancelJob`; `useJob(api, jobId)`; and `DraftWorkspace({api,project,selection})`.

- [ ] **Step 1: Expose rendered artifacts through a safe API route**

Add `GET /api/jobs/{job_id}/artifact`. It returns 409 code `artifact_not_ready`
unless the job succeeded, confirms the resolved path is inside the configured
data directory, and returns `FileResponse` with `video/mp4` and a safe filename.
Write API tests for not-ready, missing artifact, traversal rejection, and a
successful download.

- [ ] **Step 2: Write failing job-hook tests with fake timers**

```tsx
it("polls until the job succeeds", async () => {
  vi.useFakeTimers();
  const api = fakeApi({ jobStates: [queuedJob, runningJob, succeededJob] });
  const { result } = renderHook(() => useJob(api, "j1"));

  await vi.advanceTimersByTimeAsync(2_000);
  expect(result.current.job?.status).toBe("succeeded");
  expect(result.current.error).toBeNull();
});
```

Also test failed state, cancellation, component unmount, and no further polling
after a terminal state.

- [ ] **Step 3: Write failing draft-workspace tests**

Test 15/30-second choice, audio start seconds, ordered visual selection,
Generate Draft, visible progress/cancel, preview video URL after success, Export
button, final download link, retry after failure, and the plan rationale.

Run: `pnpm --dir apps/web test --run`

Expected: FAIL because hook/workspace methods are missing.

- [ ] **Step 4: Implement API methods and the polling hook**

Poll every 750 ms while queued/running. Abort in-flight fetches on unmount.
Surface `ApiError` without converting it to an opaque string. The cancel action
calls the API once and then refreshes the job.

- [ ] **Step 5: Implement the draft workspace**

Compose the deterministic plan from selected assets and numeric audio start.
Show the ordered shot list and rationale before rendering. Generate a preview,
display progress with `<progress>`, attach the succeeded artifact URL to a
9:16 `<video controls>`, and submit a separate final-profile job for export.
The final artifact link uses `download` and never replaces the source path.

- [ ] **Step 6: Run frontend and artifact-route tests**

Run: `cd apps/api && uv run pytest -q && cd ../web && pnpm test --run && pnpm exec tsc --noEmit`

Expected: all API and web tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/holden_reel/api.py apps/api/tests apps/web/src
git commit -m "feat: preview and export deterministic reels"
```

---

### Task 9: Verify the complete local workflow and document operation

**Files:**
- Create: `apps/api/tests/test_vertical_slice.py`
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/global-setup.ts`
- Create: `apps/web/e2e/vertical-slice.spec.ts`
- Create: `docs/development.md`
- Modify: `README.md`
- Modify: `Makefile`
- Modify: `package.json`
- Modify: `apps/web/package.json`

**Interfaces:**
- Consumes: all Milestone 1 APIs and screens.
- Produces: `make dev`, `make test`, `make test-e2e`, and a documented local ingest-to-export workflow.

- [ ] **Step 1: Add the backend vertical-slice acceptance test**

The test must create fixture media, create a project through HTTP, import the
folder, compose a 15-second plan, submit a final render, poll to success, fetch
the artifact, and run FFprobe assertions for MP4/H.264/AAC/1080×1920/duration.
Record source hashes before import and assert them again after export.

Run: `cd apps/api && uv run pytest tests/test_vertical_slice.py -q`

Expected: PASS within two minutes on a developer machine with FFmpeg installed.

- [ ] **Step 2: Add the failing Playwright workflow test**

`global-setup.ts` creates a directory below the repository's ignored
`test-results` directory, runs
`uv run python tests/fixture_media.py --output ABSOLUTE_PATH` with
`cwd=apps/api`, parses the JSON manifest, and sets the absolute folder path in a
small `test-results/fixture.json` file. The test reads that path, creates
“Golden Reel,” imports it, selects the song and visuals, generates a 15-second
preview, waits for playback to appear, exports, and asserts the download
filename ends in `.mp4`.

Run: `pnpm --dir apps/web exec playwright test e2e/vertical-slice.spec.ts`

Expected on the first run: FAIL until Playwright web-server commands and stable
accessible selectors are configured.

- [ ] **Step 3: Configure end-to-end servers and make the test pass**

Configure Playwright to start the API with a temporary
`HOLDEN_REEL_DATA_DIR` below `apps/web/test-results`, start Vite, run the global
setup before the servers, use one worker, retain traces on failure, and allow a
two-minute render timeout. Use `getByRole`/`getByLabel` selectors; do not add
test-only production branches.

Run: `pnpm --dir apps/web exec playwright test e2e/vertical-slice.spec.ts`

Expected: one end-to-end test passes and the downloaded MP4 is nonempty.

- [ ] **Step 4: Document exact setup and commands**

`docs/development.md` must cover Node 22, pnpm 10, uv, Python 3.12, and FFmpeg
7+ prerequisites; install commands; configuration variables; `make dev` with
two local URLs/processes; fixture tests; end-to-end tests; application data
location; generated-artifact cleanup; and the fact that import accepts an
absolute local path in this milestone.

Update README status to mark the deterministic vertical slice complete only
after the acceptance commands pass. Link `docs/development.md` from README.

- [ ] **Step 5: Run the full verification suite**

Run:

```bash
make test
make test-e2e
git diff --check
```

Expected: API tests, web unit tests, TypeScript checks, and Playwright all pass;
`git diff --check` emits no output.

- [ ] **Step 6: Manually smoke-test the local application**

Run `make dev`, open the printed local URL, and complete the workflow with the
generated fixture folder. Confirm the app remains bound to loopback, progress
is visible, cancellation is available, the preview plays, the final export is
1080×1920, and fixture source hashes remain unchanged. Record the elapsed time
and any UX friction in the commit body.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/development.md Makefile package.json apps/api/tests/test_vertical_slice.py apps/web
git commit -m "test: verify deterministic reel workflow"
```

---

## Milestone 1 completion gate

Milestone 1 is complete only when all of the following are true:

- A fresh clone can install dependencies using the documented commands.
- The API binds to `127.0.0.1` by default.
- A user can create and reopen a project.
- Import references a small local folder without copying or modifying sources.
- Mixed video, image, and audio fixtures are cataloged with FFprobe metadata.
- A deterministic, persisted, validated 15- or 30-second reel plan is visible.
- Preview and final rendering run as observable, cancellable persisted jobs.
- The preview plays in the browser and the final MP4 can be downloaded.
- FFprobe confirms MP4, H.264, AAC, 1080×1920, and requested duration.
- Unit, integration, acceptance, and Playwright tests pass.
- No test or render writes outside its configured application-data directory.
- No remote AI service or user media is required by the test suite.

## Follow-up plans

After this vertical slice is validated with real use, write separate plans for:

1. Waveforms, beat/section analysis, recommended musical moments, and proxy
   preparation.
2. The constrained creative agent, deterministic scoring, one automatic
   revision, consent boundary, and first trusted AI-provider adapter.
3. Shot refinement, plan-version undo/redo, style controls, and band brand kit.
4. Failure hardening, timed usability acceptance, and measured external USB
   3.x performance.

Do not pull those features into Milestone 1 unless the approved design changes.
