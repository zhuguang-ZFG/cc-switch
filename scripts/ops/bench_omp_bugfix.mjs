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

const source = dirname(fileURLToPath(import.meta.url));
const hash = (path) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");
const buggy =
  "module.exports=(items,page,size)=>items.slice(page*size,(page+1)*size);\n";
const corrected =
  "module.exports=(items,page,size)=>{if(!Number.isInteger(page)||page<1||!Number.isInteger(size)||size<1)throw new RangeError('invalid pagination');return items.slice((page-1)*size,page*size);};\n";
const regression = `const test=require('node:test');const assert=require('node:assert/strict');const page=require('./subject.cjs');
test('first page',()=>assert.deepEqual(page(['a','b','c','d'],1,2),['a','b']));
test('second page',()=>assert.deepEqual(page(['a','b','c','d'],2,2),['c','d']));
test('invalid page',()=>assert.throws(()=>page(['a'],0,2),RangeError));
test('invalid size',()=>assert.throws(()=>page(['a'],1,0),RangeError));
`;
const live = process.argv.includes("--live");
const reports = [];
for (const scenario of live
  ? ["live-kimi"]
  : ["repair", "stuck", "permission"]) {
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
            : requests === 3 && scenario === "repair"
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
                      path: "subject.cjs",
                      content: patch,
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
      id: "pagination",
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
          "Fix the pagination bug in subject.cjs. Page numbers are one-based; reject nonpositive or noninteger page/size; return a fresh array without mutating input. The regression already fails on the original code. Read the tests and implementation, make the smallest fix, use task_verify to verify, and report actual results. Do not change tests or verification policy.",
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
          "const assert=require('node:assert/strict');const page=require('./subject.cjs');const a=[1,2,3,4,5];assert.deepEqual(page(a,3,2),[5]);assert.deepEqual(page([],1,2),[]);assert.deepEqual(page(a,99,2),[]);assert.throws(()=>page(a,1.5,2));assert.throws(()=>page(a,1,2.5));assert.deepEqual(a,[1,2,3,4,5]);assert.notStrictEqual(page(a,1,9),a);",
        ],
        timeoutMs: 5000,
      },
      root,
    );
    const preserved =
      testHash === hash(join(root, "regression.test.cjs")) &&
      policyHash === hash(policy);
    const recovered = final.code === 0 && holdout.code === 0 && preserved;
    const ok =
      baseline.code !== 0 &&
      result.code === 0 &&
      !result.timedOut &&
      preserved &&
      (live
        ? recovered
        : scenario === "repair"
          ? recovered && requests === 4 && sawRepairContext
          : scenario === "stuck"
            ? !recovered && requests === 3 && sawRepairContext
            : !recovered && requests === 2 && !sawRepairContext);
    const report = {
      scenario,
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
process.exitCode = reports.every((report) => report.ok) ? 0 : 1;
