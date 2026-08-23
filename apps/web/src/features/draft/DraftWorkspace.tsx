import { useState } from "react";

import { ApiError, artifactUrl } from "../../api";
import type {
  ApiClient,
  MediaAsset,
  MediaSelection,
  Project,
  ReelPlan,
  RenderJob,
  RenderProfile,
} from "../../types";
import { useJob } from "../../useJob";

interface DraftWorkspaceProps {
  api: ApiClient;
  project: Project;
  selection: MediaSelection;
}

export function DraftWorkspace({ api, project, selection }: DraftWorkspaceProps) {
  const [durationMs, setDurationMs] = useState<15000 | 30000>(15000);
  const [audioStartSeconds, setAudioStartSeconds] = useState("0");
  const [visualAssetIds, setVisualAssetIds] = useState(selection.visualAssetIds);
  const [plan, setPlan] = useState<ReelPlan | null>(null);
  const [previewJobId, setPreviewJobId] = useState<string | null>(null);
  const [finalJobId, setFinalJobId] = useState<string | null>(null);
  const [isComposing, setIsComposing] = useState(false);
  const [startingProfile, setStartingProfile] = useState<RenderProfile | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  const preview = useJob(api, previewJobId);
  const final = useJob(api, finalJobId);
  const assetsById = new Map(selection.assets.map((asset) => [asset.id, asset]));
  const audioAsset = assetsById.get(selection.audioAssetId);

  async function handleGenerate() {
    if (previewInFlight || finalInFlight) return;
    const audioStartMs = validateAudioStart(audioStartSeconds, durationMs, audioAsset);
    if (typeof audioStartMs === "string") {
      setActionError(new Error(audioStartMs));
      return;
    }

    setIsComposing(true);
    setActionError(null);
    setPreviewJobId(null);
    setFinalJobId(null);
    try {
      const composed = await api.composePlan(project.id, {
        duration_ms: durationMs,
        audio_asset_id: selection.audioAssetId,
        audio_start_ms: audioStartMs,
        visual_asset_ids: visualAssetIds,
      });
      setPlan(composed);
      await startRender(composed, "preview");
    } catch (reason: unknown) {
      setActionError(toError(reason));
    } finally {
      setIsComposing(false);
    }
  }

  async function startRender(currentPlan: ReelPlan, profile: RenderProfile) {
    if (profile === "preview" ? previewInFlight : finalInFlight) return;
    setStartingProfile(profile);
    setActionError(null);
    if (profile === "preview") setPreviewJobId(null);
    else setFinalJobId(null);
    try {
      const job = await api.startRender(currentPlan.id, profile);
      if (profile === "preview") setPreviewJobId(job.id);
      else setFinalJobId(job.id);
    } catch (reason: unknown) {
      setActionError(toError(reason));
    } finally {
      setStartingProfile(null);
    }
  }

  function moveVisual(index: number, offset: -1 | 1) {
    const destination = index + offset;
    if (destination < 0 || destination >= visualAssetIds.length) return;
    setVisualAssetIds((current) => {
      const reordered = [...current];
      [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
      return reordered;
    });
  }

  const previewActive = isActive(preview.job);
  const finalActive = isActive(final.job);
  const previewInFlight =
    previewJobId !== null && (preview.job?.id !== previewJobId || previewActive);
  const finalInFlight = finalJobId !== null && (final.job?.id !== finalJobId || finalActive);

  return (
    <section className="guided-screen draft-workspace" aria-labelledby="draft-heading">
      <p className="eyebrow">Draft workspace</p>
      <h2 id="draft-heading" className="screen-heading">Shape the reel, then render it</h2>
      <p className="intro">Choose the timing and source order. The same saved plan drives both preview and final export.</p>

      <div className="draft-grid">
        <section className="panel stack" aria-labelledby="draft-settings-heading">
          <h3 id="draft-settings-heading">Draft settings</h3>
          <fieldset className="choice-group">
            <legend>Reel length</legend>
            <label className="inline-choice">
              <input
                type="radio"
                name="duration"
                checked={durationMs === 15000}
                onChange={() => setDurationMs(15000)}
              />
              15 seconds
            </label>
            <label className="inline-choice">
              <input
                type="radio"
                name="duration"
                checked={durationMs === 30000}
                onChange={() => setDurationMs(30000)}
              />
              30 seconds
            </label>
          </fieldset>

          <label htmlFor="audio-start">Audio start (seconds)</label>
          <input
            id="audio-start"
            type="number"
            min="0"
            step="0.1"
            inputMode="decimal"
            value={audioStartSeconds}
            onChange={(event) => setAudioStartSeconds(event.target.value)}
          />

          <div>
            <h3>Visual source order</h3>
            <p className="field-note">The composer rotates through sources in this order.</p>
            <ol className="source-order">
              {visualAssetIds.map((assetId, index) => {
                const asset = assetsById.get(assetId);
                const name = fileName(asset?.path ?? assetId);
                return (
                  <li key={assetId}>
                    <span>{name}</span>
                    <span className="order-controls">
                      <button
                        className="secondary-button compact-button"
                        type="button"
                        aria-label={`Move ${name} up`}
                        disabled={index === 0}
                        onClick={() => moveVisual(index, -1)}
                      >
                        ↑
                      </button>
                      <button
                        className="secondary-button compact-button"
                        type="button"
                        aria-label={`Move ${name} down`}
                        disabled={index === visualAssetIds.length - 1}
                        onClick={() => moveVisual(index, 1)}
                      >
                        ↓
                      </button>
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>

          <button
            type="button"
            disabled={isComposing || startingProfile !== null || previewInFlight || finalInFlight}
            onClick={() => void handleGenerate()}
          >
            {isComposing || startingProfile === "preview" ? "Generating draft…" : "Generate draft"}
          </button>
        </section>

        <section className="panel output-panel" aria-labelledby="render-output-heading">
          <h3 id="render-output-heading">Render output</h3>
          {!plan ? <p className="muted">Generate a draft to inspect its plan and preview.</p> : null}

          {plan ? <PlanSummary plan={plan} assetsById={assetsById} /> : null}

          {previewActive ? (
            <JobProgress
              label="Preview render"
              job={preview.job!}
              isCancelling={preview.isCancelling}
              onCancel={() => void preview.cancel()}
            />
          ) : null}

          {preview.job?.status === "failed" ? (
            <div className="job-result">
              <ErrorMessage error={new Error(preview.job.error?.message ?? "Preview render failed")} />
              <button type="button" onClick={() => plan && void startRender(plan, "preview")}>
                Retry preview
              </button>
            </div>
          ) : null}

          {preview.job?.status === "cancelled" ? <p className="muted" role="status">Preview cancelled.</p> : null}

          {preview.job?.status === "succeeded" ? (
            <div className="preview-result">
              <video
                className="preview-video"
                aria-label="Reel preview"
                src={artifactUrl(preview.job.id)}
                controls
                playsInline
              />
              <button
                type="button"
                disabled={startingProfile === "final" || finalInFlight}
                onClick={() => void startRender(plan!, "final")}
              >
                {startingProfile === "final" ? "Starting export…" : "Export final"}
              </button>
            </div>
          ) : null}

          {finalActive ? (
            <JobProgress
              label="Final export"
              job={final.job!}
              isCancelling={final.isCancelling}
              onCancel={() => void final.cancel()}
            />
          ) : null}

          {final.job?.status === "failed" ? (
            <div className="job-result">
              <ErrorMessage error={new Error(final.job.error?.message ?? "Final export failed")} />
              <button type="button" onClick={() => plan && void startRender(plan, "final")}>
                Retry export
              </button>
            </div>
          ) : null}

          {final.job?.status === "succeeded" ? (
            <a
              className="download-link"
              href={artifactUrl(final.job.id)}
              download={`holden-reel-${project.id}.mp4`}
            >
              Download final reel
            </a>
          ) : null}
        </section>
      </div>

      {actionError ? <ErrorMessage error={actionError} /> : null}
      {preview.error ? <ErrorMessage error={preview.error} /> : null}
      {final.error ? <ErrorMessage error={final.error} /> : null}
    </section>
  );
}

function PlanSummary({ plan, assetsById }: { plan: ReelPlan; assetsById: Map<string, MediaAsset> }) {
  return (
    <div className="plan-summary">
      <h3>Draft plan</h3>
      <p>{plan.rationale}</p>
      <ol aria-label="Ordered shot list" className="shot-list">
        {plan.shots.map((shot, index) => (
          <li key={`${shot.asset_id}-${shot.output_start_ms}-${index}`}>
            <span>{fileName(assetsById.get(shot.asset_id)?.path ?? shot.asset_id)}</span>
            <span className="muted">{formatTimeline(shot.output_start_ms)}–{formatTimeline(shot.output_end_ms)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function JobProgress({
  label,
  job,
  isCancelling,
  onCancel,
}: {
  label: string;
  job: RenderJob;
  isCancelling: boolean;
  onCancel: () => void;
}) {
  return (
    <div className="job-progress">
      <div className="job-progress-heading">
        <span>{label}</span>
        <span>{Math.round(job.progress * 100)}%</span>
      </div>
      <progress aria-label={`${label} progress`} max={1} value={job.progress} />
      <button className="secondary-button" type="button" disabled={isCancelling} onClick={onCancel}>
        {isCancelling ? "Cancelling…" : `Cancel ${label.toLowerCase().replace(" render", "")}`}
      </button>
    </div>
  );
}

function ErrorMessage({ error }: { error: Error }) {
  return (
    <p className="error-message" role="alert">
      {error.message}{error instanceof ApiError ? ` (${error.code})` : ""}
    </p>
  );
}

function validateAudioStart(
  rawSeconds: string,
  durationMs: 15000 | 30000,
  audioAsset: MediaAsset | undefined,
): number | string {
  if (!rawSeconds.trim()) return "Enter an audio start time in seconds.";
  const seconds = Number(rawSeconds);
  if (!Number.isFinite(seconds) || seconds < 0) return "Audio start must be zero or greater.";
  const startMs = Math.round(seconds * 1000);
  if (audioAsset?.duration_ms !== null && audioAsset?.duration_ms !== undefined) {
    if (startMs + durationMs > audioAsset.duration_ms) {
      return `Audio start must leave enough room for a ${durationMs / 1000}-second reel in this track.`;
    }
  }
  return startMs;
}

function isActive(job: RenderJob | null): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function fileName(path: string): string {
  return path.split(/[\\/]/).at(-1) || path;
}

function formatTimeline(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function toError(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error("Something went wrong. Please try again.");
}
