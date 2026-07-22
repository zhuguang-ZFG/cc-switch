import React from "react";
import type { AppId } from "@/lib/api/types";
import { ClaudeIcon, CodexIcon, OpenClawIcon } from "@/components/BrandIcons";
import { ProviderIcon } from "@/components/ProviderIcon";

export interface AppConfig {
  label: string;
  icon: React.ReactNode;
  activeClass: string;
  badgeClass: string;
}

export const APP_IDS: AppId[] = [
  "claude",
  "claude-desktop",
  "codex",
  "grokbuild",
  "opencode",
  "openclaw",
  "kimicode",
  "reasonix",
  "pi",
];

/** App IDs shown in Skills panels (excludes OpenClaw, which has its own workspace tools) */
export const SKILLS_APP_IDS: AppId[] = [
  "claude",
  "codex",
  "grokbuild",
  "opencode",
  "kimicode",
  "reasonix",
];

/** App IDs shown in MCP panels (excludes OpenClaw, which has its own workspace tools) */
export const MCP_APP_IDS: AppId[] = [...SKILLS_APP_IDS];

export const APP_ICON_MAP: Record<AppId, AppConfig> = {
  claude: {
    label: "Claude",
    icon: <ClaudeIcon size={14} />,
    activeClass:
      "bg-orange-500/10 ring-1 ring-orange-500/20 hover:bg-orange-500/20 text-orange-600 dark:text-orange-400",
    badgeClass:
      "bg-orange-500/10 text-orange-700 dark:text-orange-300 hover:bg-orange-500/20 border-0 gap-1.5",
  },
  "claude-desktop": {
    label: "Claude Desktop",
    icon: <ClaudeIcon size={14} />,
    activeClass:
      "bg-amber-500/10 ring-1 ring-amber-500/20 hover:bg-amber-500/20 text-amber-700 dark:text-amber-300",
    badgeClass:
      "bg-amber-500/10 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20 border-0 gap-1.5",
  },
  codex: {
    label: "Codex",
    icon: <CodexIcon size={14} />,
    activeClass:
      "bg-green-500/10 ring-1 ring-green-500/20 hover:bg-green-500/20 text-green-600 dark:text-green-400",
    badgeClass:
      "bg-green-500/10 text-green-700 dark:text-green-300 hover:bg-green-500/20 border-0 gap-1.5",
  },
  grokbuild: {
    label: "Grok Build",
    icon: (
      <ProviderIcon
        icon="grok"
        name="Grok Build"
        size={14}
        showFallback={false}
      />
    ),
    activeClass:
      "bg-cyan-500/10 ring-1 ring-cyan-500/20 hover:bg-cyan-500/20 text-cyan-700 dark:text-cyan-300",
    badgeClass:
      "bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 hover:bg-cyan-500/20 border-0 gap-1.5",
  },
  opencode: {
    label: "OpenCode",
    icon: (
      <ProviderIcon
        icon="opencode"
        name="OpenCode"
        size={14}
        showFallback={false}
      />
    ),
    activeClass:
      "bg-indigo-500/10 ring-1 ring-indigo-500/20 hover:bg-indigo-500/20 text-indigo-600 dark:text-indigo-400",
    badgeClass:
      "bg-indigo-500/10 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-500/20 border-0 gap-1.5",
  },
  openclaw: {
    label: "OpenClaw",
    icon: <OpenClawIcon size={14} />,
    activeClass:
      "bg-rose-500/10 ring-1 ring-rose-500/20 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400",
    badgeClass:
      "bg-rose-500/10 text-rose-700 dark:text-rose-300 hover:bg-rose-500/20 border-0 gap-1.5",
  },
  kimicode: {
    label: "Kimi Code",
    icon: (
      <ProviderIcon
        icon="moonshot"
        name="Kimi Code"
        size={14}
        showFallback={false}
      />
    ),
    activeClass:
      "bg-violet-500/10 ring-1 ring-violet-500/20 hover:bg-violet-500/20 text-violet-600 dark:text-violet-400",
    badgeClass:
      "bg-violet-500/10 text-violet-700 dark:text-violet-300 hover:bg-violet-500/20 border-0 gap-1.5",
  },
  reasonix: {
    label: "Reasonix",
    icon: (
      <ProviderIcon
        icon="deepseek"
        name="Reasonix"
        size={14}
        showFallback={false}
      />
    ),
    activeClass:
      "bg-sky-500/10 ring-1 ring-sky-500/20 hover:bg-sky-500/20 text-sky-600 dark:text-sky-400",
    badgeClass:
      "bg-sky-500/10 text-sky-700 dark:text-sky-300 hover:bg-sky-500/20 border-0 gap-1.5",
  },
  pi: {
    label: "Pi",
    icon: (
      <ProviderIcon
        icon="openai"
        name="Pi"
        size={14}
        showFallback={false}
      />
    ),
    activeClass:
      "bg-emerald-500/10 ring-1 ring-emerald-500/20 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400",
    badgeClass:
      "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/20 border-0 gap-1.5",
  },
};
