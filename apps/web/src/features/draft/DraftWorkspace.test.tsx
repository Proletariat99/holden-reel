import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ApiError } from "../../api";
import type {
  ApiClient,
  MediaAsset,
  MediaSelection,
  Project,
  ReelPlan,
  RenderJob,
} from "../../types";
import { DraftWorkspace } from "./DraftWorkspace";

const project: Project = {
  id: "p1",
  name: "Sunday Session",
  created_at: "2026-08-23T12:00:00+00:00",
  updated_at: "2026-08-23T12:00:00+00:00",
};

const assets: MediaAsset[] = [
  asset({ id: "a1", path: "/media/song.wav", kind: "audio", duration_ms: 60_000 }),
  asset({ id: "v1", path: "/media/first.mp4", kind: "video", duration_ms: 10_000 }),
  asset({ id: "v2", path: "/media/second.jpg", kind: "image", duration_ms: null }),
];

const selection: MediaSelection = {
  assets,
  audioAssetId: "a1",
  visualAssetIds: ["v1", "v2"],
};

const plan: ReelPlan = {
  schema_version: 1,
  id: "plan1",
  project_id: "p1",
  version: 1,
  duration_ms: 30_000,
  width: 1080,
  height: 1920,
  fps: 30,
  safe_area: "instagram_reels_v1",
  audio: { asset_id: "a1", source_start_ms: 2500, source_end_ms: 32500, gain_db: 0 },
  shots: [
    {
      asset_id: "v2",
      source_start_ms: null,
      source_end_ms: null,
      output_start_ms: 0,
      output_end_ms: 15_000,
      fit: "cover",
      still_motion: "slow_zoom",
    },
    {
      asset_id: "v1",
      source_start_ms: 0,
      source_end_ms: 15_000,
      output_start_ms: 15_000,
      output_end_ms: 30_000,
      fit: "cover",
      still_motion: null,
    },
  ],
  rationale: "Deterministic visual rotation using supplied source order.",
};

it("composes a 15 or 30 second plan from numeric audio start and user-ordered visuals", async () => {
  // Break caught: duration/audio conversion or user-controlled source order is lost at composition.
  const api = fakeApi();
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);

  expect(screen.getByRole("radio", { name: /15 seconds/i })).toBeChecked();
  await user.click(screen.getByRole("radio", { name: /30 seconds/i }));
  await user.clear(screen.getByLabelText(/audio start/i));
  await user.type(screen.getByLabelText(/audio start/i), "2.5");
  await user.click(screen.getByRole("button", { name: /move second.jpg up/i }));
  await user.click(screen.getByRole("button", { name: /generate draft/i }));

  await waitFor(() =>
    expect(api.composePlan).toHaveBeenCalledWith("p1", {
      duration_ms: 30_000,
      audio_asset_id: "a1",
      audio_start_ms: 2500,
      visual_asset_ids: ["v2", "v1"],
    }),
  );
  expect(api.startRender).toHaveBeenCalledWith("plan1", "preview");
});

it("validates audio start against the chosen duration before composing", async () => {
  // Break caught: negative or overlong audio ranges reach the API despite known media duration.
  const api = fakeApi();
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={{ ...selection, assets: [
    { ...assets[0], duration_ms: 18_000 }, assets[1], assets[2],
  ] }} />);

  await user.clear(screen.getByLabelText(/audio start/i));
  await user.type(screen.getByLabelText(/audio start/i), "4");
  await user.click(screen.getByRole("button", { name: /generate draft/i }));

  expect(screen.getByRole("alert")).toHaveTextContent(/audio start.*15-second reel.*track/i);
  expect(api.composePlan).not.toHaveBeenCalled();
});

it("shows the deterministic shot order and rationale before rendering completes", async () => {
  // Break caught: composition succeeds but users cannot inspect what will render or why.
  const api = fakeApi({ previewJob: renderJob({ status: "running", progress: 0.25 }) });
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);

  await user.click(screen.getByRole("button", { name: /generate draft/i }));

  expect(await screen.findByText(plan.rationale)).toBeInTheDocument();
  const shotList = screen.getByRole("list", { name: /ordered shot list/i });
  const items = within(shotList).getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("second.jpg");
  expect(items[0]).toHaveTextContent("0:00–0:15");
  expect(items[1]).toHaveTextContent("first.mp4");
  expect(items[1]).toHaveTextContent("0:15–0:30");
});

it("shows render progress and cancels the active preview", async () => {
  // Break caught: long renders have no accessible progress or usable cancellation control.
  const running = renderJob({ status: "running", progress: 0.42 });
  const cancelled = renderJob({ status: "cancelled", progress: 0.42 });
  const api = fakeApi({ previewJob: running, cancelledJob: cancelled });
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);

  await user.click(screen.getByRole("button", { name: /generate draft/i }));
  const progress = await screen.findByRole("progressbar", { name: /preview render progress/i });
  expect(progress).toHaveAttribute("value", "0.42");
  await user.click(screen.getByRole("button", { name: /cancel preview/i }));

  await waitFor(() => expect(api.cancelJob).toHaveBeenCalledTimes(1));
  expect(api.cancelJob).toHaveBeenCalledWith("preview1", expect.any(AbortSignal));
});

it("prevents regeneration from abandoning an active preview or final render", async () => {
  // Break caught: generating again clears active job controls while work remains in the queue.
  const user = userEvent.setup();
  const previewRefresh = deferred<RenderJob>();
  const previewApi = fakeApi({
    previewJob: renderJob({ status: "running", progress: 0.42 }),
  });
  previewApi.getJob = vi.fn().mockReturnValue(previewRefresh.promise);
  const previewView = render(
    <DraftWorkspace api={previewApi} project={project} selection={selection} />,
  );
  await user.click(screen.getByRole("button", { name: /generate draft/i }));
  const previewGenerate = screen.getByRole("button", { name: /generate draft/i });
  expect(previewGenerate).toBeDisabled();
  previewRefresh.resolve(renderJob({ status: "running", progress: 0.42 }));
  await screen.findByRole("button", { name: /cancel preview/i });

  expect(previewGenerate).toBeDisabled();
  expect(screen.getByRole("radio", { name: /30 seconds/i })).toBeDisabled();
  expect(screen.getByLabelText(/audio start/i)).toBeDisabled();
  expect(screen.getByRole("button", { name: /move second.jpg up/i })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(/render is active.*cancel it or wait/i);
  await user.click(screen.getByRole("radio", { name: /30 seconds/i }));
  expect(screen.getByRole("button", { name: /cancel preview/i })).toBeInTheDocument();
  await user.click(previewGenerate);
  expect(previewApi.composePlan).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /cancel preview/i })).toBeInTheDocument();
  previewView.unmount();
  localStorage.clear();

  const succeededPreview = renderJob({ status: "succeeded", progress: 1 });
  const runningFinal = renderJob({ id: "final1", kind: "final", status: "running", progress: 0.2 });
  const finalRefresh = deferred<RenderJob>();
  const finalApi = fakeApi({ previewJob: succeededPreview, finalJob: runningFinal });
  finalApi.getJob = vi.fn((jobId: string) =>
    jobId === "final1" ? finalRefresh.promise : Promise.resolve(succeededPreview),
  );
  render(<DraftWorkspace api={finalApi} project={project} selection={selection} />);
  await user.click(screen.getByRole("button", { name: /generate draft/i }));
  await user.click(await screen.findByRole("button", { name: /export final/i }));
  const finalGenerate = screen.getByRole("button", { name: /generate draft/i });
  expect(finalGenerate).toBeDisabled();
  expect(screen.getByRole("radio", { name: /30 seconds/i })).toBeDisabled();
  expect(screen.getByLabelText(/audio start/i)).toBeDisabled();
  expect(screen.getByRole("button", { name: /move second.jpg up/i })).toBeDisabled();
  finalRefresh.resolve(runningFinal);
  await screen.findByRole("button", { name: /cancel final export/i });

  expect(finalGenerate).toBeDisabled();
  await user.click(finalGenerate);
  expect(finalApi.composePlan).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: /cancel final export/i })).toBeInTheDocument();
});

it("previews the succeeded artifact and exports a separate final job for download", async () => {
  // Break caught: export reuses preview output or exposes a local artifact_path instead of the route.
  const preview = renderJob({ status: "succeeded", progress: 1, artifact_path: "/secret/preview.mp4" });
  const final = renderJob({
    id: "final1",
    kind: "final",
    status: "succeeded",
    progress: 1,
    artifact_path: "/secret/final.mp4",
  });
  const api = fakeApi({ previewJob: preview, finalJob: final });
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);

  await user.click(screen.getByRole("button", { name: /generate draft/i }));
  const video = await screen.findByLabelText(/reel preview/i);
  expect(video).toHaveAttribute("src", "/api/jobs/preview1/artifact");
  expect(video).toHaveAttribute("controls");

  await user.click(screen.getByRole("button", { name: /export final/i }));
  expect(api.startRender).toHaveBeenNthCalledWith(2, "plan1", "final");
  const download = await screen.findByRole("link", { name: /download final reel/i });
  expect(download).toHaveAttribute("href", "/api/jobs/final1/artifact");
  expect(download).toHaveAttribute("download");
  expect(download).not.toHaveAttribute("href", "/secret/final.mp4");
});

it("retries a failed preview with the same persisted plan", async () => {
  // Break caught: a terminal render failure forces recomposition or leaves no recovery path.
  const failed = renderJob({
    status: "failed",
    progress: 0.3,
    error: { code: "render_failed", message: "Encoder stopped" },
  });
  const succeeded = renderJob({
    id: "preview2",
    status: "succeeded",
    progress: 1,
    artifact_path: "/secret/preview2.mp4",
  });
  const api = fakeApi({ previewJobs: [failed, succeeded] });
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);

  await user.click(screen.getByRole("button", { name: /generate draft/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Encoder stopped");
  await user.click(screen.getByRole("button", { name: /retry preview/i }));

  expect(api.composePlan).toHaveBeenCalledTimes(1);
  expect(api.startRender).toHaveBeenNthCalledWith(2, "plan1", "preview");
  expect(await screen.findByLabelText(/reel preview/i)).toHaveAttribute(
    "src",
    "/api/jobs/preview2/artifact",
  );
});

it("shows the backend ApiError code and message", async () => {
  // Break caught: actionable structured API errors disappear behind generic failure copy.
  const api = fakeApi({
    composePlan: vi.fn().mockRejectedValue(
      new ApiError("insufficient_usable_media", "Usable media cannot cover the requested reel", {
        asset_id: "v1",
      }),
    ),
  });
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);

  await user.click(screen.getByRole("button", { name: /generate draft/i }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Usable media cannot cover the requested reel");
  expect(alert).toHaveTextContent("insufficient_usable_media");
});

it.each([
  ["duration", async (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("radio", { name: /30 seconds/i }))],
  ["audio start", async (user: ReturnType<typeof userEvent.setup>) => user.type(screen.getByLabelText(/audio start/i), "1")],
  ["visual order", async (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: /move second.jpg up/i }))],
])("invalidates a preview when %s changes", async (_label, mutate) => {
  // Break caught: export can use a stale plan after editable inputs diverge.
  const api = fakeApi();
  const user = userEvent.setup();
  render(<DraftWorkspace api={api} project={project} selection={selection} />);
  await user.click(screen.getByRole("button", { name: /generate draft/i }));
  expect(await screen.findByLabelText(/reel preview/i)).toBeInTheDocument();
  await mutate(user);
  expect(screen.queryByLabelText(/reel preview/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /export final/i })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(/settings changed.*generate a new draft/i);
});

it("restores and resumes polling an active saved render job after refresh", async () => {
  // Break caught: React-only identifiers disappear on browser reload.
  localStorage.setItem("holden-reel.active", JSON.stringify({
    projectId: "p1", selection, planId: "plan1", previewJobId: "preview1",
  }));
  const api = fakeApi({ previewJob: renderJob({ status: "running", progress: 0.42 }) });
  api.getPlan = vi.fn().mockResolvedValue(plan);
  render(<DraftWorkspace api={api} project={project} selection={selection} />);
  expect(await screen.findByText(plan.rationale)).toBeInTheDocument();
  expect(await screen.findByRole("progressbar", { name: /preview render progress/i })).toHaveAttribute("value", "0.42");
  expect(screen.getByRole("button", { name: /cancel preview/i })).toBeInTheDocument();
  expect(api.getPlan).toHaveBeenCalledWith("plan1");
  expect(api.getJob).toHaveBeenCalledWith("preview1");
});

it("gates controls while restoring and ignores a late restore after project change", async () => {
  // Break caught: late validation can overwrite the new project's draft state.
  const restore = deferred<ReelPlan>();
  localStorage.setItem("holden-reel.active", JSON.stringify({
    projectId: "p1", selection, planId: "plan1", previewJobId: "preview1",
  }));
  const api = fakeApi();
  api.getPlan = vi.fn().mockReturnValue(restore.promise);
  const view = render(<DraftWorkspace api={api} project={project} selection={selection} />);
  expect(screen.getByRole("button", { name: /restoring draft/i })).toBeDisabled();
  expect(screen.getByRole("radio", { name: /15 seconds/i })).toBeDisabled();

  const nextProject = { ...project, id: "p2", name: "Next project" };
  view.rerender(<DraftWorkspace api={api} project={nextProject} selection={selection} />);
  restore.resolve(plan);

  await waitFor(() => expect(screen.getByRole("button", { name: /generate draft/i })).toBeEnabled());
  expect(screen.queryByText(plan.rationale)).not.toBeInTheDocument();
  expect(localStorage.getItem("holden-reel.active")).toContain('"projectId":"p2"');
});

it("ignores late restoration after unmount", async () => {
  // Break caught: an unmounted workspace can publish late state back to localStorage.
  const restore = deferred<ReelPlan>();
  localStorage.setItem("holden-reel.active", JSON.stringify({ projectId: "p1", selection, planId: "plan1" }));
  const api = fakeApi();
  api.getPlan = vi.fn().mockReturnValue(restore.promise);
  const view = render(<DraftWorkspace api={api} project={project} selection={selection} />);
  view.unmount();
  localStorage.setItem("holden-reel.active", JSON.stringify({ projectId: "p2" }));
  restore.resolve(plan);
  await Promise.resolve();
  await Promise.resolve();
  expect(localStorage.getItem("holden-reel.active")).toBe('{"projectId":"p2"}');
});

function asset(overrides: Partial<MediaAsset> & Pick<MediaAsset, "id" | "path" | "kind">): MediaAsset {
  return {
    project_id: "p1",
    duration_ms: null,
    width: null,
    height: null,
    codec: null,
    available: true,
    fingerprint: `${overrides.id}-fingerprint`,
    ...overrides,
  };
}

function renderJob(overrides: Partial<RenderJob>): RenderJob {
  return {
    id: "preview1",
    project_id: "p1",
    kind: "preview",
    status: "queued",
    progress: 0,
    plan_id: "plan1",
    artifact_path: null,
    error: null,
    created_at: "2026-08-23T12:00:00+00:00",
    updated_at: "2026-08-23T12:00:00+00:00",
    ...overrides,
  };
}

function fakeApi(options: {
  composePlan?: ApiClient["composePlan"];
  previewJob?: RenderJob;
  previewJobs?: RenderJob[];
  finalJob?: RenderJob;
  cancelledJob?: RenderJob;
} = {}): ApiClient {
  const previews = [...(options.previewJobs ?? [options.previewJob ?? renderJob({ status: "succeeded", progress: 1 })])];
  const jobs = new Map<string, RenderJob>();
  const startRender = vi.fn((_planId: string, profile: "preview" | "final") => {
    const next = profile === "preview"
      ? previews.shift() ?? renderJob({ status: "failed" })
      : options.finalJob ?? renderJob({ id: "final1", kind: "final", status: "succeeded", progress: 1 });
    jobs.set(next.id, next);
    return Promise.resolve(next);
  });
  return {
    createProject: vi.fn(),
    listProjects: vi.fn(),
    getProject: vi.fn(),
    getPlan: vi.fn().mockResolvedValue(plan),
    importMedia: vi.fn(),
    listMedia: vi.fn(),
    composePlan: options.composePlan ?? vi.fn().mockResolvedValue(plan),
    startRender,
    getJob: vi.fn((jobId: string) => Promise.resolve(
      jobs.get(jobId) ?? (jobId === "preview1" && options.previewJob ? options.previewJob : renderJob({ id: jobId })),
    )),
    cancelJob: vi.fn((jobId: string) => {
      const cancelled = options.cancelledJob ?? renderJob({ id: jobId, status: "cancelled" });
      jobs.set(jobId, cancelled);
      return Promise.resolve(cancelled);
    }),
  };
}

function deferred<T>() {
  let resolve: (value: T) => void;
  let reject: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve: resolve!, reject: reject! };
}
