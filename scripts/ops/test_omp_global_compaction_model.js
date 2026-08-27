import assert from "node:assert/strict";
import test from "node:test";

import globalCompactionModel, {
  EXTENSION_REVISION,
  applyGlobalCompactionModel,
  classifyCompactionFailure,
  clearGlobalCompactionModel,
  countImageBlocks,
  createGlobalCompactionReconciler,
  createCompactionThresholdManager,
  formatCompactionStatus,
  resolveCompactionThresholdPolicy,
  resolveCompactionTarget,
} from "./omp-global-compaction-model.js";

const TARGET = "zg-newapi/deepseek-v4-flash";

function createThresholdSettings() {
  const values = {
    "compaction.thresholdPercent": -1,
    "compaction.thresholdTokens": -1,
  };
  const overrides = {};
  return {
    values,
    overrides,
    get(key) {
      return overrides[key] ?? values[key];
    },
    override(key, value) {
      overrides[key] = value;
    },
    clearOverride(key) {
      delete overrides[key];
    },
  };
}

test("derives conservative thresholds from the active model context window", () => {
  assert.equal(resolveCompactionThresholdPolicy({ contextWindow: 128000 }).thresholdPercent, 70);
  assert.equal(resolveCompactionThresholdPolicy({ contextWindow: 272000 }).thresholdPercent, 70);
  assert.equal(resolveCompactionThresholdPolicy({ contextWindow: 400000 }).thresholdPercent, 78);
  assert.equal(resolveCompactionThresholdPolicy({ contextWindow: 512000 }).thresholdPercent, 82);
  assert.equal(resolveCompactionThresholdPolicy({ contextWindow: 1000000 }).thresholdPercent, 85);
  assert.equal(resolveCompactionThresholdPolicy({}).state, "unavailable");
});

test("manages legacy thresholds, follows model switches, and preserves explicit user values", () => {
  const settings = createThresholdSettings();
  const store = new WeakMap();
  const manager = createCompactionThresholdManager(settings, { store });
  const first = manager.reconcile({ provider: "p", id: "large", contextWindow: 1000000 }, "test");
  assert.equal(first.ownership, "managed");
  assert.equal(first.thresholdTokens, 850000);
  assert.equal(settings.get("compaction.thresholdTokens"), 850000);

  const reloaded = createCompactionThresholdManager(settings, { store });
  const switched = reloaded.reconcile({ provider: "p", id: "small", contextWindow: 200000 }, "before_agent_start");
  assert.equal(switched.thresholdPercent, 70);
  assert.equal(switched.thresholdTokens, 140000);
  assert.equal(settings.get("compaction.thresholdTokens"), 140000);

  settings.values["compaction.thresholdTokens"] = 90000;
  const explicit = reloaded.reconcile({ provider: "p", id: "small", contextWindow: 200000 }, "user_change");
  assert.equal(explicit.ownership, "user-configured");
  assert.equal(explicit.thresholdTokens, 90000);
  assert.equal(settings.overrides["compaction.thresholdTokens"], undefined);
});

function createContext(models, current = models[0]) {
  return {
    model: current,
    models: { list: () => models },
  };
}

test("binds every available model without changing model identity", () => {
  const models = [
    { provider: "zg-newapi", id: "gpt-5.6-sol" },
    {
      provider: "zg-newapi",
      id: "deepseek-v4-flash",
      compactionModel: TARGET,
    },
    {
      provider: "agentrouter",
      id: "claude-opus-5",
      compactionModel: "agentrouter/claude-opus-5",
    },
  ];

  const result = applyGlobalCompactionModel(createContext(models));

  assert.deepEqual(
    models.map(({ provider, id }) => `${provider}/${id}`),
    [
      "zg-newapi/gpt-5.6-sol",
      "zg-newapi/deepseek-v4-flash",
      "agentrouter/claude-opus-5",
    ],
  );
  assert.ok(models.every((model) => model.compactionModel === TARGET));
  assert.deepEqual(result, {
    inspected: 3,
    updated: 2,
    alreadyBound: 1,
    failed: [],
  });
});
test("also binds a current model that is not in the registry snapshot", () => {
  const registered = { provider: "zg-newapi", id: "k3" };
  const current = { provider: "future-provider", id: "future-model" };

  const result = applyGlobalCompactionModel(
    createContext([registered], current),
  );

  assert.equal(registered.compactionModel, TARGET);
  assert.equal(current.compactionModel, TARGET);
  assert.equal(result.inspected, 2);
});

test("selects only authenticated registry candidates in priority order", () => {
  const qwen27b = { provider: "zg-newapi", id: "qwen3-8-27b" };
  const glm = { provider: "zg-newapi", id: "zai-glm-5-2" };
  const deepseek = { provider: "zg-newapi", id: "deepseek-v4-flash" };

  assert.equal(
    resolveCompactionTarget(createContext([qwen27b, glm, deepseek])).target,
    TARGET,
  );
  assert.equal(
    resolveCompactionTarget(createContext([qwen27b, glm])).target,
    "zg-newapi/zai-glm-5-2",
  );
  assert.equal(
    resolveCompactionTarget(
      createContext([
        qwen27b,
        glm,
        { provider: "zg-newapi", id: "glm-5.2" },
      ]),
    ).target,
    "zg-newapi/glm-5.2",
  );
  assert.equal(
    resolveCompactionTarget(createContext([qwen27b])).target,
    "zg-newapi/qwen3-8-27b",
  );
});

test("does not overwrite compaction metadata when every candidate is unavailable", () => {
  const model = {
    provider: "future",
    id: "only-model",
    compactionModel: "future/existing-target",
  };
  const reconciler = createGlobalCompactionReconciler();

  const result = reconciler.reconcile(createContext([model]), "test");

  assert.equal(result.targetAvailable, false);
  assert.equal(model.compactionModel, "future/existing-target");
});

test("reports non-writable model metadata instead of silently succeeding", () => {
  const frozen = Object.freeze({ provider: "future", id: "frozen" });
  const result = applyGlobalCompactionModel(createContext([frozen]));

  assert.equal(result.updated, 0);
  assert.equal(result.failed.length, 1);
  assert.equal(result.failed[0].selector, "future/frozen");
  assert.equal(result.failed[0].errorClass, "metadata-write-failed");
});

test("clears only bindings owned by the managed candidate policy", () => {
  const managed = { provider: "future", id: "managed", compactionModel: TARGET };
  const custom = {
    provider: "future",
    id: "custom",
    compactionModel: "future/private-compactor",
  };

  const result = clearGlobalCompactionModel(
    createContext([managed, custom]),
    [TARGET],
  );

  assert.equal(managed.compactionModel, undefined);
  assert.equal(custom.compactionModel, "future/private-compactor");
  assert.equal(result.cleared, 1);
  assert.equal(result.untouched, 1);
});

test("reconciles models added after session start", () => {
  const models = [
    { provider: "zg-newapi", id: "gpt-5.6-sol" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
  ];
  const ctx = createContext(models);
  const reconciler = createGlobalCompactionReconciler();

  reconciler.start(ctx);
  const added = { provider: "future-provider", id: "new-model" };
  models.push(added);
  reconciler.reconcile(ctx, "before_agent_start");

  assert.equal(added.compactionModel, TARGET);
});

test("the managed interval closes the immediate switch then compact gap", () => {
  const models = [
    { provider: "zg-newapi", id: "gpt-5.6-sol" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
  ];
  let intervalCallback;
  const ctx = {
    ...createContext(models),
    setInterval(callback, milliseconds) {
      intervalCallback = callback;
      assert.equal(milliseconds, 1000);
    },
  };
  const reconciler = createGlobalCompactionReconciler();

  reconciler.start(ctx);
  const added = { provider: "future-provider", id: "new-model" };
  models.push(added);
  intervalCallback();

  assert.equal(added.compactionModel, TARGET);
});

test("counts image history without reading image payloads", () => {
  const preparation = {
    messagesToSummarize: [
      {
        role: "user",
        content: [
          { type: "text", text: "describe this" },
          { type: "image", data: "secret-image-data" },
        ],
      },
    ],
    turnPrefixMessages: [
      {
        role: "toolResult",
        content: [{ type: "image_url", image_url: "secret-url" }],
      },
    ],
  };

  assert.equal(countImageBlocks(preparation), 2);
});

test("tracks compaction start, success, duration, and redacted image policy", () => {
  let currentTime = 1000;
  const logs = [];
  const logger = {
    info(message, fields) {
      logs.push({ message, fields });
    },
  };
  const models = [
    { provider: "zg-newapi", id: "gpt-5.6-sol" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
  ];
  const reconciler = createGlobalCompactionReconciler({
    now: () => currentTime,
  });

  const started = reconciler.beginCompaction(
    {
      reason: "threshold",
      preparation: {
        tokensBefore: 52012,
        messagesToSummarize: [
          { role: "user", content: [{ type: "image", data: "not-logged" }] },
        ],
        turnPrefixMessages: [],
      },
    },
    createContext(models),
    logger,
  );
  currentTime = 3450;
  const completed = reconciler.completeCompaction(
    { compactionEntry: { tokensBefore: 52012 }, fromExtension: false },
    logger,
  );

  assert.equal(started.phase, "running");
  assert.equal(started.target, TARGET);
  assert.equal(started.imageBlocks, 1);
  assert.equal(started.imagePolicy, "text-only-serialization");
  assert.equal(completed.phase, "idle");
  assert.equal(completed.result, "success");
  assert.equal(completed.durationMs, 2450);
  assert.equal(logs[0].fields.imageBlocks, 1);
  assert.equal(JSON.stringify(logs).includes("not-logged"), false);
  assert.match(formatCompactionStatus(completed), /result=success/);
  assert.match(formatCompactionStatus(completed), new RegExp(`rev=${EXTENSION_REVISION}`));
});

test("records auto-compaction failures without changing the main model", () => {
  let currentTime = 10;
  const models = [
    { provider: "agentrouter", id: "claude-opus-5" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
    { provider: "zg-newapi", id: "zai-glm-5-2" },
  ];
  const ctx = createContext(models, models[0]);
  const reconciler = createGlobalCompactionReconciler({
    now: () => currentTime,
    failureCooldownMs: 500,
  });

  reconciler.beginCompaction(
    { preparation: { tokensBefore: 50000 } },
    ctx,
  );
  currentTime = 210;
  const ended = reconciler.endAutoCompaction(
    {
      aborted: false,
      skipped: false,
      errorMessage: "HTTP 503 upstream unavailable",
    },
    ctx,
  );

  assert.equal(ended.result, "failed");
  assert.equal(ended.errorClass, "provider-http");
  assert.equal(ended.errorStatus, 503);
  assert.equal(ended.durationMs, 200);
  assert.equal(ctx.model.provider, "agentrouter");
  assert.equal(ctx.model.id, "claude-opus-5");

  const fallback = reconciler.reconcile(ctx, "after-failure");
  assert.equal(fallback.target, "zg-newapi/zai-glm-5-2");
  currentTime = 711;
  const recovered = reconciler.reconcile(ctx, "cooldown-expired");
  assert.equal(recovered.target, TARGET);
});

test("classifies only explicit provider failures as retryable", () => {
  assert.deepEqual(classifyCompactionFailure("HTTP 429 rate limited"), {
    kind: "provider-http",
    status: 429,
    retryable: true,
  });
  assert.deepEqual(classifyCompactionFailure("socket connection reset"), {
    kind: "provider-transport",
    retryable: true,
  });
  assert.deepEqual(classifyCompactionFailure("Nothing to compact (session too small)"), {
    kind: "local",
    retryable: false,
  });
  assert.deepEqual(classifyCompactionFailure("401 unauthorized"), {
    kind: "local",
    retryable: false,
  });
  assert.deepEqual(classifyCompactionFailure("upstream returned an invalid payload"), {
    kind: "unknown",
    retryable: false,
  });
  assert.deepEqual(classifyCompactionFailure("context window is 500 tokens"), {
    kind: "unknown",
    retryable: false,
  });
  assert.deepEqual(classifyCompactionFailure("503 Service Unavailable"), {
    kind: "provider-http",
    status: 503,
    retryable: true,
  });
});

test("fails closed when every authenticated candidate is cooling", () => {
  let currentTime = 10;
  const fallbackTarget = "zg-newapi/zai-glm-5-2";
  const models = [
    { provider: "agentrouter", id: "claude-opus-5" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
    { provider: "zg-newapi", id: "zai-glm-5-2" },
  ];
  const ctx = createContext(models, models[0]);
  const reconciler = createGlobalCompactionReconciler({
    candidates: [TARGET, fallbackTarget],
    now: () => currentTime,
    failureCooldownMs: 500,
    extensionRetryEnabled: false,
  });

  reconciler.beginAutoCompaction({ reason: "threshold" });
  reconciler.beginCompaction({ preparation: { tokensBefore: 50_000 } }, ctx);
  reconciler.endAutoCompaction(
    { errorMessage: "HTTP 503 primary unavailable" },
    ctx,
  );
  assert.ok(models.every((model) => model.compactionModel === fallbackTarget));

  currentTime = 20;
  reconciler.beginAutoCompaction({ reason: "threshold" });
  reconciler.beginCompaction({ preparation: { tokensBefore: 50_000 } }, ctx);
  const allCooling = reconciler.endAutoCompaction(
    { errorMessage: "HTTP 503 fallback unavailable" },
    ctx,
  );

  assert.equal(allCooling.target, undefined);
  assert.ok(models.every((model) => model.compactionModel === undefined));
  assert.ok(
    allCooling.candidates
      .filter((candidate) => candidate.available)
      .every((candidate) => candidate.state === "cooldown"),
  );

  reconciler.beginAutoCompaction({ reason: "threshold" });
  const blocked = reconciler.beginCompaction(
    { preparation: { tokensBefore: 50_000 } },
    ctx,
  );
  assert.equal(blocked.cancel, true);
  assert.equal(blocked.result, "unavailable");

  currentTime = 521;
  const recovered = reconciler.reconcile(ctx, "cooldown-expired");
  assert.equal(recovered.target, TARGET);
  assert.ok(models.every((model) => model.compactionModel === TARGET));
  assert.equal(ctx.model.provider, "agentrouter");
  assert.equal(ctx.model.id, "claude-opus-5");
});

test("schedules one managed fallback attempt and records its success", async () => {
  let currentTime = 100;
  const fallbackTarget = "zg-newapi/zai-glm-5-2";
  const timers = [];
  let compactCalls = 0;
  const models = [
    { provider: "agentrouter", id: "claude-opus-5" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
    { provider: "zg-newapi", id: "zai-glm-5-2" },
  ];
  let reconciler;
  const ctx = {
    ...createContext(models, models[0]),
    setTimeout(callback, milliseconds) {
      assert.equal(milliseconds, 50);
      timers.push(callback);
    },
    async compact() {
      compactCalls += 1;
      currentTime = 200;
      const started = reconciler.beginCompaction(
        { preparation: { tokensBefore: 60_000 } },
        ctx,
      );
      assert.equal(started.target, fallbackTarget);
      currentTime = 260;
      reconciler.completeCompaction({ compactionEntry: { tokensBefore: 60_000 } });
    },
  };
  reconciler = createGlobalCompactionReconciler({
    candidates: [TARGET, fallbackTarget],
    now: () => currentTime,
    failureCooldownMs: 500,
  });

  reconciler.beginAutoCompaction({ reason: "threshold" });
  reconciler.beginCompaction({ preparation: { tokensBefore: 60_000 } }, ctx);
  currentTime = 150;
  const ended = reconciler.endAutoCompaction(
    { errorMessage: "HTTP 503 primary unavailable" },
    ctx,
  );
  reconciler.endAutoCompaction(
    { errorMessage: "HTTP 503 duplicate terminal event" },
    ctx,
  );

  assert.equal(ended.retryState, "pending");
  assert.equal(timers.length, 1);
  reconciler.beginAutoCompaction({ reason: "threshold" });
  const concurrent = reconciler.beginCompaction(
    { preparation: { tokensBefore: 60_000 } },
    ctx,
  );
  assert.equal(concurrent.cancel, true);
  assert.equal(concurrent.result, "retry-pending");
  assert.equal(timers.length, 1);
  await timers[0]();

  const finalStatus = reconciler.getStatus();
  const primary = finalStatus.candidates.find(
    (candidate) => candidate.selector === TARGET,
  );
  const fallback = finalStatus.candidates.find(
    (candidate) => candidate.selector === fallbackTarget,
  );
  assert.equal(compactCalls, 1);
  assert.equal(finalStatus.result, "fallback-success");
  assert.equal(finalStatus.retryState, "success");
  assert.equal(finalStatus.extensionRetries, 1);
  assert.equal(primary.failures, 1);
  assert.equal(fallback.successes, 1);
  assert.equal(fallback.retryAttempts, 1);
  assert.equal(ctx.model.provider, "agentrouter");
  assert.equal(ctx.model.id, "claude-opus-5");
});

test("a failed fallback is terminal and cannot schedule a second retry", async () => {
  const fallbackTarget = "zg-newapi/zai-glm-5-2";
  const timers = [];
  const models = [
    { provider: "agentrouter", id: "claude-opus-5" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
    { provider: "zg-newapi", id: "zai-glm-5-2" },
  ];
  let reconciler;
  const ctx = {
    ...createContext(models, models[0]),
    setTimeout(callback) {
      timers.push(callback);
    },
    async compact() {
      reconciler.beginCompaction(
        { preparation: { tokensBefore: 60_000 } },
        ctx,
      );
      throw new Error("HTTP 502 backup unavailable");
    },
  };
  reconciler = createGlobalCompactionReconciler({
    candidates: [TARGET, fallbackTarget],
    now: () => 100,
  });

  reconciler.beginAutoCompaction({ reason: "threshold" });
  reconciler.beginCompaction({ preparation: { tokensBefore: 60_000 } }, ctx);
  reconciler.endAutoCompaction(
    { errorMessage: "HTTP 503 primary unavailable" },
    ctx,
  );
  await timers[0]();

  const finalStatus = reconciler.getStatus();
  assert.equal(timers.length, 1);
  assert.equal(finalStatus.result, "fallback-failed");
  assert.equal(finalStatus.extensionRetries, 1);
  assert.ok(
    finalStatus.candidates
      .filter((candidate) => candidate.available)
      .every((candidate) => candidate.state === "cooldown"),
  );
  assert.ok(models.every((model) => model.compactionModel === undefined));
});

test("aborted, skipped, and local failures neither cool nor retry", () => {
  const cases = [
    { aborted: true, errorMessage: "HTTP 503 ignored after abort" },
    { skipped: true, errorMessage: "HTTP 503 ignored after skip" },
    { errorMessage: "Nothing to compact (session too small)" },
  ];

  for (const event of cases) {
    const timers = [];
    const models = [
      { provider: "agentrouter", id: "claude-opus-5" },
      { provider: "zg-newapi", id: "deepseek-v4-flash" },
    ];
    const ctx = {
      ...createContext(models, models[0]),
      setTimeout(callback) {
        timers.push(callback);
      },
      async compact() {},
    };
    const reconciler = createGlobalCompactionReconciler();
    reconciler.beginAutoCompaction({ reason: "threshold" });
    reconciler.beginCompaction({ preparation: { tokensBefore: 50_000 } }, ctx);
    const ended = reconciler.endAutoCompaction(event, ctx);
    const primary = ended.candidates.find(
      (candidate) => candidate.selector === TARGET,
    );
    assert.equal(timers.length, 0);
    assert.equal(primary.failures, 0);
    assert.equal(primary.cooldownRemainingMs, 0);
  }
});

test("status and logs discard raw provider, transcript, URL, and credential text", () => {
  const sentinel =
    "HTTP 503 https://private.example/path sk-secret transcript-secret image-url-secret";
  const logs = [];
  const logger = {
    error(message, fields) {
      logs.push({ message, fields });
    },
    info(message, fields) {
      logs.push({ message, fields });
    },
  };
  const models = [
    { provider: "agentrouter", id: "claude-opus-5" },
    { provider: "zg-newapi", id: "deepseek-v4-flash" },
  ];
  const ctx = createContext(models, models[0]);
  const reconciler = createGlobalCompactionReconciler({
    extensionRetryEnabled: false,
  });

  reconciler.beginAutoCompaction({ reason: "threshold" });
  reconciler.beginCompaction(
    {
      preparation: {
        tokensBefore: 50_000,
        messagesToSummarize: [
          {
            content: [
              { type: "text", text: "transcript-secret" },
              { type: "image_url", image_url: "image-url-secret" },
            ],
          },
        ],
      },
    },
    ctx,
    logger,
  );
  const ended = reconciler.endAutoCompaction(
    { errorMessage: sentinel },
    ctx,
    logger,
  );
  const serialized = `${formatCompactionStatus(ended)} ${JSON.stringify(logs)}`;

  assert.match(serialized, /provider-http/);
  assert.equal(serialized.includes("private.example"), false);
  assert.equal(serialized.includes("sk-secret"), false);
  assert.equal(serialized.includes("transcript-secret"), false);
  assert.equal(serialized.includes("image-url-secret"), false);
});

test("registers lifecycle handlers and the opt-in status command", () => {
  const registrations = [];
  const commands = [];
  globalCompactionModel({
    on(event, handler) {
      registrations.push({ event, handler });
    },
    registerCommand(name, command) {
      commands.push({ name, command });
    },
    logger: { debug() {}, error() {} },
  });

  assert.deepEqual(
    registrations.map(({ event }) => event),
    [
      "session_start",
      "before_agent_start",
      "auto_compaction_start",
      "session_before_compact",
      "session_compact",
      "auto_compaction_end",
    ],
  );
  assert.equal(commands[0].name, "compaction-status");
});

test("emits one success record when compaction completion is repeated", () => {
  const writerSymbol = Symbol.for("omp.modelRoutingTelemetry.writer");
  const previousWriter = globalThis[writerSymbol];
  const records = [];
  globalThis[writerSymbol] = event => {
    records.push(event);
    return true;
  };
  try {
    const handlers = new Map();
    const model = { provider: "zg-newapi", id: "main", contextWindow: 400000 };
    const target = { provider: "zg-newapi", id: "deepseek-v4-flash", contextWindow: 1000000 };
    globalCompactionModel({
      pi: { settings: createThresholdSettings(), getAgentDir: () => "agent-dir" },
      on(event, handler) { handlers.set(event, handler); },
      registerCommand() {},
      logger: { debug() {}, info() {}, error() {} },
    });
    const ctx = { ...createContext([model, target], model) };
    handlers.get("session_start")({}, ctx);
    handlers.get("session_before_compact")({ preparation: { tokensBefore: 100 } }, ctx);
    handlers.get("session_compact")({ compactionEntry: { tokensBefore: 100 } });
    handlers.get("session_compact")({ compactionEntry: { tokensBefore: 100 } });
    assert.equal(records.filter(record => record.result === "success").length, 1);
  } finally {
    if (previousWriter === undefined) delete globalThis[writerSymbol];
    else globalThis[writerSymbol] = previousWriter;
  }
});
