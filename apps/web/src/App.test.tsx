import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import App from "./App";

test("renders the Holden Reel welcome copy", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: "Holden Reel" })).toBeInTheDocument();
  expect(
    screen.getByText("Make reels from the comfort of your own holden."),
  ).toBeInTheDocument();
});

test("restores a validated active project and media selection after reload", async () => {
  localStorage.setItem("holden-reel.active", JSON.stringify({ projectId: "p1", selection: {
    assets: [
      { id: "a1", project_id: "p1", path: "/song.wav", kind: "audio", duration_ms: 60000, width: null, height: null, codec: "pcm", available: true, fingerprint: "a" },
      { id: "v1", project_id: "p1", path: "/clip.mp4", kind: "video", duration_ms: 10000, width: 320, height: 240, codec: "h264", available: true, fingerprint: "v" },
    ], audioAssetId: "a1", visualAssetIds: ["v1"],
  }}));
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(
    url.includes("/media") ? { assets: JSON.parse(localStorage.getItem("holden-reel.active")!).selection.assets } : { id: "p1", name: "Restored", created_at: "x", updated_at: "x" },
  ), { status: 200, headers: { "Content-Type": "application/json" } }))));
  render(<App />);
  expect(await screen.findByRole("heading", { name: /shape the reel/i })).toBeInTheDocument();
  expect(screen.getByText("Restored")).toBeInTheDocument();
});

test("returns from the draft to media selection without losing checked sources", async () => {
  // Break caught: entering the draft traps the user or discards their current media choices.
  const user = userEvent.setup();
  const saved = {
    assets: [
      { id: "a1", project_id: "p1", path: "/song.wav", kind: "audio", duration_ms: 60000, width: null, height: null, codec: "pcm", available: true, fingerprint: "a" },
      { id: "v1", project_id: "p1", path: "/clip.mp4", kind: "video", duration_ms: 10000, width: 320, height: 240, codec: "h264", available: true, fingerprint: "v" },
    ],
    audioAssetId: "a1",
    visualAssetIds: ["v1"],
  };
  localStorage.setItem("holden-reel.active", JSON.stringify({ projectId: "p1", selection: saved }));
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(
    url.includes("/media") ? { assets: saved.assets } : { id: "p1", name: "Editable", created_at: "x", updated_at: "x" },
  ), { status: 200, headers: { "Content-Type": "application/json" } }))));
  render(<App />);

  await user.click(await screen.findByRole("button", { name: /back to media selection/i }));

  expect(screen.getByRole("heading", { name: /bring in your local media/i })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /song\.wav/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /clip\.mp4/i })).toBeChecked();
  await user.click(screen.getByRole("button", { name: /continue/i }));
  expect(await screen.findByRole("heading", { name: /shape the reel/i })).toBeInTheDocument();
});

test("restores a video selected for both embedded audio and visuals", async () => {
  const movie = { id: "v1", project_id: "p1", path: "/clip.mov", kind: "video", duration_ms: 60000, audio_duration_ms: 60000, has_audio: true, width: 320, height: 240, codec: "h264", available: true, fingerprint: "v" };
  localStorage.setItem("holden-reel.active", JSON.stringify({ projectId: "p1", selection: {
    assets: [movie], audioAssetId: "v1", visualAssetIds: ["v1"],
  }}));
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(
    url.includes("/media") ? { assets: [movie] } : { id: "p1", name: "Restored movie", created_at: "x", updated_at: "x" },
  ), { status: 200, headers: { "Content-Type": "application/json" } }))));

  render(<App />);

  expect(await screen.findByRole("heading", { name: /shape the reel/i })).toBeInTheDocument();
});
