import { useTranslation } from "react-i18next";
import { KeyRound } from "lucide-react";
import { KimiAuthSection } from "@/components/providers/KimiAuthSection";
import { useHermesModelConfig } from "@/hooks/useHermes";

/**
 * Kimi Code 设置区块：官方 OAuth 登录/登出（复用 KimiAuthSection），
 * 以及 live 配置中当前默认模型/默认供应商的只读展示（修改入口在
 * 供应商表单与 Kimi Code CLI）。
 */
export function KimiAuthSettings() {
  const { t } = useTranslation();
  const { data: modelConfig } = useHermesModelConfig(true);

  const notConfigured = t("settings.kimiModelNotConfigured");

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2 pb-2 border-b border-border/40">
        <KeyRound className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-medium">{t("settings.kimiAuth")}</h3>
      </div>

      <KimiAuthSection />

      <div className="rounded-lg border border-border bg-card px-4 py-3 space-y-2">
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">
            {t("settings.kimiDefaultModel")}
          </span>
          <span className="font-mono text-xs break-all text-right">
            {modelConfig?.default || notConfigured}
          </span>
        </div>
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="text-muted-foreground">
            {t("settings.kimiDefaultProvider")}
          </span>
          <span className="font-mono text-xs break-all text-right">
            {modelConfig?.provider || notConfigured}
          </span>
        </div>
      </div>
    </section>
  );
}
