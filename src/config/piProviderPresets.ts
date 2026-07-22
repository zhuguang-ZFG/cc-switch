/**
 * Pi agent provider presets.
 * Live: ~/.pi/agent/models.json + auth.json + settings.json
 *   providers.<id>: name / baseUrl / api / apiKey / compat / models[]
 * Additive mode: all providers projected; switch updates settings defaults.
 */

import type { ProviderCategory } from "../types";

export const PI_DEFAULT_API = "openai-completions";

export interface PiModelEntry {
  id: string;
  name?: string;
  reasoning?: boolean;
  input?: string[];
  contextWindow?: number;
  maxTokens?: number;
  cost?: {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
  };
  [key: string]: unknown;
}

export interface PiProviderSettingsConfig {
  name: string;
  baseUrl?: string;
  api?: string;
  apiKey?: string;
  models?: Array<string | PiModelEntry>;
  defaultModel?: string;
  compat?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PiProviderPreset {
  name: string;
  nameKey?: string;
  websiteUrl: string;
  apiKeyUrl?: string;
  settingsConfig: PiProviderSettingsConfig;
  isOfficial?: boolean;
  category?: ProviderCategory;
  icon?: string;
}

export const piProviderPresets: PiProviderPreset[] = [
  {
    name: "OpenAI Compatible",
    nameKey: "pi.presets.openaiCompatible",
    websiteUrl: "",
    settingsConfig: {
      name: "custom",
      baseUrl: "https://api.openai.com/v1",
      api: PI_DEFAULT_API,
      apiKey: "",
      models: [{ id: "gpt-4.1", name: "gpt-4.1" }],
      defaultModel: "gpt-4.1",
      compat: {
        supportsStore: false,
        supportsDeveloperRole: false,
        maxTokensField: "max_tokens",
      },
    },
    category: "custom",
    icon: "openai",
  },
  {
    name: "Custom",
    nameKey: "pi.presets.custom",
    websiteUrl: "",
    settingsConfig: {
      name: "",
      baseUrl: "",
      api: PI_DEFAULT_API,
      apiKey: "",
      models: [{ id: "", name: "" }],
      defaultModel: "",
      compat: {
        supportsStore: false,
        supportsDeveloperRole: false,
        maxTokensField: "max_tokens",
      },
    },
    category: "custom",
  },
];

export function getPiPresetByName(name: string): PiProviderPreset | undefined {
  return piProviderPresets.find((p) => p.name === name);
}
