import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import {
  COORDINATION_CONTRACT,
  acquireCanaryLease,
  buildCanaryArgs,
  canonicalRoleSnapshot,
  createAgentWatchdog,
  discoverCanarySelectors,
  extractTaskDispatchEvents,
  extractTaskRouteEvents,
  formatCanaryStatus,
  formatRoutingStatus,
  formatWatchdogStatus,
  guardHubInput,
  hashRoleSnapshot,
  injectCoordinationContract,
  isCanaryDue,
  readCanaryState,
  refreshAndValidateRoles,
  readRouteEvents,
  runModelToolCanary,
  validateModelRoles,
  writeCanaryState,
  writeRouteEvent,
} from "./omp-model-routing-observability.js";
import modelRoutingObservability from "./omp-model-routing-observability.js";

test("role snapshots are canonical and exact selectors are validated", () => {
  const left = { default: "zg-newapi/gpt-5.6-luna:max", task: "zg-newapi/gpt-5.6-luna:max" };
  const right = { task: "zg-newapi/gpt-5.6-luna:max", default: "zg-newapi/gpt-5.6-luna:max" };
  assert.equal(canonicalRoleSnapshot(left), canonicalRoleSnapshot(right));
  assert.equal(hashRoleSnapshot(left), hashRoleSnapshot(right));
  const validation = validateModelRoles(
    { task: "zg-newapi/gpt-5.6-luna:max", slow: "@task", broken: "missing/model" },
    [{ provider: "zg-newapi", id: "gpt-5.6-luna" }],
  );
  assert.deepEqual(
    validation.map(item => [item.role, item.state]),
    [["broken", "unresolved"], ["slow", "indeterminate"], ["task", "valid"]],
  );
});

test("offline refresh is observable and does not require public settings APIs", async () => {
  const ctx = {
    modelRegistry: {
      async refresh(strategy) {
        assert.equal(strategy, "offline");
      },
      getAvailable: () => [{ provider: "p", id: "m" }],
    },
  };
  const state = await refreshAndValidateRoles(ctx, { getModelRoles: () => ({ task: "p/m" }) });
  assert.equal(state.refreshState, "ok");
  assert.equal(state.unresolvedCount, 0);
  const fallback = await refreshAndValidateRoles(
    { modelRegistry: { refresh: async () => { throw new Error("network"); }, getAvailable: () => [] } },
    undefined,
  );
  assert.equal(fallback.refreshState, "failed");
  assert.equal(fallback.refreshError, "offline-refresh-failed");
});

test("canary selectors follow managed roles and future SOTA registrations", () => {
  const models = [
    { provider: "p", id: "main" },
    { provider: "p", id: "worker" },
    { provider: "p", id: "small" },
    { provider: "p", id: "omp-sota-reviewer" },
  ];
  assert.deepEqual(
    discoverCanarySelectors(
      { default: "p/main:xhigh", task: "p/worker:max", smol: "p/small", slow: "missing/model" },
      models,
    ),
    ["p/main:xhigh", "p/worker:max", "p/small", "p/omp-sota-reviewer"],
  );
  assert.equal(isCanaryDue(undefined, 1000), true);
  assert.equal(isCanaryDue({ result: "success", checkedAt: 1000 }, 1001), false);
  assert.equal(isCanaryDue({ result: "failed", checkedAt: 0 }, 30 * 60 * 1000), true);
});

test("coordination contract is idempotent and hub waits stay bounded", () => {
  const flat = injectCoordinationContract({ agent: "scout", task: "inspect" });
  assert.match(flat.task, new RegExp(`^${COORDINATION_CONTRACT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  assert.equal(injectCoordinationContract(flat), flat);
  const batch = injectCoordinationContract({
    context: "shared",
    tasks: [{ name: "one", task: "first" }, { name: "two", task: "second" }],
  });
  assert.equal(batch.context, "shared");
  assert.equal(batch.tasks.every(item => item.task.startsWith(COORDINATION_CONTRACT)), true);
  assert.deepEqual(guardHubInput({ op: "send", to: "Main", message: "done", await: true }), {
    input: { op: "send", to: "Main", message: "done", await: false },
  });
  assert.equal(guardHubInput({ op: "wait", from: "peer" }).block, true);
  assert.deepEqual(guardHubInput({ op: "wait", timeoutMs: 0 }), {
    input: { op: "wait", timeoutMs: 15000 },
  });
  assert.equal(guardHubInput({ op: "wait", name: "server", timeout: 30 }), undefined);
  assert.equal(guardHubInput({ op: "wait" }, true).block, true);
});

test("watchdog detects stalls once and clears incidents when progress resumes", () => {
  const watchdog = createAgentWatchdog({ webSearchStallMs: 100, noProgressMs: 200, maxAgeMs: 300 });
  watchdog.observeTaskProgress(
    {
      details: {
        async: { jobId: "task_web" },
        // OMP 17.3.7 forwards currentTool but omits currentToolStartMs for detached tasks.
        progress: [{ id: "agent-a", status: "running", currentTool: "web_search", toolCount: 1, requests: 1 }],
      },
    },
    1000,
  );
  const snapshot = { running: [{ id: "task_web", type: "task", status: "running", startTime: 900 }] };
  assert.equal(watchdog.sweep(snapshot, 1099).length, 0);
  assert.deepEqual(watchdog.sweep(snapshot, 1100).map(item => item.reason), ["web-search-stall"]);
  assert.equal(watchdog.sweep(snapshot, 1200).length, 0);
  assert.equal(watchdog.hasStaleJobs(), true);
  watchdog.observeTaskProgress(
    {
      details: {
        async: { jobId: "task_web" },
        progress: [{ id: "agent-a", status: "running", currentTool: "read", currentToolStartMs: 1200, toolCount: 2, requests: 2 }],
      },
    },
    1200,
  );
  assert.equal(watchdog.hasStaleJobs(), false);
  assert.deepEqual(watchdog.sweep(snapshot, 1500).map(item => item.reason), ["runtime-budget"]);
  assert.match(formatWatchdogStatus(watchdog.getIncidents()), /task_web\{reason=runtime-budget/);
  watchdog.sweep({ running: [] }, 1600);
  assert.equal(watchdog.hasStaleJobs(), false);
});

test("canary state is bounded and lease acquisition is exclusive", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-canary-state-"));
  try {
    const statePath = join(root, "state.json");
    const leasePath = join(root, "canary.lock");
    assert.equal(
      writeCanaryState(statePath, {
        selectors: {
          "p/m": { result: "success", checkedAt: 1, requestIdHash: "0123456789abcdef", raw: "drop me" },
          "https://secret.invalid/key": { result: "failed", checkedAt: 1 },
        },
      }),
      true,
    );
    const state = readCanaryState(statePath);
    assert.equal(state.selectors["p/m"].result, "success");
    assert.equal(JSON.stringify(state).includes("secret.invalid"), false);
    const release = acquireCanaryLease(leasePath, 1000);
    assert.equal(typeof release, "function");
    assert.equal(acquireCanaryLease(leasePath, 1001), undefined);
    release();
    assert.equal(existsSync(leasePath), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("real-tool canary requires child, probe, tool-result, and final nonce proof", async () => {
  const root = mkdtempSync(join(tmpdir(), "omp-canary-run-"));
  try {
    const probePath = join(root, "probe.js");
    writeFileSync(probePath, "export default function() {}\n");
    let observedArgs;
    const pi = {
      async exec(command, args) {
        assert.equal(command, "omp");
        observedArgs = args;
        const prefix = "Use the read tool to read exactly this file: ";
        const noncePath = args[1].split("\n")[0].slice(prefix.length);
        const nonce = readFileSync(noncePath, "utf8").trim();
        writeFileSync(
          `${noncePath}.result.json`,
          JSON.stringify({
            readCalled: true,
            argsValid: true,
            toolResultContainsNonce: true,
            finalContainsNonce: true,
            requestIdHash: "0123456789abcdef",
            channelId: "92",
          }),
        );
        return { code: 0, killed: false, stdout: nonce, stderr: "" };
      },
    };
    const summary = await runModelToolCanary(pi, "p/m:max", { root, probePath, cwd: root });
    assert.equal(summary.result, "success");
    assert.equal(summary.gatewayAttribution, "channel-id");
    assert.equal(observedArgs.includes("--no-extensions"), true);
    assert.deepEqual(observedArgs.slice(observedArgs.indexOf("--tools"), observedArgs.indexOf("--tools") + 2), ["--tools", "read"]);
    assert.doesNotThrow(() => buildCanaryArgs("p/m", join(root, "nonce"), probePath));
    assert.match(formatCanaryStatus({ selectors: { "p/m:max": summary } }, ["p/m:max"]), /gateway=channel-id/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("task and scout results become safe structured route records", () => {
  const events = extractTaskRouteEvents(
    {
      results: [
        {
          agent: "scout",
          modelRole: "scout",
          modelOverride: "@scout",
          resolvedModel: "zg-newapi/agnes-2.5-flash:low",
          resolvedModelIsFallback: true,
          durationMs: 1200,
          requests: 2,
          tokens: 321,
          usage: { input: 300, output: 21, secret: "drop" },
          exitCode: 0,
        },
        {
          agent: "task",
          modelOverride: "https://secret.example/key",
          error: "provider timeout with bearer token",
          exitCode: 1,
        },
      ],
    },
    { roleHash: "0123456789abcdef" },
  );
  assert.equal(events[0].route, "scout");
  assert.equal(events[0].fallback, true);
  assert.equal(events[0].usage.secret, undefined);
  assert.equal(events[1].failureClass, "timeout");
  assert.equal(events[1].configuredSelector, undefined);
  assert.equal(JSON.stringify(events).includes("bearer"), false);
});

test("task dispatch events cover flat and detached batch inputs without prompt data", () => {
  const events = extractTaskDispatchEvents(
    {
      context: "context-secret",
      tasks: [
        { agent: "scout", task: "assignment-secret" },
        { agent: "custom-agent", task: "another-secret" },
      ],
    },
    { roleHash: "0123456789abcdef" },
  );
  assert.deepEqual(events.map(event => [event.route, event.result]), [
    ["scout", "started"],
    ["task", "started"],
  ]);
  assert.equal(JSON.stringify(events).includes("secret"), false);
});

test("route log rotates at a bounded size and status stays redacted", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-routing-log-"));
  try {
    const path = join(root, "logs", "routes.jsonl");
    assert.equal(writeRouteEvent(path, { route: "task", result: "success", resolvedSelector: "p/m" }), true);
    const firstSize = statSync(path).size;
    assert.equal(writeRouteEvent(path, { route: "compaction", result: "failed", failureClass: "timeout", resolvedSelector: "p/c" }, { maxBytes: firstSize + 1 }), true);
    const records = readRouteEvents(path);
    assert.equal(records.length, 1);
    assert.equal(records[0].route, "compaction");
    assert.equal(readFileSync(`${path}.1`, "utf8").includes("task"), true);
    const status = formatRoutingStatus(records, {
      refreshState: "ok",
      roleHash: "0123456789abcdef",
      unresolvedCount: 1,
      indeterminateCount: 2,
    });
    assert.match(status, /compaction\{count=1/);
    assert.match(status, /roles\{refresh=ok,hash=0123456789abcdef,unresolved=1,indeterminate=2\}/);
    assert.equal(status.includes("secret"), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("route normalization rejects arbitrary strings and URLs", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-routing-redaction-"));
  try {
    const path = join(root, "routes.jsonl");
    writeRouteEvent(path, {
      route: "task",
      role: "assignment secret",
      resolvedSelector: "https://private.example/token",
      result: "success\nsecret",
      failureClass: "raw provider error",
      trigger: "rescue secret",
    });
    const serialized = readFileSync(path, "utf8");
    assert.equal(serialized.includes("secret"), false);
    assert.equal(serialized.includes("private.example"), false);
    assert.equal(readRouteEvents(path)[0].route, "task");
    assert.equal(readRouteEvents(path)[0].trigger, undefined);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("extension refreshes before task dispatch and records terminal details", async () => {
  const root = mkdtempSync(join(tmpdir(), "omp-routing-extension-"));
  try {
    const handlers = new Map();
    const commands = [];
    const pi = {
      pi: {
        settings: { getModelRoles: () => ({ task: "p/m" }) },
        getAgentDir: () => root,
      },
      logger: { warn() {} },
      on(name, handler) { handlers.set(name, handler); },
      registerCommand(name, command) { commands.push(name); this.command = command; },
    };
    modelRoutingObservability(pi);
    let refreshCount = 0;
    const ctx = {
      modelRegistry: {
        async refresh(strategy) { assert.equal(strategy, "offline"); refreshCount += 1; },
        getAvailable: () => [{ provider: "p", id: "m" }],
      },
      ui: { notify() {} },
    };
    const guarded = await handlers.get("tool_call")({ toolName: "task", toolCallId: "call-1", input: { agent: "scout", task: "inspect" } }, ctx);
    assert.equal(guarded.input.task.startsWith(COORDINATION_CONTRACT), true);
    handlers.get("tool_result")({
      toolName: "task",
      toolCallId: "call-1",
      details: { results: [{ agent: "scout", resolvedModel: "p/m", exitCode: 0, durationMs: 5 }] },
    });
    assert.equal(refreshCount, 1);
    assert.deepEqual(commands, ["model-routing-status", "model-tool-canary", "model-tool-canary-status", "agent-watchdog-status"]);
    const records = readRouteEvents(join(root, "logs", "omp-model-routing.jsonl"));
    assert.equal(records[0].route, "scout");
    assert.equal(records[0].result, "started");
    assert.equal(records[1].resolvedSelector, "p/m");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
