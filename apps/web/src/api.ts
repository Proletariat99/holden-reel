import type {
  ApiClient as ApiClientContract,
  ComposePlanRequest,
  ErrorResponse,
  MediaCollection,
  Project,
  ReelPlan,
  RenderJob,
  RenderProfile,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }

  const { error } = value;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    "message" in error &&
    "details" in error &&
    typeof error.code === "string" &&
    typeof error.message === "string" &&
    typeof error.details === "object" &&
    error.details !== null
  );
}

export class ApiClient implements ApiClientContract {
  async createProject(name: string): Promise<Project> {
    return this.request<Project>("/api/projects", {
      method: "POST",
      body: { name },
    });
  }

  async listProjects(): Promise<Project[]> {
    return this.request<Project[]>("/api/projects");
  }

  async getProject(projectId: string): Promise<Project> {
    return this.request<Project>(`/api/projects/${encodeURIComponent(projectId)}`);
  }

  async getPlan(planId: string): Promise<ReelPlan> {
    return this.request<ReelPlan>(`/api/plans/${encodeURIComponent(planId)}`);
  }

  async importMedia(projectId: string, path: string): Promise<MediaCollection> {
    return this.request<MediaCollection>(`/api/projects/${encodeURIComponent(projectId)}/media/import`, {
      method: "POST",
      body: { path },
    });
  }

  async listMedia(projectId: string): Promise<MediaCollection> {
    return this.request<MediaCollection>(`/api/projects/${encodeURIComponent(projectId)}/media`);
  }

  async composePlan(projectId: string, body: ComposePlanRequest): Promise<ReelPlan> {
    return this.request<ReelPlan>(`/api/projects/${encodeURIComponent(projectId)}/plans/compose`, {
      method: "POST",
      body,
    });
  }

  async startRender(planId: string, profile: RenderProfile): Promise<RenderJob> {
    return this.request<RenderJob>(`/api/plans/${encodeURIComponent(planId)}/renders`, {
      method: "POST",
      body: { profile },
    });
  }

  async getJob(jobId: string, signal?: AbortSignal): Promise<RenderJob> {
    return this.request<RenderJob>(`/api/jobs/${encodeURIComponent(jobId)}`, { signal });
  }

  async cancelJob(jobId: string, signal?: AbortSignal): Promise<RenderJob> {
    return this.request<RenderJob>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      signal,
    });
  }

  private async request<T>(
    path: string,
    init: { method?: string; body?: object; signal?: AbortSignal } = {},
  ): Promise<T> {
    const response = await fetch(path, {
      method: init.method ?? "GET",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: init.signal,
    });
    const payload: unknown = await response.json();

    if (!response.ok) {
      if (isErrorResponse(payload)) {
        throw new ApiError(payload.error.code, payload.error.message, payload.error.details);
      }
      throw new ApiError("http_error", "The request could not be completed", {
        status_code: response.status,
      });
    }

    return payload as T;
  }
}

export function artifactUrl(jobId: string): string {
  return `/api/jobs/${encodeURIComponent(jobId)}/artifact`;
}
