import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { parse as parseToml } from "smol-toml";
import { configApi } from "@/lib/api";
import { normalizeTomlText } from "@/utils/textNormalization";

const DEFAULT_KIMI_COMMON_CONFIG_SNIPPET = `# Common Kimi Code config
# Add your common TOML configuration here`;

interface UseKimiCommonConfigProps {
  enabled: boolean;
}

/**
 * 管理 Kimi Code 通用配置片段（TOML 格式）。
 * 与 Codex 不同：Kimi Code 的 live 配置是所有供应商共享的 additive TOML
 * 文档，片段由后端在同步时直接合并进 live config.toml，不存在"勾选写入
 * 单个供应商配置"的开关，因此这里只负责片段的加载 / 校验 / 保存。
 */
export function useKimiCommonConfig({ enabled }: UseKimiCommonConfigProps) {
  const { t } = useTranslation();
  const [commonConfigSnippet, setCommonConfigSnippetState] = useState<string>(
    DEFAULT_KIMI_COMMON_CONFIG_SNIPPET,
  );
  const [commonConfigError, setCommonConfigError] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  // 初始化：从 config.json 读取 Kimi Code 通用配置片段
  useEffect(() => {
    if (!enabled) {
      setIsLoading(false);
      return;
    }

    let mounted = true;

    const loadSnippet = async () => {
      try {
        const snippet = await configApi.getCommonConfigSnippet("kimicode");
        if (mounted && snippet && snippet.trim()) {
          setCommonConfigSnippetState(snippet);
        }
      } catch (error) {
        console.error("加载 Kimi Code 通用配置失败:", error);
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    };

    loadSnippet();

    return () => {
      mounted = false;
    };
  }, [enabled]);

  // 保存片段：先做前端 TOML 校验，再落库（后端会二次校验并触发 live 重同步）
  const handleCommonConfigSnippetChange = useCallback(
    async (value: string): Promise<boolean> => {
      const trimmed = value.trim();
      // 与校验口径一致：弯引号/全角引号先归一化再保存，
      // 否则后端 toml_edit 会拒绝原始弯引号文本。
      const normalized = trimmed ? normalizeTomlText(value) : "";

      if (trimmed) {
        try {
          parseToml(normalized);
        } catch (error) {
          setCommonConfigError(
            error instanceof Error ? error.message : String(error),
          );
          return false;
        }
      }

      try {
        await configApi.setCommonConfigSnippet("kimicode", normalized);
      } catch (error) {
        console.error("保存 Kimi Code 通用配置失败:", error);
        setCommonConfigError(
          t("codexConfig.saveFailed", { error: String(error) }),
        );
        // 后端可能已部分落库（如孤儿接管：DB 已保存、仅 backup 写入失败），
        // 重新拉取片段使本地状态与 DB 一致，避免下次打开用旧值覆盖。
        try {
          const snippet = await configApi.getCommonConfigSnippet("kimicode");
          setCommonConfigSnippetState(snippet ?? "");
        } catch (refetchError) {
          console.error("重拉 Kimi Code 通用配置失败:", refetchError);
        }
        return false;
      }

      setCommonConfigError("");
      setCommonConfigSnippetState(normalized);
      return true;
    },
    [t],
  );

  const clearCommonConfigError = useCallback(() => {
    setCommonConfigError("");
  }, []);

  return {
    commonConfigSnippet,
    commonConfigError,
    isLoading,
    handleCommonConfigSnippetChange,
    clearCommonConfigError,
  };
}
