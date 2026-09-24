import { createHash } from "node:crypto";
import { execFile, spawn } from "node:child_process";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";
import { promisify } from "node:util";
import { acquireCanaryLease } from "./omp-model-routing-observability.js";

export const EXTENSION_REVISION = "2026.09.25-task-verification-r1";
export const POLICY_NAME = "task-verification-policy.json";
const exec = promisify(execFile);
const digest = (value) => createHash("sha256").update(value).digest("hex");
const canonical = (path) => {
  const value = realpathSync(path);
  return process.platform === "win32" ? value.toLowerCase() : value;
};
const normalize = (path) => path.replace(/\\/g, "/");
const inside = (root, path) => {
  const value = relative(root, path);
  return (
    !isAbsolute(value) &&
    value !== ".." &&
    !value.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`)
  );
};

export function readVerificationPolicy(agentDir, cwd) {
  if (!isAbsolute(String(agentDir ?? "")))
    throw new Error("agent-directory-unavailable");
  const path = join(agentDir, POLICY_NAME);
  if (statSync(path).size > 64 * 1024) throw new Error("policy-too-large");
  const raw = readFileSync(path, "utf8");
  const policy = JSON.parse(raw);
  if (policy.schema !== 1 || !Array.isArray(policy.projects))
    throw new Error("invalid-policy");
  const root = canonical(cwd);
  const projects = policy.projects.filter((p) => {
    if (typeof p?.root !== "string" || !isAbsolute(p.root)) return false;
    try {
      return canonical(p.root) === root;
    } catch {
      return false;
    }
  });
  if (projects.length !== 1) throw new Error("project-not-configured");
  const project = projects[0];
  if (!Array.isArray(project.checks) || project.checks.length > 12)
    throw new Error("invalid-checks");
  const ids = new Set();
  for (const check of project.checks) {
    if (
      !/^[a-z0-9-]{1,64}$/.test(check.id) ||
      ids.has(check.id) ||
      typeof check.command !== "string" ||
      !check.command ||
      !Array.isArray(check.args) ||
      check.args.some((x) => typeof x !== "string" || x.includes("\0")) ||
      !Array.isArray(check.paths) ||
      !check.paths.length ||
      check.paths.some(
        (x) =>
          typeof x !== "string" ||
          !x ||
          isAbsolute(x) ||
          x.split("/").includes("..") ||
          x.includes("\\"),
      ) ||
      !Number.isInteger(check.timeoutMs) ||
      check.timeoutMs < 100 ||
      check.timeoutMs > 120000 ||
      !["node-test", "unittest", "exit-code"].includes(check.evidence)
    )
      throw new Error("invalid-check");
    ids.add(check.id);
  }
  return { ...project, root, policyHash: digest(raw) };
}

async function git(cwd, args) {
  return (
    await exec("git", args, {
      cwd,
      windowsHide: true,
      timeout: 15000,
      maxBuffer: 2 * 1024 * 1024,
      encoding: "utf8",
    })
  ).stdout;
}

export async function snapshotWorkspace(cwd, baseHead) {
  const root = canonical(
    (await git(cwd, ["rev-parse", "--show-toplevel"])).trim(),
  );
  if (canonical(cwd) !== root) throw new Error("run-from-repository-root");
  const head = (await git(root, ["rev-parse", "HEAD"])).trim();
  const [diff, untracked] = await Promise.all([
    git(root, [
      "diff",
      "--name-only",
      "--no-renames",
      "-z",
      baseHead ?? head,
      "--",
    ]),
    git(root, ["ls-files", "--others", "--exclude-standard", "-z"]),
  ]);
  const files = [
    ...new Set((diff + untracked).split("\0").filter(Boolean)),
  ].sort();
  if (files.length > 300) throw new Error("too-many-changed-files");
  const hashes = {};
  for (const file of files) {
    const path = resolve(root, file);
    if (!inside(root, path)) throw new Error("invalid-change-path");
    try {
      if (!inside(root, canonical(path))) throw new Error("external-symlink");
      if (statSync(path).size > 8 * 1024 * 1024)
        throw new Error("changed-file-too-large");
      hashes[normalize(file)] = digest(readFileSync(path));
    } catch (error) {
      if (error.code === "ENOENT") hashes[normalize(file)] = "deleted";
      else throw error;
    }
  }
  return { root, head, hashes };
}

export function verificationPlan(project, baseline, current) {
  const files = [
    ...new Set([
      ...Object.keys(current.hashes),
      ...Object.keys(baseline?.hashes ?? {}),
    ]),
  ]
    .filter((file) => current.hashes[file] !== baseline?.hashes?.[file])
    .sort();
  const matches = (check, file) =>
    check.paths.some((path) =>
      path.endsWith("/") ? file.startsWith(path) : file === path,
    );
  const checks = project.checks.filter((check) =>
    files.some((file) => matches(check, file)),
  );
  // Markdown-only documentation needs no executable check under this policy.
  const documentation = files.filter((file) => /\.md$/i.test(file));
  const uncovered = files.filter(
    (file) =>
      !documentation.includes(file) &&
      !checks.some((check) => matches(check, file)),
  );
  return {
    files,
    checks,
    documentation,
    uncovered,
    fingerprint: digest(
      JSON.stringify({
        root: current.root,
        head: current.head,
        hashes: current.hashes,
        policy: project.policyHash,
      }),
    ),
  };
}

export function testEvidence(check, result) {
  if (result.timedOut) return { status: "failed", reason: "timeout" };
  if (result.aborted) return { status: "unverified", reason: "cancelled" };
  if (result.code !== 0)
    return {
      status: "failed",
      reason: result.startFailed ? "command-start-failed" : "nonzero-exit",
    };
  if (check.evidence === "exit-code") return { status: "passed" };
  const output = result.output ?? "";
  if (check.evidence === "node-test") {
    const passed = Number(output.match(/^# pass (\d+)\s*$/m)?.[1] ?? 0);
    const failed = Number(output.match(/^# fail (\d+)\s*$/m)?.[1] ?? 0);
    return passed > 0 && failed === 0
      ? { status: "passed", tests: passed }
      : { status: "unverified", reason: "no-passing-test-evidence" };
  }
  const total = Number(output.match(/Ran (\d+) tests? in /)?.[1] ?? 0);
  const skipped = Number(output.match(/OK \(skipped=(\d+)\)/)?.[1] ?? 0);
  return total > skipped && /^OK(?: \(skipped=\d+\))?\s*$/m.test(output)
    ? { status: "passed", tests: total - skipped }
    : { status: "unverified", reason: "no-passing-test-evidence" };
}

// No shell interpolation and no model-supplied command. Policy is user-owned,
// outside repositories. Raw command output stays in memory and is never sent
// to the model/logs: failing assertions may contain live credentials.
export function runVerificationCommand(check, cwd, signal) {
  return new Promise((resolveResult) => {
    if (signal?.aborted) return resolveResult({ code: null, aborted: true });
    const started = Date.now();
    let child;
    try {
      child = spawn(check.command, check.args, {
        cwd,
        windowsHide: true,
        shell: false,
        detached: process.platform !== "win32",
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch {
      return resolveResult({ code: null, startFailed: true });
    }
    let output = "";
    let timedOut = false;
    let aborted = false;
    let finished = false;
    let stopping = false;
    let forceTimer;
    const stop = () => {
      if (!child.pid || stopping || finished) return;
      stopping = true;
      if (process.platform === "win32") {
        // Kill only our own process tree, including test-runner children.
        execFile(
          "taskkill.exe",
          ["/PID", String(child.pid), "/T", "/F"],
          { windowsHide: true, timeout: 5000 },
          () => {},
        );
      } else {
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
      }
      forceTimer = setTimeout(() => {
        child.kill("SIGKILL");
        // Allow process handles to close before callers clean their workspace.
        forceTimer = setTimeout(() => settle(null), 500);
      }, 5500);
    };
    const cancel = () => {
      aborted = true;
      stop();
    };
    const timer = setTimeout(() => {
      timedOut = true;
      stop();
    }, check.timeoutMs);
    const settle = (code, startFailed = false) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      clearTimeout(forceTimer);
      signal?.removeEventListener("abort", cancel);
      resolveResult({
        code,
        output,
        timedOut,
        aborted,
        startFailed,
        durationMs: Date.now() - started,
      });
    };
    const capture = (chunk) => {
      output = (output + String(chunk)).slice(-64 * 1024);
    };
    child.stdout?.on("data", capture);
    child.stderr?.on("data", capture);
    child.on("error", () => settle(null, true));
    child.on("close", (code) => settle(code));
    signal?.addEventListener("abort", cancel, { once: true });
    if (signal?.aborted) cancel();
  });
}

export async function executeVerification(
  plan,
  project,
  {
    agentDir,
    signal,
    run = runVerificationCommand,
    snapshot = snapshotWorkspace,
    baseHead,
  } = {},
) {
  const release = acquireCanaryLease(
    join(agentDir, "task-verification", `${digest(project.root)}.lock`),
  );
  if (!release)
    return {
      status: "unverified",
      reason: "another-session-is-verifying",
      checks: [],
    };
  const results = [];
  const started = Date.now();
  try {
    for (const check of plan.checks) {
      if (signal?.aborted || Date.now() - started >= 120000) {
        results.push({
          id: check.id,
          status: "unverified",
          reason: signal?.aborted ? "cancelled" : "total-budget-exhausted",
        });
        continue;
      }
      const result = await run(
        {
          ...check,
          timeoutMs: Math.min(check.timeoutMs, 120000 - (Date.now() - started)),
        },
        project.root,
        signal,
      );
      results.push({
        id: check.id,
        ...testEvidence(check, result),
        exitCode: result.code,
        durationMs: result.durationMs,
      });
    }
    const after = await snapshot(project.root, baseHead);
    const afterPlan = verificationPlan(project, undefined, after);
    if (afterPlan.fingerprint !== plan.fingerprint)
      return {
        status: "unverified",
        reason: "files-changed-during-verification",
        checks: results,
      };
    const status = results.some((r) => r.status === "failed")
      ? "failed"
      : plan.uncovered.length || results.some((r) => r.status !== "passed")
        ? "unverified"
        : results.length
          ? "passed"
          : "not-required";
    return {
      status,
      checks: results,
      uncovered: plan.uncovered,
      documentation: plan.documentation,
      fingerprint: plan.fingerprint,
    };
  } finally {
    release();
  }
}

function safeReport(report) {
  return JSON.stringify(report);
}

export default function taskVerificationExtension(pi) {
  const agentDir = pi?.pi?.getAgentDir?.();
  let state;
  let running;
  let freshInput = true;
  async function planFor(ctx, currentState) {
    const project = readVerificationPolicy(agentDir, ctx.cwd);
    const current = await snapshotWorkspace(
      ctx.cwd,
      currentState?.baseline?.head,
    );
    return {
      project,
      plan: verificationPlan(project, currentState?.baseline, current),
    };
  }
  async function verify(ctx, signal, automatic = false) {
    const turn = state;
    if (!turn?.baseline)
      return {
        status: "unverified",
        reason: "project-or-git-baseline-unavailable",
        checks: [],
      };
    if (running)
      return {
        status: "unverified",
        reason: "verification-already-running",
        checks: [],
      };
    running = (async () => {
      try {
        const { project, plan } = await planFor(ctx, turn);
        if (automatic && !plan.files.length) return undefined;
        if (
          turn.last?.fingerprint === plan.fingerprint &&
          (automatic || turn.last.status === "passed")
        )
          return turn.last;
        turn.last = await executeVerification(plan, project, {
          agentDir,
          signal,
          baseHead: turn.baseline.head,
        });
        return turn.last;
      } catch {
        return {
          status: "unverified",
          reason: "policy-snapshot-or-runner-unavailable",
          checks: [],
        };
      }
    })();
    try {
      return await running;
    } finally {
      running = undefined;
    }
  }
  pi.on("input", (event) => {
    if (event.source !== "extension") freshInput = true;
  });
  pi.on("before_agent_start", async (_event, ctx) => {
    if (freshInput || state?.cwd !== ctx.cwd) {
      const turn = { cwd: ctx.cwd };
      state = turn;
      freshInput = false;
      try {
        readVerificationPolicy(agentDir, ctx.cwd);
        turn.baseline = await snapshotWorkspace(ctx.cwd);
      } catch (error) {
        // Unknown projects are opt-in. Broken configured verification must be visible.
        if (error.message !== "project-not-configured") {
          pi.sendMessage(
            {
              customType: "task-verification-unavailable",
              display: true,
              content:
                "Task verification is unavailable: check task-verification-policy.json and repository-root Git access. Changes remain unverified.",
            },
            { triggerTurn: false },
          );
        }
      }
    }
    return {
      message: {
        customType: "task-verification-policy",
        display: false,
        content:
          "For code changes, use task_verify before claiming completion. It runs only operator-configured checks; report passed, failed and unverified separately. A checkpoint's test notes are not test evidence. Fix failures and rerun; do not treat a missing check as success. When GitNexus MCP is available, list indexed repositories, then use context/impact for the current repository. GitNexus 1.6.5 on Windows disables full-text search: use rg for keyword search, not empty query results as proof of absence. Check index freshness and corroborate graph results with source; do not automatically reindex unrelated projects.",
      },
    };
  });
  pi.registerTool({
    name: "task_verify",
    label: "Verify task changes",
    description:
      "Plan or run configured checks for files changed during this turn. Returns executed check evidence and unverified files. Cannot accept arbitrary commands. Unknown projects remain unverified.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        action: { type: "string", enum: ["plan", "run", "status"] },
      },
      required: ["action"],
    },
    async execute(_id, params, signal, _onUpdate, ctx) {
      let report;
      if (params.action === "status") {
        report = state?.last ?? {
          status: "unverified",
          reason: "no-checks-run",
        };
        if (state?.last?.fingerprint) {
          try {
            const { plan } = await planFor(ctx, state);
            if (plan.fingerprint !== state.last.fingerprint)
              report = {
                status: "unverified",
                reason: "files-changed-since-verification",
              };
          } catch {
            report = { status: "unverified", reason: "snapshot-unavailable" };
          }
        }
      } else if (params.action === "plan") {
        try {
          const { plan } = await planFor(ctx, state);
          report = {
            status: "unverified",
            files: plan.files,
            checks: plan.checks.map((c) => ({
              id: c.id,
              command: c.command,
              args: c.args,
            })),
            uncovered: plan.uncovered,
          };
        } catch {
          report = {
            status: "unverified",
            reason: "project-or-policy-unavailable",
          };
        }
      } else if (params.action === "run") report = await verify(ctx, signal);
      else report = { status: "unverified", reason: "invalid-action" };
      return {
        isError: report.status === "failed",
        content: [{ type: "text", text: safeReport(report) }],
        details: report,
      };
    },
  });
  pi.on("session_stop", async (event, ctx) => {
    if (event?.willContinue || !state?.baseline || running) return;
    const turn = state;
    // OMP 18 print mode emits session_stop, not agent_end. Await the bounded
    // check; deferred context timers can be cancelled during process teardown.
    const report = await verify(ctx, event?.signal, true);
    if (report && state === turn)
      pi.sendMessage(
        {
          customType: "task-verification-result",
          content: safeReport(report),
          display: true,
        },
        { triggerTurn: false },
      );
  });
}
