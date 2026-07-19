import type { AppId } from "@/lib/api";
import type { Provider } from "@/types";

export const CODEX_OFFICIAL_PROVIDER_ID = "codex-official";

/**
 * Keep the UI capability rule aligned with the Rust takeover policy
 * (src-tauri `official_provider_supports_proxy_takeover`).
 *
 * - Codex official: ChatGPT login stays outside the proxy; proxy only forwards.
 * - Managed Kimi OAuth: proxy injects refreshed tokens — the designed route for
 *   official Kimi under takeover. Mirrors the Rust conditions:
 *   `managed:kimi*` id / `oauth` key in settingsConfig / `kimi_oauth` providerType.
 */
export function supportsOfficialProxyTakeover(
  appId: AppId,
  provider: Pick<Provider, "id" | "category"> &
    Partial<Pick<Provider, "settingsConfig" | "meta">>,
): boolean {
  if (appId === "codex") {
    return (
      provider.id === CODEX_OFFICIAL_PROVIDER_ID &&
      provider.category === "official"
    );
  }
  if (appId === "kimicode") {
    return (
      provider.id.startsWith("managed:kimi") ||
      (provider.settingsConfig != null &&
        typeof provider.settingsConfig === "object" &&
        "oauth" in provider.settingsConfig) ||
      provider.meta?.providerType === "kimi_oauth"
    );
  }
  return false;
}
