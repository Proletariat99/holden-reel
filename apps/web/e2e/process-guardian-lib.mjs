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
    const directory = await open(dirname(target), "r");
    try { await directory.sync(); } finally { await directory.close(); }
  } finally {
    await file?.close();
    await rm(temporary, { force: true });
  }
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
    delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds)),
    timeoutMs = 3_000,
    runTaskkill = (args) => runTaskkillCommand(args, timeoutMs),
  } = operations ?? {};

  if (platform === "win32") await runTaskkill(["/PID", String(pid), "/T", "/F"]);
  else signalGroup(pid, "SIGKILL");

  const deadline = Date.now() + timeoutMs;
  while (isTreeAlive(pid) && Date.now() < deadline) await delay(25);
  if (isTreeAlive(pid)) throw new Error(`process tree ${pid} did not terminate within ${timeoutMs}ms`);
}

export function defaultIsTreeAlive(pid, platform = process.platform) {
  try {
    process.kill(platform === "win32" ? pid : -pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    throw error;
  }
}

export function signalGroup(pid, signal) {
  try { process.kill(process.platform === "win32" ? pid : -pid, signal); }
  catch (error) { if (error.code !== "ESRCH") throw error; }
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
