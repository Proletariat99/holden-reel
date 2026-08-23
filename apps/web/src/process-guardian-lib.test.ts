import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The production guardian executes this native ESM module directly in Node.
// @ts-ignore The runtime module intentionally has no generated declaration.
import * as lifecycle from "../e2e/process-guardian-lib.mjs";
const { atomicWriteJson, createLifecycleGate, defaultIsTreeAlive, registerOwnedGroup, settleRegistrations, syncParentDirectory, terminateOwnedTree } = lifecycle;

const roots: string[] = [];
afterEach(async () => Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true }))));

describe("lifecycle registration", () => {
  it("keeps the prior descriptor valid when interrupted before atomic rename", async () => {
    const root = await mkdtemp(resolve(tmpdir(), "holden-reel-lifecycle-"));
    roots.push(root);
    const target = resolve(root, "lifecycle.json");
    await atomicWriteJson(target, { groups: [11] });

    await expect(atomicWriteJson(target, { groups: [11, 22] }, {
      beforeRename: () => { throw new Error("simulated owner interruption"); },
    })).rejects.toThrow("simulated owner interruption");

    expect(JSON.parse(await readFile(target, "utf8"))).toEqual({ groups: [11] });
  });

  it("owns a spawned group in memory before durable persistence completes", async () => {
    const owned: number[] = [];
    let release!: () => void;
    const persistence = new Promise<void>((resolvePersistence) => { release = resolvePersistence; });
    const registration = registerOwnedGroup(owned, 22, () => persistence);

    expect(owned).toEqual([22]);
    release();
    await registration;
  });

  it("waits for an interrupted descriptor update before cleanup can remove paths", async () => {
    let release!: () => void;
    let removed = false;
    const registration = new Promise<void>((resolvePersistence) => { release = resolvePersistence; });
    const cleanup = settleRegistrations(new Set([registration]), 100).then(() => { removed = true; });
    await Promise.resolve();
    expect(removed).toBe(false);
    release();
    await cleanup;
    expect(removed).toBe(true);
  });
});

describe("platform durability", () => {
  it("does not open a Windows directory for unsupported fsync", async () => {
    let opened = false;
    await syncParentDirectory("C:\\tmp", { platform: "win32", openDirectory: async () => { opened = true; } });
    expect(opened).toBe(false);
  });

  it("fsyncs the parent directory on POSIX", async () => {
    const calls: string[] = [];
    await syncParentDirectory("/tmp", { platform: "darwin", openDirectory: async () => ({
      sync: async () => { calls.push("sync"); }, close: async () => { calls.push("close"); },
    }) });
    expect(calls).toEqual(["sync", "close"]);
  });
});

describe("cleanup serialization", () => {
  it("rejects a spawn arriving while cleanup waits for an admitted spawn", async () => {
    const gate = createLifecycleGate();
    let release!: () => void;
    const admitted = gate.runSpawn(() => new Promise<void>((resolveSpawn) => { release = resolveSpawn; }));
    const cleanup = gate.beginCleanup();
    await expect(gate.runSpawn(async () => {})).rejects.toThrow("lifecycle cleanup has started");
    let cleaned = false;
    void cleanup.then(() => { cleaned = true; });
    await Promise.resolve();
    expect(cleaned).toBe(false);
    release();
    await admitted;
    await cleanup;
    expect(cleaned).toBe(true);
  });
});

describe("Windows whole-tree teardown", () => {
  it("uses taskkill /T /F and waits until the entire tree is absent", async () => {
    const calls: string[][] = [];
    const checked: number[] = [];
    await terminateOwnedTree(77, {
      platform: "win32",
      runTaskkill: async (args: string[]) => { calls.push(args); },
      listWindowsTreePids: async () => [77, 88, 99],
      isPidAlive: (pid: number) => { checked.push(pid); return false; },
      delay: async () => {},
      timeoutMs: 100,
    });
    expect(calls).toEqual([["/PID", "77", "/T", "/F"]]);
    expect([...new Set(checked)]).toEqual([77, 88, 99]);
  });

  it("fails cleanup rather than treating a dead leader as a dead Windows tree", async () => {
    await expect(terminateOwnedTree(77, {
      platform: "win32",
      runTaskkill: async () => {},
      listWindowsTreePids: async () => [77, 88],
      isPidAlive: (pid: number) => pid === 88,
      delay: async () => {},
      timeoutMs: 1,
    })).rejects.toThrow("process tree 77 did not terminate");
  });
});

describe("POSIX liveness errors", () => {
  it("treats ESRCH as absent and EPERM as alive", () => {
    expect(defaultIsTreeAlive(1, "linux", () => { throw Object.assign(new Error(), { code: "ESRCH" }); })).toBe(false);
    expect(defaultIsTreeAlive(1, "linux", () => { throw Object.assign(new Error(), { code: "EPERM" }); })).toBe(true);
  });
});
