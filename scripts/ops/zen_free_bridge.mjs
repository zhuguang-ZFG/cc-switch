#!/usr/bin/env bun
// zen-free-bridge: expose OpenCode Zen free models as an OpenAI-compatible
// local endpoint by driving the genuine opencode CLI (the ONLY sanctioned
// transport for the free tier: raw API calls get 403 FreeTierError, the real
// client with a logged-in Console key passes the gate).
//
// Usage:  bun scripts/ops/zen_free_bridge.mjs [port=8412]
// Endpoint: POST /v1/chat/completions  (OpenAI subset; single-flight mutex)
// Auth: none (loopback-only server; NewAPI channel uses a placeholder key)
//
// Model ids are passed through as opencode/<id> to the CLI. The CLI owns the
// Console credential in ~/.local/share/opencode/auth.json — never touches
// this file, never prints keys.
//
// Failure mapping:
//   - CLI upstream rate limit  -> HTTP 429 (free daily quota; NOT a fault:
//     2026-08-20 contract treats quota as pass-with-warning)
//   - CLI busy (another spawn) -> HTTP 503 (NewAPI pool retries other legs)
//   - other CLI errors         -> HTTP 502 with first stderr line (redacted)

const PORT = Number(process.argv[2] || 8412);
const OPCODE = process.env.OPCODE_BIN || "C:/Users/zhugu/AppData/Roaming/npm/opencode.cmd";
const MODEL_PREFIX = "opencode/";
const RUN_TIMEOUT_MS = 150_000;

let running = false; // single-flight: one CLI at a time; queue depth 1
let queued = null;
let quotaUntil = 0; // fail-fast cooldown after a 429: don't spawn the CLI
const QUOTA_COOLDOWN_MS = 600_000;

function spawnCli(modelId, prompt) {
  return new Promise((resolve) => {
    const args = ["run", "-p", "--model", MODEL_PREFIX + modelId];
    const t0 = Date.now();
    const child = Bun.spawn([OPCODE, ...args], {
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
      cwd: process.env.HOME || process.env.USERPROFILE,
      env: { ...process.env },
    });
    child.stdin.write(prompt);
    child.stdin.end();
    let out = "";
    let err = "";
    const dec = new TextDecoder();
    const timer = setTimeout(() => {
      try { child.kill(); } catch {}
      resolve({ kind: "fail", status: 504, body: "opencode run timed out" });
    }, RUN_TIMEOUT_MS);
    (async () => {
      for await (const chunk of child.stdout) out += dec.decode(chunk);
      for await (const chunk of child.stderr) err += dec.decode(chunk);
    })().then(async () => {
      clearTimeout(timer);
      const code = await child.exited;
      const elapsed = Math.round((Date.now() - t0) / 1000);
      resolve({ kind: "done", code, out, err, elapsed });
    });
  });
}

function openaiCompletion(modelId, content) {
  return {
    id: "zen-bridge-" + Math.random().toString(36).slice(2, 10),
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: modelId,
    choices: [{
      index: 0,
      message: { role: "assistant", content },
      finish_reason: "stop",
    }],
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  };
}

// Stream variant: OMP always sends stream=true; without this the upstream JSON
// is relayed by NewAPI as an SSE chunk with empty choices (no delta.content),
// which makes clients wait/retry forever. Emit a real delta chunk + [DONE].
function openaiCompletionStream(modelId, content) {
  const id = "zen-bridge-" + Math.random().toString(36).slice(2, 10);
  const created = Math.floor(Date.now() / 1000);
  const chunk = {
    id,
    object: "chat.completion.chunk",
    created,
    model: modelId,
    choices: [{
      index: 0,
      delta: { role: "assistant", content },
      finish_reason: "stop",
    }],
  };
  return (
    "data: " + JSON.stringify(chunk) + "\n\n" +
    "data: [DONE]\n\n"
  );
}

function completionResponse(modelId, content, stream) {
  if (!stream) return Response.json(openaiCompletion(modelId, content));
  return new Response(openaiCompletionStream(modelId, content), {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}

Bun.serve({
  port: PORT,
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    if (req.method !== "POST" || url.pathname !== "/v1/chat/completions") {
      return Response.json({ error: { type: "invalid_request_error", message: "only POST /v1/chat/completions" } }, { status: 404 });
    }
    let body;
    try { body = await req.json(); } catch {
      return Response.json({ error: { type: "invalid_request_error", message: "invalid json" } }, { status: 400 });
    }
    const modelId = String(body.model || "");
    if (!modelId) return Response.json({ error: { type: "invalid_request_error", message: "missing model" } }, { status: 400 });
    const now = Date.now();
    if (now < quotaUntil) {
      return Response.json({ error: { type: "rate_limit_error", message: "OpenCode zen free daily quota exhausted" } }, { status: 429 });
    }
    const msg = (body.messages || [].slice(-1))[0];
    const prompt = msg && msg.content != null ? String(msg.content).slice(0, 32_000) : "";
    const stream = body.stream === true;

    if (running) {
      if (queued) return Response.json({ error: { type: "server_error", message: "zen bridge busy: a previous call is still running" } }, { status: 503 });
      queued = { modelId, prompt, stream, resolve: null };
      return new Promise((res) => { queued.resolve = res; });
    }

    running = true;
    const doRun = async (wantStream) => {
      const r = await spawnCli(modelId, prompt);
      if (r.kind === "fail") return Response.json({ error: { type: "server_error", message: r.body } }, { status: r.status });
      const text = (r.out || "").trim();
      const plain = (r.out + " " + r.err).replace(/\x1b\[[0-9;]*m/g, "");
      const rateLimited = /rate limit|Rate limit|FreeUsageLimit|free tier|429|please try again later/i.test(plain);
      if (r.code !== 0 || /^error:/i.test(text)) {
        if (rateLimited) {
          quotaUntil = Date.now() + QUOTA_COOLDOWN_MS;
          return Response.json({ error: { type: "rate_limit_error", message: "OpenCode zen free daily quota exhausted" } }, { status: 429 });
        }
        const firstErr = (r.err || text).split("\n").map((l) => l.trim()).find((l) => l.length > 0) || "unknown cli error";
        return Response.json({ error: { type: "upstream_error", message: "opencode: " + firstErr.slice(0, 400) } }, { status: 502 });
      }
      const content = stripCliChatNoise(text, modelId, r.elapsed);
      if (!content) return Response.json({ error: { type: "upstream_error", message: "empty completion from opencode" } }, { status: 502 });
      return completionResponse(modelId, content, wantStream);
    };
    const first = await doRun(stream);
    running = false;
    if (queued) {
      const next = queued; queued = null;
      running = true;
      next.resolve(doRun(next.stream).finally(() => { running = false; }));
    }
    return first;
  },
});

function stripCliChatNoise(text, _modelId, _elapsed) {
  // keep the agent's final answer; drop the "> build · <model>" banner,
  // timing footer and any leading/trailing fences around single block output.
  let t = text.replace(/\x1b\[[0-9;]*m/g, "").replace(/^>\s*build · [^\n]*\n?/g, "").trim();
  t = t.replace(/\n(\d+(\.\d+)?s|Wall time: [^\n]*)\s*$/g, "").trim();
  if (t.startsWith("```") && t.endsWith("```") && t.split("\n").length <= 80) {
    const lines = t.split("\n"); lines.shift(); lines.pop();
    t = lines.join("\n").trim();
  }
  return t.length ? t : null;
}

console.log(`zen-free-bridge listening on 127.0.0.1:${PORT} (cli=${OPCODE})`);