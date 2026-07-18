import { describe, expect, it } from "vitest";
import {
  KIMI_PROVIDER_TYPES,
  kimiProviderPresets,
} from "@/config/kimiProviderPresets";

describe("Kimi Code provider presets", () => {
  it("uses native Kimi provider types", () => {
    expect(KIMI_PROVIDER_TYPES.map(({ value }) => value)).toEqual([
      "openai",
      "openai_responses",
      "anthropic",
      "kimi",
      "google-genai",
      "vertexai",
    ]);
  });

  it("preserves model aliases and max context size", () => {
    const preset = kimiProviderPresets.find(
      ({ name }) => name === "Kimi For Coding",
    );
    expect(preset?.settingsConfig).toMatchObject({
      type: "kimi",
      models: [
        {
          id: "kimi-for-coding",
          alias: "kimi-code/kimi-for-coding",
          max_context_size: 262144,
        },
      ],
    });
  });
});
