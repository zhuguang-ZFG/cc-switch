// Default: deterministic native control-loop tests. --live: one real model task.
// Neither mode changes production configuration; all edits stay in a temp repo.
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { runVerificationCommand } from "./omp-task-verification.js";
import { cases, summarizeReports, observedUsage } from "./bugfix-cases.mjs";

const source = dirname(fileURLToPath(import.meta.url));
const hash = (path) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");
const live = process.argv.includes("--live");
const option = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i < 0 ? fallback : process.argv[i + 1];
};
const selected = option("--case", "pagination");
const repeat = Number(option("--repeat", "1"));
if (
  !Number.isInteger(repeat) ||
  repeat < 1 ||
  repeat > 3 ||
  !(selected === "all" || cases.some((c) => c.id === selected))
)
  throw new Error(
    "Use --case pagination|empty-response|config-preservation|all and --repeat 1..3",
  );
const reportPath = option("--report", undefined);
if (process.argv.includes("--report") && !reportPath)
  throw new Error("Missing report path");
const selectedCases = cases.filter(
  (c) => selected === "all" || c.id === selected,
);
const revision = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: source,
  encoding: "utf8",
}).trim();
const implementationHash = createHash("sha256")
  .update(readFileSync(join(source, "omp-task-verification.js")))
  .update(readFileSync(join(source, "omp-problem-solving.js")))
  .update(readFileSync(join(source, "bugfix-cases.mjs")))
  .update(readFileSync(fileURLToPath(import.meta.url)))
  .digest("hex");
const reports = [];
const started = Date.now();
const jobs = live
  ? Array.from({ length: repeat }, () =>
      selectedCases.map((fixture) => ({ fixture, scenario: "live-kimi" })),
    ).flat()
  : ["repair", "stuck", "permission", "tamper"].map((scenario) => ({
      fixture: cases[0],
      scenario,
    }));
for (const { fixture, scenario } of jobs) {
  if (Date.now() - started > 600000) {
    reports.push({
      mode: live ? "live" : "control",
      caseId: fixture.id,
      ok: false,
      reason: "total-budget-exhausted",
      durationMs: 0,
      unintendedEdits: [],
      costUsd: null,
    });
    continue;
  }
  const { buggy, corrected, regression } = fixture;
  const base = mkdtempSync(join(tmpdir(), "omp-bugfix-bench-"));
  const root = join(base, "repo"),
    agent = join(base, "agent");
  const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
  let server;
  try {
    mkdirSync(root);
    mkdirSync(join(agent, "extensions"), { recursive: true });
    writeFileSync(join(root, "subject.cjs"), buggy);
    writeFileSync(join(root, "regression.test.cjs"), regression);
    const git = (args) =>
      execFileSync("git", args, { cwd: root, stdio: "ignore" });
    git(["init"]);
    git(["add", "."]);
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
    const baseHead = execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
    }).trim();
    for (const name of [
      "omp-problem-solving.js",
      "omp-task-verification.js",
      "omp-sota-escalation.js",
      "omp-model-routing-observability.js",
    ]) {
      copyFileSync(join(source, name), join(agent, "extensions", name));
    }
    let requests = 0,
      sawRepairContext = false;
    if (!live) {
      server = createServer(async (request, response) => {
        let body = "";
        for await (const chunk of request) body += chunk;
        const payload = JSON.parse(body);
        requests++;
        sawRepairContext ||= JSON.stringify(payload.messages).includes(
          "single automatic repair continuation",
        );
        const patch =
          requests === 1
            ? buggy + "// attempted repair\n"
            : requests === 3 && ["repair", "tamper"].includes(scenario)
              ? corrected
              : undefined;
        const delta = patch
          ? {
              role: "assistant",
              tool_calls: [
                {
                  index: 0,
                  id: `call_${requests}`,
                  type: "function",
                  function: {
                    name: "write",
                    arguments: JSON.stringify({
                      path:
                        scenario === "tamper" && requests === 3
                          ? "regression.test.cjs"
                          : "subject.cjs",
                      content:
                        scenario === "tamper" && requests === 3
                          ? "const test=require('node:test');test('weakened',()=>{});"
                          : patch,
                    }),
                  },
                },
              ],
            }
          : { role: "assistant", content: "FIXTURE_DONE" };
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        for (const [value, finish] of [
          [delta, null],
          [{}, patch ? "tool_calls" : "stop"],
        ])
          response.write(
            `data: ${JSON.stringify({ id: "fixture", object: "chat.completion.chunk", created: 1, model: "fixture", choices: [{ index: 0, delta: value, finish_reason: finish }] })}\n\n`,
          );
        response.end("data: [DONE]\n\n");
      });
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      writeFileSync(
        join(agent, "models.yml"),
        JSON.stringify({
          providers: {
            fixture: {
              baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
              apiKey: "dummy",
              api: "openai-completions",
              models: [
                {
                  id: "fixture",
                  name: "fixture",
                  reasoning: false,
                  input: ["text"],
                  contextWindow: 32768,
                  maxTokens: 1024,
                },
              ],
            },
          },
        }),
      );
    } else {
      // Credentials stay in a private temporary home, never in reports or repo files.
      copyFileSync(
        join(homedir(), ".omp/agent/models.yml"),
        join(agent, "models.yml"),
      );
    }
    const model = live ? "zg-newapi/kimi-for-coding" : "fixture/fixture";
    writeFileSync(
      join(agent, "config.yml"),
      JSON.stringify({
        modelRoles: { default: model },
        retry: { enabled: false, maxRetries: 0, modelFallback: false },
      }),
    );
    writeFileSync(join(agent, "mcp.json"), JSON.stringify({ mcpServers: {} }));
    const check = {
      id: fixture.id,
      paths: ["subject.cjs", "regression.test.cjs"],
      command: process.execPath,
      args: ["--test", "--test-reporter=tap", "regression.test.cjs"],
      evidence: "node-test",
      timeoutMs: 5000,
    };
    const baseline = await runVerificationCommand(check, root);
    if (scenario === "permission")
      check.args = ["-e", "console.error('HTTP 403');process.exit(1)"];
    const policy = join(agent, "task-verification-policy.json");
    writeFileSync(
      policy,
      JSON.stringify({
        schema: 1,
        projects: [{ root, repairOnFailure: true, checks: [check] }],
      }),
    );
    const testHash = hash(join(root, "regression.test.cjs")),
      policyHash = hash(policy);
    process.env.PI_CODING_AGENT_DIR = agent;
    const result = await runVerificationCommand(
      {
        command: join(homedir(), ".bun/bin/omp.exe"),
        args: [
          "-p",
          fixture.prompt +
            " Read the tests and implementation, make the smallest fix, use task_verify, and report actual results. Do not change tests or verification policy.",
          "--mode",
          "json",
          "--model",
          model,
          "--no-extensions",
          "--extension",
          join(agent, "extensions/omp-problem-solving.js"),
          "--extension",
          join(agent, "extensions/omp-task-verification.js"),
          "--no-skills",
          "--no-rules",
          "--no-session",
          "--no-title",
          "--no-lsp",
          "--max-time",
          live ? "180s" : "30s",
          "--tools",
          "read,write,edit,task_verify,problem_checkpoint",
        ],
        timeoutMs: live ? 190000 : 40000,
      },
      root,
    );
    const final = await runVerificationCommand(
      {
        ...check,
        args: ["--test", "--test-reporter=tap", "regression.test.cjs"],
      },
      root,
    );
    const holdout = await runVerificationCommand(
      {
        command: process.execPath,
        args: [
          "-e",
          "const assert=require('node:assert/strict');const subject=require('./subject.cjs');" +
            fixture.holdout,
        ],
        timeoutMs: 5000,
      },
      root,
    );
    let preserved = false;
    try {
      preserved =
        testHash === hash(join(root, "regression.test.cjs")) &&
        policyHash === hash(policy);
    } catch {
      /* Deletion is tampering too. */
    }
    const changed = execFileSync(
      "git",
      ["diff", "--name-only", "--no-renames", baseHead],
      { cwd: root, encoding: "utf8" },
    )
      .trim()
      .split(/\r?\n/)
      .filter(Boolean);
    const untracked = execFileSync(
      "git",
      ["ls-files", "--others", "--exclude-standard"],
      { cwd: root, encoding: "utf8" },
    )
      .trim()
      .split(/\r?\n/)
      .filter(Boolean);
    const unintendedEdits = [...new Set([...changed, ...untracked])].filter(
      (p) => p !== "subject.cjs",
    );
    const recovered =
      final.code === 0 &&
      holdout.code === 0 &&
      preserved &&
      !unintendedEdits.length;
    const ok =
      baseline.code !== 0 &&
      result.code === 0 &&
      !result.timedOut &&
      (preserved || scenario === "tamper") &&
      (live
        ? recovered
        : scenario === "repair"
          ? recovered && requests === 4 && sawRepairContext
          : scenario === "stuck"
            ? !recovered && requests === 3 && sawRepairContext
            : scenario === "tamper"
              ? !recovered &&
                requests === 4 &&
                sawRepairContext &&
                result.output.includes(
                  "verification-inputs-changed-review-required",
                )
              : !recovered && requests === 2 && !sawRepairContext);
    const report = {
      scenario,
      mode: live ? "live" : "control",
      caseId: fixture.id,
      provenance: fixture.provenance,
      model,
      revision,
      implementationHash,
      caseHash: createHash("sha256")
        .update(JSON.stringify(fixture))
        .digest("hex"),
      unintendedEdits,
      ...observedUsage(result),
      costUsd: null,
      costStatus: "unavailable-no-trusted-billing-observation",
      ok,
      baselineFailed: baseline.code !== 0,
      recovered,
      testsPreserved: preserved,
      requests: live ? undefined : requests,
      repairContext: live ? undefined : sawRepairContext,
      durationMs: result.durationMs,
      timedOut: result.timedOut,
      exitCode: result.code,
    };
    reports.push(report);
    console.log(JSON.stringify(report));
  } finally {
    if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
    if (server) await new Promise((resolve) => server.close(resolve));
    rmSync(base, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
}
if (reportPath) {
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(
    reportPath,
    JSON.stringify(
      {
        schema: 1,
        createdAt: new Date().toISOString(),
        revision,
        implementationHash,
        reports,
        summary: summarizeReports(reports),
      },
      null,
      2,
    ) + "\n",
    { flag: "wx" },
  );
}
process.exitCode = reports.every((report) => report.ok) ? 0 : 1;
