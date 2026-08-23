import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { connect } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

interface FixtureManifest {
  "red.mp4": string;
  "blue.mp4": string;
  "still.jpg": string;
  "song.wav": string;
}
interface ProcessState { error: Error | null; output: string[] }
interface OwnedProcess { child: ChildProcess; name: string; port?: number }

const processStates = new WeakMap<ChildProcess, ProcessState>();
const FIXTURE_MEDIA_FILE_COUNT = 4;
const FIXTURE_FFMPEG_TIMEOUT_MS = 30_000;
const FIXTURE_WATCHDOG_GRACE_MS = 15_000;
const FIXTURE_GENERATION_TIMEOUT_MS =
  FIXTURE_MEDIA_FILE_COUNT * FIXTURE_FFMPEG_TIMEOUT_MS + FIXTURE_WATCHDOG_GRACE_MS;
const STARTUP_TIMEOUT_MS = 30_000;
const GRACEFUL_SHUTDOWN_TIMEOUT_MS = 10_000;
const FORCED_SHUTDOWN_TIMEOUT_MS = 3_000;

export default async function globalSetup() {
  const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const repositoryRoot = resolve(webRoot, "../..");
  const resultsRoot = resolve(webRoot, "test-results");
  await mkdir(resultsRoot, { recursive: true });
  const fixtureRoot = await mkdtemp(resolve(resultsRoot, "fixture-media-"));
  const dataRoot = await mkdtemp(resolve(resultsRoot, "api-data-"));
  const descriptorPath = resolve(resultsRoot, "fixture.json");
  const lifecyclePath = resolve(resultsRoot, `lifecycle-${process.pid}.json`);
  const processes: OwnedProcess[] = [];
  let guardian: ChildProcess | undefined;
  let cleanupPromise: Promise<void> | undefined;
  const cleanup = () => {
    cleanupPromise ??= cleanupResources(processes, [fixtureRoot, dataRoot, descriptorPath, lifecyclePath])
      .finally(() => guardian?.kill("SIGTERM"));
    return cleanupPromise;
  };

  try {
    await writeLifecycle(lifecyclePath, processes, [fixtureRoot, dataRoot, descriptorPath, lifecyclePath]);
    guardian = spawn(
      process.execPath,
      [resolve(webRoot, "e2e/process-guardian.mjs"), String(process.pid), lifecyclePath],
      { detached: true, shell: false, stdio: "ignore" },
    );
    guardian.unref();
    const stdout = runCaptured(
      "uv", ["run", "python", "tests/fixture_media.py", "--output", fixtureRoot],
      resolve(repositoryRoot, "apps/api"), FIXTURE_GENERATION_TIMEOUT_MS,
      "fixture-media generation",
    );
    const media = JSON.parse(stdout) as FixtureManifest;
    const python = runCaptured(
      "uv", ["run", "python", "-c", "import sys; print(sys.executable)"],
      resolve(repositoryRoot, "apps/api"), 10_000, "Python interpreter discovery",
    ).trim();

    const api = startOwnedProcess(
      "API", python,
      ["-m", "uvicorn", "holden_reel.main:app", "--app-dir", "src", "--host", "127.0.0.1", "--port", "0"],
      resolve(repositoryRoot, "apps/api"),
      { HOLDEN_REEL_DATA_DIR: dataRoot, PYTHONPATH: resolve(repositoryRoot, "apps/api/src") },
    );
    processes.push(api);
    await writeLifecycle(lifecyclePath, processes, [fixtureRoot, dataRoot, descriptorPath, lifecyclePath]);
    const apiBinding = await waitForBinding(
      api, /Uvicorn running on (http:\/\/127\.0\.0\.1:(\d+))\b/, STARTUP_TIMEOUT_MS,
    );
    const apiUrl = apiBinding[1];
    const apiPort = Number(apiBinding[2]);
    api.port = apiPort;
    await waitForApiIdentity(api, `${apiUrl}/api/health`, STARTUP_TIMEOUT_MS);

    const web = startOwnedProcess(
      "web", process.execPath,
      [resolve(webRoot, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "0", "--strictPort"],
      webRoot, { HOLDEN_REEL_API_URL: apiUrl },
    );
    processes.push(web);
    await writeLifecycle(lifecyclePath, processes, [fixtureRoot, dataRoot, descriptorPath, lifecyclePath]);
    const webBinding = await waitForBinding(
      web, /Local:\s+(http:\/\/127\.0\.0\.1:(\d+)\/?)/, STARTUP_TIMEOUT_MS,
    );
    const webUrl = webBinding[1].replace(/\/$/, "");
    const webPort = Number(webBinding[2]);
    web.port = webPort;
    await waitForWebIdentity(web, `${webUrl}/`, STARTUP_TIMEOUT_MS);

    await writeFile(descriptorPath, `${JSON.stringify({
      folderPath: dirname(media["song.wav"]), apiPort, apiUrl, webPort, webUrl,
    }, null, 2)}\n`, "utf8");
    console.log(`E2E loopback URLs: API ${apiUrl}; web ${webUrl}`);
  } catch (error) {
    try { await cleanup(); }
    catch (cleanupError) {
      throw new AggregateError([error, cleanupError], "E2E setup failed and its owned resources could not be fully cleaned");
    }
    throw error;
  }
  return cleanup;
}

async function writeLifecycle(path: string, processes: OwnedProcess[], paths: string[]) {
  await writeFile(path, JSON.stringify({
    groups: processes.map(({ child }) => child.pid).filter((pid) => pid !== undefined),
    paths,
  }), "utf8");
}

function runCaptured(
  command: string, arguments_: string[], cwd: string, timeoutMs: number, description: string,
): string {
  try {
    return execFileSync(command, arguments_, {
      cwd, encoding: "utf8", timeout: timeoutMs, stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    const details = error as Error & { stdout?: string | Buffer; stderr?: string | Buffer };
    const output = [details.stdout, details.stderr]
      .filter((value) => value !== undefined).map(String).join("").trim();
    throw new Error(
      `${description} failed within its ${timeoutMs}ms watchdog: ${details.message}${output ? `\n${output}` : ""}`,
      { cause: error },
    );
  }
}

function startOwnedProcess(
  name: string, command: string, arguments_: string[], cwd: string,
  environment: Record<string, string> = {},
): OwnedProcess {
  const childEnvironment = { ...process.env, ...environment };
  delete childEnvironment.FORCE_COLOR;
  delete childEnvironment.NO_COLOR;
  const child = spawn(command, arguments_, {
    cwd, env: childEnvironment, detached: process.platform !== "win32",
    shell: false, stdio: ["ignore", "pipe", "pipe"],
  });
  const state: ProcessState = { error: null, output: [] };
  processStates.set(child, state);
  child.once("error", (error) => { state.error = error; });
  child.stdout?.on("data", (chunk) => state.output.push(String(chunk)));
  child.stderr?.on("data", (chunk) => state.output.push(String(chunk)));
  return { child, name };
}

async function waitForBinding(
  owned: OwnedProcess, pattern: RegExp, timeoutMs: number,
): Promise<RegExpMatchArray> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    assertRunning(owned);
    const match = rawProcessOutput(owned.child).match(pattern);
    if (match !== null) return match;
    await delay(50);
  }
  throw new Error(`Timed out waiting ${timeoutMs}ms for ${owned.name} to report its loopback binding${processOutput(owned.child)}`);
}

async function waitForApiIdentity(owned: OwnedProcess, url: string, timeoutMs: number) {
  const response = await waitForResponse(owned, url, timeoutMs);
  const payload = (await response.json()) as unknown;
  const keys = payload !== null && typeof payload === "object"
    ? Object.keys(payload).sort()
    : [];
  if (
    keys.join(",") !== "status,version"
    || (payload as { status?: unknown }).status !== "ok"
    || (payload as { version?: unknown }).version !== "0.1.0"
  ) {
    throw new Error(`Owned API returned an unexpected health identity at ${url}: ${JSON.stringify(payload)}`);
  }
}

async function waitForWebIdentity(owned: OwnedProcess, url: string, timeoutMs: number) {
  const response = await waitForResponse(owned, url, timeoutMs);
  const html = await response.text();
  if (!html.includes("<title>Holden Reel</title>")) {
    throw new Error(`Owned web server did not return the Holden Reel index at ${url}`);
  }
}

async function waitForResponse(owned: OwnedProcess, url: string, timeoutMs: number): Promise<Response> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    assertRunning(owned);
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      if (response.status === 200) return response;
      await response.body?.cancel();
    } catch { /* Binding was reported before the owned server became request-ready. */ }
    await delay(100);
  }
  throw new Error(`Timed out waiting ${timeoutMs}ms for ${url}${processOutput(owned.child)}`);
}

function assertRunning(owned: OwnedProcess) {
  const state = processStates.get(owned.child);
  if (state?.error) throw new Error(`${owned.name} test server failed to start: ${state.error.message}${processOutput(owned.child)}`);
  if (owned.child.exitCode !== null || owned.child.signalCode !== null) {
    throw new Error(`${owned.name} test server exited with code ${owned.child.exitCode}${processOutput(owned.child)}`);
  }
}
function rawProcessOutput(child: ChildProcess) { return processStates.get(child)?.output.join("") ?? ""; }
function processOutput(child: ChildProcess) {
  const text = rawProcessOutput(child).trim();
  return text ? `\n${text}` : "";
}

async function cleanupResources(processes: OwnedProcess[], paths: string[]) {
  const failures: unknown[] = [];
  for (const owned of [...processes].reverse()) {
    try { await stopOwnedProcess(owned); } catch (error) { failures.push(error); }
  }
  if (failures.length > 0) {
    throw new AggregateError(failures, "Owned E2E servers did not terminate; temporary resources were retained");
  }
  await Promise.all(paths.map((path) => rm(path, { recursive: true, force: true })));
}

async function stopOwnedProcess(owned: OwnedProcess) {
  if (owned.child.pid === undefined) return;
  signalOwnedProcess(owned.child, "SIGTERM");
  if (await waitForShutdown(owned, GRACEFUL_SHUTDOWN_TIMEOUT_MS)) return;
  if (process.platform === "win32") {
    runCaptured(
      "taskkill", ["/PID", String(owned.child.pid), "/T", "/F"], process.cwd(),
      FORCED_SHUTDOWN_TIMEOUT_MS, `${owned.name} process-tree termination`,
    );
  } else signalOwnedProcess(owned.child, "SIGKILL");
  if (await waitForShutdown(owned, FORCED_SHUTDOWN_TIMEOUT_MS)) return;
  throw new Error(`${owned.name} process group ${owned.child.pid} or loopback listener did not terminate within ${GRACEFUL_SHUTDOWN_TIMEOUT_MS + FORCED_SHUTDOWN_TIMEOUT_MS}ms${processOutput(owned.child)}`);
}

function signalOwnedProcess(child: ChildProcess, signal: NodeJS.Signals) {
  if (child.pid === undefined) return;
  try {
    if (process.platform === "win32") child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
  }
}

async function waitForShutdown(owned: OwnedProcess, timeoutMs: number) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const childExited = owned.child.exitCode !== null || owned.child.signalCode !== null;
    const processTreeExited = process.platform === "win32"
      ? childExited
      : !isProcessGroupAlive(owned.child.pid);
    const listenerClosed = owned.port === undefined || !(await isLoopbackPortOpen(owned.port));
    if (processTreeExited && listenerClosed) return true;
    await delay(50);
  }
  return false;
}

function isProcessGroupAlive(processGroupId: number | undefined): boolean {
  if (processGroupId === undefined) return false;
  try {
    process.kill(-processGroupId, 0);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    throw error;
  }
}

async function isLoopbackPortOpen(port: number): Promise<boolean> {
  return new Promise((resolveOpen) => {
    const socket = connect({ host: "127.0.0.1", port });
    let settled = false;
    const finish = (open: boolean) => {
      if (settled) return;
      settled = true;
      socket.removeAllListeners();
      socket.destroy();
      resolveOpen(open);
    };
    socket.setTimeout(250);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}
