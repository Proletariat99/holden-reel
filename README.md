# Holden Reel

Make reels from the comfort of your own holden.

Holden Reel is a local-first creative tool that turns a loose folder of video,
audio, and images into an engaging 15–30 second Instagram Reel. It combines a
guided, playful editing experience with a constrained creative agent: the app
finds a promising section of a song, plans cuts around the beat, creates a
draft, checks its work, and gives the user fast controls to refine the result.

The first goal is simple: go from a small, messy folder of band media to a
postable Reel in under 10 minutes.

> [!NOTE]
> The deterministic local ingest-to-export vertical slice is complete. It
> creates projects, catalogs local media in place, persists a 15- or 30-second
> reel plan, renders playable previews, and exports verified MP4 files. The
> constrained creative agent and guided refinement remain future work.

## Quick start

You need Node.js 22, pnpm 10, `uv`, Python 3.12, FFmpeg, and FFprobe.
From the repository root, install the locked dependencies once:

```bash
corepack enable
pnpm install --frozen-lockfile
cd apps/api
uv python install 3.12
uv sync --python 3.12 --frozen
cd ../..
```

Then start Holden Reel:

```bash
make dev
```

Open <http://127.0.0.1:5173> in your browser. The API runs at
<http://127.0.0.1:8000>. Stop both processes with `Ctrl-C`.

For test commands, optional configuration, and troubleshooting, see the
[local development guide](docs/development.md).

## Product principles

- **Local first.** Source media stays on the user's machine by default.
- **Fast to a good draft.** The agent does the first assembly; the user remains
  in control.
- **Creative, not complicated.** Useful style controls without recreating a
  professional nonlinear editor.
- **Transparent AI.** Nothing is sent to an AI provider without clear consent
  and a preview of what will leave the machine.
- **Inspectability over magic.** Every draft is represented by an editable,
  reproducible reel plan.
- **Modular from day one.** The same services can later support a copilot,
  automation, remote workers, and a hosted multi-user application.

## MVP experience

1. Create a project and choose a local folder or several media files.
2. Select a finished song and add an optional creative brief.
3. Review recommended 15- and 30-second musical moments.
4. Generate a beat-synced draft from videos and still images.
5. Refine shot order, trims, pacing, transitions, text, and visual treatment.
6. Apply the band's reusable brand kit.
7. Export a vertical 1080×1920 H.264/AAC MP4.

The target interface is a guided creative workspace with large previews, visual
media cards, clear progress, undoable actions, and one obvious next step. It is
not a miniature Premiere Pro.

## Proposed architecture

Holden Reel will run locally as a small two-process web application:

- **React + TypeScript** for the browser-based interface
- **Python + FastAPI** for project services, media analysis, agent orchestration,
  jobs, and rendering
- **FFmpeg/FFprobe** for media inspection, proxy generation, composition, and
  export
- **SQLite** for project metadata, job state, consent records, and reel plans
- **Local project storage** for thumbnails, waveforms, proxies, previews, and
  exports; original media is referenced in place and never modified
- **Optional provider adapters** for trusted AI services such as OpenAI or
  Anthropic

The creative agent works through narrow tools and writes a structured reel plan
instead of editing media directly. The renderer executes that plan
deterministically. This separation makes drafts explainable, repeatable, and
usable by future conversational and automation interfaces.

## Storage strategy

The first milestone will use a small local media folder. The storage model is
nevertheless designed for larger libraries and external drives:

- Originals are referenced in place rather than copied.
- Generated files are stored separately and can be deleted safely.
- Editing and analysis use compact proxies where practical.
- Missing source files are reported without corrupting the project.
- Cache policy and media access sit behind interfaces that can later support a
  bounded cache, external USB storage, or a network media host.

An external 4 TB USB 3.x drive is expected to be a reasonable source library;
performance work will follow measurement, not speculation.

## Agent direction

The long-term product supports three agentic modes:

1. **Creative agent:** plans, renders, evaluates, and revises a draft.
2. **Conversational copilot:** applies feedback such as “use more crowd shots.”
3. **Automation interface:** lets a script or external agent create, generate,
   and export projects.

The MVP implements the creative agent first. It uses a bounded plan → validate →
preview → evaluate → revise loop, with at most one automatic revision. A small
slice of natural-language creative direction is included; a full copilot and
public automation API are deferred.

## MVP boundaries

The first version intentionally excludes:

- Authentication, accounts, and collaboration
- Direct publishing to Instagram or TikTok
- A multitrack timeline, keyframes, or advanced audio mixing
- Cloud storage and remote rendering
- Multiple output aspect ratios
- Face recognition, model training, or open-ended autonomous loops
- A full conversational editor or stable public API

## Project status

- [x] Product discovery
- [x] Architecture and MVP design
- [x] Implementation plan
- [x] Application scaffold
- [x] First ingest-to-export vertical slice
- [ ] Creative-agent draft generation
- [ ] Guided refinement and brand kit
- [ ] External-drive performance validation

## Documentation

- [Local development and verification](docs/development.md)
- [MVP design spec](docs/superpowers/specs/2026-08-23-holden-reel-mvp-design.md)
- [MIT license](LICENSE)

## License

Holden Reel is available under the MIT License.
