import { open, rename, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";

export async function atomicWriteJson(target, value, hooks = {}) {
  const temporary = resolve(dirname(target), `.${randomUUID()}.tmp`);
  let file;
  try {
    file = await open(temporary, "wx", 0o600);
    await file.writeFile(JSON.stringify(value), "utf8");
    await file.sync();
    await file.close();
    file = undefined;
    await hooks.beforeRename?.();
    await rename(temporary, target);
    await syncParentDirectory(dirname(target), hooks);
  } finally {
    await file?.close();
    await rm(temporary, { force: true });
  }
}

export async function syncParentDirectory(path, operations = {}) {
  const platform = operations.platform ?? process.platform;
  if (platform === "win32") return;
  const openDirectory = operations.openDirectory ?? ((directoryPath) => open(directoryPath, "r"));
  const directory = await openDirectory(path);
  try { await directory.sync(); } finally { await directory.close(); }
}

export function createLifecycleGate() {
  let state = "accepting";
  const spawnOperations = new Set();
  return {
    runSpawn(operation) {
      if (state !== "accepting") return Promise.reject(new Error("lifecycle cleanup has started; spawn rejected"));
      const running = Promise.resolve().then(operation);
      spawnOperations.add(running);
      void running.finally(() => spawnOperations.delete(running));
      return running;
    },
    async beginCleanup() {
      if (state === "closed") return;
      state = "cleaning";
      await Promise.allSettled([...spawnOperations]);
      state = "closed";
    },
  };
}

export async function registerOwnedGroup(ownedGroups, pid, persist) {
  ownedGroups.push(pid);
  await persist([...ownedGroups]);
}

export async function settleRegistrations(registrations, timeoutMs) {
  let timeout;
  try {
    await Promise.race([
      Promise.allSettled([...registrations]),
      new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error(`lifecycle registration did not settle within ${timeoutMs}ms`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

export async function terminateOwnedTree(pid, operations) {
  const {
    platform = process.platform,
    isTreeAlive = (candidate) => defaultIsTreeAlive(candidate, platform),
    isPidAlive = (candidate) => defaultIsTreeAlive(candidate, "win32"),
    listWindowsTreePids = () => getWindowsTreePids(pid, timeoutMs),
    delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds)),
    timeoutMs = 3_000,
    runTaskkill = (args) => runTaskkillCommand(args, timeoutMs),
  } = operations ?? {};

  let windowsTree = [];
  if (platform === "win32") {
    windowsTree = await listWindowsTreePids(pid);
    if (!windowsTree.includes(pid)) windowsTree.unshift(pid);
    await runTaskkill(["/PID", String(pid), "/T", "/F"]);
  } else signalGroup(pid, "SIGKILL");

  const deadline = Date.now() + timeoutMs;
  const treeAlive = () => platform === "win32"
    ? windowsTree.some((candidate) => isPidAlive(candidate))
    : isTreeAlive(pid);
  while (treeAlive() && Date.now() < deadline) await delay(25);
  if (treeAlive()) throw new Error(`process tree ${pid} did not terminate within ${timeoutMs}ms`);
}

export function defaultIsTreeAlive(pid, platform = process.platform, killOperation = process.kill) {
  try {
    killOperation(platform === "win32" ? pid : -pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code === "EPERM") return true;
    throw error;
  }
}

export function signalGroup(pid, signal) {
  try { process.kill(process.platform === "win32" ? pid : -pid, signal); return "sent"; }
  catch (error) {
    if (error.code === "ESRCH") return "absent";
    if (error.code === "EPERM") return "denied";
    throw error;
  }
}

function runTaskkillCommand(args, timeoutMs) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("taskkill", args, { shell: false, stdio: ["ignore", "pipe", "pipe"] });
    const output = [];
    child.stdout.on("data", (chunk) => output.push(String(chunk)));
    child.stderr.on("data", (chunk) => output.push(String(chunk)));
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      rejectRun(new Error(`taskkill timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.once("error", (error) => { clearTimeout(timeout); rejectRun(error); });
    child.once("exit", (code) => {
      clearTimeout(timeout);
      if (code === 0) resolveRun();
      else rejectRun(new Error(`taskkill exited ${code}: ${output.join("").trim()}`));
    });
  });
}

async function getWindowsTreePids(rootPid, timeoutMs) {
  const script = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress";
  const rows = JSON.parse(await runCapturedCommand("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], timeoutMs));
  const processes = Array.isArray(rows) ? rows : [rows];
  const result = [rootPid];
  for (let index = 0; index < result.length; index += 1) {
    for (const processRow of processes) {
      if (Number(processRow.ParentProcessId) === result[index] && !result.includes(Number(processRow.ProcessId))) {
        result.push(Number(processRow.ProcessId));
      }
    }
  }
  return result;
}

function runCapturedCommand(command, args, timeoutMs) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, { shell: false, stdio: ["ignore", "pipe", "pipe"] });
    const output = [];
    child.stdout.on("data", (chunk) => output.push(String(chunk)));
    child.stderr.on("data", (chunk) => output.push(String(chunk)));
    const timeout = setTimeout(() => { child.kill("SIGKILL"); rejectRun(new Error(`${command} timed out after ${timeoutMs}ms`)); }, timeoutMs);
    child.once("error", (error) => { clearTimeout(timeout); rejectRun(error); });
    child.once("exit", (code) => {
      clearTimeout(timeout);
      if (code === 0) resolveRun(output.join(""));
      else rejectRun(new Error(`${command} exited ${code}: ${output.join("").trim()}`));
    });
  });
}
