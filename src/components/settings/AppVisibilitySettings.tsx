import { useTranslation } from "react-i18next";
import { FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleRow } from "@/components/ui/toggle-row";
import { cn } from "@/lib/utils";
import { ProviderIcon } from "@/components/ProviderIcon";
import type { SettingsFormState } from "@/hooks/useSettings";
import type { VisibleApps } from "@/types";
import type { AppId } from "@/lib/api";

interface AppVisibilitySettingsProps {
  settings: SettingsFormState;
  onChange: (updates: Partial<SettingsFormState>) => void;
}

const APP_CONFIG: Array<{
  id: AppId;
  icon: string;
  nameKey: string;
  defaultName: string;
}> = [
  {
    id: "claude",
    icon: "claude",
    nameKey: "apps.claudeCode",
    defaultName: "Claude Code",
  },
  {
    id: "claude-desktop",
    icon: "claude",
    nameKey: "apps.claudeDesktop",
    defaultName: "Claude Desktop",
  },
  { id: "codex", icon: "openai", nameKey: "apps.codex", defaultName: "Codex" },
  {
    id: "grokbuild",
    icon: "grok",
    nameKey: "apps.grokbuild",
    defaultName: "Grok Build",
  },
  {
    id: "opencode",
    icon: "opencode",
    nameKey: "apps.opencode",
    defaultName: "OpenCode",
  },
  {
    id: "openclaw",
    icon: "openclaw",
    nameKey: "apps.openclaw",
    defaultName: "OpenClaw",
  },
  {
    id: "kimicode",
    icon: "kimi",
    nameKey: "apps.kimicode",
    defaultName: "Kimi Code",
  },
];

function isAppVisible(visibleApps: VisibleApps, appId: AppId): boolean {
  if (appId === "kimicode") {
    return visibleApps.kimicode ?? visibleApps.hermes ?? true;
  }
  const value = visibleApps[appId as keyof VisibleApps];
  return value !== false;
}

export function AppVisibilitySettings({
  settings,
  onChange,
}: AppVisibilitySettingsProps) {
  const { t } = useTranslation();

  const visibleApps: VisibleApps = settings.visibleApps ?? {
    claude: true,
    "claude-desktop": true,
    codex: true,
    grokbuild: true,
    opencode: true,
    openclaw: true,
    kimicode: true,
  };

  const visibleCount = APP_CONFIG.filter((app) =>
    isAppVisible(visibleApps, app.id),
  ).length;

  const handleToggle = (appId: AppId) => {
    const currentlyVisible = isAppVisible(visibleApps, appId);
    if (currentlyVisible && visibleCount <= 1) return;

    const canonicalVisibleApps = { ...visibleApps };
    if (
      canonicalVisibleApps.kimicode === undefined &&
      canonicalVisibleApps.hermes !== undefined
    ) {
      canonicalVisibleApps.kimicode = canonicalVisibleApps.hermes;
    }
    delete canonicalVisibleApps.hermes;

    onChange({
      visibleApps: {
        ...canonicalVisibleApps,
        [appId]: !currentlyVisible,
      },
    });
  };

  return (
    <section className="space-y-2">
      <header className="space-y-1">
        <h3 className="text-sm font-medium">
          {t("settings.appVisibility.title")}
        </h3>
        <p className="text-xs text-muted-foreground">
          {t("settings.appVisibility.description")}
        </p>
      </header>
      <div className="flex flex-wrap gap-1 rounded-md border border-border-default bg-background p-1">
        {APP_CONFIG.map((app) => {
          const visible = isAppVisible(visibleApps, app.id);
          const isDisabled = visible && visibleCount <= 1;
          const label = t(app.nameKey, { defaultValue: app.defaultName });

          return (
            <AppButton
              key={app.id}
              active={visible}
              disabled={isDisabled}
              onClick={() => handleToggle(app.id)}
              icon={app.icon}
              name={label}
            >
              {label}
            </AppButton>
          );
        })}
      </div>
      <ToggleRow
        icon={<FolderOpen className="h-4 w-4 text-emerald-500" />}
        title={t("settings.appVisibility.showProfileSwitcher")}
        description={t("settings.appVisibility.showProfileSwitcherDescription")}
        checked={settings.showProfileSwitcher ?? true}
        onCheckedChange={(value) => onChange({ showProfileSwitcher: value })}
      />
    </section>
  );
}

interface AppButtonProps {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  icon: string;
  name: string;
  children: React.ReactNode;
}

function AppButton({
  active,
  disabled,
  onClick,
  icon,
  name,
  children,
}: AppButtonProps) {
  return (
    <Button
      type="button"
      onClick={onClick}
      disabled={disabled}
      size="sm"
      variant={active ? "default" : "ghost"}
      className={cn(
        "min-w-[90px] w-auto gap-1.5 px-3",
        active
          ? "shadow-sm"
          : "text-muted-foreground hover:text-foreground hover:bg-muted",
      )}
    >
      <ProviderIcon icon={icon} name={name} size={14} />
      {children}
    </Button>
  );
}
