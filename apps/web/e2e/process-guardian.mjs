import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import { atomicWriteJson, defaultIsTreeAlive, registerOwnedGroup, settleRegistrations, signalGroup, terminateOwnedTree } from "./process-guardian-lib.mjs";

const parentPid = Number(process.argv[2]);
const lifecyclePath = process.argv[3];
const paths = JSON.parse(process.argv[4]);
const children = new Map();
const groups = [];
const registrations = new Set();
let cleanupPromise;

await atomicWriteJson(lifecyclePath, { groups, paths });
send({ type: "ready" });

process.on("message", (message) => {
  if (message?.type === "spawn") void spawnOwned(message);
  if (message?.type === "port") {
    const owned = children.get(message.id);
    if (owned) owned.port = message.port;
  }
  if (message?.type === "cleanup") {
    void cleanup().then(
      () => { send({ type: "cleaned", id: message.id }); process.exit(0); },
      (error) => send({ type: "cleanup-error", id: message.id, error: String(error) }),
    );
  }
});
process.once("disconnect", () => void cleanup().finally(() => process.exit()));
const ownerMonitor = setInterval(() => {
  if (!isPidAlive(parentPid)) void cleanup().finally(() => process.exit());
}, 100);
ownerMonitor.unref();

async function spawnOwned(message) {
  const environment = { ...process.env, ...message.environment };
  delete environment.FORCE_COLOR;
  delete environment.NO_COLOR;
  const child = spawn(message.command, message.arguments, {
    cwd: message.cwd, env: environment, detached: process.platform !== "win32",
    shell: false, stdio: ["ignore", "pipe", "pipe"],
  });
  const owned = { child, id: message.id, name: message.name, port: undefined };
  children.set(message.id, owned);
  child.stdout.on("data", (chunk) => send({ type: "output", id: message.id, data: String(chunk) }));
  child.stderr.on("data", (chunk) => send({ type: "output", id: message.id, data: String(chunk) }));
  child.once("error", (error) => send({ type: "child-error", id: message.id, error: error.message }));
  child.once("exit", (code, signal) => send({ type: "child-exit", id: message.id, code, signal }));
  if (child.pid === undefined) {
    send({ type: "spawn-error", id: message.id, error: `${message.name} did not receive a PID` });
    return;
  }
  try {
    const registration = registerOwnedGroup(groups, child.pid, (registeredGroups) =>
      atomicWriteJson(lifecyclePath, { groups: registeredGroups, paths }),
    );
    registrations.add(registration);
    try { await registration; }
    finally { registrations.delete(registration); }
    send({ type: "spawned", id: message.id, pid: child.pid });
  } catch (error) {
    await cleanup();
    send({ type: "spawn-error", id: message.id, error: String(error) });
  }
}

function cleanup() {
  cleanupPromise ??= cleanupOwnedResources();
  return cleanupPromise;
}
async function cleanupOwnedResources() {
  await settleRegistrations(registrations, 5_000);
  const errors = [];
  for (const owned of [...children.values()].reverse()) {
    try { await stopOwned(owned); } catch (error) { errors.push(error); }
  }
  if (errors.length > 0) {
    throw new AggregateError(errors, `owned process trees did not terminate: ${errors.map(String).join("; ")}`);
  }
  await Promise.all(paths.map((path) => rm(path, { recursive: true, force: true })));
}
async function stopOwned(owned) {
  const pid = owned.child.pid;
  if (pid === undefined) return;
  if (process.platform === "win32") {
    await terminateOwnedTree(pid, { platform: "win32", timeoutMs: 3_000 });
    return;
  }
  signalGroup(pid, "SIGTERM");
  const gracefulDeadline = Date.now() + 10_000;
  while (defaultIsTreeAlive(pid) && Date.now() < gracefulDeadline) await delay(50);
  if (defaultIsTreeAlive(pid)) await terminateOwnedTree(pid, { timeoutMs: 3_000 });
}
function send(message) { if (process.connected) process.send(message); }
function isPidAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code !== "ESRCH"; }
}
function delay(milliseconds) { return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds)); }
