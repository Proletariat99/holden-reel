import { FormEvent, useEffect, useState } from "react";

import type { ApiClient, Project } from "../../types";

interface ProjectStartProps {
  api: ApiClient;
  onOpen: (project: Project) => void;
}

export function ProjectStart({ api, onOpen }: ProjectStartProps) {
  const [name, setName] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isCurrent = true;
    void api
      .listProjects()
      .then((recentProjects) => {
        if (isCurrent) setProjects(recentProjects);
      })
      .catch((reason: unknown) => {
        if (isCurrent) setError(messageFrom(reason));
      })
      .finally(() => {
        if (isCurrent) setIsLoadingProjects(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [api]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || isCreating) return;

    setIsCreating(true);
    setError(null);
    try {
      onOpen(await api.createProject(trimmedName));
    } catch (reason: unknown) {
      setError(messageFrom(reason));
    } finally {
      setIsCreating(false);
    }
  }

  return (
    <section className="guided-screen" aria-labelledby="project-start-heading">
      <p className="eyebrow">Step 1 of 2</p>
      <h2 id="project-start-heading" className="screen-heading">Start a reel project</h2>
      <p className="intro">Give this batch a name. You can return to any project already on this device.</p>

      <form className="panel stack" onSubmit={handleSubmit}>
        <label htmlFor="project-name">Project name</label>
        <input
          id="project-name"
          name="project-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="e.g. August rehearsal"
          autoComplete="off"
          required
        />
        <button type="submit" disabled={isCreating || !name.trim()}>
          {isCreating ? "Creating project…" : "Create project"}
        </button>
      </form>

      {error ? <p className="error-message" role="alert">{error}</p> : null}

      <section className="recent-projects" aria-labelledby="recent-projects-heading">
        <h2 id="recent-projects-heading">Recent projects</h2>
        {isLoadingProjects ? <p className="muted">Loading projects…</p> : null}
        {!isLoadingProjects && projects.length === 0 ? <p className="muted">No projects yet. Create your first one above.</p> : null}
        <ul className="project-list">
          {projects.map((project) => (
            <li key={project.id}>
              <button className="project-button" type="button" onClick={() => onOpen(project)}>
                <span>{project.name}</span>
                <span className="muted">Open {project.name}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Something went wrong. Please try again.";
}
