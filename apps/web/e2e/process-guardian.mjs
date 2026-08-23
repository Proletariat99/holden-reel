import { readFile, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const parentPid = Number(process.argv[2]);
const lifecyclePath = process.argv[3];

while (isAlive(parentPid)) {
  await delay(100);
}

let lifecycle;
try {
  lifecycle = JSON.parse(await readFile(lifecyclePath, "utf8"));
} catch {
  process.exit(0);
}

for (const group of [...lifecycle.groups].reverse()) signal(group, "SIGTERM");
await delay(1_000);
for (const group of lifecycle.groups.filter(isGroupAlive)) {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(group), "/T", "/F"], { timeout: 3_000 });
  } else signal(group, "SIGKILL");
}
const deadline = Date.now() + 3_000;
while (lifecycle.groups.some(isGroupAlive) && Date.now() < deadline) await delay(50);
if (lifecycle.groups.some(isGroupAlive)) process.exit(1);
for (const path of lifecycle.paths) {
  await rm(path, { recursive: true, force: true });
}

function isAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code !== "ESRCH"; }
}

function isGroupAlive(pid) {
  if (process.platform === "win32") return isAlive(pid);
  try { process.kill(-pid, 0); return true; }
  catch (error) { return error.code !== "ESRCH"; }
}

function signal(pid, name) {
  try { process.kill(process.platform === "win32" ? pid : -pid, name); }
  catch (error) { if (error.code !== "ESRCH") throw error; }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
