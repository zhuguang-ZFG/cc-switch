#!/usr/bin/env node
// Regression test for the SOTA escalation snapshot timeouts (2026-10-16).
//
// Symptom: "Extension handler timed out after 30000ms" for
// extensions/omp-sota-escalation.js on event before_agent_start, every turn in
// some long-lived sessions (20 occurrences across 5 pids in one day).
//
// Contract under test:
//   1. before_agent_start must not await the git probe at all (it only starts
//      it), so an unresolved pi.exec can never exhaust the host's 30s budget;
//   2. the probe is consumed inside the agent_end managed timer under
//      SNAPSHOT_BUDGET_MS, degrading to complete:false on overrun;
//   3. the rescue path and the explicit command never await the multi-minute
//      child inside a handler;
//   4. slow git calls are reported, and changedFilesSince() degradation
//      semantics are unchanged.
//
// Run: node scripts/ops/test_omp_sota_snapshot.mjs

import { existsSync, mkdirSync, mkdtempSync, copyFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const EXT_DIR = process.env.OMP_EXTENSIONS_DIR ?? "C:/Users/zhugu/.omp/agent/extensions";
const SOURCE = join(EXT_DIR, "omp-sota-escalation.js");
const SIBLING = join(EXT_DIR, "omp-model-routing-observability.js");

if (!existsSync(SOURCE) || !existsSync(SIBLING)) {
  console.error(`missing extension sources under ${EXT_DIR}`);
  process.exit(2);
}

const sandbox = mkdtempSync(join(tmpdir(), "omp-sota-snapshot-"));
const extSandbox = join(sandbox, "ext");
mkdirSync(extSandbox);
copyFileSync(SOURCE, join(extSandbox, "omp-sota-escalation.js"));
copyFileSync(SIBLING, join(extSandbox, "omp-model-routing-observability.js"));
writeFileSync(join(extSandbox, "package.json"), '{"type":"module"}');

let failures = 0;
let passes = 0;
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${detail ? "  -> " + detail : ""}`);
  if (ok) passes += 1;
  else failures += 1;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const mod = await import(pathToFileURL(join(extSandbox, "omp-sota-escalation.js")).href);

function makeHarness(execImpl) {
  const handlers = {};
  const warns = [];
  const deferred = [];
  const pi = {
    on: (name, fn) => {
      handlers[name] = fn;
    },
    logger: {
      warn: (m, d) => warns.push({ m, d }),
      info: () => {},
      error: () => {},
      debug: () => {},
    },
    registerCommand: () => {},
    sendMessage: () => {},
    exec: execImpl,
    pi: { getAgentDir: () => join(sandbox, "agentdir") },
  };
  mod.default(pi);
  const ctx = {
    cwd: sandbox,
    models: { list: () => [] },
    ui: { notify: () => {} },
    setTimeout: (fn) => {
      deferred.push(fn);
      return deferred.length;
    },
  };
  return { handlers, warns, deferred, ctx };
}

const fastGit = async (cmd, args) => {
  if (cmd !== "git") return { code: 1, stdout: "" };
  if (args[0] === "diff") return { code: 0, stdout: "turn.txt\n" };
  if (args[0] === "ls-files") return { code: 0, stdout: "" };
  if (args[0] === "hash-object") return { code: 0, stdout: "abc123\n" };
  return { code: 1, stdout: "" };
};

// A: fast path — hook returns immediately, deferred consumption is clean
{
  const h = makeHarness(fastGit);
  const t0 = Date.now();
  await h.handlers.before_agent_start({ prompt: "hello" }, h.ctx);
  const dt = Date.now() - t0;
  check("A1 before_agent_start returns immediately", dt < 500, `${dt}ms`);
  h.handlers.agent_end({ willContinue: false }, h.ctx);
  check("A2 agent_end scheduled deferred work", h.deferred.length === 1, String(h.deferred.length));
  const t1 = Date.now();
  await h.deferred[0]();
  const dt2 = Date.now() - t1;
  check("A3 deferred consumption is fast on the fast path", dt2 < 1500, `${dt2}ms`);
  check("A4 no warnings on the fast path", h.warns.length === 0, JSON.stringify(h.warns));
}

// B: hanging pi.exec — hook still returns immediately; consumption degrades
{
  const h = makeHarness(() => new Promise(() => {}));
  const t0 = Date.now();
  await h.handlers.before_agent_start({ prompt: "hello" }, h.ctx);
  const dt = Date.now() - t0;
  check("B1 hook does not wait on a hanging probe", dt < 500, `${dt}ms`);
  h.handlers.agent_end({ willContinue: false }, h.ctx);
  const t1 = Date.now();
  await h.deferred[0]();
  const dt2 = Date.now() - t1;
  check("B2 deferred consumption bounded by SNAPSHOT_BUDGET_MS", dt2 >= 7500 && dt2 < 10500, `${dt2}ms`);
  check(
    "B3 incomplete warning names the baseline",
    h.warns.some((w) => w.m === "sota: snapshot incomplete" && w.d?.label === "baseline"),
    JSON.stringify(h.warns),
  );
}

// C: slow-but-completing git call is reported; hook still does not wait
{
  const slowGit = async (cmd, args) => {
    if (cmd === "git" && args[0] === "diff") await sleep(2700);
    return fastGit(cmd, args);
  };
  const h = makeHarness(slowGit);
  const t0 = Date.now();
  await h.handlers.before_agent_start({ prompt: "hello" }, h.ctx);
  const dt = Date.now() - t0;
  check("C1 hook does not wait on a slow probe", dt < 500, `${dt}ms`);
  await sleep(3200);
  check(
    "C2 slow-probe warning emitted",
    h.warns.some((w) => w.m === "sota: git probe slow"),
    JSON.stringify(h.warns),
  );
  h.handlers.agent_end({ willContinue: false }, h.ctx);
  await h.deferred[0]();
  check(
    "C3 no incomplete warning when probes complete",
    !h.warns.some((w) => w.m === "sota: snapshot incomplete"),
    JSON.stringify(h.warns),
  );
}

// D: rescue path never awaits the multi-minute child
{
  const h = makeHarness(() => new Promise(() => {}));
  await h.handlers.before_agent_start({ prompt: "hello" }, h.ctx);
  const ev = { isError: true, toolName: "bash", input: { command: "false" }, error: "boom" };
  await h.handlers.tool_result(ev, h.ctx);
  const t0 = Date.now();
  await h.handlers.tool_result(ev, h.ctx);
  const dt = Date.now() - t0;
  check("D1 rescue handler returns immediately", dt < 500, `${dt}ms`);
  check("D2 rescue work was deferred", h.deferred.length === 1, String(h.deferred.length));
}

// E: degraded baseline semantics unchanged
{
  const degraded = mod.changedFilesSince(
    { files: ["x"], hashes: {}, complete: false },
    { files: ["a", "b"], hashes: { a: "1" }, complete: true },
  );
  check("E1 incomplete baseline degrades to end snapshot", JSON.stringify(degraded) === '["a","b"]', JSON.stringify(degraded));
  const normal = mod.changedFilesSince(
    { files: ["a"], hashes: { a: "1" }, complete: true },
    { files: ["a", "b"], hashes: { a: "2", b: "3" }, complete: true },
  );
  check("E2 complete baselines still diff by hash", JSON.stringify(normal) === '["a","b"]', JSON.stringify(normal));
}

try {
  rmSync(sandbox, { recursive: true, force: true });
} catch {}

console.log(`\n${passes} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
