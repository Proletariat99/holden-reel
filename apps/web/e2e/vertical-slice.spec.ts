import { readFile, stat } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

interface FixtureDescriptor {
  folderPath: string;
  apiPort: number;
  apiUrl: string;
  webPort: number;
  webUrl: string;
}

const fixtureDescriptorPath = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../test-results/fixture.json",
);

test("creates a project and exports a playable, seekable reel", async ({ page }) => {
  const fixture = JSON.parse(
    await readFile(fixtureDescriptorPath, "utf8"),
  ) as FixtureDescriptor;

  expect(fixture.apiPort).toBeGreaterThan(0);
  expect(fixture.apiPort).not.toBe(8000);
  expect(fixture.apiUrl).toBe(`http://127.0.0.1:${fixture.apiPort}`);
  expect(fixture.webPort).toBeGreaterThan(0);
  expect(fixture.webPort).not.toBe(4173);
  expect(fixture.webPort).not.toBe(fixture.apiPort);
  expect(fixture.webUrl).toBe(`http://127.0.0.1:${fixture.webPort}`);

  await page.goto(fixture.webUrl);
  await page.getByLabel("Project name").fill("Golden Reel");
  await page.getByRole("button", { name: "Create project" }).click();

  await page.getByLabel("Absolute folder path").fill(fixture.folderPath);
  await page.getByRole("button", { name: "Import folder" }).click();
  await page.getByRole("radio", { name: /song\.wav/ }).check();
  await page.getByRole("checkbox", { name: /off-center\.mp4/ }).check();
  await page.getByRole("checkbox", { name: /left-red\.mp4/ }).check();
  await page.getByRole("checkbox", { name: /right-blue\.mp4/ }).check();
  await page.getByRole("button", { name: "Continue" }).click();

  await page.getByRole("radio", { name: "15 seconds" }).check();
  const quickDissolve = page.getByRole("radio", { name: "Quick dissolve" });
  await quickDissolve.check();
  await expect(quickDissolve).toBeChecked();
  await page.getByRole("button", { name: "Generate draft" }).click();

  const preview = page.getByLabel("Reel preview");
  await expect(preview).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Quick dissolve · 200 ms")).toBeVisible();
  const playback = await preview.evaluate(async (element) => {
    const video = element as HTMLVideoElement;
    video.muted = true;
    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      await new Promise<void>((resolveLoaded, rejectLoaded) => {
        const cleanup = () => {
          window.clearTimeout(timeout);
          video.removeEventListener("loadeddata", handleLoaded);
          video.removeEventListener("error", handleError);
        };
        const handleLoaded = () => {
          cleanup();
          resolveLoaded();
        };
        const handleError = () => {
          cleanup();
          rejectLoaded(new Error("preview media failed to load"));
        };
        const timeout = window.setTimeout(
          () => {
            cleanup();
            rejectLoaded(new Error("preview media load timed out"));
          },
          10_000,
        );
        video.addEventListener("loadeddata", handleLoaded, { once: true });
        video.addEventListener("error", handleError, { once: true });
      });
    }
    await video.play();
    await new Promise<void>((resolvePlayback, rejectPlayback) => {
      const timeout = window.setTimeout(
        () => rejectPlayback(new Error("preview playback did not advance")),
        5_000,
      );
      const handleTimeUpdate = () => {
        if (video.currentTime <= 0.05) return;
        window.clearTimeout(timeout);
        video.removeEventListener("timeupdate", handleTimeUpdate);
        resolvePlayback();
      };
      video.addEventListener("timeupdate", handleTimeUpdate);
    });
    const playedTime = video.currentTime;
    video.pause();
    const seeked = new Promise<void>((resolveSeek, rejectSeek) => {
      const timeout = window.setTimeout(
        () => rejectSeek(new Error("preview seek timed out")),
        5_000,
      );
      video.addEventListener(
        "seeked",
        () => {
          window.clearTimeout(timeout);
          resolveSeek();
        },
        { once: true },
      );
    });
    video.currentTime = 5;
    await seeked;
    return {
      duration: video.duration,
      playedTime,
      seekedTime: video.currentTime,
    };
  });
  expect(playback.duration).toBeCloseTo(15, 1);
  expect(playback.playedTime).toBeGreaterThan(0);
  expect(playback.seekedTime).toBeCloseTo(5, 1);

  await page.getByRole("button", { name: "Export final" }).click();
  const downloadPromise = page.waitForEvent("download", { timeout: 120_000 });
  await page.getByRole("link", { name: "Download final reel" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.mp4$/);
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  expect((await stat(downloadPath!)).size).toBeGreaterThan(0);
});
