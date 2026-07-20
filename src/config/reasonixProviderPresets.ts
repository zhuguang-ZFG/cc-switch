/**
 * Reasonix CLI provider presets.
 * Live config: ~/.reasonix/config.toml（Windows: %APPDATA%\reasonix\config.toml）
 *   [providers.<name>] kind / base_url / api_key / models / default
 * 累加模式：供应商叠加写入 live 配置，切换只改默认模型。
 */
import type { ProviderCategory } from "../types";

export type ReasonixProviderKind = "openai" | "anthropic";

export const REASONIX_PROVIDER_KINDS: Array<{
  value: ReasonixProviderKind;
  labelKey: string;
}> = [
  { value: "openai", labelKey: "reasonix.form.kindOpenai" },
  { value: "anthropic", labelKey: "reasonix.form.kindAnthropic" },
];

export interface ReasonixProviderSettingsConfig {
  name: string;
  kind: ReasonixProviderKind;
  base_url?: string;
  api_key?: string;
  /** 模型 ID 列表（字符串数组） */
  models?: string[];
  /** 默认模型；缺省取 models[0] */
  default?: string;
  /** 可选：完全覆盖 Chat Completions URL */
  chat_url?: string;
  /** 可选：覆盖 /models 探测 URL */
  models_url?: string;
  [key: string]: unknown;
}

export interface ReasonixProviderPreset {
  name: string;
  nameKey?: string;
  websiteUrl: string;
  apiKeyUrl?: string;
  settingsConfig: ReasonixProviderSettingsConfig;
  isOfficial?: boolean;
  isPartner?: boolean;
  partnerPromotionKey?: string;
  category?: ProviderCategory;
  icon?: string;
  iconColor?: string;
}

export const reasonixProviderPresets: ReasonixProviderPreset[] = [
  {
    name: "DeepSeek 官方",
    nameKey: "reasonix.presets.deepseekOfficial",
    websiteUrl: "https://platform.deepseek.com",
    apiKeyUrl: "https://platform.deepseek.com/api_keys",
    settingsConfig: {
      name: "deepseek",
      kind: "openai",
      base_url: "https://api.deepseek.com",
      api_key: "",
      models: ["deepseek-v4-flash", "deepseek-v4-pro"],
      default: "deepseek-v4-flash",
    },
    isOfficial: true,
    category: "cn_official",
    icon: "deepseek",
  },
  {
    name: "Custom",
    nameKey: "reasonix.presets.custom",
    websiteUrl: "",
    settingsConfig: {
      name: "",
      kind: "openai",
      base_url: "",
      api_key: "",
    },
    category: "custom",
  },
];

export function getReasonixPresetByName(
  name: string,
): ReasonixProviderPreset | undefined {
  return reasonixProviderPresets.find((p) => p.name === name);
}
