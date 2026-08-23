import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

interface FixtureManifest {
  "red.mp4": string;
  "blue.mp4": string;
  "still.jpg": string;
  "song.wav": string;
}

interface ProcessState {
  error: Error | null;
  output: string[];
}

const processStates = new WeakMap<ChildProcess, ProcessState>();

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

    const python = execFileSync(
      "uv",
      ["run", "python", "-c", "import sys; print(sys.executable)"],
      {
        cwd: resolve(repositoryRoot, "apps/api"),
        encoding: "utf8",
        timeout: 10_000,
        stdio: ["ignore", "pipe", "pipe"],
      },
    ).trim();
    const apiPort = await allocateLoopbackPort(new Set([8000, 4173]));
    const apiUrl = `http://127.0.0.1:${apiPort}`;
    const api = startProcess(
      python,
      [
        "-m",
        "uvicorn",
        "holden_reel.main:app",
        "--app-dir",
        "src",
        "--host",
        "127.0.0.1",
        "--port",
        String(apiPort),
      ],
      resolve(repositoryRoot, "apps/api"),
      {
        HOLDEN_REEL_DATA_DIR: dataRoot,
        PYTHONPATH: resolve(repositoryRoot, "apps/api/src"),
      },
    );
    processes.push(api);
    await waitForUrl(api, `${apiUrl}/api/health`, 30_000);

    const webPort = await allocateLoopbackPort(
      new Set([8000, 4173, apiPort]),
    );
    const webUrl = `http://127.0.0.1:${webPort}`;
    const web = startProcess(
      process.execPath,
      [
        resolve(webRoot, "node_modules/vite/bin/vite.js"),
        "--host",
        "127.0.0.1",
        "--port",
        String(webPort),
        "--strictPort",
      ],
      webRoot,
      { HOLDEN_REEL_API_URL: apiUrl },
    );
    processes.push(web);
    await waitForUrl(web, `${webUrl}/`, 30_000);

    await writeFile(
      descriptorPath,
      `${JSON.stringify(
        {
          folderPath: dirname(media["song.wav"]),
          apiPort,
          apiUrl,
          webPort,
          webUrl,
        },
        null,
        2,
      )}\n`,
      "utf8",
    );
    console.log(`E2E loopback URLs: API ${apiUrl}; web ${webUrl}`);
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
  const childEnvironment = { ...process.env, ...environment };
  delete childEnvironment.FORCE_COLOR;
  delete childEnvironment.NO_COLOR;
  const child = spawn(command, arguments_, {
    cwd,
    env: childEnvironment,
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const state: ProcessState = { error: null, output: [] };
  processStates.set(child, state);
  child.once("error", (error) => {
    state.error = error;
  });
  child.stdout?.on("data", (chunk) => state.output.push(String(chunk)));
  child.stderr?.on("data", (chunk) => state.output.push(String(chunk)));
  return child;
}

async function allocateLoopbackPort(excluded: Set<number>): Promise<number> {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const port = await new Promise<number>((resolvePort, rejectPort) => {
      const server = createServer();
      server.once("error", rejectPort);
      server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
        const address = server.address();
        if (address === null || typeof address === "string") {
          server.close();
          rejectPort(new Error("Could not allocate a loopback TCP port"));
          return;
        }
        server.close((error) => {
          if (error) rejectPort(error);
          else resolvePort(address.port);
        });
      });
    });
    if (!excluded.has(port)) return port;
  }
  throw new Error("Could not allocate a non-default loopback TCP port");
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
        await response.body?.cancel();
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
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
  const state = processStates.get(child);
  if (state?.error) {
    throw new Error(`Local test server failed to start: ${state.error.message}${processOutput(child)}`);
  }
  if (child.exitCode !== null) {
    throw new Error(`Local test server exited with code ${child.exitCode}${processOutput(child)}`);
  }
}

function processOutput(child: ChildProcess): string {
  const text = processStates.get(child)?.output.join("").trim();
  return text ? `\n${text}` : "";
}

async function stopProcess(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined) return;
  const exited = new Promise<void>((resolveExit) => child.once("exit", () => resolveExit()));
  if (!child.kill("SIGTERM")) return;
  const timeout = new Promise<"timeout">((resolveTimeout) =>
    setTimeout(() => resolveTimeout("timeout"), 10_000),
  );
  if ((await Promise.race([exited, timeout])) === "timeout" && child.exitCode === null) {
    child.kill("SIGKILL");
    await Promise.race([exited, new Promise((resolveDelay) => setTimeout(resolveDelay, 1_000))]);
  }
}
