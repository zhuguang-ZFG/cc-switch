import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { cases, summarizeReports, observedUsage } from "./bugfix-cases.mjs";
import { runVerificationCommand as run } from "./omp-task-verification.js";
test("usage requires complete successful events and distinguishes zero price from free billing", () => {
  const result = {
    code: 0,
    output: JSON.stringify({
      type: "message_end",
      message: {
        role: "assistant",
        usage: { totalTokens: 12, cost: { total: 0.01 } },
      },
    }),
  };
  assert.deepEqual(observedUsage(result), {
    tokens: 12,
    estimatedCostUsd: 0.01,
  });
  assert.equal(
    observedUsage({ ...result, outputTruncated: true }).tokens,
    null,
  );
  assert.equal(observedUsage({ ...result, code: 1 }).tokens, null);
  assert.equal(
    observedUsage({ ...result, output: result.output.replace("0.01", "0") })
      .estimatedCostUsd,
    null,
  );
});
for (const c of cases)
  test(`catalog ${c.id}: broken fails, oracle passes, weakened tests fail holdout`, async () => {
    const root = mkdtempSync(join(tmpdir(), "omp-catalog-"));
    const check = {
      command: process.execPath,
      args: ["--test", "--test-reporter=tap", "regression.test.cjs"],
      timeoutMs: 5000,
    };
    const holdout = {
      ...check,
      args: [
        "-e",
        "const assert=require('node:assert/strict');const subject=require('./subject.cjs');" +
          c.holdout,
      ],
    };
    try {
      writeFileSync(join(root, "subject.cjs"), c.buggy);
      writeFileSync(join(root, "regression.test.cjs"), c.regression);
      assert.equal((await run(check, root)).code, 1);
      writeFileSync(
        join(root, "regression.test.cjs"),
        "const test=require('node:test');test('cheat',()=>{});",
      );
      assert.equal((await run(check, root)).code, 0);
      assert.equal((await run(holdout, root)).code, 1);
      writeFileSync(join(root, "regression.test.cjs"), c.regression);
      writeFileSync(join(root, "subject.cjs"), c.corrected);
      assert.equal((await run(check, root)).code, 0);
      assert.equal((await run(holdout, root)).code, 0);
    } finally {
      rmSync(root, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 100,
      });
    }
  });
test("evaluation summary excludes scripted controls and leaves cost unknown", () => {
  const summary = summarizeReports([
    { mode: "control", ok: true },
    { mode: "live", ok: true, durationMs: 100, unintendedEdits: [] },
    { mode: "live", ok: false, durationMs: 300, unintendedEdits: ["test"] },
  ]);
  assert.equal(summary.successRate, 0.5);
  assert.equal(summary.meanDurationMs, 200);
  assert.equal(summary.unintendedEditRate, 0.5);
  assert.equal(summary.costUsd, null);
});
