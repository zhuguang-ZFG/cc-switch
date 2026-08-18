import assert from "node:assert/strict";
import { join } from "node:path";
import test from "node:test";

import globalExtension, {
  EXTENSION_REVISION,
  SOTA_ALIAS_PREFIX,
  changedFilesSince,
  classifyEscalationSignal,
  classifyHutujiGate,
  createSotaEscalationCoordinator,
  discoverSotaCandidates,
  formatHutujiGatePlan,
  formatSotaStatus,
  safeExecArgs,
} from "./omp-sota-escalation.js";

const MODELS = [
  { provider: "zg-newapi", id: "omp-sota-claude-opus-5" },
  { provider: "zg-newapi", id: "deepseek-v4-flash" },
  { provider: "agentrouter", id: "claude-opus-5" },
];

test("discovers only marked SOTA aliases and deduplicates selectors", () => {
  assert.deepEqual(
    discoverSotaCandidates([
      ...MODELS,
      { provider: "zg-newapi", id: "omp-sota-claude-opus-5" },
      { provider: "sotamodel-canary", id: "claude-opus-5" },
    ]),
    ["zg-newapi/omp-sota-claude-opus-5"],
  );
  assert.equal(SOTA_ALIAS_PREFIX, "omp-sota-");
});

test("classifies explicit, rescue, high-risk, complexity, and normal signals", () => {
  assert.equal(
    classifyEscalationSignal({ prompt: "/sota-review this" }).kind,
    "explicit",
  );
  assert.equal(
    classifyEscalationSignal({ prompt: "fix auth migration" }).kind,
    "high-risk",
  );
  assert.equal(
    classifyEscalationSignal({ prompt: "检查生产数据库迁移" }).kind,
    "high-risk",
  );
  assert.equal(
    classifyEscalationSignal({ prompt: "small task", toolFailures: 2 }).kind,
    "rescue",
  );
  assert.equal(
    classifyEscalationSignal({ prompt: "x".repeat(2000) }).kind,
    "complexity",
  );
  assert.equal(
    classifyEscalationSignal({ prompt: "rename a local variable" }).kind,
    "none",
  );
  assert.equal(
    classifyEscalationSignal({ prompt: "remove an unused import" }).kind,
    "none",
  );
});

test("selects hutuji docs, hub, and fail-closed full gate profiles", () => {
  const cwd = "D:\\Users\\hutuji";
  assert.deepEqual(classifyHutujiGate({ cwd, changedFiles: [] }), {
    project: true,
    profile: "none",
    available: true,
    reasons: [],
    commands: [],
  });
  const docs = classifyHutujiGate({
    cwd,
    changedFiles: [
      "README.md",
      "docs/protocol.md",
      ".cursor/rules/hutuji-agent-rails.mdc",
    ],
  });
  assert.equal(docs.profile, "docs");
  assert.equal(docs.available, true);
  assert.match(formatHutujiGatePlan(docs), /profile=docs/);
  assert.doesNotMatch(formatHutujiGatePlan(docs), /&&/);
  assert.equal(
    classifyHutujiGate({
      cwd,
      changedFiles: ["docs/protocol.md", "mcp-server/hutuji_mcp/server.py"],
    }).profile,
    "hub",
  );
  const blockedFull = classifyHutujiGate({
    cwd,
    changedFiles: ["../Grbl_Esp32/src/hutuji_pipe.cc"],
  });
  assert.equal(blockedFull.profile, "full");
  assert.equal(blockedFull.available, false);
  assert.equal(blockedFull.commands.length, 2);
  assert.equal(
    classifyHutujiGate({
      cwd,
      changedFiles: ["D:/Users/Grbl_Esp32/src/hutuji_pipe.cc"],
      grblRootAvailable: true,
    }).available,
    true,
  );
  assert.equal(
    classifyHutujiGate({
      cwd: "D:\\Users\\cc-switch",
      changedFiles: ["docs/protocol.md"],
    }).project,
    false,
  );
});

test("hutuji risk paths trigger SOTA while ordinary docs do not", () => {
  const cwd = "D:/Users/hutuji";
  const risky = classifyEscalationSignal({
    prompt: "继续",
    cwd,
    changedFiles: ["scripts/agent_gate.py"],
  });
  assert.equal(risky.kind, "high-risk");
  assert.equal(risky.source, "hutuji-path");
  assert.equal(
    classifyEscalationSignal({
      prompt: "继续",
      cwd,
      changedFiles: ["docs/troubleshooting.md"],
    }).kind,
    "none",
  );
  assert.equal(
    classifyEscalationSignal({
      prompt: "继续",
      cwd: "D:/Users/other",
      changedFiles: ["scripts/agent_gate.py"],
    }).kind,
    "none",
  );
});

test("detects only files changed after the turn baseline", () => {
  const before = {
    files: ["docs/protocol.md", "README.md"],
    hashes: { "docs/protocol.md": "aaa", "README.md": "bbb" },
    complete: true,
  };
  const after = {
    files: ["docs/protocol.md", "README.md", "scripts/agent_gate.py"],
    hashes: {
      "docs/protocol.md": "ccc",
      "README.md": "bbb",
      "scripts/agent_gate.py": "ddd",
    },
    complete: true,
  };
  assert.deepEqual(changedFilesSince(before, after), [
    "docs/protocol.md",
    "scripts/agent_gate.py",
  ]);
  assert.deepEqual(changedFilesSince({ complete: false }, after), after.files);
  assert.deepEqual(
    changedFilesSince(
      { files: [], hashes: {}, complete: true },
      {
        files: ["docs/protocol.md"],
        hashes: { "docs/protocol.md": "missing" },
        complete: true,
      },
    ),
    ["docs/protocol.md"],
  );
  assert.deepEqual(
    changedFilesSince(
      {
        files: ["docs/protocol.md"],
        hashes: { "docs/protocol.md": "missing" },
        complete: true,
      },
      {
        files: ["docs/protocol.md"],
        hashes: { "docs/protocol.md": "missing" },
        complete: true,
      },
    ),
    [],
  );
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
  let current = 100;
  const coordinator = createSotaEscalationCoordinator({ now: () => current });
  coordinator.beginTurn("review this", MODELS, true);
  const first = coordinator.start(MODELS);
  const second = coordinator.start(MODELS);
  assert.equal(first.started, true);
  assert.equal(first.target, "zg-newapi/omp-sota-claude-opus-5");
  assert.equal(second.started, false);
  assert.equal(second.reason, "running");
  current = 175;
  const completed = coordinator.complete({ ok: true });
  assert.equal(completed.retryState, "success");
  assert.equal(completed.durationMs, 75);
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
  const coordinator = createSotaEscalationCoordinator({
    now: () => current,
    cooldownMs: 500,
  });
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
  assert.equal(
    coordinator
      .getStatus()
      .candidates.every((candidate) => candidate.state === "cooldown"),
    true,
  );
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

test("coordinator promotes a normal turn after hutuji path observation", () => {
  const coordinator = createSotaEscalationCoordinator({ now: () => 100 });
  coordinator.beginTurn("继续", MODELS);
  assert.equal(coordinator.getSignal().kind, "none");
  const signal = coordinator.observeProjectContext({
    cwd: "D:/Users/hutuji",
    changedFiles: ["deploy/preflight.sh"],
  });
  assert.equal(signal.kind, "high-risk");
  assert.equal(signal.source, "hutuji-path");
});

test("status is bounded and contains no raw prompt or secret", () => {
  const coordinator = createSotaEscalationCoordinator({ now: () => 100 });
  coordinator.beginTurn(
    "token sk-secret-123 https://private.example/x",
    MODELS,
  );
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
      userPrompt:
        "Authorization: abc api_key=secret-value https://private.example/x",
      gatePlan: "hutuji-gate profile=hub",
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
  assert.equal(rendered.includes("hutuji-gate profile=hub"), true);
});

test("registers lifecycle handlers and status commands", () => {
  const registrations = [];
  const commands = [];
  const pi = {
    logger: { info() {} },
    on(name) {
      registrations.push(name);
    },
    registerCommand(name) {
      commands.push(name);
    },
  };
  globalExtension(pi);
  assert.deepEqual(registrations, [
    "session_start",
    "before_agent_start",
    "tool_result",
    "agent_end",
  ]);
  assert.deepEqual(commands, [
    "sota",
    "sota-review",
    "sota-plan",
    "sota-escalate",
    "sota-status",
    "hutuji-gate-status",
  ]);
});

test("runs one automatic read-only child review at agent end", async () => {
  const writerSymbol = Symbol.for("omp.modelRoutingTelemetry.writer");
  const previousWriter = globalThis[writerSymbol];
  globalThis[writerSymbol] = () => {
    throw new Error("telemetry unavailable");
  };
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  const pi = {
    pi: { getAgentDir: () => "agent-dir" },
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
      if (command === "git")
        return { code: 0, stdout: "src/risky.ts\n", stderr: "", killed: false };
      return {
        code: 0,
        stdout: "severity: low; evidence: src/risky.ts",
        stderr: "",
        killed: false,
      };
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
  await handlers.get("before_agent_start")(
    { prompt: "review production migration" },
    ctx,
  );
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  assert.equal(timers.length, 1);
  await timers[0]();
  assert.equal(execCalls.at(-1)[0], "omp");
  assert.equal(execCalls.at(-1)[1].includes("--no-extensions"), true);
  assert.equal(execCalls.at(-1)[1].includes("--tools"), true);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].customType, "sota-escalation-review");
  if (previousWriter === undefined) delete globalThis[writerSymbol];
  else globalThis[writerSymbol] = previousWriter;
});

test("hutuji high-risk path emits a gate plan and automatic SOTA review", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  let diffCalls = 0;
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
      if (command === "git") {
        if (args[0] === "diff") {
          const stdout = diffCalls++ === 0 ? "" : "scripts/agent_gate.py\n";
          return { code: 0, stdout, stderr: "", killed: false };
        }
        if (args[0] === "hash-object") {
          return {
            code: 0,
            stdout: "hash-agent-gate\n",
            stderr: "",
            killed: false,
          };
        }
        return { code: 0, stdout: "", stderr: "", killed: false };
      }
      return { code: 0, stdout: "severity: low", stderr: "", killed: false };
    },
  };
  globalExtension(pi);
  const ctx = {
    cwd: join("D:", "Users", "hutuji"),
    models: { list: () => MODELS },
    setTimeout(callback) {
      timers.push(callback);
    },
  };
  await handlers.get("before_agent_start")({ prompt: "继续" }, ctx);
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  assert.equal(timers.length, 1);
  await timers[0]();
  assert.equal(execCalls.at(-1)[0], "omp");
  assert.deepEqual(
    messages.map((message) => message.customType),
    ["hutuji-gate-plan", "sota-escalation-review"],
  );
  assert.match(messages[0].content, /profile=hub/);
  assert.match(execCalls.at(-1)[1].join(" "), /hutuji-path/);
});

test("ordinary hutuji documentation emits only a docs gate plan", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  let diffCalls = 0;
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
      execCalls.push(command);
      if (args[0] === "diff") {
        const stdout = diffCalls++ === 0 ? "" : "docs/troubleshooting.md\n";
        return { code: 0, stdout, stderr: "", killed: false };
      }
      if (args[0] === "hash-object") {
        return {
          code: 0,
          stdout: "hash-troubleshooting\n",
          stderr: "",
          killed: false,
        };
      }
      return { code: 0, stdout: "", stderr: "", killed: false };
    },
  };
  globalExtension(pi);
  const ctx = {
    cwd: join("D:", "Users", "hutuji"),
    models: { list: () => MODELS },
    setTimeout(callback) {
      timers.push(callback);
    },
  };
  await handlers.get("before_agent_start")({ prompt: "继续" }, ctx);
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  await timers[0]();
  assert.deepEqual(
    messages.map((message) => message.customType),
    ["hutuji-gate-plan"],
  );
  assert.match(messages[0].content, /profile=docs/);
  assert.equal(execCalls.includes("omp"), false);
});

test("successful external firmware writes trigger a fail-closed full plan", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const pi = {
    logger: { info() {}, error() {} },
    on(name, handler) {
      handlers.set(name, handler);
    },
    registerCommand() {},
    sendMessage(message) {
      messages.push(message);
    },
    async exec(command) {
      if (command === "git") {
        return { code: 0, stdout: "", stderr: "", killed: false };
      }
      return { code: 0, stdout: "severity: high", stderr: "", killed: false };
    },
  };
  globalExtension(pi);
  const ctx = {
    cwd: "D:/Users/hutuji",
    models: { list: () => MODELS },
    setTimeout(callback) {
      timers.push(callback);
    },
  };
  await handlers.get("before_agent_start")({ prompt: "继续" }, ctx);
  handlers.get("tool_result")({
    toolName: "write",
    input: { path: "D:/Users/Grbl_Esp32/src/hutuji_pipe.cc" },
    isError: false,
  });
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  await timers[0]();
  assert.deepEqual(
    messages.map((message) => message.customType),
    ["hutuji-gate-plan", "sota-escalation-review"],
  );
  assert.match(messages[0].content, /profile=full/);
  assert.match(messages[0].content, /external-grbl-change/);
});
