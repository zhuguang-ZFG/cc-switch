import { useEffect } from "react";
import { CheckCircle2, Loader2, LogIn, LogOut } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useManagedAuth } from "@/components/providers/forms/hooks/useManagedAuth";

export function KimiAuthSection() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const {
    isAuthenticated,
    isLoadingStatus,
    isPolling,
    deviceCode,
    error,
    startAuth,
    cancelAuth,
    logout,
  } = useManagedAuth("kimi_oauth");

  useEffect(() => {
    if (isLoadingStatus) return;
    void queryClient.invalidateQueries({ queryKey: ["providers", "kimicode"] });
    void queryClient.invalidateQueries({ queryKey: ["kimicode"] });
  }, [isAuthenticated, isLoadingStatus, queryClient]);

  return (
    <section className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            {isLoadingStatus ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : isAuthenticated ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : (
              <LogIn className="h-4 w-4 text-muted-foreground" />
            )}
            {t("kimicode.auth.title", { defaultValue: "Kimi official login" })}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {isAuthenticated
              ? t("kimicode.auth.connected", {
                  defaultValue: "Connected to Kimi For Coding",
                })
              : t("kimicode.auth.disconnected", {
                  defaultValue: "Use your Kimi subscription in Kimi Code",
                })}
          </p>
        </div>

        {isAuthenticated ? (
          <Button type="button" variant="outline" size="sm" onClick={logout}>
            <LogOut className="h-4 w-4" />
            {t("common.logout", { defaultValue: "Log out" })}
          </Button>
        ) : isPolling ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={cancelAuth}
          >
            {t("common.cancel")}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            onClick={startAuth}
            disabled={isLoadingStatus}
          >
            <LogIn className="h-4 w-4" />
            {t("kimicode.auth.login", { defaultValue: "Log in" })}
          </Button>
        )}
      </div>

      {isPolling && deviceCode && (
        <div className="mt-3 border-t border-border pt-3 text-xs">
          <span className="text-muted-foreground">
            {t("kimicode.auth.code", { defaultValue: "Authorization code" })}
          </span>
          <code className="ml-2 rounded bg-muted px-2 py-1 font-mono text-sm font-semibold">
            {deviceCode.user_code}
          </code>
        </div>
      )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </section>
  );
}
