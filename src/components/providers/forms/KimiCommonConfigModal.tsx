import React, { useEffect, useMemo, useState } from "react";
import { Save, Package } from "lucide-react";
import { useTranslation } from "react-i18next";
import { FullScreenPanel } from "@/components/common/FullScreenPanel";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import JsonEditor from "@/components/JsonEditor";
import {
  KIMI_THINKING_EFFORTS,
  canEditKimiThinkingStructured,
  mergeKimiThinkingState,
  parseKimiThinkingState,
  type KimiThinkingEffort,
  type KimiThinkingState,
} from "@/utils/kimiThinkingConfig";

interface KimiCommonConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  value: string;
  onSave: (value: string) => boolean | Promise<boolean>;
  error?: string;
}

/**
 * Kimi Code shared live-config snippet editor.
 * Structured controls map to global `[thinking]` (effort / enabled);
 * advanced users can still edit the full TOML.
 */
export const KimiCommonConfigModal: React.FC<KimiCommonConfigModalProps> = ({
  isOpen,
  onClose,
  value,
  onSave,
  error,
}) => {
  const { t } = useTranslation();
  const [isDarkMode, setIsDarkMode] = useState(false);
  const [draftValue, setDraftValue] = useState(value);

  useEffect(() => {
    setIsDarkMode(document.documentElement.classList.contains("dark"));

    const observer = new MutationObserver(() => {
      setIsDarkMode(document.documentElement.classList.contains("dark"));
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (isOpen) {
      setDraftValue(value);
    }
  }, [isOpen, value]);

  const structuredOk = canEditKimiThinkingStructured(draftValue);
  const thinking = useMemo(
    () => parseKimiThinkingState(draftValue),
    [draftValue],
  );

  const applyThinking = (next: KimiThinkingState) => {
    const merged = mergeKimiThinkingState(draftValue, next);
    if (merged === null) return;
    setDraftValue(merged);
  };

  const handleClose = () => {
    setDraftValue(value);
    onClose();
  };

  const handleSave = async () => {
    if (await onSave(draftValue)) {
      onClose();
    }
  };

  return (
    <FullScreenPanel
      isOpen={isOpen}
      title={t("kimicode.commonConfig.editTitle")}
      onClose={handleClose}
      footer={
        <>
          <Button type="button" variant="outline" onClick={handleClose}>
            {t("common.cancel")}
          </Button>
          <Button type="button" onClick={handleSave} className="gap-2">
            <Save className="w-4 h-4" />
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/30 p-3 space-y-1.5">
          <p className="text-sm font-medium text-blue-800 dark:text-blue-300">
            {t("commonConfig.guideTitle")}
          </p>
          <p className="text-xs text-blue-700/80 dark:text-blue-400/80">
            {t("commonConfig.guidePurpose")}
          </p>
          <p className="text-xs text-blue-700/80 dark:text-blue-400/80">
            {t("kimicode.commonConfig.guideUsage")}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("commonConfig.guideReassurance")}
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          {t("kimicode.commonConfig.hint")}
        </p>

        {/* Structured thinking intensity — maps to global [thinking] in config.toml */}
        <div className="rounded-lg border border-border p-4 space-y-3">
          <div>
            <p className="text-sm font-medium">
              {t("kimicode.thinking.title", {
                defaultValue: "思维强度",
              })}
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {t("kimicode.thinking.hint", {
                defaultValue:
                  "写入 Kimi Code 全局 [thinking]（effort / enabled）。与 Claude「最大强度思考」、Codex「思考等级」同类；保存后合并进 live config.toml。",
              })}
            </p>
          </div>

          {!structuredOk ? (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              {t("kimicode.thinking.invalidToml", {
                defaultValue:
                  "当前片段 TOML 无效，请先在下方编辑器修复后再用可视化控件。",
              })}
            </p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="kimi-thinking-effort">
                  {t("kimicode.thinking.effort", {
                    defaultValue: "思考等级 (effort)",
                  })}
                </Label>
                <Select
                  value={thinking.effort || "__unset__"}
                  onValueChange={(v) =>
                    applyThinking({
                      ...thinking,
                      effort:
                        v === "__unset__" ? "" : (v as KimiThinkingEffort),
                    })
                  }
                >
                  <SelectTrigger id="kimi-thinking-effort">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__unset__">
                      {t("kimicode.thinking.effortUnset", {
                        defaultValue: "不设置（沿用 CLI / 模型默认）",
                      })}
                    </SelectItem>
                    {KIMI_THINKING_EFFORTS.map((level) => (
                      <SelectItem key={level} value={level}>
                        {level}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3 min-h-9">
                  <div>
                    <Label htmlFor="kimi-thinking-enabled">
                      {t("kimicode.thinking.enabled", {
                        defaultValue: "显式启用 Thinking",
                      })}
                    </Label>
                    <p className="text-xs text-muted-foreground">
                      {t("kimicode.thinking.enabledHint", {
                        defaultValue:
                          "写入 enabled = true/false；关闭「不设置」则只保留 effort。",
                      })}
                    </p>
                  </div>
                  <Switch
                    id="kimi-thinking-enabled"
                    checked={thinking.enabled === true}
                    onCheckedChange={(checked) =>
                      applyThinking({
                        ...thinking,
                        // Unchecked → drop the key (null), not force false,
                        // matching live configs that only set effort.
                        enabled: checked ? true : null,
                      })
                    }
                  />
                </div>
                {thinking.enabled === false && (
                  <p className="text-xs text-muted-foreground">
                    {t("kimicode.thinking.enabledFalse", {
                      defaultValue: "当前片段含 enabled = false。",
                    })}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        {(!draftValue || draftValue.trim() === "") && (
          <div className="flex flex-col items-center justify-center py-6 text-center text-muted-foreground">
            <Package className="h-8 w-8 mb-2 opacity-40" />
            <p className="text-sm font-medium">
              {t("commonConfig.emptyTitle")}
            </p>
            <p className="text-xs mt-1">{t("kimicode.commonConfig.emptyHint")}</p>
          </div>
        )}

        <div className="space-y-1">
          <Label>
            {t("kimicode.commonConfig.rawToml", {
              defaultValue: "高级：完整 TOML 片段",
            })}
          </Label>
          <JsonEditor
            value={draftValue}
            onChange={setDraftValue}
            placeholder={`# Common Kimi Code config
# [thinking]
# effort = "max"
# enabled = true`}
            darkMode={isDarkMode}
            rows={14}
            showValidation={false}
            language="javascript"
          />
        </div>

        {error && (
          <p className="text-sm text-red-500 dark:text-red-400">{error}</p>
        )}
      </div>
    </FullScreenPanel>
  );
};
