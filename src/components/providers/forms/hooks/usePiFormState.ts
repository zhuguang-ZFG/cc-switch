import { useState, useCallback, useMemo } from "react";
import type { AppId } from "@/lib/api";
import { useProvidersQuery } from "@/lib/query/queries";
import {
  PI_DEFAULT_API,
  type PiProviderSettingsConfig,
} from "@/config/piProviderPresets";

const PI_DEFAULT_CONFIG_OBJ = {
  name: "",
  baseUrl: "",
  api: PI_DEFAULT_API,
  apiKey: "",
  models: [] as Array<{ id: string; name: string }>,
  defaultModel: "",
  compat: {
    supportsStore: false,
    supportsDeveloperRole: false,
    maxTokensField: "max_tokens",
  },
} as const;

export const PI_DEFAULT_CONFIG = JSON.stringify(PI_DEFAULT_CONFIG_OBJ, null, 2);

interface UsePiFormStateParams {
  initialData?: {
    settingsConfig?: Record<string, unknown>;
  };
  appId: AppId;
  providerId?: string;
  onSettingsConfigChange: (config: string) => void;
  getSettingsConfig: () => string;
}

export interface PiFormState {
  piProviderName: string;
  setPiProviderName: (name: string) => void;
  piBaseUrl: string;
  piApiKey: string;
  piModels: string[];
  piDefaultModel: string;
  existingPiKeys: string[];
  handlePiBaseUrlChange: (baseUrl: string) => void;
  handlePiApiKeyChange: (apiKey: string) => void;
  handlePiModelsChange: (models: string[]) => void;
  handlePiDefaultModelChange: (model: string) => void;
  resetPiState: (config?: Partial<PiProviderSettingsConfig>) => void;
}

function parseField<T>(
  initialData: UsePiFormStateParams["initialData"],
  field: string,
  fallback: T,
): T {
  try {
    if (initialData?.settingsConfig) {
      return (initialData.settingsConfig[field] as T) ?? fallback;
    }
    return (
      ((PI_DEFAULT_CONFIG_OBJ as Record<string, unknown>)[field] as T) ??
      fallback
    );
  } catch {
    return fallback;
  }
}

function parseModelsForUi(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const mapped = raw.map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object" && "id" in item) {
      const id = (item as { id?: unknown }).id;
      return typeof id === "string" ? id : "";
    }
    return "";
  });
  if (mapped.length === 0) return [];
  if (mapped.every((m) => !String(m).trim())) return [""];
  return mapped.map((m) => String(m));
}

function normalizeDefaultModel(models: string[], currentDefault: string): string {
  const trimmed = currentDefault.trim();
  if (trimmed && models.includes(trimmed)) return trimmed;
  return models[0] ?? "";
}

function writeConfig(
  getSettingsConfig: () => string,
  onSettingsConfigChange: (config: string) => void,
  patch: Record<string, unknown>,
) {
  try {
    const current = JSON.parse(getSettingsConfig() || "{}") as Record<
      string,
      unknown
    >;
    onSettingsConfigChange(JSON.stringify({ ...current, ...patch }, null, 2));
  } catch {
    onSettingsConfigChange(
      JSON.stringify({ ...PI_DEFAULT_CONFIG_OBJ, ...patch }, null, 2),
    );
  }
}

export function usePiFormState({
  initialData,
  appId,
  providerId,
  onSettingsConfigChange,
  getSettingsConfig,
}: UsePiFormStateParams): PiFormState {
  const { data: piProvidersData } = useProvidersQuery("pi");
  const existingPiKeys = useMemo(() => {
    if (!piProvidersData?.providers) return [];
    return Object.keys(piProvidersData.providers).filter((k) => k !== providerId);
  }, [piProvidersData?.providers, providerId]);

  const initialModels = useMemo(
    () =>
      appId === "pi"
        ? parseModelsForUi(parseField(initialData, "models", []))
        : [],
    [appId, initialData],
  );

  const [piProviderName, setPiProviderNameState] = useState<string>(() => {
    if (appId !== "pi") return "";
    return providerId || parseField<string>(initialData, "name", "");
  });

  const [piBaseUrl, setPiBaseUrl] = useState<string>(() => {
    if (appId !== "pi") return "";
    const camel = parseField<string>(initialData, "baseUrl", "");
    if (camel) return camel;
    return parseField<string>(initialData, "base_url", "");
  });

  const [piApiKey, setPiApiKey] = useState<string>(() => {
    if (appId !== "pi") return "";
    const camel = parseField<string>(initialData, "apiKey", "");
    if (camel) return camel;
    return parseField<string>(initialData, "api_key", "");
  });

  const [piModels, setPiModels] = useState<string[]>(() => initialModels);
  const [piDefaultModel, setPiDefaultModel] = useState<string>(() => {
    if (appId !== "pi") return "";
    const dm = parseField<string>(initialData, "defaultModel", "");
    return normalizeDefaultModel(initialModels.filter(Boolean), dm);
  });

  const setPiProviderName = useCallback(
    (name: string) => {
      setPiProviderNameState(name);
      writeConfig(getSettingsConfig, onSettingsConfigChange, { name });
    },
    [getSettingsConfig, onSettingsConfigChange],
  );

  const handlePiBaseUrlChange = useCallback(
    (baseUrl: string) => {
      setPiBaseUrl(baseUrl);
      writeConfig(getSettingsConfig, onSettingsConfigChange, { baseUrl });
    },
    [getSettingsConfig, onSettingsConfigChange],
  );

  const handlePiApiKeyChange = useCallback(
    (apiKey: string) => {
      setPiApiKey(apiKey);
      writeConfig(getSettingsConfig, onSettingsConfigChange, { apiKey });
    },
    [getSettingsConfig, onSettingsConfigChange],
  );

  const handlePiModelsChange = useCallback(
    (models: string[]) => {
      setPiModels(models);
      const cleaned = models.map((m) => m.trim()).filter(Boolean);
      const defaultModel = normalizeDefaultModel(cleaned, piDefaultModel);
      setPiDefaultModel(defaultModel);
      writeConfig(getSettingsConfig, onSettingsConfigChange, {
        models: cleaned.map((id) => ({ id, name: id })),
        defaultModel,
      });
    },
    [getSettingsConfig, onSettingsConfigChange, piDefaultModel],
  );

  const handlePiDefaultModelChange = useCallback(
    (model: string) => {
      setPiDefaultModel(model);
      writeConfig(getSettingsConfig, onSettingsConfigChange, {
        defaultModel: model,
      });
    },
    [getSettingsConfig, onSettingsConfigChange],
  );

  const resetPiState = useCallback(
    (config?: Partial<PiProviderSettingsConfig>) => {
      const name = (config?.name as string) || "";
      const baseUrl =
        (config?.baseUrl as string) || (config?.base_url as string) || "";
      const apiKey =
        (config?.apiKey as string) || (config?.api_key as string) || "";
      const models = parseModelsForUi(config?.models ?? []);
      const defaultModel = normalizeDefaultModel(
        models.filter(Boolean),
        (config?.defaultModel as string) || "",
      );
      setPiProviderNameState(name);
      setPiBaseUrl(baseUrl);
      setPiApiKey(apiKey);
      setPiModels(models);
      setPiDefaultModel(defaultModel);
      onSettingsConfigChange(
        JSON.stringify(
          {
            name,
            baseUrl,
            api: PI_DEFAULT_API,
            apiKey,
            models: models
              .filter(Boolean)
              .map((id) => ({ id, name: id })),
            defaultModel,
            compat: {
              supportsStore: false,
              supportsDeveloperRole: false,
              maxTokensField: "max_tokens",
            },
          },
          null,
          2,
        ),
      );
    },
    [onSettingsConfigChange],
  );

  return {
    piProviderName,
    setPiProviderName,
    piBaseUrl,
    piApiKey,
    piModels,
    piDefaultModel,
    existingPiKeys,
    handlePiBaseUrlChange,
    handlePiApiKeyChange,
    handlePiModelsChange,
    handlePiDefaultModelChange,
    resetPiState,
  };
}
