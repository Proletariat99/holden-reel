import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

interface FixtureManifest {
  "red.mp4": string;
  "blue.mp4": string;
  "off-center.mp4": string;
  "left-red.mp4": string;
  "right-blue.mp4": string;
  "still.jpg": string;
  "song.wav": string;
}
interface ProcessState { error: Error | null; output: string[]; exited: boolean }
interface OwnedProcess { id: number; name: string; pid?: number; port?: number; state: ProcessState }
interface GuardianMessage { type: string; id?: number; pid?: number; data?: string; error?: string; code?: number | null }

const FIXTURE_MEDIA_FILE_COUNT = 7;
const FIXTURE_FFMPEG_TIMEOUT_MS = 30_000;
const FIXTURE_WATCHDOG_GRACE_MS = 15_000;
const FIXTURE_GENERATION_TIMEOUT_MS = FIXTURE_MEDIA_FILE_COUNT * FIXTURE_FFMPEG_TIMEOUT_MS + FIXTURE_WATCHDOG_GRACE_MS;
const STARTUP_TIMEOUT_MS = 30_000;
const CLEANUP_TIMEOUT_MS = 15_000;
let nextProcessId = 1;

export default async function globalSetup() {
  const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const repositoryRoot = resolve(webRoot, "../..");
  const resultsRoot = resolve(webRoot, "test-results");
  await mkdir(resultsRoot, { recursive: true });
  const fixtureRoot = await mkdtemp(resolve(resultsRoot, "fixture-media-"));
  const dataRoot = await mkdtemp(resolve(resultsRoot, "api-data-"));
  const descriptorPath = resolve(resultsRoot, "fixture.json");
  const lifecyclePath = resolve(resultsRoot, `lifecycle-${process.pid}.json`);
  const temporaryPaths = [fixtureRoot, dataRoot, descriptorPath, lifecyclePath];
  let guardian: ChildProcess | undefined;
  let cleanupPromise: Promise<void> | undefined;
  const cleanup = () => {
    cleanupPromise ??= guardian === undefined
      ? Promise.all(temporaryPaths.map((path) => rm(path, { recursive: true, force: true }))).then(() => undefined)
      : requestGuardianCleanup(guardian);
    return cleanupPromise;
  };

  try {
    guardian = spawn(
      process.execPath,
      [resolve(webRoot, "e2e/process-guardian.mjs"), String(process.pid), lifecyclePath, JSON.stringify(temporaryPaths)],
      { detached: true, shell: false, stdio: ["ignore", "pipe", "pipe", "ipc"] },
    );
    const guardianOutput: string[] = [];
    guardian.stdout?.on("data", (chunk) => guardianOutput.push(String(chunk)));
    guardian.stderr?.on("data", (chunk) => guardianOutput.push(String(chunk)));
    await waitForGuardianMessage(guardian, (message) => message.type === "ready", STARTUP_TIMEOUT_MS, guardianOutput);

    const stdout = runCaptured(
      "uv", ["run", "python", "tests/fixture_media.py", "--output", fixtureRoot],
      resolve(repositoryRoot, "apps/api"), FIXTURE_GENERATION_TIMEOUT_MS, "fixture-media generation",
    );
    const media = JSON.parse(stdout) as FixtureManifest;
    const python = runCaptured(
      "uv", ["run", "python", "-c", "import sys; print(sys.executable)"],
      resolve(repositoryRoot, "apps/api"), 10_000, "Python interpreter discovery",
    ).trim();

    const api = await startOwnedProcess(
      guardian, "API", python,
      ["-m", "uvicorn", "holden_reel.main:create_app", "--factory", "--app-dir", "src", "--host", "127.0.0.1", "--port", "0"],
      resolve(repositoryRoot, "apps/api"),
      { HOLDEN_REEL_DATA_DIR: dataRoot, PYTHONPATH: resolve(repositoryRoot, "apps/api/src") },
    );
    const apiBinding = await waitForBinding(api, /Uvicorn running on (http:\/\/127\.0\.0\.1:(\d+))\b/, STARTUP_TIMEOUT_MS);
    const apiUrl = apiBinding[1];
    const apiPort = Number(apiBinding[2]);
    api.port = apiPort;
    guardian.send?.({ type: "port", id: api.id, port: apiPort });
    await waitForApiIdentity(api, `${apiUrl}/api/health`, STARTUP_TIMEOUT_MS);

    const web = await startOwnedProcess(
      guardian, "web", process.execPath,
      [resolve(webRoot, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "0", "--strictPort"],
      webRoot, { HOLDEN_REEL_API_URL: apiUrl },
    );
    const webBinding = await waitForBinding(web, /Local:\s+(http:\/\/127\.0\.0\.1:(\d+)\/?)/, STARTUP_TIMEOUT_MS);
    const webUrl = webBinding[1].replace(/\/$/, "");
    const webPort = Number(webBinding[2]);
    web.port = webPort;
    guardian.send?.({ type: "port", id: web.id, port: webPort });
    await waitForWebIdentity(web, `${webUrl}/`, STARTUP_TIMEOUT_MS);

    await writeFile(descriptorPath, `${JSON.stringify({ folderPath: dirname(media["song.wav"]), apiPort, apiUrl, webPort, webUrl }, null, 2)}\n`, "utf8");
    console.log(`E2E loopback URLs: API ${apiUrl}; web ${webUrl}`);
  } catch (error) {
    try { await cleanup(); }
    catch (cleanupError) { throw new AggregateError([error, cleanupError], "E2E setup and cleanup both failed"); }
    throw error;
  }
  return cleanup;
}

async function startOwnedProcess(
  guardian: ChildProcess, name: string, command: string, arguments_: string[], cwd: string,
  environment: Record<string, string> = {},
): Promise<OwnedProcess> {
  const owned: OwnedProcess = { id: nextProcessId++, name, state: { error: null, output: [], exited: false } };
  guardian.on("message", (raw) => {
    const message = raw as GuardianMessage;
    if (message.id !== owned.id) return;
    if (message.type === "output" && message.data !== undefined) owned.state.output.push(message.data);
    if (message.type === "child-error") owned.state.error = new Error(message.error);
    if (message.type === "child-exit") owned.state.exited = true;
  });
  const response = waitForGuardianMessage(
    guardian,
    (message) => message.id === owned.id && ["spawned", "spawn-error"].includes(message.type),
    STARTUP_TIMEOUT_MS,
  );
  guardian.send({ type: "spawn", id: owned.id, name, command, arguments: arguments_, cwd, environment });
  const message = await response;
  if (message.type === "spawn-error" || message.pid === undefined) throw new Error(message.error ?? `${name} spawn failed`);
  owned.pid = message.pid;
  return owned;
}

function waitForGuardianMessage(
  guardian: ChildProcess, predicate: (message: GuardianMessage) => boolean,
  timeoutMs: number, output: string[] = [],
): Promise<GuardianMessage> {
  return new Promise((resolveMessage, rejectMessage) => {
    const finish = (error?: Error, message?: GuardianMessage) => {
      clearTimeout(timeout);
      guardian.removeListener("message", handleMessage);
      guardian.removeListener("error", handleError);
      guardian.removeListener("exit", handleExit);
      if (error) rejectMessage(error); else resolveMessage(message!);
    };
    const handleMessage = (raw: unknown) => { const message = raw as GuardianMessage; if (predicate(message)) finish(undefined, message); };
    const handleError = (error: Error) => finish(error);
    const handleExit = (code: number | null) => finish(new Error(`lifecycle guardian exited ${code}${output.length ? `\n${output.join("")}` : ""}`));
    const timeout = setTimeout(() => finish(new Error(`timed out ${timeoutMs}ms waiting for lifecycle guardian`)), timeoutMs);
    guardian.on("message", handleMessage);
    guardian.once("error", handleError);
    guardian.once("exit", handleExit);
  });
}

async function requestGuardianCleanup(guardian: ChildProcess) {
  if (!guardian.connected) throw new Error("lifecycle guardian disconnected before cleanup confirmation");
  const id = nextProcessId++;
  const response = waitForGuardianMessage(
    guardian, (message) => message.id === id && ["cleaned", "cleanup-error"].includes(message.type), CLEANUP_TIMEOUT_MS,
  );
  guardian.send({ type: "cleanup", id });
  const message = await response;
  if (message.type === "cleanup-error") throw new Error(message.error);
}

function runCaptured(command: string, arguments_: string[], cwd: string, timeoutMs: number, description: string): string {
  try { return execFileSync(command, arguments_, { cwd, encoding: "utf8", timeout: timeoutMs, stdio: ["ignore", "pipe", "pipe"] }); }
  catch (error) {
    const details = error as Error & { stdout?: string | Buffer; stderr?: string | Buffer };
    const output = [details.stdout, details.stderr].filter((value) => value !== undefined).map(String).join("").trim();
    throw new Error(`${description} failed within its ${timeoutMs}ms watchdog: ${details.message}${output ? `\n${output}` : ""}`, { cause: error });
  }
}

async function waitForBinding(owned: OwnedProcess, pattern: RegExp, timeoutMs: number): Promise<RegExpMatchArray> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    assertRunning(owned);
    const match = owned.state.output.join("").match(pattern);
    if (match !== null) return match;
    await delay(50);
  }
  throw new Error(`Timed out waiting ${timeoutMs}ms for ${owned.name} binding${processOutput(owned)}`);
}
async function waitForApiIdentity(owned: OwnedProcess, url: string, timeoutMs: number) {
  const payload = (await (await waitForResponse(owned, url, timeoutMs)).json()) as Record<string, unknown>;
  if (Object.keys(payload).sort().join(",") !== "status,version" || payload.status !== "ok" || payload.version !== "0.1.0") {
    throw new Error(`Owned API returned unexpected health identity: ${JSON.stringify(payload)}`);
  }
}
async function waitForWebIdentity(owned: OwnedProcess, url: string, timeoutMs: number) {
  if (!(await (await waitForResponse(owned, url, timeoutMs)).text()).includes("<title>Holden Reel</title>")) {
    throw new Error(`Owned web server did not return Holden Reel index at ${url}`);
  }
}
async function waitForResponse(owned: OwnedProcess, url: string, timeoutMs: number): Promise<Response> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    assertRunning(owned);
    try { const response = await fetch(url, { signal: AbortSignal.timeout(1_000) }); if (response.status === 200) return response; await response.body?.cancel(); }
    catch { /* Child reported its binding before becoming request-ready. */ }
    await delay(100);
  }
  throw new Error(`Timed out waiting ${timeoutMs}ms for ${url}${processOutput(owned)}`);
}
function assertRunning(owned: OwnedProcess) {
  if (owned.state.error) throw new Error(`${owned.name} failed: ${owned.state.error.message}${processOutput(owned)}`);
  if (owned.state.exited) throw new Error(`${owned.name} exited${processOutput(owned)}`);
}
function processOutput(owned: OwnedProcess) { const text = owned.state.output.join("").trim(); return text ? `\n${text}` : ""; }
function delay(milliseconds: number): Promise<void> { return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds)); }
