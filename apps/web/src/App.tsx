import { useEffect, useMemo, useState } from "react";

import { ApiClient } from "./api";
import { DraftWorkspace } from "./features/draft/DraftWorkspace";
import { MediaImport } from "./features/import/MediaImport";
import { ProjectStart } from "./features/projects/ProjectStart";
import type { MediaSelection, Project } from "./types";
import { ACTIVE_WORKSPACE_KEY, loadActiveWorkspace, saveActiveWorkspace } from "./workspaceStorage";

export default function App() {
  const api = useMemo(() => new ApiClient(), []);
  const [project, setProject] = useState<Project | null>(null);
  const [selection, setSelection] = useState<MediaSelection | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);

  useEffect(() => {
    const saved = loadActiveWorkspace();
    if (!saved) {
      setIsRestoring(false);
      return;
    }
    void Promise.all([api.getProject(saved.projectId), api.listMedia(saved.projectId)])
      .then(([restoredProject, media]) => {
        const restoredSelection = validateSelection(saved.selection, media.assets);
        setProject(restoredProject);
        setSelection(restoredSelection);
        saveActiveWorkspace({ ...saved, selection: restoredSelection ?? undefined });
      })
      .catch(() => localStorage.removeItem(ACTIVE_WORKSPACE_KEY))
      .finally(() => setIsRestoring(false));
  }, [api]);

  function openProject(next: Project) {
    setProject(next);
    setSelection(null);
    saveActiveWorkspace({ projectId: next.id });
  }

  function openSelection(next: MediaSelection) {
    setSelection(next);
    saveActiveWorkspace({ projectId: project!.id, selection: next });
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <h1 className="brand">Holden Reel</h1>
          <p className="tagline">Make reels from the comfort of your own holden.</p>
        </div>
        {project ? <p className="muted">{project.name}</p> : null}
      </header>
      {isRestoring ? <p className="muted" role="status">Restoring your workspace…</p> : project ? (
        selection ? (
          <DraftWorkspace api={api} project={project} selection={selection} />
        ) : (
          <MediaImport api={api} project={project} onReady={openSelection} />
        )
      ) : (
        <ProjectStart api={api} onOpen={openProject} />
      )}
    </main>
  );
}

function validateSelection(saved: MediaSelection | undefined, assets: MediaSelection["assets"]): MediaSelection | null {
  if (!saved) return null;
  const available = new Map(assets.filter((asset) => asset.available).map((asset) => [asset.id, asset]));
  const audio = available.get(saved.audioAssetId);
  const visuals = saved.visualAssetIds.filter((id) => {
    const asset = available.get(id);
    return asset?.kind === "video" || asset?.kind === "image";
  });
  const audioIsUsable = audio?.kind === "audio" || (audio?.kind === "video" && audio.has_audio === true);
  if (!audioIsUsable || visuals.length === 0) return null;
  return { assets: [...available.values()], audioAssetId: audio.id, visualAssetIds: visuals };
}
