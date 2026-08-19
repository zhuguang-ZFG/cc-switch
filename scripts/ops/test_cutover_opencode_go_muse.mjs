import assert from "node:assert/strict";
import test from "node:test";

import {
  planChannel,
  requireContributorConsent,
  transformConfigYaml,
  transformModelsYaml,
  validateCanaryState,
} from "./cutover_opencode_go_muse.mjs";
import { EXTENSION_REVISION } from "./omp-model-routing-observability.js";

const models = [
  "providers:",
  "  zg-newapi:",
  "    api: openai-completions",
  "    models:",
  "    - id: gpt-5.6-luna",
  "      name: GPT 5.6 Luna",
  "      contextWindow: 272000",
  "    - id: gpt-5.6-terra",
  "      contextWindow: 272000",
  "  other:",
  "    models: []",
  "",
].join("\n");

const config = [
  "modelRoles:",
  "  tiny: zg-newapi/gpt-5.6-luna",
  "  task: zg-newapi/gpt-5.6-luna:max",
  "retry:",
  "  fallbackChains:",
  "    zg-newapi/gpt-5.6-luna:",
  "      - fallback/model",
  "    smol:",
  "      - zg-newapi/gpt-5.6-luna",
  "",
].join("\n");

test("stage adds a Responses Muse model but leaves Luna and roles intact", () => {
  const stagedModels = transformModelsYaml(models, "stage");
  assert.equal(stagedModels.changed, true);
  assert.match(stagedModels.output, /- id: gpt-5\.6-luna/);
  assert.match(
    stagedModels.output,
    /- id: muse-spark-1\.2-contributor\n      api: openai-responses/,
  );
  assert.match(stagedModels.output, /contextWindow: 1048576/);
  const stagedConfig = transformConfigYaml(config, "stage");
  assert.equal(stagedConfig.changed, false);
  assert.equal(stagedConfig.output, config);
  assert.equal(transformModelsYaml(stagedModels.output, "stage").changed, false);
});

test("finalize removes Luna and replaces every selector including suffixes", () => {
  const staged = transformModelsYaml(models, "stage").output;
  const finalizedModels = transformModelsYaml(staged, "finalize");
  assert.doesNotMatch(finalizedModels.output, /- id: gpt-5\.6-luna/);
  assert.equal(
    (finalizedModels.output.match(/- id: muse-spark-1\.2-contributor/g) || [])
      .length,
    1,
  );
  const finalizedConfig = transformConfigYaml(config, "finalize");
  assert.doesNotMatch(finalizedConfig.output, /zg-newapi\/gpt-5\.6-luna/);
  assert.match(
    finalizedConfig.output,
    /task: zg-newapi\/muse-spark-1\.2-contributor:max/,
  );
  assert.match(
    finalizedConfig.output,
    /tiny: zg-newapi\/muse-spark-1\.2-contributor/,
  );
  assert.equal(transformModelsYaml(finalizedModels.output, "finalize").changed, false);
  assert.equal(transformConfigYaml(finalizedConfig.output, "finalize").changed, false);
});

test("rollback removes staged Muse and restores Luna selectors", () => {
  const finalizedModels = transformModelsYaml(models, "finalize").output;
  const rolledModels = transformModelsYaml(finalizedModels, "rollback");
  assert.doesNotMatch(rolledModels.output, /- id: muse-spark-1\.2-contributor/);
  assert.match(rolledModels.output, /- id: gpt-5\.6-luna/);
  const finalizedConfig = transformConfigYaml(config, "finalize").output;
  const rolledConfig = transformConfigYaml(finalizedConfig, "rollback");
  assert.equal(rolledConfig.output, config);
  assert.equal(transformModelsYaml(rolledModels.output, "rollback").changed, false);
});

test("channel phases are narrow and preserve unrelated settings", () => {
  const channel = {
    id: 48,
    name: "opencode-go-luna",
    type: 1,
    status: 1,
    base_url: "https://opencode.ai/zen/go",
    models: "gpt-5.6-luna",
    model_mapping: "{}",
    priority: 51,
    weight: 13,
    setting: "{\"proxy\":\"http://127.0.0.1:7897\"}",
  };
  const staged = planChannel(channel, "stage");
  assert.equal(staged.models, "gpt-5.6-luna,muse-spark-1.2-contributor");
  assert.equal(staged.priority, 51);
  assert.equal(staged.weight, 12);
  assert.equal(staged.test_model, "muse-spark-1.2-contributor");
  assert.equal(staged.setting, channel.setting);
  const finalized = planChannel(staged, "finalize");
  assert.equal(finalized.models, "muse-spark-1.2-contributor");
  assert.equal(finalized.name, "opencode-go-muse");
  assert.equal(finalized.weight, 12);
  const rolled = planChannel(staged, "rollback");
  assert.equal(rolled.models, "gpt-5.6-luna");
  assert.equal(rolled.name, "opencode-go-luna");
  assert.equal(rolled.weight, 20);
  assert.equal(rolled.test_model, "gpt-5.6-luna");
});

test("channel planner refuses identity drift and unrelated models", () => {
  const base = {
    id: 48,
    type: 1,
    status: 1,
    base_url: "https://opencode.ai/zen/go",
    models: "gpt-5.6-luna",
  };
  assert.throws(() => planChannel({ ...base, id: 49 }, "stage"), /channel id/);
  assert.throws(
    () => planChannel({ ...base, models: "gpt-5.6-luna,other-model" }, "stage"),
    /unrelated model/,
  );
  assert.throws(() => planChannel(base, "other"), /invalid phase/);
});

test("finalize proof requires a fresh no-fallback Canary revision", () => {
  const now = 2_000_000;
  const selector = "zg-newapi/muse-spark-1.2-contributor:max";
  const state = {
    revision: EXTENSION_REVISION,
    selectors: { [selector]: { result: "success", checkedAt: now - 1_000 } },
  };
  assert.equal(validateCanaryState(state, now).selector, selector);
  assert.throws(
    () => validateCanaryState({ ...state, revision: "routing-r4" }, now),
    /revision/,
  );
  assert.throws(
    () => validateCanaryState({ ...state, selectors: { [selector]: { result: "failed", checkedAt: now } } }, now),
    /successful/,
  );
  assert.throws(
    () => validateCanaryState({ ...state, selectors: { [selector]: { result: "success", checkedAt: 0 } } }, now),
    /stale/,
  );
});

test("stage and finalize require explicit contributor data-policy consent", () => {
  assert.throws(() => requireContributorConsent("stage", false), /data-policy/);
  assert.throws(() => requireContributorConsent("finalize", false), /data-policy/);
  assert.doesNotThrow(() => requireContributorConsent("stage", true));
  assert.doesNotThrow(() => requireContributorConsent("rollback", false));
});
