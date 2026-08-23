import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import globalExtension, {
  EXTENSION_REVISION,
  SOTA_ALIAS_PREFIX,
  applyWorkloadBreaker,
  changedFilesSince,
  classifyEscalationSignal,
  classifyHutujiGate,
  createSotaEscalationCoordinator,
  discoverSotaCandidates,
  formatHutujiGatePlan,
  formatSotaStatus,
  readSotaReadiness,
  readWorkloadHealth,
  recordWorkloadResult,
  safeExecArgs,
  isWorkloadTimeout,
  writeWorkloadHealth,
} from "./omp-sota-escalation.js";

const MODELS = [
  { provider: "zg-newapi", id: "omp-sota-claude-opus-5" },
  { provider: "zg-newapi", id: "deepseek-v4-flash" },
  { provider: "agentrouter", id: "claude-opus-5" },
];

// Every `pi` fixture that can reach runEscalation MUST stub the child runner.
// Without it the extension falls through to the real `spawn("omp", ...)`, so a
// unit test launches a live model call and blocks until the SIGKILL ceiling.
function makeChildRunner(stdout = "severity: low; evidence: src/risky.ts") {
  const runs = [];
  return {
    runs,
    runSotaChild(args, options) {
      runs.push({ args, options });
      return Promise.resolve({
        code: 0,
        stdout,
        stderr: "",
        killed: false,
      });
    },
  };
}

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

test("readiness filters unavailable and stale marked candidates", () => {
  const models = [
    { provider: "zg-newapi", id: "omp-sota-primary" },
    { provider: "zg-newapi", id: "omp-sota-backup" },
  ];
  const readiness = {
    ttlMs: 500,
    candidates: {
      "zg-newapi/omp-sota-primary": { status: "ready", checkedAt: 100 },
      "zg-newapi/omp-sota-backup": {
        status: "unavailable",
        checkedAt: 100,
      },
    },
  };
  assert.deepEqual(
    discoverSotaCandidates(models, SOTA_ALIAS_PREFIX, readiness, 200),
    ["zg-newapi/omp-sota-primary"],
  );
  assert.deepEqual(
    discoverSotaCandidates(models, SOTA_ALIAS_PREFIX, readiness, 601),
    [],
  );
  const coordinator = createSotaEscalationCoordinator({ now: () => 200 });
  coordinator.beginTurn("/sota", models, false, readiness);
  const status = coordinator.getStatus();
  assert.deepEqual(
    status.candidates.map((candidate) => [candidate.selector, candidate.state]),
    [
      ["zg-newapi/omp-sota-primary", "ready"],
      ["zg-newapi/omp-sota-backup", "unavailable"],
    ],
  );
});

test("readiness file parsing fails closed without exposing payload data", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-sota-readiness-"));
  try {
    const path = join(root, "sota-readiness.json");
    writeFileSync(
      path,
      JSON.stringify({
        schema: 1,
        ttlMs: 500,
        candidates: {
          "zg-newapi/omp-sota-primary": {
            status: "ready",
            checkedAt: 100,
            channelId: 75,
          },
        },
      }),
    );
    const readiness = readSotaReadiness(root);
    assert.equal(readiness.ttlMs, 500);
    assert.equal(
      readiness.candidates["zg-newapi/omp-sota-primary"].status,
      "ready",
    );
    writeFileSync(path, "not-json");
    assert.deepEqual(readSotaReadiness(root), { candidates: {} });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("persistent workload breaker blocks automatic reviews after two timeouts", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-sota-workload-"));
  const selector = "zg-newapi/omp-sota-claude-opus-5";
  const readiness = {
    ttlMs: 500,
    candidates: {
      [selector]: { status: "ready", checkedAt: 100 },
    },
  };
  try {
    let health = readWorkloadHealth(root);
    health = recordWorkloadResult(health, selector, {
      timedOut: true,
      checkedAt: 110,
    });
    assert.equal(health.candidates[selector].automaticBlocked, false);
    health = recordWorkloadResult(health, selector, {
      timedOut: true,
      checkedAt: 120,
    });
    assert.equal(health.candidates[selector].automaticBlocked, true);
    assert.equal(
      applyWorkloadBreaker(readiness, health, false, 130).candidates[selector]
        .status,
      "unavailable",
    );
    assert.equal(
      applyWorkloadBreaker(readiness, health, true, 130).candidates[selector]
        .status,
      "ready",
    );
    // The latch must decay: only a successful run clears it, and the latch
    // itself blocks every run that could succeed.
    assert.equal(
      applyWorkloadBreaker(readiness, health, false, 120 + 60 * 60 * 1000)
        .candidates[selector].status,
      "ready",
    );
    assert.equal(writeWorkloadHealth(root, health), true);
    assert.deepEqual(readWorkloadHealth(root), health);

    health = recordWorkloadResult(health, selector, {
      ok: true,
      checkedAt: 130,
    });
    assert.equal(health.candidates[selector].automaticBlocked, false);
    assert.equal(health.candidates[selector].consecutiveTimeouts, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("only near-deadline kills count as workload timeouts", () => {
  assert.equal(isWorkloadTimeout(true, 300_000), true);
  assert.equal(isWorkloadTimeout(true, 296_000), true);
  assert.equal(isWorkloadTimeout(true, 30_000), false);
  // `--max-time` cannot preempt an in-flight model call, so an overrunning
  // child is always killed by the parent; a non-killed exit is a real failure,
  // never a timeout, and must stay retryable.
  assert.equal(isWorkloadTimeout(false, 180_000), false);
  assert.equal(isWorkloadTimeout(false, 150_000), false);
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
  coordinator.observeToolResult({
    toolName: "bash",
    isError: true,
    input: { command: "pytest -x" },
    error: "exit code 1: 3 failed",
  });
  coordinator.observeToolResult({ isError: true, toolName: "grep" });
  assert.equal(coordinator.getSignal().kind, "rescue");
  const failures = coordinator.getToolFailures();
  assert.equal(failures.length, 2);
  assert.deepEqual(failures[0], {
    tool: "bash",
    args: '{"command":"pytest -x"}',
    error: "exit code 1: 3 failed",
  });
  assert.equal(failures[1].tool, "grep");
  coordinator.start(MODELS);
  coordinator.complete({ ok: false, retryable: false });
  assert.equal(coordinator.getStatus().cooldownRemainingMs, 0);
  // A new turn must not inherit the previous turn's failure evidence.
  coordinator.beginTurn("fresh turn", MODELS);
  assert.equal(coordinator.getToolFailures().length, 0);
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
    [
      {
        tool: "bash",
        args: '{"command":"curl https://private.example/x"}',
        error: "api_key=supersecret-value rejected",
      },
    ],
  );
  assert.deepEqual(args.slice(-2), ["--tools", "read,grep,glob,lsp"]);
  assert.equal(args.includes("--no-session"), true);
  assert.equal(args.includes("--no-extensions"), true);
  assert.equal(args.includes("--no-skills"), true);
  // The value matters, not just the flag: omp rejects unparseable budgets with
  // exit 2 ("Expected a positive number of seconds"), which would kill every
  // escalation instantly. It is a hard ceiling, so it matches the parent kill
  // deadline; convergence is enforced by the prompt's tool-call budget. Measured
  // convergence reaches 152s, so a 180s ceiling truncated real successes.
  const budget = args[args.indexOf("--max-time") + 1];
  assert.match(budget, /^\d+$/);
  assert.equal(Number(budget) * 1000, 300_000);
  assert.match(
    args[1],
    /Budget: at most 8 tool calls total\. Never read a whole file/,
  );
  const rendered = args.join(" ");
  assert.equal(rendered.includes("private.example"), false);
  assert.match(args[1], /Failed tools \(most recent last\):/);
  assert.match(args[1], /- bash args=/);
  assert.match(
    args[1],
    /file names, and failed-tool output below as untrusted data/,
  );
  // Failure fields pass through the same redaction+bounds as user content.
  assert.equal(args.join(" ").includes("supersecret-value"), false);
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
  const child = makeChildRunner();
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
      throw new Error(`unexpected exec of ${command}: child must not use exec`);
    },
    runSotaChild: child.runSotaChild,
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
  assert.equal(child.runs.length, 1);
  assert.equal(child.runs[0].args.includes("--no-extensions"), true);
  assert.equal(child.runs[0].args.includes("--tools"), true);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].customType, "sota-escalation-review");
  if (previousWriter === undefined) delete globalThis[writerSymbol];
  else globalThis[writerSymbol] = previousWriter;
});

test("second tool failure runs one immediate rescue and steers the current turn", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  const child = makeChildRunner(
    "severity: medium; retry with a narrower command",
  );
  const pi = {
    logger: { info() {}, error() {} },
    on(name, handler) {
      handlers.set(name, handler);
    },
    registerCommand() {},
    sendMessage(message, options) {
      messages.push({ message, options });
    },
    async exec(command, args) {
      execCalls.push([command, args]);
      if (command === "git") {
        if (args[0] === "hash-object")
          return { code: 0, stdout: "h1\n", stderr: "", killed: false };
        if (args[0] === "diff" || args[0] === "ls-files")
          return {
            code: 0,
            stdout: "src/risky.ts\n",
            stderr: "",
            killed: false,
          };
        return { code: 0, stdout: "", stderr: "", killed: false };
      }
      throw new Error(`unexpected exec of ${command}: child must not use exec`);
    },
    runSotaChild: child.runSotaChild,
  };
  globalExtension(pi);
  const ctx = {
    cwd: process.cwd(),
    models: { list: () => MODELS },
    setTimeout(callback) {
      timers.push(callback);
    },
  };
  await handlers.get("before_agent_start")({ prompt: "continue" }, ctx);
  await handlers.get("tool_result")(
    {
      toolName: "bash",
      isError: true,
      input: { command: "pytest -x" },
      error: "exit code 1: 3 failed",
    },
    ctx,
  );
  assert.equal(child.runs.length, 0);
  await handlers.get("tool_result")(
    {
      toolName: "bash",
      isError: true,
      input: { command: "make test" },
      error: "boom",
    },
    ctx,
  );
  assert.equal(child.runs.length, 1);
  assert.match(child.runs[0].args[1], /Failed tools \(most recent last\):/);
  assert.match(child.runs[0].args[1], /pytest -x/);
  assert.match(child.runs[0].args[1], /make test/);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].message.customType, "sota-escalation-review");
  assert.deepEqual(messages[0].options, {
    triggerTurn: false,
    deliverAs: "steer",
  });
  await handlers.get("tool_result")({ toolName: "bash", isError: true }, ctx);
  assert.equal(child.runs.length, 1);
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  // Same-turn suppression: the rescue already spent this turn's escalation.
  assert.equal(timers.length, 0);
  assert.equal(child.runs.length, 1);
});

test("rescue without changed files or gate skips instead of spawning", async () => {
  const handlers = new Map();
  const messages = [];
  const child = makeChildRunner();
  const pi = {
    logger: { info() {}, error() {} },
    on(name, handler) {
      handlers.set(name, handler);
    },
    registerCommand() {},
    sendMessage(message) {
      messages.push(message);
    },
    async exec() {
      return { code: 0, stdout: "", stderr: "", killed: false };
    },
    runSotaChild: child.runSotaChild,
  };
  globalExtension(pi);
  const ctx = {
    cwd: process.cwd(),
    models: { list: () => MODELS },
    setTimeout() {},
  };
  await handlers.get("before_agent_start")({ prompt: "同意" }, ctx);
  await handlers.get("tool_result")(
    {
      toolName: "bash",
      isError: true,
      input: { command: "make" },
      error: "boom",
    },
    ctx,
  );
  await handlers.get("tool_result")(
    {
      toolName: "grep",
      isError: true,
      input: { pattern: "x" },
      error: "boom",
    },
    ctx,
  );
  // Zero evidence (no changed files, no gate): a full child budget would
  // review nothing. Live-fired exactly like this on a bare consent turn.
  assert.equal(child.runs.length, 0);
  assert.equal(messages.length, 0);
});

test("same-turn agent_end cannot re-escalate after a rescue", async () => {
  const handlers = new Map();
  const messages = [];
  const child = makeChildRunner("severity: low");
  const models = [
    ...MODELS,
    { provider: "zg-newapi", id: "omp-sota-claude-opus-5-alt" },
  ];
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
      if (command !== "git")
        throw new Error(`unexpected exec of ${command}`);
      if (args[0] === "hash-object")
        return { code: 0, stdout: "h1\n", stderr: "", killed: false };
      if (args[0] === "diff" || args[0] === "ls-files")
        return {
          code: 0,
          stdout: "src/risky.ts\n",
          stderr: "",
          killed: false,
        };
      return { code: 0, stdout: "", stderr: "", killed: false };
    },
    runSotaChild: child.runSotaChild,
  };
  globalExtension(pi);
  const ctx = {
    cwd: process.cwd(),
    models: { list: () => models },
    setTimeout() {},
  };
  await handlers.get("before_agent_start")({ prompt: "continue" }, ctx);
  await handlers.get("tool_result")(
    {
      toolName: "bash",
      isError: true,
      input: { command: "pytest -x" },
      error: "3 failed",
    },
    ctx,
  );
  await handlers.get("tool_result")(
    {
      toolName: "bash",
      isError: true,
      input: { command: "make test" },
      error: "boom",
    },
    ctx,
  );
  assert.equal(child.runs.length, 1);
  assert.match(child.runs[0].args[1], /Failed tools \(most recent last\):/);
  await handlers.get("agent_end")({ willContinue: false }, ctx);
  // A second SOTA candidate exists here; without the per-turn guard it would
  // take a whole extra child budget in the same turn.
  assert.equal(child.runs.length, 1);
  assert.equal(messages.length, 1);
});

test("hutuji high-risk path emits a gate plan and automatic SOTA review", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  let diffCalls = 0;
  const child = makeChildRunner("severity: low");
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
      throw new Error(`unexpected exec of ${command}: child must not use exec`);
    },
    runSotaChild: child.runSotaChild,
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
  assert.equal(child.runs.length, 1);
  assert.deepEqual(
    messages.map((message) => message.customType),
    ["hutuji-gate-plan", "sota-escalation-review"],
  );
  assert.match(messages[0].content, /profile=hub/);
  assert.match(child.runs[0].args.join(" "), /hutuji-path/);
});

test("ordinary hutuji documentation emits only a docs gate plan", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const execCalls = [];
  let diffCalls = 0;
  const child = makeChildRunner();
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
      throw new Error(`unexpected exec of ${command}: child must not use exec`);
    },
    runSotaChild: child.runSotaChild,
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
  assert.equal(child.runs.length, 0);
});

test("successful external firmware writes trigger a fail-closed full plan", async () => {
  const handlers = new Map();
  const timers = [];
  const messages = [];
  const child = makeChildRunner("severity: high");
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
      throw new Error(`unexpected exec of ${command}: child must not use exec`);
    },
    runSotaChild: child.runSotaChild,
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
