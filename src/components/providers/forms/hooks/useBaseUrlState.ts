import { useState, useCallback, useRef, useEffect } from "react";
import {
  extractCodexBaseUrl,
  setCodexBaseUrl as setCodexBaseUrlInConfig,
} from "@/utils/providerConfigUtils";
import type { ProviderCategory } from "@/types";
import type { AppId } from "@/lib/api";

interface UseBaseUrlStateProps {
  appType: AppId;
  category: ProviderCategory | undefined;
  settingsConfig: string;
  codexConfig?: string;
  /**
   * Returns the freshest settingsConfig at call time (form.getValues). Needed
   * because raw-JSON edits (form.setValue) don't re-render the parent, so the
   * `settingsConfig` prop snapshot goes stale; falls back to it when omitted.
   */
  getLatestSettingsConfig?: () => string | undefined;
  onSettingsConfigChange: (config: string) => void;
  onCodexConfigChange?: (config: string) => void;
}

/**
 * 管理 Base URL 状态
 * 支持 Claude (JSON) 和 Codex (TOML) 两种格式
 */
export function useBaseUrlState({
  appType,
  category,
  settingsConfig,
  codexConfig,
  getLatestSettingsConfig,
  onSettingsConfigChange,
  onCodexConfigChange,
}: UseBaseUrlStateProps) {
  const [baseUrl, setBaseUrl] = useState("");
  const [codexBaseUrl, setCodexBaseUrl] = useState("");
  const [geminiBaseUrl, setGeminiBaseUrl] = useState("");
  const isUpdatingRef = useRef(false);

  // Getter reads the freshest config outside React's render cycle (sees
  // raw-JSON edits that never re-rendered the parent). codexConfig has no
  // getter today (Codex base-url edits go through a dedicated editor), so it
  // keeps the prop-ref which is refreshed each render.
  const getLatestSettingsConfigRef = useRef(getLatestSettingsConfig);
  getLatestSettingsConfigRef.current = getLatestSettingsConfig;
  const readSettings = () =>
    getLatestSettingsConfigRef.current?.() ?? settingsConfig ?? "{}";
  const latestCodexConfigRef = useRef(codexConfig);
  latestCodexConfigRef.current = codexConfig;

  // 从配置同步到 state（Claude / Claude Desktop）
  useEffect(() => {
    if (appType !== "claude" && appType !== "claude-desktop") return;
    // 只有 official 类别不显示 Base URL 输入框，其他类别都需要回填
    if (category === "official") return;
    if (isUpdatingRef.current) return;

    try {
      const config = JSON.parse(settingsConfig || "{}");
      const envUrl: unknown = config?.env?.ANTHROPIC_BASE_URL;
      const nextUrl = typeof envUrl === "string" ? envUrl.trim() : "";
      if (nextUrl !== baseUrl) {
        setBaseUrl(nextUrl);
      }
    } catch {
      // ignore
    }
  }, [appType, category, settingsConfig, baseUrl]);

  // 从配置同步到 state（Codex）
  useEffect(() => {
    if (appType !== "codex") return;
    // 只有 official 类别不显示 Base URL 输入框，其他类别都需要回填
    if (category === "official") return;
    if (isUpdatingRef.current) return;
    if (!codexConfig) return;

    const extracted = extractCodexBaseUrl(codexConfig) || "";
    setCodexBaseUrl((prev) => (prev === extracted ? prev : extracted));
  }, [appType, category, codexConfig]);

  // 从Claude配置同步到 state（Gemini）
  useEffect(() => {
    if ((appType as string) !== "gemini") return;
    // 只有 official 类别不显示 Base URL 输入框，其他类别都需要回填
    if (category === "official") return;
    if (isUpdatingRef.current) return;

    try {
      const config = JSON.parse(settingsConfig || "{}");
      const envUrl: unknown = config?.env?.GOOGLE_GEMINI_BASE_URL;
      const nextUrl = typeof envUrl === "string" ? envUrl.trim() : "";
      if (nextUrl !== geminiBaseUrl) {
        setGeminiBaseUrl(nextUrl);
        setBaseUrl(nextUrl); // 也更新 baseUrl 用于 UI
      }
    } catch {
      // ignore
    }
  }, [appType, category, settingsConfig, geminiBaseUrl]);

  // 处理 Claude Base URL 变化
  const handleClaudeBaseUrlChange = useCallback(
    (url: string) => {
      const sanitized = url.trim();
      setBaseUrl(sanitized);
      isUpdatingRef.current = true;

      try {
        const config = JSON.parse(readSettings());
        if (!config.env) {
          config.env = {};
        }
        config.env.ANTHROPIC_BASE_URL = sanitized;
        onSettingsConfigChange(JSON.stringify(config, null, 2));
      } catch {
        // ignore
      } finally {
        setTimeout(() => {
          isUpdatingRef.current = false;
        }, 0);
      }
    },
    [onSettingsConfigChange],
  );

  // 处理 Codex Base URL 变化
  const handleCodexBaseUrlChange = useCallback(
    (url: string) => {
      const sanitized = url.trim();
      setCodexBaseUrl(sanitized);

      if (!onCodexConfigChange) {
        return;
      }

      isUpdatingRef.current = true;
      const updatedConfig = setCodexBaseUrlInConfig(
        latestCodexConfigRef.current || "",
        sanitized,
      );
      onCodexConfigChange(updatedConfig);

      setTimeout(() => {
        isUpdatingRef.current = false;
      }, 0);
    },
    [onCodexConfigChange],
  );

  // 处理 Gemini Base URL 变化
  const handleGeminiBaseUrlChange = useCallback(
    (url: string) => {
      const sanitized = url.trim();
      setGeminiBaseUrl(sanitized);
      setBaseUrl(sanitized); // 也更新 baseUrl 用于 UI
      isUpdatingRef.current = true;

      try {
        const config = JSON.parse(readSettings());
        if (!config.env) {
          config.env = {};
        }
        config.env.GOOGLE_GEMINI_BASE_URL = sanitized;
        onSettingsConfigChange(JSON.stringify(config, null, 2));
      } catch {
        // ignore
      } finally {
        setTimeout(() => {
          isUpdatingRef.current = false;
        }, 0);
      }
    },
    [onSettingsConfigChange],
  );

  return {
    baseUrl,
    setBaseUrl,
    codexBaseUrl,
    setCodexBaseUrl,
    geminiBaseUrl,
    setGeminiBaseUrl,
    handleClaudeBaseUrlChange,
    handleCodexBaseUrlChange,
    handleGeminiBaseUrlChange,
  };
}
