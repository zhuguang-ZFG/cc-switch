import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));

function readAgent(name) {
  return readFileSync(join(here, "omp-agents", `${name}.md`), "utf8");
}

for (const name of ["hutuji-worker", "dsv4pro-worker"]) {
  test(`${name} inherits the task role without stale model assumptions`, () => {
    const source = readAgent(name);
    assert.match(source, new RegExp(`^name: ${name}$`, "m"));
    assert.match(source, /^model:\r?\n  - "@task"$/m);
    assert.doesNotMatch(source, /deepseek|gpt-5\.6-luna|zg-newapi\//i);
    assert.match(source, /gate/i);
    assert.match(source, /dirty(?:[- ]|\s)+worktrees?/i);
    assert.match(source, /hardware|HIL/i);
    assert.match(source, /token files/i);
  });
}
