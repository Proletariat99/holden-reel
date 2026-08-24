import { FormEvent, useEffect, useRef, useState } from "react";

import type { ApiClient, MediaAsset, MediaSelection, Project } from "../../types";

interface MediaImportProps {
  api: ApiClient;
  project: Project;
  onReady: (selection: MediaSelection) => void;
}

export function MediaImport({ api, project, onReady }: MediaImportProps) {
  const [path, setPath] = useState("");
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [audioAssetId, setAudioAssetId] = useState("");
  const [visualAssetIds, setVisualAssetIds] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);

  useEffect(() => {
    let isCurrent = true;
    const generation = ++requestGeneration.current;
    setIsLoading(true);
    void api
      .listMedia(project.id)
      .then((result) => {
        if (isCurrent && requestGeneration.current === generation) updateCatalog(result.assets);
      })
      .catch((reason: unknown) => {
        if (isCurrent && requestGeneration.current === generation) setError(messageFrom(reason));
      })
      .finally(() => {
        if (isCurrent && requestGeneration.current === generation) setIsLoading(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [api, project.id]);

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const sourcePath = path.trim();
    if (!sourcePath || isImporting) return;

    const generation = ++requestGeneration.current;
    setIsImporting(true);
    setIsLoading(false);
    setError(null);
    try {
      const result = await api.importMedia(project.id, sourcePath);
      if (requestGeneration.current === generation) updateCatalog(result.assets);
    } catch (reason: unknown) {
      if (requestGeneration.current === generation) setError(messageFrom(reason));
    } finally {
      if (requestGeneration.current === generation) setIsImporting(false);
    }
  }

  function toggleVisual(assetId: string) {
    setVisualAssetIds((current) =>
      current.includes(assetId) ? current.filter((id) => id !== assetId) : [...current, assetId],
    );
  }

  const audioAssets = assets.filter(isAudioCapable);
  const visualAssets = assets.filter((asset) => asset.kind === "video" || asset.kind === "image");
  const selectedAudio = audioAssets.find((asset) => asset.available && asset.id === audioAssetId);
  const selectedVisuals = visualAssets.filter(
    (asset) => asset.available && visualAssetIds.includes(asset.id),
  );
  const availableAssets = assets.filter((asset) => asset.available);
  const canContinue = selectedAudio !== undefined && selectedVisuals.length > 0;

  function updateCatalog(nextAssets: MediaAsset[]) {
    setAssets(nextAssets);
    setAudioAssetId((current) =>
      nextAssets.some((asset) => asset.id === current && isAudioCapable(asset) && asset.available)
        ? current
        : nextAssets.filter((asset) => asset.available && isAudioCapable(asset)).length === 1
          ? nextAssets.find((asset) => asset.available && isAudioCapable(asset))!.id
          : "",
    );
    setVisualAssetIds((current) =>
      current.filter((id) =>
        nextAssets.some(
          (asset) =>
            asset.id === id &&
            (asset.kind === "video" || asset.kind === "image") &&
            asset.available,
        ),
      ),
    );
  }

  function handleContinue() {
    if (!selectedAudio || selectedVisuals.length === 0) return;
    onReady({
      assets: availableAssets,
      audioAssetId: selectedAudio.id,
      visualAssetIds: selectedVisuals.map((asset) => asset.id),
    });
  }

  return (
    <section className="guided-screen" aria-labelledby="media-import-heading">
      <p className="eyebrow">Step 2 of 2</p>
      <h2 id="media-import-heading" className="screen-heading">Bring in your local media</h2>
      <p className="intro">Holden Reel catalogs files where they already live. Nothing is copied into this project.</p>

      <form className="panel stack" onSubmit={handleImport}>
        <label htmlFor="media-path">Absolute folder path</label>
        <input
          id="media-path"
          name="media-path"
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder="/Users/you/Movies/rehearsal"
          spellCheck={false}
          autoComplete="off"
          required
        />
        <p className="field-note">For this local milestone, enter a full path that the backend can read.</p>
        <button type="submit" disabled={isImporting || !path.trim()}>
          {isImporting ? "Importing folder…" : "Import folder"}
        </button>
      </form>

      {error ? <p className="error-message" role="alert">{error}</p> : null}
      {isLoading ? <p className="muted">Checking this project’s catalog…</p> : null}

      {!isLoading ? (
        <div className="media-sections">
          <MediaGroup
            title="Choose one soundtrack"
            empty="Import audio or a video with embedded audio to choose a soundtrack."
            assets={audioAssets}
            selectionType="audio"
            selectedIds={audioAssetId ? [audioAssetId] : []}
            onSelect={(assetId) => setAudioAssetId(assetId)}
          />
          <MediaGroup
            title="Choose one or more visuals"
            empty="Import a folder with video or image files to choose visuals."
            assets={visualAssets}
            selectionType="visual"
            selectedIds={visualAssetIds}
            onSelect={toggleVisual}
          />
        </div>
      ) : null}

      <button
        className="continue-button"
        type="button"
        disabled={!canContinue}
        onClick={handleContinue}
      >
        Continue
      </button>
    </section>
  );
}

interface MediaGroupProps {
  title: string;
  empty: string;
  assets: MediaAsset[];
  selectionType: "audio" | "visual";
  selectedIds: string[];
  onSelect: (assetId: string) => void;
}

function MediaGroup({ title, empty, assets, selectionType, selectedIds, onSelect }: MediaGroupProps) {
  return (
    <fieldset className="media-group">
      <legend>{title}</legend>
      {assets.length === 0 ? <p className="muted">{empty}</p> : null}
      <div className="media-grid">
        {assets.map((asset) => {
          const selected = selectedIds.includes(asset.id);
          const inputId = `${selectionType}-${asset.id}`;
          return (
            <label className="media-card" key={asset.id} htmlFor={inputId}>
              <input
                id={inputId}
                type={selectionType === "audio" ? "radio" : "checkbox"}
                name={selectionType === "audio" ? "audio-asset" : undefined}
                checked={selected}
                disabled={!asset.available}
                onChange={() => onSelect(asset.id)}
              />
              <span className="media-card-body">
                <span className="media-name">{fileName(asset.path)}</span>
                <span className="media-details">{mediaDetails(asset, selectionType)}</span>
                {!asset.available ? <span className="offline-badge">Offline</span> : null}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts.at(-1) || path;
}

function mediaDetails(asset: MediaAsset, selectionType: "audio" | "visual"): string {
  if (selectionType === "audio" && asset.kind === "video") {
    const parts = ["embedded audio"];
    if (asset.audio_duration_ms !== null && asset.audio_duration_ms !== undefined) {
      parts.push(formatDuration(asset.audio_duration_ms));
    }
    return parts.join(" · ");
  }
  const parts: string[] = [asset.kind];
  if (asset.width !== null && asset.height !== null) parts.push(`${asset.width} × ${asset.height}`);
  if (asset.duration_ms !== null) parts.push(formatDuration(asset.duration_ms));
  return parts.join(" · ");
}

function isAudioCapable(asset: MediaAsset): boolean {
  return asset.kind === "audio" || (asset.kind === "video" && asset.has_audio === true);
}

function formatDuration(durationMs: number): string {
  const seconds = Math.floor(durationMs / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Something went wrong. Please try again.";
}
