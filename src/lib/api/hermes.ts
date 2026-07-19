/**
 * Kimi Code configuration API (legacy module name kept for fewer import churn).
 */

export interface HermesModelConfig {
  /** Active provider id (best-effort from default_model prefix) */
  provider?: string | null;
  default?: string | null;
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
};

async function invokeOptionalString(cmd: string): Promise<string | null> {
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<string | null>(cmd);
}
