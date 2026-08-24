# Subject-aware crop and simple transitions

**Date:** 2026-08-24  
**Status:** Approved design, pending implementation plan  
**Scope:** Local subject-aware fixed crops and one reel-level transition choice

## 1. Problem

Holden Reel currently scales every visual to cover the vertical canvas and
then crops from its geometric center. A performer or other important subject
away from the center can be removed from the reel. Shot boundaries are plain
FFmpeg concatenations, so the interface also offers no transition choice.

The next slice must keep an interesting subject visible in a stable portrait
crop and let the user choose either a clean cut or a restrained quick dissolve.
The work remains local, deterministic, and suitable for a modest development
machine.

## 2. Goals

- Choose one stable focus point for each imported visual asset.
- Prefer faces, then people, then a motion/contrast-derived fallback.
- Fill the complete 9:16 frame without letterboxing or black space.
- Cache analysis and repeat it only when the source fingerprint or analyzer
  version changes.
- Store crop and transition decisions in the reel plan so preview and final
  renders are identical and reproducible.
- Offer exactly two reel-level transition styles: `cut` and `dissolve`.
- Preserve exact 15- and 30-second output durations.
- Keep all source media and analysis data local.

## 3. Non-goals

- Tracking or panning the crop during a shot
- Manual crop editing
- Per-shot or per-boundary transition selection
- Additional transition styles
- Cloud vision APIs or downloaded object-detection model weights
- Changes to audio transitions, ducking, or mixing

## 4. Focus analysis

### 4.1 Service boundary

A new `FocusAnalyzer` owns OpenCV use. Callers receive only a small result:

- normalized horizontal focus `x` in the inclusive range `0.0..1.0`
- normalized vertical focus `y` in the inclusive range `0.0..1.0`
- confidence in the inclusive range `0.0..1.0`
- analyzer version
- analysis method: `face`, `person`, `motion`, `contrast`, or `center`

The media catalog persists this result alongside the source fingerprint. A
changed fingerprint or analyzer version invalidates the cached result.

### 4.2 Images

The analyzer decodes a bounded, downscaled image and evaluates candidates in
this order:

1. detected faces
2. detected people
3. contrast and edge concentration
4. geometric center

Multiple face/person detections are combined into a subject region. Candidate
weight favors confident, larger detections without allowing one weak detection
to pull the focus far away from the group.

### 4.3 Video

The analyzer samples a small fixed number of evenly distributed, low-resolution
frames without decoding the entire asset. Per-frame candidates use the same
face/person priority as images. If those detections are absent, temporal motion
between sampled frames is combined with contrast. Candidate positions are
aggregated with a robust median/weighted center to produce one fixed point for
the asset; the crop never tracks or pans inside a shot.

Sampling and analysis have explicit time and frame-count bounds. A decode,
timeout, or detection failure returns a center result rather than failing media
import.

### 4.4 Local dependency

The API adds a locked headless OpenCV dependency. It uses bundled classical
face/person detectors and local motion/contrast analysis. It does not fetch
models at runtime or transmit media.

## 5. Crop semantics

Each composed `Shot` copies the asset focus coordinates and analysis method.
This freezes the creative decision inside the versioned plan even if the media
is analyzed again later.

The renderer continues to scale each source with
`force_original_aspect_ratio=increase`. It calculates the scaled dimensions,
centers the output-sized crop window on the normalized focus point, and clamps
the crop origin to the valid source bounds. The output is always completely
filled; no pad filter or black background is used.

If a plan created before this feature lacks focus fields, plan decoding defaults
to `(0.5, 0.5)` and retains the previous center-crop behavior.

## 6. Transition semantics

`ReelPlan` and `ComposePlanRequest` gain one transition style:

- `cut`: zero-duration boundary using direct visual concatenation
- `dissolve`: FFmpeg `xfade=transition=fade` with a fixed 200 ms duration

The draft interface presents these as **Clean cut** and **Quick dissolve**.
Clean cut is the default for new and older plans.

For cut plans, shot output intervals remain contiguous. For dissolve plans,
each adjacent pair overlaps by exactly 200 ms. The first shot starts at zero,
the final shot ends at the reel duration, and the plan validator requires each
interior boundary to have the declared overlap. Source ranges include the full
shot interval and must remain inside their assets.

The renderer chains normalized visual segments with `xfade`; calculated offsets
must reproduce the plan timeline and yield an exact 15- or 30-second result.
Audio remains one continuous bed and is not faded at shot boundaries.

Changing transition style invalidates the saved plan, preview, and final output
through the existing draft invalidation path. The control is locked while a
render is active.

## 7. Data and migration

The media catalog gains nullable focus coordinates, confidence, analysis method,
analyzer version, and the fingerprint associated with the analysis. Existing
rows remain valid and are analyzed on the next import when the analysis fields
are missing or stale.

Persisted reel-plan JSON gains backward-compatible shot focus fields and a
reel-level transition style. No destructive migration of existing plans is
required.

## 8. User flow

1. The user imports or re-imports a folder.
2. Holden Reel probes media and performs bounded local focus analysis, showing
   the existing import progress state.
3. The user selects soundtrack and visuals and continues.
4. In Draft settings, the user chooses Clean cut or Quick dissolve.
5. Generate draft stores focus and transition decisions in the plan.
6. Preview and final export render the same crop and transition behavior.

No manual focus control is added in this slice. A failed or low-confidence
analysis silently uses center framing; the plan summary may label this as
`center` so the decision remains inspectable.

## 9. Testing and acceptance

Unit and integration coverage must prove:

- face candidates outrank person and motion/contrast candidates
- person candidates outrank the fallback
- video aggregation returns one stable fixed point
- cached analysis is reused only for the same fingerprint and analyzer version
- failure and low confidence return `(0.5, 0.5)` without blocking import
- focus-derived cover crops are clamped and never add padding
- older plans decode with center focus and clean cuts
- cut plans require contiguous shots
- dissolve plans require exactly 200 ms adjacent overlap
- FFmpeg emits concat for cuts and `xfade` for dissolves
- final output duration remains exactly 15 or 30 seconds
- UI transition changes invalidate prior render state and are locked during jobs
- a generated off-center subject remains visible in a real portrait render
- frames around a real dissolve boundary contain blended content
- source hashes are unchanged after analysis and rendering

Full API, web, typecheck, and Playwright gates remain required. Manual acceptance
should re-import one of the user's existing Holden Reed `.mov` files, compare
center and subject-aware framing, and render both transition styles.

## 10. Deferred work

- Per-shot focus overrides and crop preview controls
- Tracking a moving subject through a shot
- Semantic object classes beyond people/faces
- Background blur or padded fit modes
- Per-boundary transition editing and more styles
- Moving focus analysis into a background job for very large libraries
