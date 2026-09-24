import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import extension, {
  POLICY_NAME,
  executeVerification,
  readVerificationPolicy,
  runVerificationCommand,
  snapshotWorkspace,
  testEvidence,
  verificationPlan,
  failureDiagnostic,
  repairContinuation,
  snapshotVerificationInputs,
  verificationInputChanges,
} from "./omp-task-verification.js";
import { acquireCanaryLease } from "./omp-model-routing-observability.js";
import { createHash } from "node:crypto";

test(
  "integrity snapshots catch assertion edits, skips, deletions, additions and committed changes",
  fixture(async (root) => {
    init(root);
    const file = join(root, "regression.test.cjs");
    writeFileSync(file, "test('works',()=>assert.equal(1,1));");
    const project = { root, checks: [] };
    const baseline = await snapshotVerificationInputs(project);
    assert.deepEqual(
      verificationInputChanges(
        baseline,
        await snapshotVerificationInputs(project),
      ),
      [],
    );
    for (const text of [
      "test.skip('works',()=>assert.equal(1,1));",
      "test('works',()=>{});",
    ]) {
      writeFileSync(file, text);
      assert.equal(
        verificationInputChanges(
          baseline,
          await snapshotVerificationInputs(project),
        )[0].reason,
        "verification-input-modified",
      );
    }
    execFileSync("git", ["add", "regression.test.cjs"], {
      cwd: root,
      stdio: "ignore",
    });
    execFileSync(
      "git",
      [
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@localhost",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=none",
        "commit",
        "-m",
        "test edit",
      ],
      { cwd: root, stdio: "ignore" },
    );
    assert.equal(
      verificationInputChanges(
        baseline,
        await snapshotVerificationInputs(project),
      ).length,
      1,
    );
    rmSync(file);
    assert.equal(
      verificationInputChanges(
        baseline,
        await snapshotVerificationInputs(project),
      )[0].reason,
      "verification-input-deleted",
    );
    writeFileSync(join(root, "new.test.cjs"), "test.only('new',()=>{});");
    assert.equal(
      verificationInputChanges(
        baseline,
        await snapshotVerificationInputs(project),
      ).length,
      2,
    );
  }),
);

test(
  "pinned policy rejects replacement commands before execution and invalidates cached success",
  fixture(async (root) => {
    init(root);
    const agent = join(root, ".agent");
    policy(root, agent);
    const handlers = new Map();
    let tool;
    extension({
      pi: { getAgentDir: () => agent },
      on: (n, f) => handlers.set(n, f),
      registerTool: (t) => (tool = t),
      sendMessage: () => {},
    });
    const ctx = { cwd: root };
    await handlers.get("before_agent_start")({}, ctx);
    writeFileSync(join(root, "subject.js"), "changed");
    assert.equal(
      (await tool.execute("a", { action: "run" }, null, null, ctx)).details
        .status,
      "passed",
    );
    policy(root, agent, [
      check({
        args: ["-e", "require('node:fs').writeFileSync('executed.txt','bad')"],
      }),
    ]);
    for (const action of ["status", "run", "plan"]) {
      const r = await tool.execute("b", { action }, null, null, ctx);
      assert.equal(
        r.details.reason,
        "verification-policy-changed-review-required",
      );
    }
    assert.throws(() => readFileSync(join(root, "executed.txt")), {
      code: "ENOENT",
    });
  }),
);

test(
  "test edits refuse automatic repair and do not disappear on extension continuation",
  fixture(async (root) => {
    init(root);
    const agent = join(root, ".agent");
    policy(root, agent);
    writeFileSync(join(root, "regression.test.cjs"), "original assertions");
    const handlers = new Map();
    let tool;
    const messages = [];
    extension({
      pi: { getAgentDir: () => agent },
      on: (n, f) => handlers.set(n, f),
      registerTool: (t) => (tool = t),
      sendMessage: (m) => messages.push(m),
    });
    const ctx = { cwd: root };
    await handlers.get("before_agent_start")({}, ctx);
    writeFileSync(join(root, "regression.test.cjs"), "test.skip");
    handlers.get("input")({ source: "extension" });
    await handlers.get("before_agent_start")({}, ctx);
    assert.equal(await handlers.get("session_stop")({}, ctx), undefined);
    assert.equal(
      JSON.parse(messages[0].content).reason,
      "verification-inputs-changed-review-required",
    );
    assert.equal(
      (await tool.execute("a", { action: "run" }, null, null, ctx)).details
        .status,
      "unverified",
    );
  }),
);

test(
  "scoped checks explicitly leave the rest of a project unverified",
  fixture(async (root) => {
    init(root);
    mkdirSync(join(root, "included"));
    writeFileSync(join(root, "included", "code.js"), "a");
    const project = {
      root,
      workspacePaths: ["included/"],
      checks: [check({ paths: ["included/"] })],
      policyHash: "fixture",
    };
    const baseline = await snapshotWorkspace(
      root,
      undefined,
      project.workspacePaths,
    );
    writeFileSync(join(root, "included", "code.js"), "b");
    writeFileSync(join(root, "outside.js"), "outside scope");
    const current = await snapshotWorkspace(
      root,
      baseline.head,
      project.workspacePaths,
    );
    const plan = verificationPlan(project, baseline, current);
    assert.deepEqual(plan.files, ["included/code.js"]);
    const report = await executeVerification(plan, project, {
      agentDir: root,
      baseHead: baseline.head,
    });
    assert.equal(report.status, "unverified");
    assert.equal(report.scopedStatus, "passed");
    assert.deepEqual(report.scope, ["included/"]);
  }),
);

test(
  "ignored explicit runner edits and inputs changed during verification invalidate evidence",
  fixture(async (root) => {
    init(root);
    mkdirSync(join(root, ".agent"));
    const runner = join(root, ".agent", "runner.cjs");
    writeFileSync(runner, "process.exit(0)");
    const project = { root, checks: [check({ args: [".agent/runner.cjs"] })] };
    const before = await snapshotVerificationInputs(project);
    writeFileSync(runner, "process.exit(1)");
    assert.equal(
      verificationInputChanges(
        before,
        await snapshotVerificationInputs(project),
      )[0].path,
      ".agent/runner.cjs",
    );
    const agent = join(root, ".agent");
    policy(root, agent, [
      check({
        args: [
          "-e",
          "require('node:fs').writeFileSync('.agent/task-verification-policy.json','{}')",
        ],
      }),
    ]);
    const handlers = new Map();
    let tool;
    extension({
      pi: { getAgentDir: () => agent },
      on: (n, f) => handlers.set(n, f),
      registerTool: (t) => (tool = t),
      sendMessage: () => {},
    });
    const ctx = { cwd: root };
    await handlers.get("before_agent_start")({}, ctx);
    writeFileSync(join(root, "subject.js"), "changed");
    const report = await tool.execute("a", { action: "run" }, null, null, ctx);
    assert.equal(
      report.details.reason,
      "verification-policy-changed-review-required",
    );
  }),
);

function fixture(fn) {
  return async () => {
    const root = mkdtempSync(join(tmpdir(), "omp-verify-test-"));
    try {
      await fn(root);
    } finally {
      rmSync(root, {
        recursive: true,
        force: true,
        maxRetries: 10,
        retryDelay: 100,
      });
    }
  };
}
function init(root) {
  const git = (args) =>
    execFileSync("git", args, { cwd: root, stdio: "ignore" });
  git(["init"]);
  writeFileSync(join(root, ".gitignore"), ".agent/\n");
  writeFileSync(join(root, "subject.js"), "export const value = 1;\n");
  git(["add", "subject.js", ".gitignore"]);
  git([
    "-c",
    "user.name=Fixture",
    "-c",
    "user.email=fixture@localhost",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=none",
    "commit",
    "-m",
    "fixture",
  ]);
}
function policy(root, agent, checks = [check()]) {
  mkdirSync(agent, { recursive: true });
  writeFileSync(
    join(agent, POLICY_NAME),
    JSON.stringify({ schema: 1, projects: [{ root, checks }] }),
  );
}
function check(extra = {}) {
  return {
    id: "fixture",
    paths: ["subject.js"],
    command: process.execPath,
    args: ["-e", "process.exit(0)"],
    timeoutMs: 2000,
    evidence: "exit-code",
    ...extra,
  };
}

test(
  "policy requires an exact configured project and validated check contract",
  fixture((root) => {
    const agent = join(root, "agent");
    policy(root, agent);
    assert.equal(readVerificationPolicy(agent, root).checks.length, 1);
    const data = JSON.parse(readFileSync(join(agent, POLICY_NAME), "utf8"));
    data.projects.unshift({ root: join(root, "no-longer-exists"), checks: [] });
    writeFileSync(join(agent, POLICY_NAME), JSON.stringify(data));
    assert.equal(readVerificationPolicy(agent, root).checks.length, 1);
    assert.throws(() => readVerificationPolicy(agent, agent), /not-configured/);
    policy(root, agent, [check({ paths: ["../outside"] })]);
    assert.throws(() => readVerificationPolicy(agent, root), /invalid-check/);
    policy(root, agent, [check({ timeoutMs: 999999 })]);
    assert.throws(() => readVerificationPolicy(agent, root), /invalid-check/);
    writeFileSync(join(agent, POLICY_NAME), "{");
    assert.throws(() => readVerificationPolicy(agent, root));
  }),
);

test("planner includes reverted baseline changes, excludes unchanged user work and reports uncovered files", () => {
  const project = { checks: [check()], policyHash: "policy" };
  const baseline = { hashes: { "existing.js": "same", "subject.js": "dirty" } };
  const current = {
    root: "fixture",
    head: "head",
    hashes: { "existing.js": "same", "unknown.py": "new", "docs.md": "new" },
  };
  const plan = verificationPlan(project, baseline, current);
  assert.deepEqual(plan.files, ["docs.md", "subject.js", "unknown.py"]);
  assert.equal(plan.checks.length, 1);
  assert.deepEqual(plan.uncovered, ["unknown.py"]);
  assert.deepEqual(plan.documentation, ["docs.md"]);
});

test("test evidence rejects zero tests, all-skipped tests, failures, timeout and cancellation", () => {
  const vitest = check({ evidence: "vitest" });
  assert.equal(
    testEvidence(vitest, { code: 0, output: " Tests 502 passed (502)\n" })
      .tests,
    502,
  );
  assert.equal(
    testEvidence(vitest, { code: 0, output: " Tests 4 skipped (4)\n" }).status,
    "unverified",
  );
  assert.equal(
    testEvidence(vitest, {
      code: 0,
      output: " Tests 1 failed | 3 passed (4)\n",
    }).status,
    "unverified",
  );
  const node = check({ evidence: "node-test" });
  assert.equal(
    testEvidence(node, { code: 0, output: "# pass 0\n# fail 0\n" }).status,
    "unverified",
  );
  assert.equal(
    testEvidence(node, { code: 0, output: "# pass 2\n# fail 0\n" }).tests,
    2,
  );
  assert.equal(
    testEvidence(node, { code: 1, output: "secret data" }).status,
    "failed",
  );
  assert.equal(
    testEvidence(node, { code: 0, timedOut: true }).reason,
    "timeout",
  );
  assert.equal(
    testEvidence(node, { code: null, aborted: true }).reason,
    "cancelled",
  );
  const py = check({ evidence: "unittest" });
  assert.equal(
    testEvidence(py, {
      code: 0,
      output: "Ran 2 tests in 0.02s\n\nOK (skipped=2)\n",
    }).status,
    "unverified",
  );
  assert.equal(
    testEvidence(py, { code: 0, output: "Ran 2 tests in 0.02s\n\nOK\n" }).tests,
    2,
  );
});

test(
  "failure diagnostics expose only existing code locations and categories",
  fixture((root) => {
    const file = join(root, "regression.test.js");
    writeFileSync(file, "// fixture");
    const output = `  location: '${file}:1:2'\nerror: secret=do-not-emit\ncode: 'ERR_ASSERTION'\n  location: '/outside/secret.js:8:1'\n`;
    const diagnostic = failureDiagnostic({ code: 1, output }, root);
    assert.equal(diagnostic.category, "assertion-failure");
    assert.deepEqual(diagnostic.locations, [
      { path: "regression.test.js", line: 1 },
    ]);
    assert.doesNotMatch(JSON.stringify(diagnostic), /do-not-emit|outside/);
    assert.equal(
      failureDiagnostic(
        { code: 1, output: "ModuleNotFoundError: private" },
        root,
      ).category,
      "missing-dependency",
    );
    assert.equal(
      failureDiagnostic({ code: 1, output: "ERR_ASSERTION HTTP 403" }, root)
        .category,
      "permission-or-auth",
    );
    assert.equal(
      failureDiagnostic({ code: null, timedOut: true, output }, root).category,
      "timeout",
    );
  }),
);

test("repair continuation is opt-in, code-failure-only and bounded", () => {
  const report = {
    status: "failed",
    checks: [
      { status: "failed", diagnostic: { category: "assertion-failure" } },
    ],
    uncovered: [],
  };
  const event = { last_assistant_message: { stopReason: "stop" } };
  assert.equal(
    repairContinuation(report, { repairOnFailure: true }, event, false)
      .continue,
    true,
  );
  assert.equal(repairContinuation(report, {}, event, false), undefined);
  assert.equal(
    repairContinuation(report, { repairOnFailure: true }, event, true),
    undefined,
  );
  assert.equal(
    repairContinuation(
      report,
      { repairOnFailure: true },
      { ...event, stop_hook_active: true },
      false,
    ),
    undefined,
  );
  assert.equal(
    repairContinuation(
      { ...report, uncovered: ["unknown.js"] },
      { repairOnFailure: true },
      event,
      false,
    ),
    undefined,
  );
  for (const category of [
    "permission-or-auth",
    "missing-dependency",
    "timeout",
    "command-failure",
  ]) {
    assert.equal(
      repairContinuation(
        { ...report, checks: [{ status: "failed", diagnostic: { category } }] },
        { repairOnFailure: true },
        event,
        false,
      ),
      undefined,
    );
  }
  const controller = new AbortController();
  controller.abort();
  assert.equal(
    repairContinuation(
      report,
      { repairOnFailure: true },
      { ...event, signal: controller.signal },
      false,
    ),
    undefined,
  );
});

test(
  "real git snapshots handle changed, deleted, staged and untracked files with spaces",
  fixture(async (root) => {
    init(root);
    const before = await snapshotWorkspace(root);
    writeFileSync(join(root, "subject.js"), "changed\n");
    writeFileSync(join(root, "with spaces.js"), "new\n");
    const after = await snapshotWorkspace(root, before.head);
    assert.deepEqual(Object.keys(after.hashes), [
      "subject.js",
      "with spaces.js",
    ]);
    execFileSync("git", ["add", "subject.js"], { cwd: root });
    assert.equal(
      (await snapshotWorkspace(root, before.head)).hashes["subject.js"],
      after.hashes["subject.js"],
    );
    rmSync(join(root, "subject.js"));
    assert.equal(
      (await snapshotWorkspace(root, before.head)).hashes["subject.js"],
      "deleted",
    );
  }),
);

test(
  "runner bounds execution and respects cancellation",
  fixture(async (root) => {
    const passed = await runVerificationCommand(check(), root);
    assert.equal(passed.code, 0);
    const hanging = check({
      args: ["-e", "setInterval(()=>{},1000)"],
      timeoutMs: 200,
    });
    const timeout = await runVerificationCommand(hanging, root);
    assert.equal(timeout.timedOut, true);
    assert.ok(timeout.durationMs < 6500);
    const controller = new AbortController();
    controller.abort();
    const cancelled = await runVerificationCommand(
      hanging,
      root,
      controller.signal,
    );
    assert.equal(cancelled.aborted, true);
  }),
);

test(
  "verification never promotes unknown coverage or mid-run changes to passed; hides test secrets",
  fixture(async (root) => {
    const project = { root, checks: [check()], policyHash: "policy" };
    const current = {
      root,
      head: "head",
      hashes: { "subject.js": "a", "unknown.py": "b" },
    };
    const plan = verificationPlan(project, undefined, current);
    const options = {
      agentDir: root,
      run: async () => ({ code: 0, output: "API_KEY=secret-must-not-leak" }),
      snapshot: async () => current,
    };
    const report = await executeVerification(plan, project, options);
    assert.equal(report.status, "unverified");
    assert.doesNotMatch(JSON.stringify(report), /secret-must-not-leak/);
    const stale = await executeVerification(plan, project, {
      ...options,
      snapshot: async () => ({
        ...current,
        hashes: { "subject.js": "different" },
      }),
    });
    assert.equal(stale.reason, "files-changed-during-verification");
    const failed = await executeVerification(plan, project, {
      ...options,
      run: async () => ({ code: 1 }),
    });
    assert.equal(failed.status, "failed");
  }),
);

test(
  "concurrent sessions cannot run duplicate suites",
  fixture(async (root) => {
    const project = { root, checks: [check()], policyHash: "policy" };
    const lease = join(
      root,
      "task-verification",
      `${createHash("sha256").update(root).digest("hex")}.lock`,
    );
    const release = acquireCanaryLease(lease);
    let ran = false;
    try {
      const report = await executeVerification({ checks: [check()] }, project, {
        agentDir: root,
        run: async () => {
          ran = true;
        },
      });
      assert.equal(report.status, "unverified");
      assert.equal(ran, false);
    } finally {
      release();
    }
  }),
);

test(
  "native extension lifecycle runs real checks and invalidates stale success without retry loops",
  fixture(async (root) => {
    init(root);
    const agent = join(root, ".agent");
    policy(root, agent);
    const handlers = new Map();
    const messages = [];
    let tool;
    extension({
      pi: { getAgentDir: () => agent },
      on: (name, fn) => handlers.set(name, fn),
      registerTool: (value) => {
        tool = value;
      },
      sendMessage: (...args) => messages.push(args),
    });
    const ctx = { cwd: root };
    await handlers.get("before_agent_start")({}, ctx);
    writeFileSync(join(root, "subject.js"), "changed\n");
    const first = tool.execute("1", { action: "run" }, undefined, null, ctx);
    const duplicate = await tool.execute(
      "duplicate",
      { action: "run" },
      undefined,
      null,
      ctx,
    );
    assert.equal(duplicate.details.reason, "verification-already-running");
    const passed = await first;
    assert.equal(passed.details.status, "passed");
    handlers.get("input")({ source: "extension" });
    await handlers.get("before_agent_start")({}, ctx);
    assert.equal(
      (await tool.execute("2", { action: "status" }, undefined, null, ctx))
        .details.status,
      "passed",
    );
    writeFileSync(join(root, "subject.js"), "changed again\n");
    assert.equal(
      (await tool.execute("3", { action: "status" }, undefined, null, ctx))
        .details.status,
      "unverified",
    );
    await handlers.get("session_stop")({}, ctx);
    assert.equal(messages.length, 1);
    assert.equal(messages[0][1].triggerTurn, false);
    assert.equal(JSON.parse(messages[0][0].content).status, "passed");
  }),
);
