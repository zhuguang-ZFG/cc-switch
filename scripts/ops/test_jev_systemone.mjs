import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const bridgePath = fileURLToPath(new URL("./jev_systemone_bridge.mjs", import.meta.url));
const selectedKey = "fixture-newapi-selected-key";
const customQuestions = {
  customer_urgent: { type: "noul", instructions: "Is the customer blocked?" },
};
const customAnswer = { customer_urgent: { type: "noul", noul: 0.91 } };
const defaultAnswer = {
  is_blocking: { type: "noul", noul: 0.7 },
  urgency: { type: "score", score: 1.8 },
  route: { type: "choice", choice: "escalate" },
};

async function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "jev-contract-"));
  mkdirSync(join(root, ".omp", "guardian"), { recursive: true });
  // Old code reads this credential instead of the channel-selected one.
  writeFileSync(join(root, ".omp", "guardian", "secrets.json"), JSON.stringify({
    jev_shared_key: "fixture-obsolete-gateway-key",
  }));
  const requests = [];
  const upstream = createServer(async (req, res) => {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    requests.push(body);
    res.setHeader("Content-Type", "application/json");
    if (req.headers.authorization !== `Bearer ${selectedKey}`) {
      res.writeHead(401).end(JSON.stringify({ detail: { message: "Invalid credential" } }));
      return;
    }
    if (body.state?.upstreamStatus) {
      res.writeHead(body.state.upstreamStatus).end(JSON.stringify({
        detail: { message: `rejected ${selectedKey}` },
      }));
      return;
    }
    const expectedState = Array.isArray(body.state)
      ? body.state[0]?.ticket === "blocked"
      : body.state?.ticket === "blocked";
    const isCustom = expectedState && body.questions?.customer_urgent?.type === "noul";
    res.end(JSON.stringify({
      model: "jev-1.13.0",
      answers: isCustom ? customAnswer : defaultAnswer,
      usage: { input_tokens: 302, output_tokens: 21 },
    }));
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  const reservation = createServer();
  reservation.listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  const child = spawn(process.env.BUN_EXECUTABLE || "bun", [bridgePath, String(port)], {
    env: {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      JEV_UPSTREAM_BASE: `http://127.0.0.1:${upstream.address().port}`,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(async () => {
    if (child.exitCode === null) {
      const exited = once(child, "exit");
      child.kill();
      await exited;
    }
    upstream.closeAllConnections();
    await new Promise((resolve) => upstream.close(resolve));
    rmSync(root, { recursive: true, force: true });
  });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("bridge startup timed out")), 8000);
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("exit", () => { clearTimeout(timer); reject(new Error("bridge exited before ready")); });
    child.stdout.on("data", (chunk) => {
      if (chunk.toString().includes("listening on")) { clearTimeout(timer); resolve(); }
    });
  });
  async function post(path, body, key = selectedKey) {
    const headers = { "Content-Type": "application/json" };
    if (key !== null) headers.Authorization = `Bearer ${key}`;
    const response = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(5000),
    });
    return { status: response.status, body: await response.json() };
  }
  return { post, requests };
}

function chat(content) {
  return { model: "jev-latest", messages: [{ role: "user", content }], stream: false };
}

// A structured decision must not silently turn into the fixed triage template.
test("custom decision questions survive the chat transport with structured state", async (t) => {
  const { post } = await fixture(t);
  const envelope = { type: "jev.systemone", state: { ticket: "blocked" }, questions: customQuestions };
  const result = await post("/v1/chat/completions", chat(JSON.stringify(envelope)));
  assert.equal(result.status, 200);
  assert.deepEqual(JSON.parse(result.body.choices[0].message.content), customAnswer);
});

test("native decision input accepts arrays without replacing the question", async (t) => {
  const { post } = await fixture(t);
  const result = await post("/v1/systemone", {
    model: "jev-latest", state: [{ ticket: "blocked" }], questions: customQuestions,
  });
  assert.equal(result.status, 200);
  assert.deepEqual(result.body.answers, customAnswer);
});

test("missing authentication and malformed decision envelopes fail before upstream", async (t) => {
  const { post, requests } = await fixture(t);
  assert.equal((await post("/v1/chat/completions", chat("blocked"), null)).status, 401);
  const malformed = { type: "jev.systemone", state: null, questions: customQuestions };
  assert.equal((await post("/v1/chat/completions", chat(JSON.stringify(malformed)))).status, 400);
  assert.equal(requests.length, 0);
});

test("upstream credential and rate-limit failures retain their status without leaking keys", async (t) => {
  const { post } = await fixture(t);
  for (const upstreamStatus of [401, 429]) {
    const result = await post("/v1/systemone", {
      model: "jev-latest", state: { upstreamStatus }, questions: customQuestions,
    });
    assert.equal(result.status, upstreamStatus);
    assert.equal(JSON.stringify(result.body).includes(selectedKey), false);
  }
});

test("unmarked JSON remains ordinary input instead of an accidental decision envelope", async (t) => {
  const { post } = await fixture(t);
  const content = JSON.stringify({ state: { ticket: "blocked" }, questions: customQuestions });
  const result = await post("/v1/chat/completions", chat(content));
  assert.equal(result.status, 200);
  assert.deepEqual(JSON.parse(result.body.choices[0].message.content), defaultAnswer);
});

test("invalid model returns a tool error without terminating the MCP session", async () => {
  const root = mkdtempSync(join(tmpdir(), "jev-mcp-contract-"));
  mkdirSync(join(root, ".omp", "guardian"), { recursive: true });
  writeFileSync(join(root, ".omp", "guardian", "secrets.json"), JSON.stringify({
    newapi_probe_key: "fixture-local-relay-token",
  }));
  const script = fileURLToPath(new URL("./jev_mcp_tool.mjs", import.meta.url));
  const child = spawn(process.env.BUN_EXECUTABLE || "bun", [script], {
    env: { ...process.env, HOME: root, USERPROFILE: root },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const timer = setTimeout(() => child.kill(), 8000);
  try {
    const exited = once(child, "exit");
    child.stdin.end([
      { jsonrpc: "2.0", id: 1, method: "tools/call", params: {
        name: "jev_judge", arguments: { state: "blocked", model: "invalid-model" },
      } },
      { jsonrpc: "2.0", id: 2, method: "ping" },
    ].map((message) => JSON.stringify(message)).join("\n") + "\n");
    const [code] = await exited;
    assert.equal(code, 0, stderr);
    const replies = stdout.trim().split("\n").map((line) => JSON.parse(line));
    assert.equal(replies.find((reply) => reply.id === 1).result.isError, true);
    assert.deepEqual(replies.find((reply) => reply.id === 2).result, {});
  } finally {
    clearTimeout(timer);
    if (child.exitCode === null) child.kill();
    rmSync(root, { recursive: true, force: true });
  }
});
