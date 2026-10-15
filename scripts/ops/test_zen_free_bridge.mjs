// Hermetic tests for the zen bridge's message serialization (no CLI spawn).
import test from "node:test";
import assert from "node:assert/strict";
import { promptFromMessages } from "./zen_free_bridge.mjs";

test("single string message passes through verbatim", () => {
  assert.deepEqual(promptFromMessages([{ role: "user", content: "hello" }]), { prompt: "hello" });
});

test("multi-turn serializes with role prefixes in order", () => {
  const out = promptFromMessages([
    { role: "system", content: "S" },
    { role: "user", content: "A" },
    { role: "assistant", content: "B" },
    { role: "user", content: "C" },
  ]);
  assert.equal(out.prompt, "system: S\n\nuser: A\n\nassistant: B\n\nuser: C");
});

test("content arrays join text parts", () => {
  const out = promptFromMessages([{ role: "user", content: [
    { type: "text", text: "a" },
    { type: "text", text: "b" },
  ] }]);
  assert.equal(out.prompt, "a\nb");
});

test("image parts fail loudly instead of being silently dropped", () => {
  const out = promptFromMessages([{ role: "user", content: [
    { type: "text", text: "hi" },
    { type: "image_url", image_url: { url: "data:image/png;base64,AAAA" } },
  ] }]);
  assert.match(out.error, /image parts not supported/);
});

test("empty or missing messages yield an empty prompt", () => {
  assert.deepEqual(promptFromMessages([]), { prompt: "" });
  assert.deepEqual(promptFromMessages(undefined), { prompt: "" });
});

test("long prompts are not truncated (the 2026-10-15 32k bug)", () => {
  const long = "x".repeat(200_000);
  assert.equal(promptFromMessages([{ role: "user", content: long }]).prompt.length, 200_000);
});

test("missing role defaults to user and null content is empty", () => {
  assert.deepEqual(promptFromMessages([{ role: "user" }]), { prompt: "" });
  const out = promptFromMessages([
    { content: "first" },
    { role: "assistant", content: "second" },
  ]);
  assert.equal(out.prompt, "user: first\n\nassistant: second");
});
