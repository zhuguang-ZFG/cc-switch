#!/usr/bin/env bun
// jev_systemone_bridge.mjs — OpenAI-compatible local relay that translates
// chat/completions calls into TypeSafe SystemOne (Jev) structured decisions.
//
// Jev is a decision model, NOT a chat model: given `state` + typed `questions`
// it returns structured answers (probabilities/judgments). This relay maps a
// prompt to a fixed judgment template, or decodes an explicit jev.systemone
// envelope carrying the caller's state/questions. Answers remain JSON text;
// NewAPI is only the transport and key-pool owner, not a chat-model adapter.
// Consumers MUST treat the output as structured decisions, never dialogue.
//
// Usage: bun scripts/ops/jev_systemone_bridge.mjs [port=8413]
// Endpoints (loopback only; Bearer key supplied by the NewAPI channel):
//   POST /v1/chat/completions   OpenAI subset (stream & non-stream), model jev-latest
//   POST /v1/systemone          native passthrough for structured callers
//   GET  /healthz               -> {"ok":true}
//
// Upstream:  POST <JEV_UPSTREAM_BASE>/v1/systemone
//   default  https://api.typesafe.ai/v1/systemone
//   key      request Authorization, selected by NewAPI (never stored here)
//
// Consumption tracking: every call logs one line to stdout:
//   [ts] chat|systemone model=<m> upstream=<status> in=<tok> out=<tok> ms=<ms>
//   (tokens from upstream usage; no request content is ever logged)
//
// Official contract: docs.typesafe.ai/api. Credentials are forwarded only to
// the configured upstream; redirects are rejected and errors never echo keys.

import { randomUUID } from "node:crypto";

const PORT = Number(process.argv[2] || 8413);
const UPSTREAM_BASE = (
  process.env.JEV_UPSTREAM_BASE || "https://api.typesafe.ai"
).replace(/\/+$/, "");
const MODEL = "jev-latest";
const TIMEOUT_MS = 120_000;
const MAX_INFLIGHT = 4;
const ALLOWED_MODELS = new Set(["jev-latest", "jev-preview", "jev-1.13", "jev-1.13.0"]);

// Fixed judgment template (opencode-zen-free-tier-lock runbook agent scenario).
const DEFAULT_QUESTIONS = {
  is_blocking: { type: "noul", instructions: "此状态是否阻塞当前任务？" },
  urgency: {
    type: "score",
    instructions: "处理紧迫度",
    criteria: ["低", "中", "高"],
  },
  route: {
    type: "choice",
    instructions: "建议走哪条处理路径",
    criteria: {
      retry: "瞬时错误，重试",
      escalate: "需要人工/上游",
      proceed: "可继续",
    },
  },
};


// bounded in-flight: Jev is a shared public pool, keep fan-out small
let active = 0;
const waiters = [];
async function acquire() {
  if (active < MAX_INFLIGHT) {
    active++;
    return;
  }
  await new Promise((resolve) => waiters.push(resolve));
  active++;
}
function release() {
  active--;
  const next = waiters.shift();
  if (next) next();
}

function logLine(kind, model, upstreamStatus, usage, ms) {
  const inT = usage && Number.isFinite(usage.input_tokens) ? usage.input_tokens : "-";
  const outT = usage && Number.isFinite(usage.output_tokens) ? usage.output_tokens : "-";
  console.log(
    `[${new Date().toISOString()}] ${kind} model=${model} upstream=${upstreamStatus} in=${inT} out=${outT} ms=${ms}`
  );
}

async function callSystemOne(state, questions, model, apiKey) {
  const started = Date.now();
  let res;
  try {
    res = await fetch(`${UPSTREAM_BASE}/v1/systemone`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ model, state, questions }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      redirect: "error",
    });
  } catch (error) {
    logLine("systemone", model, "network_error", null, Date.now() - started);
    return {
      httpStatus: 502,
      body: { error: { message: "upstream unreachable", type: "upstream_error" } },
    };
  }
  const text = await res.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {}
  if (!res.ok) {
    logLine("systemone", model, res.status, null, Date.now() - started);
    return {
      httpStatus: res.status,
      body: {
        error: {
          message: `Jev upstream HTTP ${res.status}`,
          type: res.status === 429 ? "rate_limit_error" :
            res.status === 401 || res.status === 403 ? "authentication_error" : "upstream_error",
        },
      },
    };
  }
  logLine("systemone", model, res.status, data && data.usage, Date.now() - started);
  if (!data || typeof data.answers !== "object" || data.answers === null || Array.isArray(data.answers)) {
    return {
      httpStatus: 502,
      body: { error: { message: "malformed upstream response", type: "upstream_error" } },
    };
  }
  return { httpStatus: 200, body: data };
}

function completionBody(model, data) {
  const answers = data.answers;
  const usage = data.usage || {};
  return {
    id: `chatcmpl-${randomUUID()}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: String(data.model || model),
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: JSON.stringify(answers) },
        finish_reason: "stop",
      },
    ],
    usage: {
      prompt_tokens: usage.input_tokens || 0,
      completion_tokens: usage.output_tokens || 0,
      total_tokens: (usage.input_tokens || 0) + (usage.output_tokens || 0),
    },
  };
}

function lastUserContent(messages) {
  if (!Array.isArray(messages)) throw new HttpError(400, "messages must be an array");
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.role !== "user") continue;
    if (typeof m.content === "string" && m.content.trim()) return m.content;
    if (Array.isArray(m.content)) {
      const parts = m.content
        .filter((p) => p && typeof p === "object" && typeof p.text === "string")
        .map((p) => p.text);
      if (parts.length) return parts.join("\n");
    }
  }
  throw new HttpError(400, "no user message content found");
}

function validateDecision(state, questions) {
  if (state === null || (typeof state !== "string" && typeof state !== "object") ||
      (typeof state === "string" && !state.trim())) {
    throw new HttpError(400, "state (non-empty string, object or array) is required");
  }
  if (typeof questions !== "object" || questions === null || Array.isArray(questions)) {
    throw new HttpError(400, "questions (object) is required");
  }
}

function chatDecision(messages, model) {
  const content = lastUserContent(messages);
  let envelope;
  try {
    envelope = JSON.parse(content);
  } catch {
    // Plain text remains the established fixed-template input.
  }
  if (envelope?.type !== "jev.systemone") {
    return { state: content, questions: DEFAULT_QUESTIONS, model: MODEL };
  }
  validateDecision(envelope.state, envelope.questions);
  const evaluationModel = envelope.model ?? model;
  if (!ALLOWED_MODELS.has(evaluationModel)) {
    throw new HttpError(400, `model must be one of: ${[...ALLOWED_MODELS].join(", ")}`);
  }
  return { state: envelope.state, questions: envelope.questions, model: evaluationModel };
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function errorResponse(status, message) {
  return Response.json(
    { error: { message, type: "invalid_request_error" } },
    { status }
  );
}

function streamCompletion(body) {
  const encoder = new TextEncoder();
  const payload = body.choices[0].message.content;
  const chunks = [];
  for (let i = 0; i < payload.length; i += 2048) {
    chunks.push(payload.slice(i, i + 2048));
  }
  const stream = new ReadableStream({
    async start(controller) {
      const send = (obj) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`));
      chunks.forEach((chunk, i) => {
        send({
          id: body.id,
          object: "chat.completion.chunk",
          created: body.created,
          model: body.model,
          choices: [
            {
              index: 0,
              delta: { role: "assistant", content: chunk },
              finish_reason: i === chunks.length - 1 ? "stop" : null,
            },
          ],
        });
      });
      send({ id: body.id, object: "chat.completion.chunk", created: body.created, model: body.model, choices: [] });
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
  });
}

Bun.serve({
  port: PORT,
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    if (req.method === "GET" && url.pathname === "/healthz") {
      return Response.json({ ok: true });
    }
    if (req.method !== "POST") return errorResponse(405, "method not allowed");
    const apiKey = /^Bearer\s+(\S+)$/i.exec(req.headers.get("Authorization") || "")?.[1];
    if (!apiKey) return errorResponse(401, "Bearer API key is required");
    if (url.pathname === "/v1/chat/completions") {
      let body;
      try {
        body = await req.json();
      } catch {
        return errorResponse(400, "invalid JSON body");
      }
      try {
        if (!body || typeof body !== "object" || Array.isArray(body)) {
          return errorResponse(400, "request body must be an object");
        }
        const model = String(body.model || MODEL);
        if (!ALLOWED_MODELS.has(model) && model !== MODEL) {
          return errorResponse(400, `model must be one of: ${[...ALLOWED_MODELS].join(", ")}`);
        }
        const decision = chatDecision(body.messages, model);
        await acquire();
        let result;
        try {
          result = await callSystemOne(decision.state, decision.questions, decision.model, apiKey);
        } finally {
          release();
        }
        if (result.httpStatus !== 200) return Response.json(result.body, { status: result.httpStatus });
        const completion = completionBody(model, result.body);
        if (body.stream === true) return streamCompletion(completion);
        return Response.json(completion);
      } catch (error) {
        if (error instanceof HttpError) return errorResponse(error.status, error.message);
        console.error(`chat handler error: ${error instanceof Error ? error.message : String(error)}`);
        return errorResponse(500, "internal relay error");
      }
    }
    if (url.pathname === "/v1/systemone") {
      let body;
      try {
        body = await req.json();
      } catch {
        return errorResponse(400, "invalid JSON body");
      }
      if (!body || typeof body !== "object" || Array.isArray(body)) {
        return errorResponse(400, "request body must be an object");
      }
      const model = String(body.model || "");
      if (!ALLOWED_MODELS.has(model)) {
        return errorResponse(400, `model must be one of: ${[...ALLOWED_MODELS].join(", ")}`);
      }
      try {
        validateDecision(body.state, body.questions);
      } catch (error) {
        return errorResponse(error.status, error.message);
      }
      await acquire();
      let result;
      try {
        result = await callSystemOne(body.state, body.questions, model, apiKey);
      } finally {
        release();
      }
      return Response.json(result.body, { status: result.httpStatus });
    }
    return errorResponse(404, "not found");
  },
});

console.log(
  `jev-systemone-bridge listening on 127.0.0.1:${PORT} (upstream=${UPSTREAM_BASE})`
);
