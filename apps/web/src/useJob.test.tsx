import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import type { ApiClient, RenderJob } from "./types";
import { useJob } from "./useJob";

const queuedJob = job({ status: "queued", progress: 0 });
const runningJob = job({ status: "running", progress: 0.5 });
const succeededJob = job({ status: "succeeded", progress: 1, artifact_path: "/data/j1.mp4" });
const failedJob = job({
  status: "failed",
  progress: 0.5,
  error: { code: "render_failed", message: "FFmpeg stopped" },
});

afterEach(() => {
  vi.useRealTimers();
});

it("polls every 750 ms until the job succeeds", async () => {
  // Break caught: active jobs stop refreshing or terminal success is never exposed.
  vi.useFakeTimers();
  const api = fakeApi({ jobStates: [queuedJob, runningJob, succeededJob] });
  const { result } = renderHook(() => useJob(api, "j1"));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_000);
  });

  expect(result.current.job?.status).toBe("succeeded");
  expect(result.current.error).toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });
  expect(api.getJob).toHaveBeenCalledTimes(3);
});

it("stops polling after a failed terminal state", async () => {
  // Break caught: a failed render creates an endless polling loop.
  vi.useFakeTimers();
  const api = fakeApi({ jobStates: [queuedJob, failedJob] });
  const { result } = renderHook(() => useJob(api, "j1"));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });

  expect(result.current.job).toEqual(failedJob);
  expect(api.getJob).toHaveBeenCalledTimes(2);
});

it("cancels once and refreshes the persisted job state", async () => {
  // Break caught: repeated cancel calls race or the UI trusts a cancel response without refreshing.
  const cancelledJob = job({ status: "cancelled", progress: 0.5 });
  const api = fakeApi({ jobStates: [runningJob, cancelledJob], cancelledJob });
  const { result } = renderHook(() => useJob(api, "j1"));
  await act(async () => undefined);

  await act(async () => {
    await Promise.all([result.current.cancel(), result.current.cancel()]);
  });

  expect(api.cancelJob).toHaveBeenCalledTimes(1);
  expect(api.getJob).toHaveBeenCalledTimes(2);
  expect(result.current.job?.status).toBe("cancelled");
});

it("aborts an in-flight request and schedules no updates after unmount", async () => {
  // Break caught: an abandoned workspace keeps polling or updates React after teardown.
  vi.useFakeTimers();
  const pending = deferred<RenderJob>();
  let signal: AbortSignal | undefined;
  const api = fakeApi({
    getJob: vi.fn((_jobId: string, requestSignal?: AbortSignal) => {
      signal = requestSignal;
      return pending.promise;
    }),
  });
  const { unmount } = renderHook(() => useJob(api, "j1"));
  await act(async () => undefined);

  unmount();
  expect(signal?.aborted).toBe(true);
  pending.resolve(runningJob);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });

  expect(api.getJob).toHaveBeenCalledTimes(1);
});

it("aborts the old request and ignores its result when the job id changes", async () => {
  // Break caught: a slow prior job overwrites the current job after selection changes.
  const oldRequest = deferred<RenderJob>();
  let oldSignal: AbortSignal | undefined;
  const newJob = job({ id: "j2", status: "succeeded", progress: 1, artifact_path: "/data/j2.mp4" });
  const api = fakeApi({
    getJob: vi.fn((jobId: string, signal?: AbortSignal) => {
      if (jobId === "j1") {
        oldSignal = signal;
        return oldRequest.promise;
      }
      return Promise.resolve(newJob);
    }),
  });
  const { result, rerender } = renderHook(({ jobId }) => useJob(api, jobId), {
    initialProps: { jobId: "j1" as string | null },
  });
  await act(async () => undefined);

  rerender({ jobId: "j2" });
  await act(async () => undefined);
  expect(oldSignal?.aborted).toBe(true);
  oldRequest.resolve(succeededJob);
  await act(async () => undefined);

  expect(result.current.job?.id).toBe("j2");
});

it("preserves an ApiError from a polling failure", async () => {
  // Break caught: structured backend failures become an opaque generic string.
  const apiError = new ApiError("job_not_found", "Render job was not found", {});
  const api = fakeApi({ getJob: vi.fn().mockRejectedValue(apiError) });
  const { result } = renderHook(() => useJob(api, "j1"));

  await act(async () => undefined);

  expect(result.current.error).toBe(apiError);
  expect(result.current.error).toBeInstanceOf(ApiError);
});

it("keeps polling an active job after a transient ApiError", async () => {
  // Break caught: one temporary request failure permanently strands an otherwise active render.
  vi.useFakeTimers();
  const apiError = new ApiError("temporarily_unavailable", "Try again", {});
  const getJob = vi
    .fn<ApiClient["getJob"]>()
    .mockResolvedValueOnce(queuedJob)
    .mockRejectedValueOnce(apiError)
    .mockResolvedValueOnce(runningJob)
    .mockResolvedValueOnce(succeededJob);
  const api = fakeApi({ getJob });
  const { result } = renderHook(() => useJob(api, "j1"));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(800);
  });
  expect(result.current.job?.status).toBe("queued");
  expect(result.current.error).toBe(apiError);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_000);
  });
  expect(result.current.job?.status).toBe("succeeded");
  expect(result.current.error).toBeNull();
  expect(getJob).toHaveBeenCalledTimes(4);
});

it("retries when the first poll fails before any job state is known", async () => {
  // Break caught: an initial transient failure strands a valid job before its first state arrives.
  vi.useFakeTimers();
  const apiError = new ApiError("temporarily_unavailable", "Try again", {});
  const getJob = vi
    .fn<ApiClient["getJob"]>()
    .mockRejectedValueOnce(apiError)
    .mockResolvedValueOnce(runningJob)
    .mockResolvedValueOnce(succeededJob);
  const api = fakeApi({ getJob });
  const { result } = renderHook(() => useJob(api, "j1"));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(result.current.job).toBeNull();
  expect(result.current.error).toBe(apiError);
  expect(getJob).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_000);
  });
  expect(result.current.job?.status).toBe("succeeded");
  expect(result.current.error).toBeNull();
  expect(getJob).toHaveBeenCalledTimes(3);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(getJob).toHaveBeenCalledTimes(3);
});

it("aborts a deferred cancel refresh and resets cancelling when the job id changes", async () => {
  // Break caught: a cancel started for an old ID leaves the replacement job permanently cancelling.
  const cancelRequest = deferred<RenderJob>();
  let cancelSignal: AbortSignal | undefined;
  const newJob = job({ id: "j2", status: "succeeded", progress: 1, artifact_path: "/data/j2.mp4" });
  const getJob = vi.fn((jobId: string) =>
    Promise.resolve(jobId === "j1" ? runningJob : newJob),
  );
  const api = fakeApi({
    getJob,
    cancelledJob: job({ status: "cancelled" }),
  });
  api.cancelJob = vi.fn((_jobId: string, signal?: AbortSignal) => {
    cancelSignal = signal;
    return cancelRequest.promise;
  });
  const { result, rerender } = renderHook(({ jobId }) => useJob(api, jobId), {
    initialProps: { jobId: "j1" as string | null },
  });
  await act(async () => undefined);

  let cancelPromise: Promise<void>;
  act(() => {
    cancelPromise = result.current.cancel();
  });
  expect(result.current.isCancelling).toBe(true);

  rerender({ jobId: "j2" });
  await act(async () => undefined);
  expect(cancelSignal?.aborted).toBe(true);
  expect(result.current.isCancelling).toBe(false);
  expect(result.current.job?.id).toBe("j2");

  cancelRequest.resolve(job({ status: "cancelled" }));
  await act(async () => {
    await cancelPromise!;
  });
  expect(getJob).toHaveBeenCalledTimes(2);
  expect(result.current.job?.id).toBe("j2");
  expect(result.current.isCancelling).toBe(false);
});

function job(overrides: Partial<RenderJob>): RenderJob {
  return {
    id: "j1",
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

function fakeApi(
  options: {
    jobStates?: RenderJob[];
    cancelledJob?: RenderJob;
    getJob?: ApiClient["getJob"];
  } = {},
): ApiClient {
  const states = [...(options.jobStates ?? [queuedJob])];
  return {
    createProject: vi.fn(),
    listProjects: vi.fn(),
    getProject: vi.fn(),
    getPlan: vi.fn(),
    importMedia: vi.fn(),
    listMedia: vi.fn(),
    composePlan: vi.fn(),
    startRender: vi.fn(),
    getJob: options.getJob ?? vi.fn().mockImplementation(() => Promise.resolve(states.shift() ?? states.at(-1))),
    cancelJob: vi.fn().mockResolvedValue(options.cancelledJob ?? job({ status: "cancelled" })),
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
