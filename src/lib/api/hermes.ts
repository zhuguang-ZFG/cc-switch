/**
 * Kimi Code configuration API (legacy module name kept for fewer import churn).
 *
 * Memory / Web UI from Hermes are stubs that throw clear errors.
 */

export interface HermesModelConfig {
  /** Active provider id (best-effort from default_model prefix) */
  provider?: string | null;
  default?: string | null;
}

export type HermesMemoryKind = "memory" | "user";

export interface HermesMemoryLimits {
  maxChars?: number;
  memory?: number;
  user?: number;
  memoryEnabled?: boolean;
  userEnabled?: boolean;
}

export const hermesApi = {
  async getModelConfig(): Promise<HermesModelConfig | null> {
    try {
      const [defaultModel, provider] = await Promise.all([
        invokeOptionalString("get_kimicode_default_model"),
        invokeOptionalString("get_kimicode_default_provider"),
      ]);
      if (!defaultModel) return null;
      return { provider, default: defaultModel };
    } catch {
      return null;
    }
  },

  async openWebUI(_path?: string | null): Promise<void> {
    throw new Error("Hermes Web UI is not available; use Kimi Code CLI");
  },

  async launchDashboard(): Promise<void> {
    throw new Error("Hermes dashboard is not available; use Kimi Code CLI");
  },

  async getMemory(_kind: HermesMemoryKind): Promise<string> {
    throw new Error("Hermes memory is not available");
  },

  async setMemory(_kind: HermesMemoryKind, _content: string): Promise<void> {
    throw new Error("Hermes memory is not available");
  },

  async getMemoryLimits(): Promise<HermesMemoryLimits> {
    return {};
  },

  async setMemoryEnabled(
    _kind: HermesMemoryKind,
    _enabled: boolean,
  ): Promise<void> {
    throw new Error("Hermes memory is not available");
  },
};

async function invokeOptionalString(cmd: string): Promise<string | null> {
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<string | null>(cmd);
}
