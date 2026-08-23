import { useMemo, useState } from "react";

import { ApiClient } from "./api";
import { MediaImport } from "./features/import/MediaImport";
import { ProjectStart } from "./features/projects/ProjectStart";
import type { MediaSelection, Project } from "./types";

export default function App() {
  const api = useMemo(() => new ApiClient(), []);
  const [project, setProject] = useState<Project | null>(null);
  const [selection, setSelection] = useState<MediaSelection | null>(null);

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <h1 className="brand">Holden Reel</h1>
          <p className="tagline">Make reels from the comfort of your own holden.</p>
        </div>
        {project ? <p className="muted">{project.name}</p> : null}
      </header>
      {project ? (
        <>
          <MediaImport api={api} project={project} onReady={setSelection} />
          {selection ? <p className="success-message" role="status">Sources selected. Your reel is ready for planning.</p> : null}
        </>
      ) : (
        <ProjectStart api={api} onOpen={setProject} />
      )}
    </main>
  );
}
