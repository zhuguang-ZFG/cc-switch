import assert from "node:assert/strict";
import test from "node:test";

import globalCompactionModel, {
  applyGlobalCompactionModel,
  createGlobalCompactionReconciler,
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

test("reports non-writable model metadata instead of silently succeeding", () => {
  const frozen = Object.freeze({ provider: "future", id: "frozen" });
  const result = applyGlobalCompactionModel(createContext([frozen]));

  assert.equal(result.updated, 0);
  assert.equal(result.failed.length, 1);
  assert.equal(result.failed[0].selector, "future/frozen");
});

test("reconciles models added after session start", () => {
  const models = [{ provider: "zg-newapi", id: "gpt-5.6-sol" }];
  const ctx = createContext(models);
  const reconciler = createGlobalCompactionReconciler();

  reconciler.start(ctx);
  const added = { provider: "future-provider", id: "new-model" };
  models.push(added);
  reconciler.reconcile(ctx, "before_agent_start");

  assert.equal(added.compactionModel, TARGET);
});

test("the managed interval closes the immediate switch then compact gap", () => {
  const models = [{ provider: "zg-newapi", id: "gpt-5.6-sol" }];
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

test("registers the three lifecycle handlers used by OMP", () => {
  const registrations = [];
  globalCompactionModel({
    on(event, handler) {
      registrations.push({ event, handler });
    },
    logger: { debug() {}, error() {} },
  });

  assert.deepEqual(
    registrations.map(({ event }) => event),
    ["session_start", "before_agent_start", "session_before_compact"],
  );
});
