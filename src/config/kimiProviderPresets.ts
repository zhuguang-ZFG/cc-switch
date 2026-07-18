/**
 * Kimi Code CLI provider presets.
 * Live config: ~/.kimi-code/config.toml
 *   [providers.<name>] type / base_url / api_key
 *   [models."<alias>"] provider / model / max_context_size
 *   default_model = "<alias>"
 */
import type { ProviderCategory } from "../types";

export type KimiProviderType =
  | "kimi"
  | "anthropic"
  | "openai"
  | "openai_responses"
  | "google-genai"
  | "vertexai";

export const KIMI_PROVIDER_TYPES: Array<{
  value: KimiProviderType;
  labelKey: string;
}> = [
  { value: "openai", labelKey: "kimicode.form.typeOpenai" },
  {
    value: "openai_responses",
    labelKey: "kimicode.form.typeOpenaiResponses",
  },
  { value: "anthropic", labelKey: "kimicode.form.typeAnthropic" },
  { value: "kimi", labelKey: "kimicode.form.typeKimi" },
  { value: "google-genai", labelKey: "kimicode.form.typeGoogleGenai" },
  { value: "vertexai", labelKey: "kimicode.form.typeVertexai" },
];

export interface KimiModel {
  /** Upstream model id written to models.*.model */
  id: string;
  /** Optional full alias; defaults to `${provider}/${id}` */
  alias?: string;
  name?: string;
  max_context_size?: number;
}

export interface KimiProviderSettingsConfig {
  name: string;
  type: KimiProviderType;
  base_url?: string;
  api_key?: string;
  models?: KimiModel[];
  [key: string]: unknown;
}

export interface KimiProviderPreset {
  name: string;
  nameKey?: string;
  websiteUrl: string;
  apiKeyUrl?: string;
  settingsConfig: KimiProviderSettingsConfig;
  isOfficial?: boolean;
  isPartner?: boolean;
  partnerPromotionKey?: string;
  category?: ProviderCategory;
  icon?: string;
  iconColor?: string;
}

export const kimiProviderPresets: KimiProviderPreset[] = [
  {
    name: "Kimi For Coding",
    websiteUrl: "https://www.kimi.com/code/?aff=cc-switch",
    apiKeyUrl: "https://www.kimi.com/code/?aff=cc-switch",
    settingsConfig: {
      name: "kimi-coding",
      type: "kimi",
      base_url: "https://api.kimi.com/coding/v1",
      api_key: "",
      models: [
        {
          id: "kimi-for-coding",
          alias: "kimi-code/kimi-for-coding",
          name: "Kimi For Coding",
          max_context_size: 262144,
        },
      ],
    },
    isOfficial: true,
    category: "cn_official",
    icon: "kimi",
  },
  {
    name: "Kimi Open Platform",
    websiteUrl: "https://platform.kimi.com?aff=cc-switch",
    apiKeyUrl: "https://platform.kimi.com/console/api-keys?aff=cc-switch",
    settingsConfig: {
      name: "moonshot",
      type: "openai",
      base_url: "https://api.moonshot.cn/v1",
      api_key: "",
      models: [
        {
          id: "kimi-k2.7-code",
          alias: "moonshot/kimi-k2.7-code",
          name: "Kimi K2.7 Code",
          max_context_size: 262144,
        },
      ],
    },
    isOfficial: true,
    category: "cn_official",
    icon: "kimi",
  },
  {
    name: "OpenAI Compatible",
    websiteUrl: "",
    settingsConfig: {
      name: "custom-openai",
      type: "openai",
      base_url: "https://api.openai.com/v1",
      api_key: "",
      models: [
        {
          id: "gpt-5.5",
          alias: "custom-openai/gpt-5.5",
          name: "GPT-5.5",
          max_context_size: 128000,
        },
      ],
    },
    category: "custom",
    icon: "openai",
  },
];

export function getKimiPresetByName(
  name: string,
): KimiProviderPreset | undefined {
  return kimiProviderPresets.find((p) => p.name === name);
}
