import { useState, useCallback, useMemo } from "react";
import type { AppId } from "@/lib/api";
import { useProvidersQuery } from "@/lib/query/queries";
import {
  type ReasonixProviderKind,
  type ReasonixProviderSettingsConfig,
} from "@/config/reasonixProviderPresets";

export const REASONIX_DEFAULT_KIND: ReasonixProviderKind = "openai";

const REASONIX_DEFAULT_CONFIG_OBJ = {
  name: "",
  kind: REASONIX_DEFAULT_KIND,
  base_url: "",
  api_key: "",
  models: [] as string[],
  default: "",
} as const;

export const REASONIX_DEFAULT_CONFIG = JSON.stringify(
  REASONIX_DEFAULT_CONFIG_OBJ,
  null,
  2,
);

interface UseReasonixFormStateParams {
  initialData?: {
    settingsConfig?: Record<string, unknown>;
  };
  appId: AppId;
  providerId?: string;
  onSettingsConfigChange: (config: string) => void;
  getSettingsConfig: () => string;
}

export interface ReasonixFormState {
  reasonixProviderName: string;
  setReasonixProviderName: (name: string) => void;
  reasonixKind: ReasonixProviderKind;
  reasonixBaseUrl: string;
  reasonixApiKey: string;
  reasonixChatUrl: string;
  reasonixModelsUrl: string;
  reasonixModels: string[];
  reasonixDefault: string;
  existingReasonixKeys: string[];
  handleReasonixKindChange: (kind: ReasonixProviderKind) => void;
  handleReasonixBaseUrlChange: (baseUrl: string) => void;
  handleReasonixApiKeyChange: (apiKey: string) => void;
  handleReasonixChatUrlChange: (chatUrl: string) => void;
  handleReasonixModelsUrlChange: (modelsUrl: string) => void;
  handleReasonixModelsChange: (models: string[]) => void;
  handleReasonixDefaultChange: (model: string) => void;
  resetReasonixState: (config?: Partial<ReasonixProviderSettingsConfig>) => void;
}

function parseReasonixField<T>(
  initialData: UseReasonixFormStateParams["initialData"],
  field: string,
  fallback: T,
): T {
  try {
    if (initialData?.settingsConfig) {
      return (initialData.settingsConfig[field] as T) ?? fallback;
    }
    return (
      ((REASONIX_DEFAULT_CONFIG_OBJ as Record<string, unknown>)[field] as T) ??
      fallback
    );
  } catch {
    return fallback;
  }
}

function parseModels(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
}

/** UI may keep empty draft rows; persist path still uses parseModels / filter(Boolean). */
function parseModelsForUi(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const mapped = raw.map((item) => (typeof item === "string" ? item : ""));
  if (mapped.length === 0) return [];
  if (mapped.every((m) => !String(m).trim())) return [""];
  return mapped.map((m) => String(m));
}

function normalizeDefaultModel(
  models: string[],
  currentDefault: string,
): string {
  const trimmed = currentDefault.trim();
  if (trimmed && models.includes(trimmed)) return trimmed;
  return models[0] ?? "";
}

export function useReasonixFormState({
  initialData,
  appId,
  providerId,
  onSettingsConfigChange,
  getSettingsConfig,
}: UseReasonixFormStateParams): ReasonixFormState {
  const { data: reasonixProvidersData } = useProvidersQuery("reasonix");
  const existingReasonixKeys = useMemo(() => {
    if (!reasonixProvidersData?.providers) return [];
    return Object.keys(reasonixProvidersData.providers).filter(
      (k) => k !== providerId,
    );
  }, [reasonixProvidersData?.providers, providerId]);

  const initialModels = useMemo(
    () =>
      appId === "reasonix"
        ? parseModelsForUi(parseReasonixField(initialData, "models", []))
        : [],
    [appId, initialData],
  );

  const [reasonixProviderName, setReasonixProviderNameState] =
    useState<string>(() => {
      if (appId !== "reasonix") return "";
      return (
        providerId ||
        parseReasonixField<string>(initialData, "name", "")
      );
    });

  const [reasonixKind, setReasonixKind] = useState<ReasonixProviderKind>(() => {
    if (appId !== "reasonix") return REASONIX_DEFAULT_KIND;
    const stored = parseReasonixField<string>(initialData, "kind", "");
    return stored === "anthropic" ? "anthropic" : REASONIX_DEFAULT_KIND;
  });

  const [reasonixBaseUrl, setReasonixBaseUrl] = useState<string>(() => {
    if (appId !== "reasonix") return "";
    return parseReasonixField(initialData, "base_url", "");
  });

  const [reasonixApiKey, setReasonixApiKey] = useState<string>(() => {
    if (appId !== "reasonix") return "";
    return parseReasonixField(initialData, "api_key", "");
  });

  const [reasonixChatUrl, setReasonixChatUrl] = useState<string>(() => {
    if (appId !== "reasonix") return "";
    return parseReasonixField(initialData, "chat_url", "");
  });

  const [reasonixModelsUrl, setReasonixModelsUrl] = useState<string>(() => {
    if (appId !== "reasonix") return "";
    return parseReasonixField(initialData, "models_url", "");
  });

  const [reasonixModels, setReasonixModels] =
    useState<string[]>(initialModels);

  const [reasonixDefault, setReasonixDefault] = useState<string>(() => {
    if (appId !== "reasonix") return "";
    const parsedDefault = parseReasonixField<string>(initialData, "default", "");
    return normalizeDefaultModel(initialModels, parsedDefault);
  });

  const updateReasonixConfig = useCallback(
    (updater: (config: Record<string, unknown>) => void) => {
      try {
        const config = JSON.parse(getSettingsConfig() || REASONIX_DEFAULT_CONFIG);
        updater(config);
        onSettingsConfigChange(JSON.stringify(config, null, 2));
      } catch {
        // ignore parse errors during editing
      }
    },
    [getSettingsConfig, onSettingsConfigChange],
  );

  const setReasonixProviderName = useCallback(
    (name: string) => {
      setReasonixProviderNameState(name);
      updateReasonixConfig((config) => {
        config.name = name;
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixKindChange = useCallback(
    (kind: ReasonixProviderKind) => {
      setReasonixKind(kind);
      updateReasonixConfig((config) => {
        config.kind = kind;
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixBaseUrlChange = useCallback(
    (baseUrl: string) => {
      setReasonixBaseUrl(baseUrl);
      updateReasonixConfig((config) => {
        config.base_url = baseUrl.trim().replace(/\/+$/, "");
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixApiKeyChange = useCallback(
    (apiKey: string) => {
      setReasonixApiKey(apiKey);
      updateReasonixConfig((config) => {
        config.api_key = apiKey;
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixChatUrlChange = useCallback(
    (chatUrl: string) => {
      setReasonixChatUrl(chatUrl);
      updateReasonixConfig((config) => {
        const trimmed = chatUrl.trim();
        if (trimmed) {
          config.chat_url = trimmed;
        } else {
          delete config.chat_url;
        }
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixModelsUrlChange = useCallback(
    (modelsUrl: string) => {
      setReasonixModelsUrl(modelsUrl);
      updateReasonixConfig((config) => {
        const trimmed = modelsUrl.trim();
        if (trimmed) {
          config.models_url = trimmed;
        } else {
          delete config.models_url;
        }
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixModelsChange = useCallback(
    (models: string[]) => {
      // Keep empty draft rows in UI state so "Add model" / typing works.
      // Only persist non-empty IDs into settings_config.
      setReasonixModels(models);
      const normalized = models.map((m) => m.trim()).filter(Boolean);
      setReasonixDefault((prev) => normalizeDefaultModel(normalized, prev));
      updateReasonixConfig((config) => {
        if (normalized.length === 0) {
          delete config.models;
          delete config.default;
        } else {
          config.models = normalized;
          const nextDefault = normalizeDefaultModel(
            normalized,
            typeof config.default === "string" ? config.default : "",
          );
          if (nextDefault) {
            config.default = nextDefault;
          } else {
            delete config.default;
          }
        }
      });
    },
    [updateReasonixConfig],
  );

  const handleReasonixDefaultChange = useCallback(
    (model: string) => {
      const trimmed = model.trim();
      setReasonixDefault(trimmed);
      setReasonixModels((prev) => {
        const hasNonEmpty = prev.some((m) => m.trim());
        if (!hasNonEmpty && trimmed) {
          return [trimmed];
        }
        return prev;
      });
      updateReasonixConfig((config) => {
        const existingModels = Array.isArray(config.models)
          ? (config.models as unknown[])
              .map((item) => (typeof item === "string" ? item.trim() : ""))
              .filter(Boolean)
          : [];
        if (existingModels.length === 0 && trimmed) {
          config.models = [trimmed];
          config.default = trimmed;
          return;
        }
        if (!trimmed) {
          delete config.default;
        } else {
          config.default = trimmed;
        }
      });
    },
    [updateReasonixConfig],
  );

  const resetReasonixState = useCallback(
    (config?: Partial<ReasonixProviderSettingsConfig>) => {
      const nextName = config?.name ?? "";
      const nextModels = parseModelsForUi(config?.models);
      const nextDefault = normalizeDefaultModel(
        parseModels(config?.models),
        config?.default ?? "",
      );

      setReasonixProviderNameState(nextName);
      setReasonixKind(config?.kind ?? REASONIX_DEFAULT_KIND);
      setReasonixBaseUrl(config?.base_url ?? "");
      setReasonixApiKey(config?.api_key ?? "");
      setReasonixChatUrl(config?.chat_url ?? "");
      setReasonixModelsUrl(config?.models_url ?? "");
      setReasonixModels(nextModels);
      setReasonixDefault(nextDefault);
    },
    [],
  );

  return {
    reasonixProviderName,
    setReasonixProviderName,
    reasonixKind,
    reasonixBaseUrl,
    reasonixApiKey,
    reasonixChatUrl,
    reasonixModelsUrl,
    reasonixModels,
    reasonixDefault,
    existingReasonixKeys,
    handleReasonixKindChange,
    handleReasonixBaseUrlChange,
    handleReasonixApiKeyChange,
    handleReasonixChatUrlChange,
    handleReasonixModelsUrlChange,
    handleReasonixModelsChange,
    handleReasonixDefaultChange,
    resetReasonixState,
  };
}
