import assert from "node:assert/strict";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const pluginPath = join(
  homedir(),
  ".omp",
  "plugins",
  "node_modules",
  "omp-cache-optimizer",
  "index.ts",
);

const pluginUrl = pathToFileURL(pluginPath);
pluginUrl.searchParams.set("test", `${Date.now()}`);
// The plugin lives under the active user's OMP runtime, outside this repository;
// this test intentionally exercises that runtime-loaded module boundary.
const plugin = await import(pluginUrl.href);
const { selectAdapterForAssistantMessage } = plugin.__internals_for_tests;

function assistantMessage(provider: string, model: string) {
  return {
    role: "assistant",
    content: [{ type: "text", text: "ok" }],
    api: "openai-completions",
    provider,
    model,
    usage: {
      input: 100,
      output: 1,
      cacheRead: 0,
      cacheWrite: 0,
      totalTokens: 101,
    },
    stopReason: "stop",
  };
}

type RegisteredModel = {
  provider: string;
  id: string;
  name: string;
  api?: string;
  baseUrl?: string;
};

const registeredModels: Record<string, RegisteredModel> = {
  "zg-newapi/k3": {
    provider: "zg-newapi",
    id: "k3",
    name: "Kimi K3",
    api: "openai-completions",
    baseUrl: "http://registered-k3.invalid/v1",
  },
  "codebuddy/hy3-preview-agent": {
    provider: "codebuddy",
    id: "hy3-preview-agent",
    name: "Hunyuan Hy3 (WorkBuddy)",
  },
  "zg-newapi/opencode-go": {
    provider: "zg-newapi",
    id: "opencode-go",
    name: "DeepSeek V4 Flash (opencode-go 独立)",
  },
};
const modelRegistry = {
  find(provider: string, id: string) {
    return registeredModels[`${provider}/${id}`];
  },
};
const routerModel = {
  provider: "router",
  id: "auto",
  name: "Auto Router",
  api: "router-api",
  baseUrl: "http://router.invalid",
};

const routedCases = [
  ["zg-newapi", "k3", "Kimi cache"],
  ["codebuddy", "hy3-preview-agent", "Hunyuan cache"],
  ["zg-newapi", "opencode-go", "DS cache"],
] as const;

for (const [provider, id, expected] of routedCases) {
  const adapter = selectAdapterForAssistantMessage(
    assistantMessage(provider, id),
    routerModel,
    modelRegistry,
  );
  assert.equal(adapter?.label, expected, `${provider}/${id} routed adapter`);
}

const routedK3 = plugin.__internals_for_tests.modelFromAssistantMessage(
  assistantMessage("zg-newapi", "k3"),
  routerModel,
  modelRegistry,
);
assert.equal(routedK3?.name, "Kimi K3", "routed k3 keeps the registered display name");
assert.equal(routedK3?.api, "openai-completions", "message/registered API wins over router API");
assert.equal(
  routedK3?.baseUrl,
  "http://registered-k3.invalid/v1",
  "routed k3 metadata comes from the registered upstream model",
);

const directK3 = registeredModels["zg-newapi/k3"];
assert.ok(directK3);
assert.equal(
  selectAdapterForAssistantMessage(
    assistantMessage("zg-newapi", "k3"),
    directK3,
    modelRegistry,
  )?.label,
  "Kimi cache",
  "direct k3 must remain supported",
);

assert.equal(
  selectAdapterForAssistantMessage(
    assistantMessage("unknown-provider", "opaque-id"),
    routerModel,
    modelRegistry,
  ),
  undefined,
  "unknown routed models must not be misclassified",
);

console.log("PASS: routed aliases resolve through modelRegistry; direct and unknown fallbacks are safe");
