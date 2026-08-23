import type { MediaSelection } from "./types";

export const ACTIVE_WORKSPACE_KEY = "holden-reel.active";

export interface ActiveWorkspace {
  projectId: string;
  selection?: MediaSelection;
  planId?: string;
  previewJobId?: string;
  finalJobId?: string;
}

export function loadActiveWorkspace(): ActiveWorkspace | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(ACTIVE_WORKSPACE_KEY) ?? "null");
    if (!value || typeof value !== "object" || !("projectId" in value) || typeof value.projectId !== "string") return null;
    return value as ActiveWorkspace;
  } catch {
    localStorage.removeItem(ACTIVE_WORKSPACE_KEY);
    return null;
  }
}

export function saveActiveWorkspace(value: ActiveWorkspace): void {
  localStorage.setItem(ACTIVE_WORKSPACE_KEY, JSON.stringify(value));
}
