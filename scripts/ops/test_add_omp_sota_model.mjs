import assert from "node:assert/strict";
import test from "node:test";

import { buildSotaModelId, transformModelsYaml } from "./add_omp_sota_model.mjs";

const SOURCE = `providers:
  zg-newapi:
    baseUrl: http://127.0.0.1:3002/v1
    models:
    - id: deepseek-v4-flash
      contextWindow: 1000000
      maxTokens: 131072
  zg-newapi-anthropic:
    baseUrl: http://127.0.0.1:3003
    models:
    - id: claude-opus-5
      compactionModel: zg-newapi/deepseek-v4-flash
      name: Claude Opus 5
      reasoning: true
      input:
      - text
      - image
      contextWindow: 200000
      maxTokens: 128000
  other:
    models:
    - id: keep
`;

test("builds the marked alias from a verified base model", () => {
  assert.equal(buildSotaModelId("claude-opus-5"), "omp-sota-claude-opus-5");
});

test("clones capability metadata into the marked target model", () => {
  const result = transformModelsYaml(SOURCE, { baseModel: "claude-opus-5" });
  assert.equal(result.changed, true);
  const target = result.output.slice(
    result.output.indexOf("  zg-newapi:"),
    result.output.indexOf("  zg-newapi-anthropic:"),
  );
  assert.match(target, /id: omp-sota-claude-opus-5/);
  assert.match(target, /compactionModel: zg-newapi\/deepseek-v4-flash/);
  assert.match(target, /name: OMP SOTA escalation \(claude-opus-5, bounded review\)/);
  assert.match(target, /input:\n      - text\n      - image/);
  assert.match(target, /contextWindow: 200000/);
  assert.match(target, /maxTokens: 16384/);
  assert.equal(result.output.indexOf("omp-sota-claude-opus-5") < result.output.indexOf("other:"), true);
});

test("is idempotent and rejects unmarked ids", () => {
  const first = transformModelsYaml(SOURCE);
  const second = transformModelsYaml(first.output);
  assert.equal(second.changed, false);
  assert.throws(() => transformModelsYaml(SOURCE, { modelId: "claude-opus-5-sota" }), /prefix/);
});

test("repairs existing alias metadata and accepts an explicit output budget", () => {
  const first = transformModelsYaml(SOURCE);
  const drifted = first.output.replace("      maxTokens: 16384", "      maxTokens: 128000");
  const repaired = transformModelsYaml(drifted, { maxTokens: 8192 });
  assert.equal(repaired.changed, true);
  assert.match(repaired.output, /id: omp-sota-claude-opus-5[\s\S]*maxTokens: 8192/);
  assert.equal(transformModelsYaml(repaired.output, { maxTokens: 8192 }).changed, false);
  assert.throws(() => transformModelsYaml(SOURCE, { maxTokens: 0 }), /positive integer/);
});

test("rejects missing or duplicate source models", () => {
  assert.throws(() => transformModelsYaml(SOURCE, { baseModel: "missing" }), /source model/);
  const duplicate = SOURCE.replace(
    "    - id: claude-opus-5\n      compactionModel:",
    "    - id: claude-opus-5\n      contextWindow: 200000\n      maxTokens: 128000\n    - id: claude-opus-5\n      compactionModel:",
  );
  assert.throws(() => transformModelsYaml(duplicate), /source model/);
});

test("rejects a source model without required capability metadata", () => {
  const incomplete = SOURCE.replace("      maxTokens: 128000\n  other:", "  other:");
  assert.throws(() => transformModelsYaml(incomplete), /capability metadata/);
});
