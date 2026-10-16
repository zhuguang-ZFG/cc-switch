#!/usr/bin/env node
// Regression test for the SOTA escalation baseline probe timeout (2026-10-16).
//
// Symptom: "Extension handler timed out after 30000ms" for
// extensions/omp-sota-escalation.js on event before_agent_start, every turn in
// some long-lived sessions (20 occurrences across 5 pids in one day).
//
// Contract under test: the before_agent_start handler must never spend the
// host's 30s handler budget on an open-ended pi.exec("git", ...) chain.
// collectBaselineSnapshot() bounds the probe (SNAPSHOT_BUDGET_MS) and degrades
// to complete:false, which changedFilesSince() already handles.
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

const mod = await import(pathToFileURL(join(extSandbox, "omp-sota-escalation.js")).href);

function makePi(execImpl) {
  const handlers = {};
  const warns = [];
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
    exec: execImpl,
    pi: { getAgentDir: () => join(sandbox, "agentdir") },
  };
  mod.default(pi);
  return { handlers, warns };
}

const ctx = { cwd: sandbox, models: { list: () => [] } };

const fastGit = async (cmd, args) => {
  if (cmd !== "git") return { code: 1, stdout: "" };
  if (args[0] === "diff") return { code: 0, stdout: "turn.txt\n" };
  if (args[0] === "ls-files") return { code: 0, stdout: "" };
  if (args[0] === "hash-object") return { code: 0, stdout: "abc123\n" };
  return { code: 1, stdout: "" };
};

// A: fast path stays fast and silent
{
  const { handlers, warns } = makePi(fastGit);
  const t0 = Date.now();
  await handlers.before_agent_start({ prompt: "hello" }, ctx);
  const dt = Date.now() - t0;
  check("fast path resolves under 2s", dt < 2000, `${dt}ms`);
  check("no warnings on fast path", warns.length === 0, JSON.stringify(warns));
}

// B: a pi.exec call that never resolves must not exhaust the handler budget
{
  const { handlers, warns } = makePi(() => new Promise(() => {}));
  const t0 = Date.now();
  await handlers.before_agent_start({ prompt: "hello" }, ctx);
  const dt = Date.now() - t0;
  check("hanging exec is bounded (8s budget, not 30s)", dt >= 7500 && dt < 10500, `${dt}ms`);
  check(
    "incomplete-baseline warning emitted",
    warns.some((w) => w.m === "sota: baseline snapshot incomplete"),
    JSON.stringify(warns),
  );
}

// C: slow-but-completing call is reported, handler still under budget
{
  const slowGit = async (cmd, args) => {
    if (cmd === "git" && args[0] === "diff") await new Promise((r) => setTimeout(r, 2700));
    return fastGit(cmd, args);
  };
  const { handlers, warns } = makePi(slowGit);
  const t0 = Date.now();
  await handlers.before_agent_start({ prompt: "hello" }, ctx);
  const dt = Date.now() - t0;
  check("slow path completes under budget", dt < 8000, `${dt}ms`);
  check(
    "slow-probe warning emitted",
    warns.some((w) => w.m === "sota: git probe slow"),
    JSON.stringify(warns),
  );
  check(
    "no incomplete warning when the probe completes",
    !warns.some((w) => w.m === "sota: baseline snapshot incomplete"),
    JSON.stringify(warns),
  );
}

// D: degraded baseline semantics unchanged
{
  const degraded = mod.changedFilesSince(
    { files: ["x"], hashes: {}, complete: false },
    { files: ["a", "b"], hashes: { a: "1" }, complete: true },
  );
  check("incomplete baseline degrades to end snapshot", JSON.stringify(degraded) === '["a","b"]', JSON.stringify(degraded));
  const normal = mod.changedFilesSince(
    { files: ["a"], hashes: { a: "1" }, complete: true },
    { files: ["a", "b"], hashes: { a: "2", b: "3" }, complete: true },
  );
  check("complete baselines still diff by hash", JSON.stringify(normal) === '["a","b"]', JSON.stringify(normal));
}

try {
  rmSync(sandbox, { recursive: true, force: true });
} catch {}

console.log(`\n${passes} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
