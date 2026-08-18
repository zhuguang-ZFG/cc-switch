import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import {
  canonicalRoleSnapshot,
  extractTaskDispatchEvents,
  extractTaskRouteEvents,
  formatRoutingStatus,
  hashRoleSnapshot,
  refreshAndValidateRoles,
  readRouteEvents,
  validateModelRoles,
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
    });
    const serialized = readFileSync(path, "utf8");
    assert.equal(serialized.includes("secret"), false);
    assert.equal(serialized.includes("private.example"), false);
    assert.equal(readRouteEvents(path)[0].route, "task");
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
    await handlers.get("tool_call")({ toolName: "task", toolCallId: "call-1", input: { agent: "scout" } }, ctx);
    handlers.get("tool_result")({
      toolName: "task",
      toolCallId: "call-1",
      details: { results: [{ agent: "scout", resolvedModel: "p/m", exitCode: 0, durationMs: 5 }] },
    });
    assert.equal(refreshCount, 1);
    assert.deepEqual(commands, ["model-routing-status"]);
    const records = readRouteEvents(join(root, "logs", "omp-model-routing.jsonl"));
    assert.equal(records[0].route, "scout");
    assert.equal(records[0].result, "started");
    assert.equal(records[1].resolvedSelector, "p/m");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
