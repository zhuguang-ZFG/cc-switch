import { useState, useCallback, useMemo } from "react";
import type { AppId } from "@/lib/api";
import { useProvidersQuery } from "@/lib/query/queries";
import {
  type KimiModel,
  type KimiProviderSettingsConfig,
  type KimiProviderType,
} from "@/config/kimiProviderPresets";

export const KIMI_DEFAULT_PROVIDER_TYPE: KimiProviderType = "openai";

interface UseHermesFormStateParams {
  initialData?: {
    settingsConfig?: Record<string, unknown>;
  };
  appId: AppId;
  providerId?: string;
  onSettingsConfigChange: (config: string) => void;
  getSettingsConfig: () => string;
}

const HERMES_DEFAULT_CONFIG_OBJ = {
  name: "",
  type: KIMI_DEFAULT_PROVIDER_TYPE,
  base_url: "",
  api_key: "",
} as const;

export const HERMES_DEFAULT_CONFIG = JSON.stringify(
  HERMES_DEFAULT_CONFIG_OBJ,
  null,
  2,
);

export interface HermesFormState {
  hermesProviderKey: string;
  setHermesProviderKey: (key: string) => void;
  hermesBaseUrl: string;
  hermesApiKey: string;
  hermesApiMode: KimiProviderType;
  hermesModels: KimiModel[];
  hermesRateLimitDelay: number | undefined;
  hermesEnv: Record<string, string>;
  hermesCustomHeaders: Record<string, string>;
  existingHermesKeys: string[];
  handleHermesBaseUrlChange: (baseUrl: string) => void;
  handleHermesApiKeyChange: (apiKey: string) => void;
  handleHermesApiModeChange: (mode: KimiProviderType) => void;
  handleHermesModelsChange: (models: KimiModel[]) => void;
  handleHermesRateLimitDelayChange: (delay: number | undefined) => void;
  handleHermesEnvFieldChange: (key: string, value: string) => void;
  handleHermesCustomHeadersChange: (headers: Record<string, string>) => void;
  resetHermesState: (config?: Partial<KimiProviderSettingsConfig>) => void;
}

function parseHermesField<T>(
  initialData: UseHermesFormStateParams["initialData"],
  field: string,
  fallback: T,
): T {
  try {
    if (initialData?.settingsConfig) {
      return (initialData.settingsConfig[field] as T) || fallback;
    }
    return (
      ((HERMES_DEFAULT_CONFIG_OBJ as Record<string, unknown>)[field] as T) ||
      fallback
    );
  } catch {
    return fallback;
  }
}

function parseRateLimitDelay(raw: unknown): number | undefined {
  return typeof raw === "number" && Number.isFinite(raw) && raw >= 0
    ? raw
    : undefined;
}

// Legacy Hermes configs stored the protocol as `api_mode`; map it to the
// Kimi-native `type` so editing an old provider doesn't reset the protocol.
function legacyApiModeToType(mode: unknown): KimiProviderType | "" {
  switch (mode) {
    case "anthropic_messages":
      return "anthropic";
    case "codex_responses":
      return "openai_responses";
    case "chat_completions":
      return "openai";
    default:
      return "";
  }
}

function parseEnvMap(raw: unknown): Record<string, string> {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === "string") out[key] = value;
  }
  return out;
}

// Legacy Hermes models carried `context_length`; Kimi uses `max_context_size`.
function normalizeLegacyModels(models: KimiModel[]): KimiModel[] {
  return models.map((model) => {
    const legacy = (model as KimiModel & { context_length?: unknown })
      .context_length;
    if (
      model.max_context_size === undefined &&
      typeof legacy === "number" &&
      Number.isFinite(legacy) &&
      legacy > 0
    ) {
      return { ...model, max_context_size: legacy };
    }
    return model;
  });
}

export function useHermesFormState({
  initialData,
  appId,
  providerId,
  onSettingsConfigChange,
  getSettingsConfig,
}: UseHermesFormStateParams): HermesFormState {
  const { data: hermesProvidersData } = useProvidersQuery("kimicode");
  const existingHermesKeys = useMemo(() => {
    if (!hermesProvidersData?.providers) return [];
    return Object.keys(hermesProvidersData.providers).filter(
      (k) => k !== providerId,
    );
  }, [hermesProvidersData?.providers, providerId]);

  const [hermesProviderKey, setHermesProviderKey] = useState<string>(() => {
    if (appId !== "kimicode") return "";
    return providerId || "";
  });

  const [hermesBaseUrl, setHermesBaseUrl] = useState<string>(() => {
    if (appId !== "kimicode") return "";
    return parseHermesField(initialData, "base_url", "");
  });

  const [hermesApiKey, setHermesApiKey] = useState<string>(() => {
    if (appId !== "kimicode") return "";
    return parseHermesField(initialData, "api_key", "");
  });

  const [hermesApiMode, setHermesApiMode] = useState<KimiProviderType>(() => {
    if (appId !== "kimicode") return KIMI_DEFAULT_PROVIDER_TYPE;
    const stored = parseHermesField<KimiProviderType | "">(
      initialData,
      "type",
      "",
    );
    return (
      stored ||
      legacyApiModeToType(initialData?.settingsConfig?.api_mode) ||
      KIMI_DEFAULT_PROVIDER_TYPE
    );
  });

  const [hermesModels, setHermesModels] = useState<KimiModel[]>(() => {
    if (appId !== "kimicode") return [];
    return normalizeLegacyModels(
      parseHermesField<KimiModel[]>(initialData, "models", []),
    );
  });

  const [hermesRateLimitDelay, setHermesRateLimitDelay] = useState<
    number | undefined
  >(() => {
    if (appId !== "kimicode") return undefined;
    return parseRateLimitDelay(initialData?.settingsConfig?.rate_limit_delay);
  });

  const [hermesEnv, setHermesEnv] = useState<Record<string, string>>(() => {
    if (appId !== "kimicode") return {};
    return parseEnvMap(initialData?.settingsConfig?.env);
  });

  const [hermesCustomHeaders, setHermesCustomHeaders] = useState<
    Record<string, string>
  >(() => {
    if (appId !== "kimicode") return {};
    return parseEnvMap(initialData?.settingsConfig?.custom_headers);
  });

  const updateHermesConfig = useCallback(
    (updater: (config: Record<string, unknown>) => void) => {
      try {
        const config = JSON.parse(getSettingsConfig() || HERMES_DEFAULT_CONFIG);
        updater(config);
        onSettingsConfigChange(JSON.stringify(config, null, 2));
      } catch {
        // ignore
      }
    },
    [getSettingsConfig, onSettingsConfigChange],
  );

  const handleHermesBaseUrlChange = useCallback(
    (baseUrl: string) => {
      setHermesBaseUrl(baseUrl);
      updateHermesConfig((config) => {
        config.base_url = baseUrl.trim().replace(/\/+$/, "");
      });
    },
    [updateHermesConfig],
  );

  const handleHermesApiKeyChange = useCallback(
    (apiKey: string) => {
      setHermesApiKey(apiKey);
      updateHermesConfig((config) => {
        config.api_key = apiKey;
      });
    },
    [updateHermesConfig],
  );

  const handleHermesApiModeChange = useCallback(
    (mode: KimiProviderType) => {
      setHermesApiMode(mode);
      updateHermesConfig((config) => {
        config.type = mode;
        delete config.api_mode;
      });
    },
    [updateHermesConfig],
  );

  const handleHermesModelsChange = useCallback(
    (models: KimiModel[]) => {
      setHermesModels(models);
      updateHermesConfig((config) => {
        if (models.length === 0) {
          delete config.models;
        } else {
          config.models = models;
        }
      });
    },
    [updateHermesConfig],
  );

  const handleHermesRateLimitDelayChange = useCallback(
    (delay: number | undefined) => {
      setHermesRateLimitDelay(delay);
      updateHermesConfig((config) => {
        if (delay === undefined) {
          delete config.rate_limit_delay;
        } else {
          config.rate_limit_delay = delay;
        }
      });
    },
    [updateHermesConfig],
  );

  const handleHermesEnvFieldChange = useCallback(
    (key: string, value: string) => {
      setHermesEnv((prev) => {
        const next = { ...prev };
        if (value.trim()) {
          next[key] = value;
        } else {
          delete next[key];
        }
        return next;
      });
      updateHermesConfig((config) => {
        const env =
          typeof config.env === "object" &&
          config.env !== null &&
          !Array.isArray(config.env)
            ? { ...(config.env as Record<string, unknown>) }
            : {};
        if (value.trim()) {
          env[key] = value.trim();
        } else {
          delete env[key];
        }
        if (Object.keys(env).length === 0) {
          delete config.env;
        } else {
          config.env = env;
        }
      });
    },
    [updateHermesConfig],
  );

  const handleHermesCustomHeadersChange = useCallback(
    (headers: Record<string, string>) => {
      const cleaned: Record<string, string> = {};
      for (const [key, value] of Object.entries(headers)) {
        const k = key.trim();
        const v = value.trim();
        if (k && v) cleaned[k] = v;
      }
      setHermesCustomHeaders(cleaned);
      updateHermesConfig((config) => {
        if (Object.keys(cleaned).length === 0) {
          delete config.custom_headers;
        } else {
          config.custom_headers = cleaned;
        }
      });
    },
    [updateHermesConfig],
  );

  const resetHermesState = useCallback(
    (config?: Partial<KimiProviderSettingsConfig>) => {
      setHermesProviderKey("");
      setHermesBaseUrl(config?.base_url || "");
      setHermesApiKey(config?.api_key || "");
      setHermesApiMode(config?.type ?? KIMI_DEFAULT_PROVIDER_TYPE);
      setHermesModels(config?.models ?? []);
      setHermesRateLimitDelay(parseRateLimitDelay(config?.rate_limit_delay));
      setHermesEnv(parseEnvMap(config?.env));
      setHermesCustomHeaders(parseEnvMap(config?.custom_headers));
    },
    [],
  );

  return {
    hermesProviderKey,
    setHermesProviderKey,
    hermesBaseUrl,
    hermesApiKey,
    hermesApiMode,
    hermesModels,
    hermesRateLimitDelay,
    hermesEnv,
    hermesCustomHeaders,
    existingHermesKeys,
    handleHermesBaseUrlChange,
    handleHermesApiKeyChange,
    handleHermesApiModeChange,
    handleHermesModelsChange,
    handleHermesRateLimitDelayChange,
    handleHermesEnvFieldChange,
    handleHermesCustomHeadersChange,
    resetHermesState,
  };
}
