import { parse as parseToml, stringify as stringifyToml } from "smol-toml";
import { normalizeTomlText } from "@/utils/textNormalization";

/** Values observed in Kimi Code live models (`support_efforts`) and `[thinking].effort`. */
export const KIMI_THINKING_EFFORTS = ["low", "high", "max"] as const;
export type KimiThinkingEffort = (typeof KIMI_THINKING_EFFORTS)[number];

export interface KimiThinkingState {
  /** `null` = key absent; true/false = explicit `[thinking].enabled`. */
  enabled: boolean | null;
  /** Empty string = key absent. */
  effort: KimiThinkingEffort | "";
}

export function isKimiThinkingEffort(
  value: unknown,
): value is KimiThinkingEffort {
  return (
    typeof value === "string" &&
    (KIMI_THINKING_EFFORTS as readonly string[]).includes(value)
  );
}

/**
 * Read `[thinking]` from a Kimi common-config TOML snippet.
 * Invalid TOML returns empty state (caller still uses raw editor).
 */
export function parseKimiThinkingState(snippet: string): KimiThinkingState {
  const trimmed = snippet.trim();
  if (!trimmed) {
    return { enabled: null, effort: "" };
  }
  try {
    const parsed = parseToml(normalizeTomlText(snippet)) as Record<
      string,
      unknown
    >;
    const thinking = parsed.thinking;
    if (!thinking || typeof thinking !== "object" || Array.isArray(thinking)) {
      return { enabled: null, effort: "" };
    }
    const table = thinking as Record<string, unknown>;
    const enabled =
      typeof table.enabled === "boolean" ? table.enabled : null;
    const effortRaw = table.effort;
    const effort = isKimiThinkingEffort(effortRaw) ? effortRaw : "";
    return { enabled, effort };
  } catch {
    return { enabled: null, effort: "" };
  }
}

/**
 * Merge thinking controls into a Kimi common-config snippet.
 * Preserves other top-level tables (hooks, permission, …).
 * Returns `null` if the snippet is non-empty but not valid TOML.
 */
export function mergeKimiThinkingState(
  snippet: string,
  next: KimiThinkingState,
): string | null {
  const trimmed = snippet.trim();
  let root: Record<string, unknown> = {};
  if (trimmed) {
    try {
      const parsed = parseToml(normalizeTomlText(snippet));
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return null;
      }
      root = { ...(parsed as Record<string, unknown>) };
    } catch {
      return null;
    }
  }

  const hasEnabled = next.enabled !== null;
  const hasEffort = next.effort !== "";

  if (!hasEnabled && !hasEffort) {
    delete root.thinking;
  } else {
    const table: Record<string, unknown> = {};
    if (hasEnabled) {
      table.enabled = next.enabled;
    }
    if (hasEffort) {
      table.effort = next.effort;
    }
    root.thinking = table;
  }

  const out = stringifyToml(root).trim();
  return out;
}

/** True when the snippet can be safely rewritten by structured thinking controls. */
export function canEditKimiThinkingStructured(snippet: string): boolean {
  const trimmed = snippet.trim();
  if (!trimmed) return true;
  try {
    parseToml(normalizeTomlText(snippet));
    return true;
  } catch {
    return false;
  }
}
