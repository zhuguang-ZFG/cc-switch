import { createHash } from "node:crypto";
import { basename, dirname } from "node:path";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";

export const PROBE_REVISION = "2026.08.19-tool-canary-probe-r1";
const CANARY_NAME = /^omp-model-tool-canary-[a-f0-9]{16}\.txt$/;
const CHANNEL_HEADERS = ["x-oneapi-channel-id", "x-newapi-channel-id", "x-channel-id", "channel-id"];

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function textBlocks(value) {
  const blocks = Array.isArray(value?.content) ? value.content : [];
  return blocks
    .filter(block => block?.type === "text" && typeof block.text === "string")
    .map(block => block.text)
    .join("\n");
}

function hash(value) {
  return createHash("sha256").update(value).digest("hex").slice(0, 16);
}

export function safeChannelId(headers) {
  if (!isRecord(headers)) return undefined;
  for (const key of CHANNEL_HEADERS) {
    const value = headers[key];
    if (typeof value === "string" && /^\d{1,12}$/.test(value.trim())) return value.trim();
  }
  return undefined;
}

export function isCanaryReadPath(path) {
  return typeof path === "string" && CANARY_NAME.test(basename(path));
}

export function buildProbeResult(state, lastAssistantMessage) {
  return {
    revision: PROBE_REVISION,
    readCalled: state.readCalled === true,
    argsValid: state.argsValid === true,
    toolResultContainsNonce: state.toolResultContainsNonce === true,
    finalContainsNonce:
      typeof state.nonce === "string" && state.nonce.length > 0
        ? textBlocks(lastAssistantMessage).includes(state.nonce)
        : false,
    pathHash: typeof state.path === "string" ? hash(state.path) : undefined,
    requestIdHash: typeof state.requestId === "string" && state.requestId ? hash(state.requestId) : undefined,
    channelId: state.channelId,
  };
}

export function writeProbeResult(path, result) {
  const destination = `${path}.result.json`;
  mkdirSync(dirname(destination), { recursive: true });
  const temporary = `${destination}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(result)}\n`, "utf8");
  renameSync(temporary, destination);
  return destination;
}

export default function modelToolCanaryProbe(pi) {
  const state = {
    path: undefined,
    nonce: undefined,
    toolCallId: undefined,
    readCalled: false,
    argsValid: false,
    toolResultContainsNonce: false,
    requestId: undefined,
    channelId: undefined,
    written: false,
  };

  pi.on("after_provider_response", event => {
    if (typeof event?.requestId === "string" && event.requestId) state.requestId = event.requestId;
    state.channelId = safeChannelId(event?.headers) ?? state.channelId;
  });
  pi.on("tool_call", event => {
    if (event.toolName !== "read" || state.readCalled) return;
    state.readCalled = true;
    const path = typeof event.input?.path === "string" ? event.input.path : event.input?.file_path;
    state.argsValid = isCanaryReadPath(path);
    if (!state.argsValid) return;
    state.path = path;
    state.toolCallId = event.toolCallId;
    try {
      state.nonce = readFileSync(path, "utf8").trim();
    } catch {
      state.argsValid = false;
    }
  });
  pi.on("tool_result", event => {
    if (event.toolName !== "read" || event.toolCallId !== state.toolCallId || !state.nonce) return;
    state.toolResultContainsNonce = event.isError !== true && textBlocks(event).includes(state.nonce);
  });
  pi.on("session_stop", event => {
    if (state.written || !state.path) return;
    state.written = true;
    try {
      writeProbeResult(state.path, buildProbeResult(state, event.last_assistant_message));
    } catch {}
  });
}
