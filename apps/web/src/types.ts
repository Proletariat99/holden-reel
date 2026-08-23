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
}

export interface MediaSelection {
  assets: MediaAsset[];
  audioAssetId: string;
  visualAssetIds: string[];
}
