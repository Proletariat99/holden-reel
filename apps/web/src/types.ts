export type MediaKind = "audio" | "video" | "image";

export interface Project {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface MediaAsset {
  id: string;
  project_id: string;
  path: string;
  kind: MediaKind;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  codec: string | null;
  available: boolean;
  fingerprint: string;
}

export interface MediaCollection {
  assets: MediaAsset[];
}

export interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface ApiClient {
  createProject(name: string): Promise<Project>;
  listProjects(): Promise<Project[]>;
  getProject(projectId: string): Promise<Project>;
  importMedia(projectId: string, path: string): Promise<MediaCollection>;
  listMedia(projectId: string): Promise<MediaCollection>;
  composePlan(projectId: string, request: ComposePlanRequest): Promise<ReelPlan>;
  startRender(planId: string, profile: RenderProfile): Promise<RenderJob>;
  getJob(jobId: string, signal?: AbortSignal): Promise<RenderJob>;
  cancelJob(jobId: string, signal?: AbortSignal): Promise<RenderJob>;
}

export interface MediaSelection {
  assets: MediaAsset[];
  audioAssetId: string;
  visualAssetIds: string[];
}

export interface ComposePlanRequest {
  duration_ms: 15000 | 30000;
  audio_asset_id: string;
  audio_start_ms: number;
  visual_asset_ids: string[];
}

export interface ReelShot {
  asset_id: string;
  source_start_ms: number | null;
  source_end_ms: number | null;
  output_start_ms: number;
  output_end_ms: number;
  fit: "cover";
  still_motion: "slow_zoom" | null;
}

export interface ReelPlan {
  schema_version: 1;
  id: string;
  project_id: string;
  version: number;
  duration_ms: 15000 | 30000;
  width: 1080;
  height: 1920;
  fps: 30;
  safe_area: "instagram_reels_v1";
  audio: {
    asset_id: string;
    source_start_ms: number;
    source_end_ms: number;
    gain_db: number;
  };
  shots: ReelShot[];
  rationale: string;
}

export type RenderProfile = "preview" | "final";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface RenderJob {
  id: string;
  project_id: string;
  kind: RenderProfile;
  status: JobStatus;
  progress: number;
  plan_id: string;
  artifact_path: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
}
