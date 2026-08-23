import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { ApiClient, MediaAsset, Project } from "../../types";
import { MediaImport } from "./MediaImport";

const project: Project = {
  id: "p1",
  name: "Rehearsal",
  created_at: "2026-08-23T12:00:00+00:00",
  updated_at: "2026-08-23T12:00:00+00:00",
};

const assets: MediaAsset[] = [
  {
    id: "a1",
    project_id: "p1",
    path: "/Users/dave/Media/song.wav",
    kind: "audio",
    duration_ms: 18000,
    width: null,
    height: null,
    codec: "pcm_s16le",
    available: true,
    fingerprint: "audio-fingerprint",
  },
  {
    id: "v1",
    project_id: "p1",
    path: "/Users/dave/Media/rehearsal.mp4",
    kind: "video",
    duration_ms: 4000,
    width: 320,
    height: 240,
    codec: "h264",
    available: true,
    fingerprint: "video-fingerprint",
  },
  {
    id: "v2",
    project_id: "p1",
    path: "/Users/dave/Media/still.jpg",
    kind: "image",
    duration_ms: null,
    width: 320,
    height: 240,
    codec: "mjpeg",
    available: false,
    fingerprint: "image-fingerprint",
  },
];

function fakeApi(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    createProject: vi.fn().mockResolvedValue(project),
    listProjects: vi.fn().mockResolvedValue([project]),
    getProject: vi.fn().mockResolvedValue(project),
    importMedia: vi.fn().mockResolvedValue({ assets }),
    listMedia: vi.fn().mockResolvedValue({ assets }),
    ...overrides,
  };
}

it("imports an absolute folder path and shows catalogued media details", async () => {
  // Break caught: a local path does not reach the import API or returned metadata is hidden.
  const api = fakeApi({ listMedia: vi.fn().mockResolvedValue({ assets: [] }) });
  const user = userEvent.setup();
  render(<MediaImport api={api} project={project} onReady={vi.fn()} />);

  await user.type(screen.getByLabelText(/absolute folder path/i), "/Users/dave/Media");
  await user.click(screen.getByRole("button", { name: /import folder/i }));

  expect(api.importMedia).toHaveBeenCalledWith("p1", "/Users/dave/Media");
  expect(await screen.findByText("song.wav")).toBeInTheDocument();
  expect(screen.getByText(/audio · 0:18/i)).toBeInTheDocument();
  expect(screen.getByText(/video · 320 × 240 · 0:04/i)).toBeInTheDocument();
  expect(screen.getByText(/offline/i)).toBeInTheDocument();
});

it("keeps Continue disabled until keyboard selection includes audio and visual media", async () => {
  // Break caught: the workflow advances without both required, available source types.
  const onReady = vi.fn();
  const user = userEvent.setup();
  render(<MediaImport api={fakeApi()} project={project} onReady={onReady} />);

  const continueButton = await screen.findByRole("button", { name: /continue/i });
  expect(continueButton).toBeDisabled();

  const audioOption = screen.getByRole("radio", { name: /song.wav/i });
  audioOption.focus();
  await user.keyboard(" ");
  expect(audioOption).toBeChecked();
  expect(continueButton).toBeDisabled();

  const visualOption = screen.getByRole("checkbox", { name: /rehearsal.mp4/i });
  visualOption.focus();
  await user.keyboard(" ");
  expect(visualOption).toBeChecked();
  expect(continueButton).toBeEnabled();

  await user.click(continueButton);
  expect(onReady).toHaveBeenCalledWith({
    assets,
    audioAssetId: "a1",
    visualAssetIds: ["v1"],
  });
});

it("shows import errors and prevents a second submission while importing", async () => {
  // Break caught: import failures are invisible or repeated clicks start duplicate imports.
  let rejectImport: ((reason?: unknown) => void) | undefined;
  const importMedia = vi.fn(
    () => new Promise<{ assets: MediaAsset[] }>((_, reject) => {
      rejectImport = reject;
    }),
  );
  const user = userEvent.setup();
  render(
    <MediaImport
      api={fakeApi({ importMedia, listMedia: vi.fn().mockResolvedValue({ assets: [] }) })}
      project={project}
      onReady={vi.fn()}
    />,
  );

  await user.type(screen.getByLabelText(/absolute folder path/i), "/Users/dave/missing");
  const importButton = screen.getByRole("button", { name: /import folder/i });
  await user.click(importButton);
  await user.click(importButton);

  expect(importMedia).toHaveBeenCalledTimes(1);
  expect(importButton).toBeDisabled();

  rejectImport?.(new Error("Media path was not found"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Media path was not found"));
});
