import type { AppId } from "@/lib/api";
import type { VisibleApps } from "@/types";
import { ProviderIcon } from "@/components/ProviderIcon";
import { cn } from "@/lib/utils";
import { Monitor, Terminal } from "lucide-react";

const APP_BADGE_ICON: Partial<
  Record<AppId, { icon: typeof Terminal; offsetY?: number }>
> = {
  claude: { icon: Terminal },
  "claude-desktop": { icon: Monitor, offsetY: 0.5 },
};

interface AppSwitcherProps {
  activeApp: AppId;
  onSwitch: (app: AppId) => void;
  visibleApps?: VisibleApps;
  compact?: boolean;
}

const ALL_APPS: AppId[] = [
  "claude",
  "claude-desktop",
  "codex",
  "grokbuild",
  "opencode",
  "openclaw",
  "kimicode",
  "reasonix",
];
const STORAGE_KEY = "cc-switch-last-app";

function isAppVisible(app: AppId, visibleApps?: VisibleApps): boolean {
  if (!visibleApps) return true;
  if (app === "kimicode") {
    return visibleApps.kimicode ?? visibleApps.hermes ?? true;
  }
  const value = visibleApps[app as keyof VisibleApps];
  return value !== false;
}

export function AppSwitcher({
  activeApp,
  onSwitch,
  visibleApps,
  compact,
}: AppSwitcherProps) {
  const handleSwitch = (app: AppId) => {
    if (app === activeApp) return;
    localStorage.setItem(STORAGE_KEY, app);
    onSwitch(app);
  };
  const iconSize = 20;
  const appIconName: Record<AppId, string> = {
    claude: "claude",
    "claude-desktop": "claude",
    codex: "openai",
    grokbuild: "grok",
    opencode: "opencode",
    openclaw: "openclaw",
    kimicode: "kimi",
    reasonix: "deepseek",
  };
  const appDisplayName: Record<AppId, string> = {
    claude: "Claude Code",
    "claude-desktop": "Claude Desktop",
    codex: "Codex",
    grokbuild: "Grok Build",
    opencode: "OpenCode",
    openclaw: "OpenClaw",
    kimicode: "Kimi Code",
    reasonix: "Reasonix",
  };

  const appsToShow = ALL_APPS.filter((app) => isAppVisible(app, visibleApps));

  return (
    <div className="inline-flex bg-muted rounded-xl p-1 gap-1">
      {appsToShow.map((app) => {
        const badgeConfig = APP_BADGE_ICON[app];
        const BadgeIcon = badgeConfig?.icon;
        const isActive = activeApp === app;
        return (
          <button
            key={app}
            type="button"
            onClick={() => handleSwitch(app)}
            className={cn(
              "group inline-flex items-center px-3 h-8 rounded-md text-sm font-medium transition-all duration-200",
              isActive
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-background/50",
              compact && "px-2",
            )}
          >
            <span className="relative inline-flex shrink-0">
              <ProviderIcon
                icon={appIconName[app]}
                name={appDisplayName[app]}
                size={iconSize}
              />
              {BadgeIcon && (
                <span
                  className={cn(
                    "absolute -bottom-0.5 -right-0.5 flex items-center justify-center rounded-[3px] border h-[11px] w-[11px]",
                    isActive
                      ? "bg-background border-border text-foreground"
                      : "bg-muted border-border text-muted-foreground",
                  )}
                  style={
                    badgeConfig?.offsetY
                      ? { transform: `translateY(${badgeConfig.offsetY}px)` }
                      : undefined
                  }
                >
                  <BadgeIcon size={8} strokeWidth={2.5} />
                </span>
              )}
            </span>
            {!compact && (
              <span className="ml-2 hidden sm:inline">
                {appDisplayName[app]}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
