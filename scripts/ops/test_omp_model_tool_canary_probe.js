import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import modelToolCanaryProbe, {
  buildProbeResult,
  isCanaryReadPath,
  safeChannelId,
} from "./omp-model-tool-canary-probe.js";

test("probe accepts only canary paths and numeric gateway channel headers", () => {
  assert.equal(isCanaryReadPath("C:\\tmp\\omp-model-tool-canary-0123456789abcdef.txt"), true);
  assert.equal(isCanaryReadPath("C:\\tmp\\secrets.txt"), false);
  assert.equal(safeChannelId({ "x-oneapi-channel-id": "92" }), "92");
  assert.equal(safeChannelId({ "x-oneapi-channel-id": "92 secret" }), undefined);
});

test("probe result contains hashes and booleans but no raw request or path", () => {
  const result = buildProbeResult(
    {
      readCalled: true,
      argsValid: true,
      toolResultContainsNonce: true,
      nonce: "OMP_CANARY_VALUE",
      path: "C:\\private\\omp-model-tool-canary-0123456789abcdef.txt",
      requestId: "raw-request-id",
      channelId: "92",
    },
    { content: [{ type: "text", text: "OMP_CANARY_VALUE" }] },
  );
  assert.equal(result.finalContainsNonce, true);
  assert.match(result.pathHash, /^[a-f0-9]{16}$/);
  assert.match(result.requestIdHash, /^[a-f0-9]{16}$/);
  assert.equal(JSON.stringify(result).includes("raw-request-id"), false);
  assert.equal(JSON.stringify(result).includes("private"), false);
});

test("probe correlates structured read call, result, response, and final answer", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-canary-probe-"));
  try {
    const noncePath = join(root, "omp-model-tool-canary-0123456789abcdef.txt");
    const nonce = "OMP_CANARY_STRUCTURED_PROOF";
    writeFileSync(noncePath, `${nonce}\n`);
    const handlers = new Map();
    modelToolCanaryProbe({
      on(name, handler) {
        handlers.set(name, handler);
      },
    });
    handlers.get("after_provider_response")({
      requestId: "request-123",
      headers: { "x-newapi-channel-id": "45", authorization: "must-drop" },
    });
    handlers.get("tool_call")({
      toolName: "read",
      toolCallId: "read-1",
      input: { path: noncePath },
    });
    handlers.get("tool_result")({
      toolName: "read",
      toolCallId: "read-1",
      isError: false,
      content: [{ type: "text", text: nonce }],
    });
    handlers.get("session_stop")({
      last_assistant_message: { content: [{ type: "text", text: nonce }] },
    });
    const serialized = readFileSync(`${noncePath}.result.json`, "utf8");
    const result = JSON.parse(serialized);
    assert.equal(result.readCalled, true);
    assert.equal(result.argsValid, true);
    assert.equal(result.toolResultContainsNonce, true);
    assert.equal(result.finalContainsNonce, true);
    assert.equal(result.channelId, "45");
    assert.equal(serialized.includes("authorization"), false);
    assert.equal(serialized.includes("request-123"), false);
    assert.equal(serialized.includes(noncePath), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
