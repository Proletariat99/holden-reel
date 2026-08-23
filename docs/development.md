# Local development

Holden Reel runs entirely on the local machine as a FastAPI process and a Vite
process. Both development servers bind to loopback by default.

## Prerequisites

- Node.js 22
- pnpm 10
- uv
- Python 3.12
- FFmpeg and FFprobe 7 or newer

Verify the installed tools:

```bash
node --version
pnpm --version
uv --version
python3 --version
ffmpeg -version
ffprobe -version
```

## Install

From the repository root, install the locked JavaScript and Python
dependencies and the Playwright Chromium build:

```bash
corepack enable
pnpm install --frozen-lockfile
cd apps/api
uv python install 3.12
uv sync --python 3.12 --frozen
cd ../..
pnpm --dir apps/web exec playwright install chromium
```

## Run locally

Start both processes from the repository root:

```bash
make dev
```

The command keeps the API at <http://127.0.0.1:8000> and the web application at
<http://127.0.0.1:5173>. Stop both with `Ctrl-C`.

The first milestone accepts an **absolute local folder path** in the import
screen. The API catalogs supported files in place; it does not copy or modify
source media. Create a project, import a folder, choose one audio file and at
least one video or image, generate the 15-second draft, inspect and seek the
preview, export the final reel, and download the MP4.

## Configuration

The API reads these optional environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `HOLDEN_REEL_DATA_DIR` | SQLite database, previews, and final exports | Platform-specific user data directory |
| `HOLDEN_REEL_FFMPEG_BIN` | FFmpeg executable or absolute path | `ffmpeg` |
| `HOLDEN_REEL_FFPROBE_BIN` | FFprobe executable or absolute path | `ffprobe` |

Print the resolved application-data directory:

```bash
cd apps/api
uv run python -c "from holden_reel.config import Settings; print(Settings().data_dir)"
```

To isolate a manual run inside the repository, use an absolute path:

```bash
HOLDEN_REEL_DATA_DIR="$PWD/test-results/manual-data" make dev
```

Generated previews and exports live below
`HOLDEN_REEL_DATA_DIR/projects/<project-id>/previews` and
`HOLDEN_REEL_DATA_DIR/projects/<project-id>/exports`. After stopping the app,
remove those generated `.mp4` files to reclaim space. Do not remove or edit the
original imported folder; Holden Reel only references it. The end-to-end test
uses temporary fixture, application-data, and download directories below
`apps/web/test-results` and removes its media and application data during
teardown. Playwright keeps traces only for failed tests.

## Tests

Run API tests, web unit tests, and the strict TypeScript check:

```bash
make test
```

Run the backend ingest-to-export acceptance test by itself:

```bash
cd apps/api
uv run pytest tests/test_vertical_slice.py -q
```

Generate synthetic media for a manual smoke test (the output must be an
absolute path):

```bash
cd apps/api
uv run python tests/fixture_media.py --output "$PWD/test-results/manual-fixture"
```

Copy the containing folder path printed in the JSON manifest into the import
screen. The fixtures are generated colors, a still image, and a sine wave; the
test suite never requires user media or a remote AI service.

Run the complete Playwright workflow with one Chromium worker:

```bash
make test-e2e
```

The test allocates fresh non-default loopback ports for its API and Vite
servers, starts them with bounded readiness and render timeouts, uses an
isolated temporary `HOLDEN_REEL_DATA_DIR`, verifies actual preview playback and
seeking, exports the final render, and checks that the downloaded MP4 is
nonempty. It prints the exact loopback URLs for each run and tears down both
processes and their generated fixture/application data on success or failure.
