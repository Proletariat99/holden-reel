import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The production guardian executes this native ESM module directly in Node.
// @ts-ignore The runtime module intentionally has no generated declaration.
import * as lifecycle from "../e2e/process-guardian-lib.mjs";
const { atomicWriteJson, registerOwnedGroup, settleRegistrations, terminateOwnedTree } = lifecycle;

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

describe("Windows whole-tree teardown", () => {
  it("uses taskkill /T /F and waits until the entire tree is absent", async () => {
    const calls: string[][] = [];
    const liveness = [true, false];
    await terminateOwnedTree(77, {
      platform: "win32",
      runTaskkill: async (args: string[]) => { calls.push(args); },
      isTreeAlive: () => liveness.shift() ?? false,
      delay: async () => {},
      timeoutMs: 100,
    });
    expect(calls).toEqual([["/PID", "77", "/T", "/F"]]);
  });

  it("fails cleanup rather than treating a dead leader as a dead Windows tree", async () => {
    await expect(terminateOwnedTree(77, {
      platform: "win32",
      runTaskkill: async () => {},
      isTreeAlive: () => true,
      delay: async () => {},
      timeoutMs: 1,
    })).rejects.toThrow("process tree 77 did not terminate");
  });
});
