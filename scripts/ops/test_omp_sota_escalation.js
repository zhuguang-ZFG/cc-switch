import assert from "node:assert/strict";
import test from "node:test";

import globalExtension, {
  EXTENSION_REVISION,
  SOTA_ALIAS_PREFIX,
  classifyEscalationSignal,
  createSotaEscalationCoordinator,
  discoverSotaCandidates,
  formatSotaStatus,
  safeExecArgs,
} from "./omp-sota-escalation.js";

const MODELS = [
  { provider: "zg-newapi", id: "omp-sota-claude-opus-5" },
  { provider: "zg-newapi", id: "deepseek-v4-flash" },
  { provider: "agentrouter", id: "claude-opus-5" },
];

test("discovers only marked SOTA aliases and deduplicates selectors", () => {
  assert.deepEqual(discoverSotaCandidates([
    ...MODELS,
    { provider: "zg-newapi", id: "omp-sota-claude-opus-5" },
    { provider: "sotamodel-canary", id: "claude-opus-5" },
  ]), ["zg-newapi/omp-sota-claude-opus-5"]);
  assert.equal(SOTA_ALIAS_PREFIX, "omp-sota-");
});

test("classifies explicit, rescue, high-risk, complexity, and normal signals", () => {
  assert.equal(classifyEscalationSignal({ prompt: "/sota-review this" }).kind, "explicit");
  assert.equal(classifyEscalationSignal({ prompt: "fix auth migration" }).kind, "high-risk");
  assert.equal(classifyEscalationSignal({ prompt: "检查生产数据库迁移" }).kind, "high-risk");
  assert.equal(classifyEscalationSignal({ prompt: "small task", toolFailures: 2 }).kind, "rescue");
  assert.equal(classifyEscalationSignal({ prompt: "x".repeat(2000) }).kind, "complexity");
  assert.equal(classifyEscalationSignal({ prompt: "rename a local variable" }).kind, "none");
  assert.equal(classifyEscalationSignal({ prompt: "remove an unused import" }).kind, "none");
});

test("does nothing for a normal turn and preserves main model identity", () => {
  const main = { provider: "agentrouter", id: "claude-opus-5" };
  const coordinator = createSotaEscalationCoordinator({ now: () => 100 });
  coordinator.beginTurn("read one file", MODELS);
  const started = coordinator.start(MODELS);
  assert.equal(started.started, false);
  assert.deepEqual(main, { provider: "agentrouter", id: "claude-opus-5" });
  assert.equal(coordinator.getStatus().extensionRuns, 0);
});

test("runs one explicit escalation and blocks concurrent duplicate runs", () => {
  const coordinator = createSotaEscalationCoordinator({ now: () => 100 });
  coordinator.beginTurn("review this", MODELS, true);
  const first = coordinator.start(MODELS);
  const second = coordinator.start(MODELS);
  assert.equal(first.started, true);
  assert.equal(first.target, "zg-newapi/omp-sota-claude-opus-5");
  assert.equal(second.started, false);
  assert.equal(second.reason, "running");
  assert.equal(coordinator.complete({ ok: true }).retryState, "success");
  assert.equal(coordinator.getStatus().successes, 1);
  assert.equal(coordinator.start(MODELS).reason, "budget-exhausted");
});

test("cools a failed target, then selects the next marked candidate", () => {
  let current = 100;
  const coordinator = createSotaEscalationCoordinator({
    now: () => current,
    cooldownMs: 500,
  });
  const models = [
    { provider: "zg-newapi", id: "omp-sota-primary" },
    { provider: "zg-newapi", id: "omp-sota-backup" },
  ];
  coordinator.beginTurn("/sota", models);
  assert.equal(coordinator.start(models).target, "zg-newapi/omp-sota-primary");
  const failed = coordinator.complete({ ok: false, retryable: true });
  assert.equal(failed.cooldownRemainingMs, 500);
  coordinator.beginTurn("/sota", models);
  const backup = coordinator.start(models);
  assert.equal(backup.target, "zg-newapi/omp-sota-backup");
  current = 700;
  coordinator.complete({ ok: false, retryable: true });
  coordinator.beginTurn("/sota", models);
  assert.equal(coordinator.start(models).started, true);
  assert.equal(coordinator.getStatus().target, "zg-newapi/omp-sota-primary");
});

test("fails closed when every marked target is cooling", () => {
  let current = 100;
  const coordinator = createSotaEscalationCoordinator({ now: () => current, cooldownMs: 500 });
  const models = [
    { provider: "zg-newapi", id: "omp-sota-primary" },
    { provider: "zg-newapi", id: "omp-sota-backup" },
  ];
  coordinator.beginTurn("/sota", models);
  coordinator.start(models);
  coordinator.complete({ ok: false, retryable: true });
  coordinator.beginTurn("/sota", models);
  coordinator.start(models);
  coordinator.complete({ ok: false, retryable: true });
  coordinator.beginTurn("/sota", models);
  const blocked = coordinator.start(models);
  assert.equal(blocked.started, false);
  assert.equal(blocked.reason, "cooldown");
  assert.equal(coordinator.getStatus().candidates.every((candidate) => candidate.state === "cooldown"), true);
  current = 601;
  coordinator.beginTurn("/sota", models);
  assert.equal(coordinator.start(models).started, true);
});

test("tool failures promote rescue but cancellation/local failures do not cool", () => {
  const coordinator = createSotaEscalationCoordinator({ now: () => 100 });
  coordinator.beginTurn("continue the task", MODELS);
  coordinator.observeToolResult({ isError: true });
  coordinator.observeToolResult({ isError: true });
  assert.equal(coordinator.getSignal().kind, "rescue");
  coordinator.start(MODELS);
  coordinator.complete({ ok: false, retryable: false });
  assert.equal(coordinator.getStatus().cooldownRemainingMs, 0);
});

test("status is bounded and contains no raw prompt or secret", () => {
  const coordinator = createSotaEscalationCoordinator({ now: () => 100 });
  coordinator.beginTurn("token sk-secret-123 https://private.example/x", MODELS);
  const status = coordinator.getStatus();
  const rendered = `${formatSotaStatus(status)} ${JSON.stringify(status)}`;
  assert.match(rendered, new RegExp(`rev=${EXTENSION_REVISION}`));
  assert.equal(rendered.includes("sk-secret"), false);
  assert.equal(rendered.includes("private.example"), false);
  assert.equal(rendered.length < 2000, true);
});

test("child invocation is ephemeral, bounded, read-only, and redacted", () => {
  const args = safeExecArgs(
    "zg-newapi/omp-sota-claude-opus-5",
    {
      reason: "high-risk",
      userPrompt: "Authorization: abc api_key=secret-value https://private.example/x",
    },
    ["src/good.ts", "bad\nname.ts"],
  );
  assert.deepEqual(args.slice(-2), ["--tools", "read,grep,glob,lsp"]);
  assert.equal(args.includes("--no-session"), true);
  assert.equal(args.includes("--no-extensions"), true);
  assert.equal(args.includes("--no-skills"), true);
  assert.equal(args.includes("--max-time"), true);
  const rendered = args.join(" ");
  assert.equal(rendered.includes("private.example"), false);
  assert.equal(rendered.includes("secret-value"), false);
  assert.equal(rendered.includes("bad\nname.ts"), false);
});

test("registers lifecycle handlers and status commands", () => {
  const registrations = [];
  const commands = [];
  const pi = {
    logger: { info() {} },
    on(name) { registrations.push(name); },
    registerCommand(name) { commands.push(name); },
  };
  globalExtension(pi);
  assert.deepEqual(registrations, ["session_start", "before_agent_start", "tool_result", "agent_end"]);
  assert.deepEqual(commands, ["sota", "sota-review", "sota-plan", "sota-escalate", "sota-status"]);
});

test("runs one automatic read-only child review at agent end", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  const pi = {
    logger: { info() {}, error() {} },
    on(name, handler) {
      handlers.set(name, handler);
    },
    registerCommand() {},
    sendMessage(message) {
      messages.push(message);
    },
    async exec(command, args) {
      execCalls.push([command, args]);
      if (command === "git") return { code: 0, stdout: "src/risky.ts\n", stderr: "", killed: false };
      return { code: 0, stdout: "severity: low; evidence: src/risky.ts", stderr: "", killed: false };
    },
  };
  globalExtension(pi);
  const ctx = {
    cwd: process.cwd(),
    models: { list: () => MODELS },
    setTimeout(callback) {
      timers.push(callback);
    },
  };
  await handlers.get("before_agent_start")({ prompt: "review production migration" }, ctx);
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  assert.equal(timers.length, 1);
  await timers[0]();
  assert.equal(execCalls.at(-1)[0], "omp");
  assert.equal(execCalls.at(-1)[1].includes("--no-extensions"), true);
  assert.equal(execCalls.at(-1)[1].includes("--tools"), true);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].customType, "sota-escalation-review");
});
