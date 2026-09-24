import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import extension, {
  checkpointPath,
  createFailureTracker,
  classifyFailure,
  loadCheckpoint,
  normalizeCheckpoint,
  saveCheckpoint,
} from "./omp-problem-solving.js";
import sotaExtension, {
  applyWorkloadBreaker,
  discoverSotaCandidates,
  readReviewReadiness,
  REVIEW_POLICY_FILENAME,
  REVIEW_SELECTOR,
} from "./omp-sota-escalation.js";
import { acquireCanaryLease } from "./omp-model-routing-observability.js";
import guard from "./review/omp-review-guard.js";

const checkpoint = {
  goal: "Repair parsing",
  nextStep: "Inspect malformed input",
  completionCriteria: "Regression passes",
  constraints: ["Keep public API"],
  evidence: ["Empty input fails"],
  rejectedHypotheses: ["Network timeout"],
  verifiedTests: ["parse test failed"],
};
function temporary(fn) {
  return async () => {
    const root = mkdtempSync(join(tmpdir(), "omp-problem-test-"));
    try {
      await fn(root);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  };
}
function policy(root, extra = {}) {
  writeFileSync(
    join(root, REVIEW_POLICY_FILENAME),
    JSON.stringify({
      schema: 1,
      enabled: true,
      mode: "existing-slow",
      selector: REVIEW_SELECTOR,
      ...extra,
    }),
  );
}

test(
  "only an explicit valid policy selects the approved registered slow model",
  temporary((root) => {
    const models = [
      { provider: "zg-newapi-anthropic", id: "claude-opus-5" },
      { provider: "other", id: "claude-opus-5" },
      { provider: "zg-newapi", id: "omp-sota-old" },
    ];
    assert.deepEqual(
      discoverSotaCandidates(models, undefined, readReviewReadiness(root)),
      [],
    );
    policy(root);
    assert.deepEqual(
      discoverSotaCandidates(models, undefined, readReviewReadiness(root)),
      [REVIEW_SELECTOR],
    );
    assert.deepEqual(
      discoverSotaCandidates([], undefined, readReviewReadiness(root)),
      [],
    );
    for (const extra of [
      { enabled: false },
      { selector: "other/claude-opus-5" },
      { schema: 2 },
    ]) {
      policy(root, extra);
      assert.deepEqual(
        discoverSotaCandidates(models, undefined, readReviewReadiness(root)),
        [],
      );
      assert.ok(readReviewReadiness(root).diagnostic);
    }
    writeFileSync(join(root, REVIEW_POLICY_FILENAME), "{");
    assert.ok(readReviewReadiness(root).diagnostic);
  }),
);

test(
  "shared failure cooldown and timeout breaker apply to opted-in model",
  temporary((root) => {
    policy(root);
    const health = {
      candidates: {
        [REVIEW_SELECTOR]: { lastResult: "failure", checkedAt: 1000 },
      },
    };
    assert.equal(
      applyWorkloadBreaker(readReviewReadiness(root), health, false, 2000)
        .candidates[REVIEW_SELECTOR].status,
      "unavailable",
    );
    assert.equal(
      applyWorkloadBreaker(readReviewReadiness(root), health, false, 302000)
        .candidates[REVIEW_SELECTOR].status,
      "ready",
    );
  }),
);

test("repeated failed action prompts once, success resets, different arguments stay independent", () => {
  const tracker = createFailureTracker();
  const event = {
    toolName: "bash",
    input: { command: "test", cwd: "/test" },
    isError: true,
  };
  assert.equal(tracker.observe(event), false);
  assert.equal(
    tracker.observe({ ...event, input: { cwd: "/test", command: "test" } }),
    true,
  );
  assert.equal(tracker.observe(event), false);
  assert.equal(
    tracker.observe({ ...event, input: { command: "other" } }),
    false,
  );
  tracker.observe({ ...event, isError: false });
  assert.equal(tracker.observe(event), false);
  assert.equal(tracker.observe(event), true);
  tracker.reset();
  assert.equal(tracker.observe(event), false);
});

test("different failing commands share actionable categories without leaking output", () => {
  const tracker = createFailureTracker();
  const failure = (command) => ({
    toolName: "bash",
    input: { command },
    isError: false,
    details: { exitCode: 1 },
    content: [{ type: "text", text: "ENOENT secret=do-not-emit" }],
  });
  assert.equal(tracker.observe(failure("first")), false);
  assert.equal(tracker.observe(failure("second")), false);
  assert.equal(tracker.observe(failure("third")), true);
  assert.equal(tracker.observe(failure("fourth")), false);
  assert.deepEqual(classifyFailure(failure("third")), {
    failed: true,
    category: "missing-resource",
  });
  tracker.observe({
    toolName: "bash",
    input: { command: "working" },
    details: { exitCode: 0 },
  });
  assert.equal(tracker.observe(failure("fifth")), false);
  assert.deepEqual(
    classifyFailure({
      isError: false,
      content: [{ type: "text", text: "document mentions ENOENT" }],
    }),
    { failed: false },
  );
});

test(
  "checkpoint persists bounded redacted facts, preserves backup and rejects stale writers",
  temporary((root) => {
    const path = checkpointPath(root, root);
    const first = saveCheckpoint(path, {
      ...checkpoint,
      evidence: [
        "api_key=sk-sensitive password=hunter token=abc",
        "https://private.example",
      ],
    });
    assert.doesNotMatch(
      readFileSync(path, "utf8"),
      /sk-sensitive|hunter|token=abc|private.example/,
    );
    assert.equal(loadCheckpoint(path).goal, checkpoint.goal);
    assert.throws(
      () => saveCheckpoint(path, checkpoint, "stale"),
      /another session/,
    );
    const second = saveCheckpoint(path, checkpoint, first.revision);
    assert.equal(
      JSON.parse(readFileSync(`${path}.previous`, "utf8")).revision,
      first.revision,
    );
    assert.notEqual(first.revision, second.revision);
    assert.equal(
      loadCheckpoint(path, Date.now() + 31 * 86400000).expired,
      true,
    );
    assert.throws(() =>
      normalizeCheckpoint({ ...checkpoint, evidence: Array(7).fill("x") }),
    );
    writeFileSync(path, "corrupt");
    assert.throws(() => saveCheckpoint(path, checkpoint, second.revision));
    assert.equal(readFileSync(path, "utf8"), "corrupt");
  }),
);

test(
  "lifecycle restores historical checkpoint, compaction reinjects and strategy never starts extra turn",
  temporary(async (root) => {
    const handlers = new Map();
    const messages = [];
    let tool;
    const pi = {
      pi: { getAgentDir: () => root },
      on: (name, fn) => handlers.set(name, fn),
      registerTool: (value) => {
        tool = value;
      },
      sendMessage: (...args) => messages.push(args),
    };
    extension(pi);
    const ctx = { cwd: root };
    handlers.get("before_agent_start")({}, ctx);
    assert.equal(
      (await tool.execute("1", checkpoint, null, null, ctx)).isError,
      undefined,
    );
    assert.match(
      handlers.get("before_agent_start")({ prompt: "New unrelated task" }, ctx)
        .message.content,
      /Historical project checkpoint/,
    );
    for (let i = 0; i < 3; i++) {
      handlers.get("tool_call")({
        toolCallId: String(i),
        input: { path: "missing" },
      });
      handlers.get("tool_result")({
        toolCallId: String(i),
        toolName: "read",
        isError: true,
      });
    }
    assert.equal(messages.length, 1);
    assert.equal(messages[0][1].triggerTurn, false);
    handlers.get("session_compact")({}, ctx);
    assert.match(messages[1][0].content, /Empty input fails/);
    assert.equal(messages[1][1].triggerTurn, false);
  }),
);

test(
  "shared review lease prevents sibling spawn and is released after failure",
  temporary(async (root) => {
    policy(root);
    const handlers = new Map();
    const commands = new Map();
    let runs = 0;
    const pi = {
      pi: { getAgentDir: () => root },
      on: (name, fn) => handlers.set(name, fn),
      registerCommand: (name, command) => commands.set(name, command),
      exec: async () => ({ code: 0, stdout: "" }),
      runSotaChild: async () => {
        runs++;
        throw new Error("local fixture error");
      },
    };
    sotaExtension(pi);
    const ctx = {
      cwd: root,
      models: {
        list: () => [{ provider: "zg-newapi-anthropic", id: "claude-opus-5" }],
      },
    };
    handlers.get("session_start")({}, ctx);
    const release = acquireCanaryLease(join(root, "sota-review.lock"));
    await commands.get("sota").handler("Review", ctx);
    assert.equal(runs, 0);
    release();
    // Fresh extension instance removes in-session cooldown from the busy skip.
    sotaExtension(pi);
    await commands.get("sota").handler("Review", ctx);
    assert.equal(runs, 1);
    const reacquire = acquireCanaryLease(join(root, "sota-review.lock"));
    assert.ok(reacquire);
    reacquire();
  }),
);

test(
  "review guard enforces tool budget, bounded reads and workspace scope",
  temporary((root) => {
    let handler;
    guard({
      on: (_name, fn) => {
        handler = fn;
      },
    });
    const ctx = { cwd: root };
    writeFileSync(join(root, "ok.txt"), "evidence");
    assert.equal(
      handler({ toolName: "write", input: { path: "ok.txt" } }, ctx).block,
      true,
    );
    assert.equal(
      handler({ toolName: "read", input: { path: "ok.txt", limit: 200 } }, ctx),
      undefined,
    );
    assert.equal(
      handler({ toolName: "read", input: { path: "ok.txt" } }, ctx).block,
      true,
    );
    assert.equal(
      handler({ toolName: "read", input: { path: "..", limit: 20 } }, ctx)
        .block,
      true,
    );
    assert.equal(
      handler({ toolName: "glob", input: { pattern: "../*" } }, ctx).block,
      true,
    );
    assert.equal(handler({ toolName: "lsp", input: {} }, ctx).block, true);
    for (let i = 0; i < 2; i++)
      assert.equal(
        handler({ toolName: "grep", input: { path: "." } }, ctx),
        undefined,
      );
    assert.equal(
      handler({ toolName: "read", input: { path: "ok.txt", limit: 1 } }, ctx)
        .block,
      true,
    );
  }),
);
