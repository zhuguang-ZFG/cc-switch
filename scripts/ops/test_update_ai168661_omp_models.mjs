import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  transformModelsYaml,
  updateModelsFile,
} from "./update_ai168661_omp_models.mjs";

const fixture = [
  "providers:",
  "  zg-newapi:",
  "    models:",
  "    - id: deepseek-v4-flash",
  "      contextWindow: 380000",
  "      maxTokens: 128000",
  "    - id: grok-4.5",
  "      contextWindow: 500000",
  "      maxTokens: 33000",
  "  other:",
  "    models: []",
  "",
].join("\n");

test("adds the two ai.168661 models once", () => {
  const first = transformModelsYaml(fixture);
  assert.equal(first.changed, true);
  assert.match(first.output, /- id: deepseek-v4-flash-0731/);
  assert.match(first.output, /- id: hy3/);
  assert.match(first.output, /input:\n      - text\n      - image/);

  const second = transformModelsYaml(first.output);
  assert.equal(second.changed, false);
  assert.equal(second.output, first.output);
});

test("refuses a missing anchor", () => {
  assert.throws(
    () => transformModelsYaml(fixture.replace("    - id: grok-4.5\n", "")),
    /expected one anchor model/,
  );
});

test("dry-run does not write and apply creates an exact backup", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "ai168661-models-"));
  const modelsPath = path.join(directory, "models.yml");
  fs.writeFileSync(modelsPath, fixture);

  const dryRun = updateModelsFile(modelsPath, false);
  assert.equal(dryRun.changed, true);
  assert.equal(dryRun.applied, false);
  assert.equal(fs.readFileSync(modelsPath, "utf8"), fixture);

  const applied = updateModelsFile(modelsPath, true);
  assert.equal(applied.applied, true);
  assert.ok(applied.backup);
  assert.equal(fs.readFileSync(path.join(directory, applied.backup), "utf8"), fixture);
  assert.match(fs.readFileSync(modelsPath, "utf8"), /- id: hy3/);
});
