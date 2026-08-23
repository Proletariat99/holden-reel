# Holden Reel MVP Design

**Status:** Approved product design

**Date:** 2026-08-23

**Target:** Local-first MVP for creating Instagram Reels

## 1. Summary

Holden Reel turns a mixed folder of video clips, images, and finished audio into
an engaging 15–30 second vertical Instagram Reel. It is a guided creative tool,
not a general-purpose video editor: a constrained creative agent builds the
first beat-synced draft, then the user refines it through fast visual controls.

The MVP runs locally without authentication. Media remains on the machine by
default, rendering is local, and calls to trusted AI providers are optional and
explicit. The internal design remains modular so the same capabilities can
later support a conversational copilot, external automation, remote rendering,
and a hosted multi-user product.

## 2. Problem and outcome

Band media arrives as an unorganized mix of performance clips, backstage
footage, photos, and finished songs. Turning those assets into a short social
video currently requires sorting, selecting, timing, cropping, styling, and
rendering in a traditional editor. That effort is disproportionate to a
15–30 second post and makes regular publishing difficult.

Holden Reel succeeds when a band member can give it a small local folder of
mixed media and a song, then produce a postable Reel in under 10 minutes. This
10-minute target covers active use after the selected files have completed any
required initial proxy preparation.

The MVP optimizes for:

1. **Speed to a usable result:** a credible first draft arrives quickly.
2. **Creative control:** the user can make meaningful changes without learning
   a professional timeline editor.
3. **Approachability:** another band member can understand the workflow without
   formal training.

First-draft quality is measured during product iteration, with the eventual aim
that most drafts require no more than one or two small edits.

## 3. Product principles

### 3.1 Local first

Original assets stay where the user selected them and are never modified.
Project metadata, generated artifacts, and renders remain local unless the user
explicitly approves an AI-provider request.

### 3.2 Guided rather than fully automatic

The app recommends a song segment and creates a draft, but the user confirms
the musical moment and can change the media, timing, style, and text before
export.

### 3.3 Inspectable agent decisions

The agent produces a structured reel plan. Selection, timing, transitions,
crop behavior, and overlays are data, not hidden side effects of a prompt. A
draft can be explained, edited, reproduced, and rendered without another model
call.

### 3.4 Narrow modules and stable interfaces

Project storage, cataloging, analysis, planning, rendering, jobs, and AI
providers have separate responsibilities. The MVP avoids distributed-system
infrastructure, but its modules do not assume that every future job will run in
the local web process.

### 3.5 Measure before optimizing

Development begins with a small local folder. External USB 3.x media is tested
early, but elaborate cache eviction, library indexing, and network storage are
added only when measurements justify them.

## 4. User workflow

### 4.1 Create a project

The user names a project, selects a local folder or individual video/image
files, and chooses a finished audio track. They may enter a short creative brief
such as “high energy, emphasize crowd shots.”

The app references source files in place. It does not copy them into the
project workspace.

### 4.2 Prepare the media

The backend extracts:

- File type, codec, dimensions, duration, frame rate, and orientation
- Thumbnails and representative frames
- Audio waveform and loudness metadata
- Beat and section candidates for the chosen song
- Compact editing proxies when source formats or sizes justify them

Preparation runs as background jobs with visible progress and cancellation.
The user can browse completed media while other items continue processing.

### 4.3 Select the musical moment

The system recommends promising 15- and 30-second song segments, favoring
strong section boundaries and energetic passages. The user listens to the
recommendations, selects one, and can adjust its start point and duration on a
waveform.

### 4.4 Generate a draft

The user submits the selected song segment, optional creative brief, and saved
band style. The creative agent catalogs usable material, creates a beat-aligned
reel plan, validates it, renders a low-resolution preview, evaluates basic
quality signals, and may revise the plan once.

### 4.5 Refine

The guided editor supports:

- Replacing a shot from the media browser
- Reordering shots
- Adjusting source in/out points within valid ranges
- Choosing overall pacing
- Selecting a transition style and intensity
- Selecting a color treatment
- Editing title/caption text and choosing a text theme
- Applying or changing the saved band brand kit
- Regenerating with a short revised creative direction
- Undoing and redoing plan changes

The MVP does not expose a multitrack timeline, keyframes, effect graphs, or
detailed audio mixing.

### 4.6 Export

The user exports a 1080×1920 vertical MP4 with H.264 video and AAC audio. The
renderer reads original source media for final quality, applies the approved
reel plan, and writes a new file without modifying any source asset.

## 5. Experience design

The UI is a guided workspace with large visual targets, immediate feedback, and
one obvious primary action per state. The intended personality is quick,
playful, and confident without obscuring job progress or failures.

Primary views are:

1. **Projects:** recent local projects and a create action.
2. **Import:** selected media, readiness, warnings, song selection, and creative
   brief.
3. **Music moment:** waveform, recommended segments, 15/30-second duration, and
   preview playback.
4. **Draft workspace:** large reel preview, horizontal shot sequence, visual
   media browser, creative-direction input, and grouped style controls.
5. **Export:** final settings, render progress, validation result, and output
   location.
6. **Band style:** logo, font choices, colors, default text placement, pacing,
   transitions, and color treatment.

Controls remain disabled only when their prerequisites are unavailable, with a
specific explanation. Long work always shows progress and can be cancelled.

## 6. System architecture

### 6.1 Runtime shape

The local application has two processes:

- A React + TypeScript web client served locally and opened in the user's
  browser.
- A Python + FastAPI service that owns the application API, project services,
  job execution, media analysis, agent orchestration, and rendering.

The backend initially runs one in-process background job runner. Redis, Celery,
message brokers, containers, and remote workers are not MVP dependencies. Job
interfaces and persisted job state allow those implementations to change later.

### 6.2 Core modules

#### Project service

Creates and opens projects, owns project settings, records source references,
and exposes a consistent project snapshot to the UI.

Dependencies: repository interfaces, media catalog, job service.

#### Media catalog

Tracks source assets and derived artifacts. It resolves source availability,
records technical metadata, and never mutates source files.

Dependencies: file-system media store, SQLite repositories, FFprobe adapter.

#### Analysis service

Produces thumbnails, representative frames, proxies, waveforms, beat markers,
and musical-section candidates. Analysis results are versioned so algorithm
changes can invalidate only affected artifacts.

Dependencies: media catalog, FFmpeg adapter, local audio-analysis libraries,
artifact store.

#### Reel planner

Accepts the media catalog, music segment, creative brief, band style, and
provider policy. It creates and revises structured reel plans. It contains the
creative-agent loop but does not render or write source media.

Dependencies: analysis service interfaces, plan validator, optional AI provider
gateway.

#### Plan validator

Checks a reel plan without model calls. It enforces supported duration, valid
source ranges, output framing, media availability, shot-length bounds, audio
coverage, and renderer compatibility.

Dependencies: media catalog and schema definitions.

#### Renderer

Compiles a validated reel plan into an FFmpeg execution graph, renders proxy
previews or final output, and reports progress. It accepts plan data and asset
locations; it contains no creative decision logic.

Dependencies: FFmpeg adapter, media catalog, artifact store.

#### Job service

Runs preparation, planning, preview, and export work outside request/response
handlers. It persists state, progress, diagnostic messages, cancellation, and
retry metadata.

Dependencies: job repository and registered job handlers.

#### AI provider gateway

Defines trusted providers, estimates/request metadata, consent, redaction,
logging, timeouts, and fallback. Provider-specific adapters implement the same
interface.

Dependencies: provider SDK adapters, consent repository, project policy.

### 6.3 Persistence

SQLite stores:

- Projects and project settings
- Source media references and fingerprints
- Analysis result records and versions
- Reel plan versions and current selection
- Job state and diagnostics
- AI-provider policy, consent, request metadata, and estimated/actual usage when
  available
- Band brand-kit settings

The artifact store contains:

- Thumbnails and representative frames
- Waveforms and analysis sidecars
- Optional editing proxies
- Proxy previews
- Final exports

Binary media is not stored in SQLite.

## 7. Reel plan contract

The reel plan is a versioned JSON document. At minimum it contains:

- Schema version and plan identifier
- Project and source-analysis versions
- Output duration, dimensions, frame rate, and safe-area policy
- Music asset, start/end time, gain, and beat grid reference
- Ordered shots with source asset, source in/out, output in/out, crop/fit mode,
  focal point when known, and still-image motion behavior
- Transitions with type, duration, and parameters
- Text/image overlays with timing, content, style reference, and placement
- Global pacing, color-treatment, and brand-kit references
- Creative brief and a short agent rationale
- Validation state and warnings

Every mutation creates a new plan version. Undo and redo select earlier or later
versions rather than attempting to reverse FFmpeg operations.

## 8. Creative agent

### 8.1 MVP loop

The creative agent is bounded to the following sequence:

1. Inspect catalog metadata, proxy samples, beat markers, creative direction,
   and band style.
2. Choose candidate media moments and construct a reel plan through narrow
   planner tools.
3. Run the deterministic plan validator.
4. Render a low-resolution preview.
5. Evaluate beat alignment, repeated media, shot-length distribution, media
   variety, blank frames, and framing warnings.
6. Revise at most once when the evaluation identifies a correctable problem.
7. Persist the plan, preview, rationale, and warnings for user review.

Hard execution limits include a maximum tool-call count, one automatic
revision, provider timeouts, and user cancellation. A failed AI call falls back
to deterministic planning or returns a recoverable error; it never causes an
unbounded retry loop.

### 8.2 Local baseline

The system can produce a basic draft without a remote model by using beat
markers, technical-quality filters, scene/shot boundaries, visual-change
scores, orientation, and media-variety rules. This baseline supports offline
development, predictable tests, and provider failure recovery.

### 8.3 Future agent modes

The architecture prepares for but does not fully implement:

- A conversational copilot that converts feedback into reel-plan operations.
- An automation API or CLI that creates projects, starts jobs, monitors them,
  and exports results.

These interfaces must call the same services as the UI rather than bypassing
validation, consent, or persistence.

## 9. AI privacy boundary

Remote AI is disabled until a provider is configured and the user opts in.
Before a request, the UI identifies:

- The provider
- The purpose of the request
- The types and approximate amount of data to be sent
- Whether data includes sampled frames, transcript text, metadata, or the
  creative brief
- The best available cost estimate

Requests prefer sampled low-resolution proxy frames, derived metadata, and
transcripts over original media. Full original video or audio is outside the
MVP provider contract.

The project records consent, provider, model, purpose, request metadata, status,
and reported usage. Secrets are loaded from local configuration or environment
variables and are never stored in project files or logs.

Provider adapters must support timeouts, cancellation where available, response
validation, and a local fallback. Provider terms and retention policies are a
user configuration concern; Holden Reel does not silently treat a configured
provider as trusted for every data type.

## 10. Storage and performance

### 10.1 MVP behavior

The initial acceptance flow uses a small local folder or a handful of selected
files. Original media is referenced in place. Generated artifacts live in an
application-managed working directory and can be removed without affecting the
source library.

SQLite and working artifacts should default to a fast local disk. The project
records logical source references separately from generated artifact paths.

### 10.2 External-drive readiness

The expected larger source library is a 4 TB USB 3.x external drive. No special
optimization is assumed necessary before measurement. The design nevertheless
supports:

- Proxy-first interaction for large or expensive source formats
- Incremental analysis with versioned results
- Explicit missing/offline media states
- Relinking sources without rewriting reel plans
- A configurable bounded cache and least-recently-used eviction as a follow-up
- A future media-store implementation backed by a local Linux/network host

The first external-drive test measures catalog time, proxy throughput, preview
latency, final-render throughput, and working-set size. Optimization follows the
observed bottleneck.

## 11. Failures and recovery

The app preserves the project and reports a specific next action for:

- Missing or disconnected source files
- Unsupported or damaged media
- Insufficient usable footage for the requested duration
- Proxy or analysis failure
- Invalid reel plans
- AI-provider timeout, rejection, malformed output, or quota failure
- Preview or final-render failure
- Backend restart or user cancellation during a job

Jobs persist their last stable state. Idempotent analysis steps may retry. A
cancelled job leaves completed reusable artifacts intact and removes only
incomplete temporary outputs. Source media is never deleted or overwritten.

Final exports are written to a temporary file, validated, and atomically moved
to the selected output path when possible.

## 12. MVP scope

### 12.1 Included

- Local project creation and reopening
- File/folder import for common video, image, and audio formats supported by the
  bundled or installed FFmpeg build
- In-place source references and separate generated artifacts
- Metadata, thumbnails, representative frames, waveform, beat detection, and
  optional proxy preparation
- Recommended 15- and 30-second music sections with manual adjustment
- Short text creative brief
- Agent-generated beat-aligned draft using video and still images
- Deterministic validation and at most one automatic agent revision
- Proxy preview
- Replace, reorder, and trim shot controls
- Pacing, transition, color-treatment, and text-theme controls
- Reusable band brand kit
- Plan version history with undo/redo
- One opt-in provider adapter behind a provider-neutral gateway, initially
  targeting either OpenAI or Anthropic
- Provider-request consent and metadata history
- 1080×1920 H.264/AAC MP4 export
- Recoverable jobs and explicit missing-media handling

### 12.2 Excluded

- Authentication, accounts, permissions, and multiple users
- Remote collaboration
- Direct Instagram or TikTok publishing
- General-purpose multitrack editing, keyframes, advanced effects, or detailed
  audio mixing
- Cloud storage, network media, remote workers, or distributed queues
- Multiple aspect ratios and platform-specific variants
- Face recognition or identity labeling
- Training custom models
- Open-ended autonomous agent loops
- A full conversational editing experience
- A stable public automation API or CLI
- Large-library performance guarantees and automatic cache eviction

## 13. Testing strategy

### 13.1 Unit and contract tests

- Reel-plan schema parsing, migration, and validation
- Beat-to-shot timing and duration calculations
- Crop/fit and safe-area calculations
- Plan versioning and undo/redo selection
- Media-store and repository contracts
- Provider gateway consent, redaction, timeout, and fallback behavior
- Job state transitions, cancellation, and retry rules

### 13.2 Integration tests

Small licensed or generated fixture media covers video, still images, audio,
portrait/landscape input, awkward frame rates, missing files, and one unsupported
asset. Tests exercise cataloging, analysis, planning, preview, restart recovery,
and export.

### 13.3 Export verification

Every automated export test runs FFprobe and asserts:

- MP4 container
- H.264 video and AAC audio
- 1080×1920 dimensions
- Duration within the selected 15- or 30-second tolerance
- Expected frame rate and nonempty video/audio streams
- No missing source ranges or renderer errors

### 13.4 Golden project

A small checked-in or reproducibly generated “golden project” provides stable
inputs, a fixed reel plan, a preview, and export metadata. It supports manual
visual review and catches unexpected renderer changes without requiring brittle
pixel-identical video assertions.

### 13.5 Workflow acceptance

During a timed manual test, a band member who did not implement the feature can:

1. Create a project from a small local mixed-media folder.
2. Choose or adjust a recommended song segment.
3. Generate and preview a valid draft.
4. Make at least one content change and one style change.
5. Export a technically valid, postable Reel.

The active workflow completes in under 10 minutes once required preparation is
ready. Preparation time is recorded separately so proxy performance can be
improved without obscuring usability.

## 14. Delivery milestones

### Milestone 1: Deterministic vertical slice

Create/open a local project, catalog a small fixture set, author a simple reel
plan, render a proxy preview, and export a valid vertical MP4. No remote AI is
required.

### Milestone 2: Analysis and guided music selection

Add waveforms, beat/section analysis, recommended 15/30-second segments,
background preparation, progress, and cancellation.

### Milestone 3: Creative-agent draft

Add candidate selection, planner tools, validation, evaluation, one automatic
revision, local fallback behavior, and the first trusted provider adapter with
explicit consent.

### Milestone 4: Refinement and identity

Add shot replacement/reordering/trimming, style controls, band brand kit,
plan-version undo/redo, and regeneration from revised creative direction.

### Milestone 5: Hardening

Exercise failure recovery, run the timed acceptance workflow, validate the
external USB 3.x drive, profile actual bottlenecks, and improve only the
measured constraints.

## 15. Deferred decisions

The following decisions are intentionally made during implementation planning
or the relevant milestone, when evidence is available:

- Exact React build framework and component library
- Python audio-analysis library selection
- Whether FFmpeg is bundled or documented as a system prerequisite for the
  first developer release
- Initial trusted AI provider and model
- Exact application data-directory location by operating system
- Cache-size default after external-drive measurement
- Packaging approach after the local browser workflow is validated

Each decision must preserve the module boundaries and privacy requirements in
this design.

## 16. Acceptance of this design

The approved direction is a local React/TypeScript web UI with a Python/FastAPI
media and agent backend; SQLite metadata; FFmpeg-based deterministic rendering;
an editable reel-plan contract; optional, consented trusted AI; and a guided
music-first workflow. The first implementation target uses a small local media
set while remaining structurally ready for the external USB 3.x library and
future agentic interfaces.
