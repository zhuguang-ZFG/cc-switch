import assert from "node:assert/strict";
import test from "node:test";

import globalCompactionModel, {
  applyGlobalCompactionModel,
  countImageBlocks,
  createGlobalCompactionReconciler,
  formatCompactionStatus,
  resolveCompactionTarget,
} from "./omp-global-compaction-model.js";

const TARGET = "zg-newapi/deepseek-v4-flash";

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
  const longcat = { provider: "longcat", id: "LongCat-2.0" };
  const glm = { provider: "zg-newapi", id: "zai-glm-5-2" };
  const deepseek = { provider: "zg-newapi", id: "deepseek-v4-flash" };

  assert.equal(
    resolveCompactionTarget(createContext([longcat, glm, deepseek])).target,
    TARGET,
  );
  assert.equal(
    resolveCompactionTarget(createContext([longcat, glm])).target,
    "zg-newapi/zai-glm-5-2",
  );
  assert.equal(
    resolveCompactionTarget(
      createContext([
        longcat,
        glm,
        { provider: "zg-newapi", id: "glm-5.2" },
      ]),
    ).target,
    "zg-newapi/glm-5.2",
  );
  assert.equal(
    resolveCompactionTarget(createContext([longcat])).target,
    "longcat/LongCat-2.0",
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
  const ended = reconciler.endAutoCompaction({
    aborted: false,
    skipped: false,
    errorMessage: "upstream unavailable",
  });

  assert.equal(ended.result, "failed");
  assert.equal(ended.error, "upstream unavailable");
  assert.equal(ended.durationMs, 200);
  assert.equal(ctx.model.provider, "agentrouter");
  assert.equal(ctx.model.id, "claude-opus-5");

  const fallback = reconciler.reconcile(ctx, "after-failure");
  assert.equal(fallback.target, "zg-newapi/zai-glm-5-2");
  currentTime = 711;
  const recovered = reconciler.reconcile(ctx, "cooldown-expired");
  assert.equal(recovered.target, TARGET);
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
      "session_before_compact",
      "session_compact",
      "auto_compaction_end",
    ],
  );
  assert.equal(commands[0].name, "compaction-status");
});
