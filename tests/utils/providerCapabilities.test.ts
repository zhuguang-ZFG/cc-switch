import { describe, expect, it } from "vitest";
import {
  CODEX_OFFICIAL_PROVIDER_ID,
  supportsOfficialProxyTakeover,
} from "@/utils/providerCapabilities";

// 前端白名单必须镜像 Rust `official_provider_supports_proxy_takeover`
// (src-tauri/src/services/provider/mod.rs)。
describe("supportsOfficialProxyTakeover", () => {
  it("allows the built-in Codex official provider", () => {
    expect(
      supportsOfficialProxyTakeover("codex", {
        id: CODEX_OFFICIAL_PROVIDER_ID,
        category: "official",
      }),
    ).toBe(true);
  });

  it("rejects Codex official copies without the built-in id", () => {
    expect(
      supportsOfficialProxyTakeover("codex", {
        id: "generated-uuid",
        category: "official",
      }),
    ).toBe(false);
  });

  it("rejects apps without any official takeover route", () => {
    expect(
      supportsOfficialProxyTakeover("claude", {
        id: CODEX_OFFICIAL_PROVIDER_ID,
        category: "official",
      }),
    ).toBe(false);
  });

  it("allows Kimi managed providers by id prefix", () => {
    expect(
      supportsOfficialProxyTakeover("kimicode", {
        id: "managed:kimi-code",
        category: "official",
      }),
    ).toBe(true);
    expect(
      supportsOfficialProxyTakeover("kimicode", {
        id: "managed:kimi-for-coding",
        category: "official",
      }),
    ).toBe(true);
  });

  it("allows Kimi providers carrying an oauth settings key", () => {
    expect(
      supportsOfficialProxyTakeover("kimicode", {
        id: "kimi-oauth-imported",
        category: "official",
        settingsConfig: { oauth: { access_token: "token" } },
      }),
    ).toBe(true);
  });

  it("allows Kimi providers tagged as kimi_oauth", () => {
    expect(
      supportsOfficialProxyTakeover("kimicode", {
        id: "kimi-oauth-tagged",
        category: "official",
        meta: { providerType: "kimi_oauth" },
      }),
    ).toBe(true);
  });

  it("rejects plain Kimi providers without any OAuth signal", () => {
    expect(
      supportsOfficialProxyTakeover("kimicode", {
        id: "kimi-third-party",
        category: "official",
        settingsConfig: { type: "openai", base_url: "https://example.test" },
      }),
    ).toBe(false);
  });

  it("does not leak the Kimi managed rule to other apps", () => {
    expect(
      supportsOfficialProxyTakeover("codex", {
        id: "managed:kimi-code",
        category: "official",
      }),
    ).toBe(false);
  });
});
