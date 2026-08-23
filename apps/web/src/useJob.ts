import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";
import type { ApiClient, RenderJob } from "./types";

const POLL_INTERVAL_MS = 750;
const TERMINAL_STATUSES = new Set<RenderJob["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);

export function useJob(api: ApiClient, jobId: string | null) {
  const [job, setJob] = useState<RenderJob | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const mounted = useRef(false);
  const currentJobId = useRef(jobId);
  const stopPolling = useRef<() => void>(() => undefined);
  const cancelController = useRef<AbortController | null>(null);
  const cancelInFlight = useRef<Promise<void> | null>(null);
  const cancelToken = useRef<object | null>(null);

  currentJobId.current = jobId;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      cancelController.current?.abort();
    };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();

    const stop = () => {
      active = false;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
    stopPolling.current();
    stopPolling.current = stop;
    setJob(null);
    setError(null);

    const targetJobId = jobId;
    if (targetJobId === null) return stop;

    async function poll(pollJobId: string) {
      try {
        const nextJob = await api.getJob(pollJobId, controller.signal);
        if (!active || controller.signal.aborted) return;
        setJob(nextJob);
        setError(null);
        if (!TERMINAL_STATUSES.has(nextJob.status)) {
          timer = setTimeout(() => void poll(pollJobId), POLL_INTERVAL_MS);
        }
      } catch (reason: unknown) {
        if (!active || controller.signal.aborted) return;
        setError(toError(reason));
      }
    }

    void poll(targetJobId);
    return stop;
  }, [api, jobId]);

  const cancel = useCallback((): Promise<void> => {
    if (jobId === null) return Promise.resolve();
    if (cancelInFlight.current !== null) return cancelInFlight.current;

    const targetJobId = jobId;
    stopPolling.current();
    const controller = new AbortController();
    cancelController.current?.abort();
    cancelController.current = controller;
    setIsCancelling(true);
    setError(null);

    const token = {};
    cancelToken.current = token;
    const operation = (async () => {
      try {
        await api.cancelJob(targetJobId);
        const refreshed = await api.getJob(targetJobId, controller.signal);
        if (mounted.current && currentJobId.current === targetJobId && !controller.signal.aborted) {
          setJob(refreshed);
        }
      } catch (reason: unknown) {
        if (mounted.current && currentJobId.current === targetJobId && !controller.signal.aborted) {
          setError(toError(reason));
        }
      } finally {
        if (cancelToken.current === token) {
          cancelToken.current = null;
          cancelInFlight.current = null;
        }
        if (mounted.current && currentJobId.current === targetJobId) setIsCancelling(false);
      }
    })();
    cancelInFlight.current = operation;
    return operation;
  }, [api, jobId]);

  return { job, error, isCancelling, cancel };
}

function toError(reason: unknown): Error {
  if (reason instanceof Error) return reason;
  return new ApiError("job_request_failed", "Render job request failed", {});
}
