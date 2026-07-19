import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { DeepLinkImportRequest } from "../../lib/api/deeplink";
import { decodeBase64Utf8 } from "../../lib/utils/base64";

export function McpConfirmation({
  request,
}: {
  request: DeepLinkImportRequest;
}) {
  const { t } = useTranslation();

  const mcpServers = useMemo(() => {
    if (!request.config) return null;
    try {
      const decoded = decodeBase64Utf8(request.config);
      const parsed = JSON.parse(decoded);
      return parsed.mcpServers || {};
    } catch (e) {
      console.error("Failed to parse MCP config:", e);
      return null;
    }
  }, [request.config]);

  const targetApps = request.apps?.split(",") || [];
  const serverCount = Object.keys(mcpServers || {}).length;

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">{t("deeplink.mcp.title")}</h3>

      <div>
        <label className="block text-sm font-medium text-muted-foreground">
          {t("deeplink.mcp.targetApps")}
        </label>
        <div className="mt-1 flex gap-2 flex-wrap">
          {targetApps.map((app) => (
            <span
              key={app}
              className="px-2 py-1 bg-primary/10 text-primary text-xs rounded capitalize"
            >
              {app.trim()}
            </span>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-muted-foreground">
          {t("deeplink.mcp.serverCount", { count: serverCount })}
        </label>
        <div className="mt-1 space-y-2 max-h-64 overflow-auto border rounded p-2 bg-muted/30">
          {mcpServers &&
            Object.entries(mcpServers).map(([id, spec]: [string, any]) => (
              <div key={id} className="p-2 bg-background rounded border">
                <div className="font-semibold text-sm">{id}</div>
                {/* Show the FULL command line. An MCP stdio server is a
                    command the CLI will execute — truncating or hiding args
                    (e.g. `bash -c "curl … | sh"` rendered as just "bash")
                    turns this consent dialog into a rubber stamp. */}
                <div className="text-xs text-muted-foreground mt-1 font-mono whitespace-pre-wrap break-all">
                  {spec.command
                    ? `Command: ${[spec.command, ...(Array.isArray(spec.args) ? spec.args : [])].join(" ")}`
                    : `URL: ${spec.url ?? ""}`}
                </div>
                {spec.env && Object.keys(spec.env).length > 0 && (
                  <div className="text-xs text-muted-foreground mt-1 font-mono whitespace-pre-wrap break-all">
                    {`Env: ${Object.entries(spec.env)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" ")}`}
                  </div>
                )}
              </div>
            ))}
        </div>
      </div>

      {mcpServers &&
        Object.values(mcpServers).some((spec: any) => spec.command) && (
          <div className="text-yellow-600 dark:text-yellow-500 text-sm flex items-start gap-2">
            <span>⚠️</span>
            <span>
              {t("deeplink.mcp.commandWarning", {
                defaultValue:
                  "导入的 stdio MCP 服务器会在目标 CLI 下次启动时执行上面的命令。请确认命令与参数完全可信后再导入。",
              })}
            </span>
          </div>
        )}

      {request.enabled && (
        <div className="text-yellow-600 dark:text-yellow-500 text-sm flex items-center gap-2">
          <span>⚠️</span>
          <span>{t("deeplink.mcp.enabledWarning")}</span>
        </div>
      )}
    </div>
  );
}
