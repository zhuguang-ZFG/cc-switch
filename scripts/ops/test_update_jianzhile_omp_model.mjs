import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  transformModelsYaml,
  updateModelsFile,
} from "./update_jianzhile_omp_model.mjs";

const source = [
  "providers:",
  "  zg-newapi:",
  "    api: openai-completions",
  "    models:",
  "    - id: gpt-5.6-sol",
  "      contextWindow: 400000",
  "    - id: gpt-5.6-terra",
  "      contextWindow: 272000",
  "  other:",
  "    models: []",
  "",
].join("\n");

test("inserts the ch91 canary after the aggregate Sol model and is idempotent", () => {
  const first = transformModelsYaml(source);
  assert.equal(first.changed, true);
  assert.match(first.output, /- id: gpt-5\.6-sol[\s\S]*- id: jianzhile-codex-gpt-5\.6-sol[\s\S]*- id: gpt-5\.6-terra/);
  const second = transformModelsYaml(first.output);
  assert.equal(second.changed, false);
  assert.equal(second.output, first.output);
});

test("apply creates a byte-identical backup and writes the target once", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "jianzhile-omp-model-"));
  const target = path.join(directory, "models.yml");
  fs.writeFileSync(target, source);
  try {
    const result = updateModelsFile(target, true);
    assert.equal(result.applied, true);
    assert.deepEqual(
      fs.readFileSync(path.join(directory, result.backup)),
      Buffer.from(source),
    );
    assert.equal(result.afterHash, updateModelsFile(target, false).afterHash);
    assert.equal(
      (fs.readFileSync(target, "utf8").match(/jianzhile-codex-gpt-5\.6-sol/g) || []).length,
      1,
    );
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
