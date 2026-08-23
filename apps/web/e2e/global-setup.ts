import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

interface FixtureManifest {
  "red.mp4": string;
  "blue.mp4": string;
  "still.jpg": string;
  "song.wav": string;
}

export default async function globalSetup() {
  const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const repositoryRoot = resolve(webRoot, "../..");
  const resultsRoot = resolve(webRoot, "test-results");
  await mkdir(resultsRoot, { recursive: true });
  const fixtureRoot = await mkdtemp(resolve(resultsRoot, "fixture-media-"));
  const dataRoot = await mkdtemp(resolve(resultsRoot, "api-data-"));
  const descriptorPath = resolve(resultsRoot, "fixture.json");
  const processes: ChildProcess[] = [];

  try {
    const stdout = execFileSync(
      "uv",
      ["run", "python", "tests/fixture_media.py", "--output", fixtureRoot],
      {
        cwd: resolve(repositoryRoot, "apps/api"),
        encoding: "utf8",
        timeout: 30_000,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    const media = JSON.parse(stdout) as FixtureManifest;
    await writeFile(
      descriptorPath,
      `${JSON.stringify({ folderPath: dirname(media["song.wav"]) }, null, 2)}\n`,
      "utf8",
    );

    const api = startProcess(
      "uv",
      [
        "run",
        "uvicorn",
        "holden_reel.main:app",
        "--app-dir",
        "src",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
      ],
      resolve(repositoryRoot, "apps/api"),
      { HOLDEN_REEL_DATA_DIR: dataRoot },
    );
    processes.push(api);
    await waitForUrl(api, "http://127.0.0.1:8000/api/health", 30_000);

    const web = startProcess(
      "pnpm",
      ["exec", "vite", "--host", "127.0.0.1", "--port", "4173", "--strictPort"],
      webRoot,
    );
    processes.push(web);
    await waitForUrl(web, "http://127.0.0.1:4173/", 30_000);
  } catch (error) {
    await Promise.all(processes.map(stopProcess));
    await Promise.all([
      rm(fixtureRoot, { recursive: true, force: true }),
      rm(dataRoot, { recursive: true, force: true }),
      rm(descriptorPath, { force: true }),
    ]);
    throw error;
  }

  return async () => {
    await Promise.all(processes.map(stopProcess));
    await Promise.all([
      rm(fixtureRoot, { recursive: true, force: true }),
      rm(dataRoot, { recursive: true, force: true }),
      rm(descriptorPath, { force: true }),
    ]);
  };
}

function startProcess(
  command: string,
  arguments_: string[],
  cwd: string,
  environment: Record<string, string> = {},
): ChildProcess {
  const child = spawn(command, arguments_, {
    cwd,
    env: { ...process.env, ...environment },
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const output: string[] = [];
  child.stdout?.on("data", (chunk) => output.push(String(chunk)));
  child.stderr?.on("data", (chunk) => output.push(String(chunk)));
  Reflect.set(child, "capturedOutput", output);
  return child;
}

async function waitForUrl(
  child: ChildProcess,
  url: string,
  timeoutMs: number,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    assertRunning(child);
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) {
        assertRunning(child);
        return;
      }
    } catch {
      // The server has not bound its loopback port yet.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
  }
  throw new Error(`Timed out waiting ${timeoutMs}ms for ${url}${processOutput(child)}`);
}

function assertRunning(child: ChildProcess) {
  if (child.exitCode !== null) {
    throw new Error(`Local test server exited with code ${child.exitCode}${processOutput(child)}`);
  }
}

function processOutput(child: ChildProcess): string {
  const output = Reflect.get(child, "capturedOutput") as string[] | undefined;
  const text = output?.join("").trim();
  return text ? `\n${text}` : "";
}

async function stopProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null) return;
  child.kill("SIGTERM");
  const exited = new Promise<void>((resolveExit) => child.once("exit", () => resolveExit()));
  const timeout = new Promise<"timeout">((resolveTimeout) =>
    setTimeout(() => resolveTimeout("timeout"), 5_000),
  );
  if ((await Promise.race([exited, timeout])) === "timeout" && child.exitCode === null) {
    child.kill("SIGKILL");
    await Promise.race([exited, new Promise((resolveDelay) => setTimeout(resolveDelay, 1_000))]);
  }
}
