#!/usr/bin/env bun
// jev_mcp_tool.mjs — MCP (stdio) server exposing one tool, jev_judge, that
// calls NewAPI's Jev channel with caller-chosen `state` and typed `questions`.
// NewAPI selects the official API key; the bridge preserves the decision shape.
// This is the sanctioned independent MCP integration (see the SystemOne runbook).
// Jev must NEVER be registered as a chat-role model.
//
// Register in ~/.omp/agent/mcp.json (mcpServers):
//   "jev": {
//     "type": "stdio",
//     "command": "C:\\Users\\zhugu\\.bun\\bin\\bun.exe",
//     "args": ["D:\\Users\\cc-switch\\scripts\\ops\\jev_mcp_tool.mjs"],
//     "timeout": 60000
//   }
//
// Tool: jev_judge(state: string|object, questions?: object, model?: string)
//   - questions absent -> default judgment template (is_blocking/urgency/route)
//   - returns { model, answers, usage } (never chat text)
//
// Upstream: POST <JEV_NEWAPI_BASE>/v1/chat/completions (local NewAPI),
// token from ~/.omp/guardian/secrets.json -> "newapi_probe_key" (never in repo).

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";

const NEWAPI_BASE = (
  process.env.JEV_NEWAPI_BASE || "http://127.0.0.1:3002"
).replace(/\/+$/, "");
const SECRETS_FILE = join(homedir(), ".omp", "guardian", "secrets.json");
const SECRET_KEY = "newapi_probe_key";
const TIMEOUT_MS = 120_000;
const ALLOWED_MODELS = ["jev-latest", "jev-preview", "jev-1.13", "jev-1.13.0"];

const DEFAULT_QUESTIONS = {
  is_blocking: { type: "noul", instructions: "此状态是否阻塞当前任务？" },
  urgency: { type: "score", instructions: "处理紧迫度", criteria: ["低", "中", "高"] },
  route: {
    type: "choice",
    instructions: "建议走哪条处理路径",
    criteria: { retry: "瞬时错误，重试", escalate: "需要人工/上游", proceed: "可继续" },
  },
};

function readSecret() {
  const raw = JSON.parse(readFileSync(SECRETS_FILE, "utf8"));
  const key = raw[SECRET_KEY];
  if (typeof key !== "string" || key.length < 8) {
    throw new Error(`secrets.json missing "${SECRET_KEY}" (${SECRETS_FILE})`);
  }
  return key;
}

const API_KEY = readSecret();

async function callSystemOne(state, questions, model) {
  const res = await fetch(`${NEWAPI_BASE}/v1/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "jev-latest",
      messages: [{
        role: "user",
        content: JSON.stringify({ type: "jev.systemone", model, state, questions }),
      }],
      stream: false,
    }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
    redirect: "error",
  });
  const text = await res.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {}
  if (!res.ok) {
    throw new Error(`Jev NewAPI HTTP ${res.status}; inspect the Jev channel and bridge logs`);
  }
  const content = data?.choices?.[0]?.message?.content;
  let answers;
  try {
    answers = JSON.parse(content);
  } catch {
    throw new Error("Jev NewAPI returned a malformed response (answer JSON missing)");
  }
  if (!answers || typeof answers !== "object" || Array.isArray(answers)) {
    throw new Error("Jev NewAPI returned a malformed response (no answers)");
  }
  for (const [id, question] of Object.entries(questions)) {
    if (!answers[id] || answers[id].type !== question?.type) {
      throw new Error("Jev NewAPI returned answers that do not match the requested questions");
    }
  }
  return {
    model: data.model,
    answers,
    usage: {
      input_tokens: data.usage?.prompt_tokens ?? 0,
      output_tokens: data.usage?.completion_tokens ?? 0,
    },
  };
}

async function handleToolCall(params) {
  if (!params || typeof params !== "object") {
    throw new Error("jev_judge requires an object argument");
  }
  const state = params.state;
  if (state === null || (typeof state !== "string" && typeof state !== "object")) {
    throw new Error("jev_judge requires `state` (string or object)");
  }
  const questions = params.questions === undefined ? DEFAULT_QUESTIONS : params.questions;
  if (typeof questions !== "object" || questions === null || Array.isArray(questions)) {
    throw new Error("`questions` must be an object map of typed questions");
  }
  let model = params.model === undefined ? "jev-latest" : String(params.model);
  if (!ALLOWED_MODELS.includes(model)) {
    throw new Error(`model must be one of: ${ALLOWED_MODELS.join(", ")}`);
  }
  return callSystemOne(state, questions, model);
}

const rl = createInterface({ input: process.stdin, terminal: false });
const send = (obj) => process.stdout.write(`${JSON.stringify(obj)}\n`);

rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let msg;
  try {
    msg = JSON.parse(trimmed);
  } catch {
    return; // malformed line: ignore per MCP spec
  }
  if (!msg || typeof msg !== "object" || msg.method === undefined) return;

  const method = String(msg.method);
  const respond = (result) => send({ jsonrpc: "2.0", id: msg.id, result });
  const respondError = (message) =>
    send({ jsonrpc: "2.0", id: msg.id, error: { code: -32000, message } });

  if (msg.id === undefined) return; // notification, no reply

  switch (method) {
    case "initialize":
      respond({
        protocolVersion: "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: "jev-mcp", version: "0.1.0" },
      });
      break;
    case "tools/list":
      respond({
        tools: [
          {
            name: "jev_judge",
            description:
              "Jev (TypeSafe System One): evaluate a `state` against typed " +
              "`questions` and return structured answers (noul=probability 0-1, " +
              "choice=option + probabilities, score=level). NOT a chat model; " +
              "use for decisions/classification only. Question types: " +
              "noul {type,instructions,criteria?{true,false}}, choice " +
              "{type,instructions,criteria:{option:rubric}}, score " +
              "{type,instructions,criteria:[levels]}. Default template when " +
              "questions omitted: is_blocking/urgency/route.",
            inputSchema: {
              type: "object",
              properties: {
                state: {
                  description: "Content to evaluate (string, or object/array of text)",
                },
                questions: {
                  description: "Map of typed questions; defaults to is_blocking/urgency/route",
                  type: "object",
                },
                model: {
                  description: `Model id (default jev-latest); allowed: ${ALLOWED_MODELS.join(", ")}`,
                  type: "string",
                },
              },
              required: ["state"],
            },
          },
        ],
      });
      break;
    case "tools/call": {
      const name = msg.params && msg.params.name;
      if (name !== "jev_judge") {
        respondError(`unknown tool: ${name}`);
        break;
      }
      handleToolCall(msg.params && msg.params.arguments)
        .then((data) =>
          respond({
            content: [
              { type: "text", text: JSON.stringify(data, null, 2) },
            ],
            isError: false,
          })
        )
        .catch((error) =>
          respond({
            content: [
              {
                type: "text",
                text: `jev_judge failed: ${error instanceof Error ? error.message : String(error)}`,
              },
            ],
            isError: true,
          })
        );
      break;
    }
    case "ping":
      respond({});
      break;
    case "resources/list":
      respond({ resources: [] });
      break;
    case "prompts/list":
      respond({ prompts: [] });
      break;
    default:
      respondError(`method not supported: ${method}`);
  }
});
