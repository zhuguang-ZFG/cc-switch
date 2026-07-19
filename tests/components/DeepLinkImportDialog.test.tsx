import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { DeepLinkImportDialog } from "@/components/DeepLinkImportDialog";
import { emitTauriEvent } from "../msw/tauriMocks";
import { server } from "../msw/server";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const TAURI_ENDPOINT = "http://tauri.local";

const kimiConfig = {
  type: "openai",
  base_url: "https://config.kimi.example/v1",
  api_key: "sk-kimi-config-key",
  models: [{ id: "anthropic/claude-opus-4-8" }],
};

const kimiPayload = {
  version: "v1",
  resource: "provider",
  app: "kimicode",
  name: "Kimi Relay",
  homepage: "https://www.kimi.com",
  endpoint: "https://api.kimi.com/coding/v1",
  apiKey: "sk-kimi-test",
  model: "anthropic/claude-opus-4-8",
  config: btoa(JSON.stringify(kimiConfig)),
  configFormat: "json",
};

const renderDialog = () => {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <DeepLinkImportDialog />
    </QueryClientProvider>,
  );
};

describe("DeepLinkImportDialog (kimicode provider)", () => {
  it("shows Kimi provider fields and parsed config details", async () => {
    server.use(
      http.post(`${TAURI_ENDPOINT}/merge_deeplink_config`, async ({
        request,
      }) => {
        const body = (await request.json()) as {
          request: Record<string, unknown>;
        };
        return HttpResponse.json(body.request);
      }),
    );

    renderDialog();
    emitTauriEvent("deeplink-import", kimiPayload);

    // Provider basic fields
    expect(await screen.findByText("Kimi Relay")).toBeInTheDocument();
    expect(screen.getByText("kimicode")).toBeInTheDocument();
    expect(
      screen.getByText(/api\.kimi\.com\/coding\/v1/),
    ).toBeInTheDocument();

    // Generic model field (Kimi Code uses the shared model row)
    expect(
      screen.getAllByText("anthropic/claude-opus-4-8").length,
    ).toBeGreaterThanOrEqual(1);

    // Parsed config details (native projection: base_url / api_key / models)
    expect(screen.getByText("deeplink.configEmbedded")).toBeInTheDocument();
    expect(screen.getByText("base_url")).toBeInTheDocument();
    expect(
      screen.getByText("https://config.kimi.example/v1"),
    ).toBeInTheDocument();
    expect(screen.getByText("api_key")).toBeInTheDocument();
    expect(screen.getByText("models")).toBeInTheDocument();
    // api_key is sensitive and must be masked
    expect(screen.queryByText("sk-kimi-config-key")).not.toBeInTheDocument();
  });

  it("submits the import with app=kimicode", async () => {
    let captured: { request?: { app?: string } } = {};
    server.use(
      http.post(`${TAURI_ENDPOINT}/merge_deeplink_config`, async ({
        request,
      }) => {
        const body = (await request.json()) as {
          request: Record<string, unknown>;
        };
        return HttpResponse.json(body.request);
      }),
      http.post(`${TAURI_ENDPOINT}/import_from_deeplink_unified`, async ({
        request,
      }) => {
        captured = (await request.json()) as { request?: { app?: string } };
        return HttpResponse.json({ type: "provider", id: "kimi-relay-1" });
      }),
    );

    renderDialog();
    emitTauriEvent("deeplink-import", kimiPayload);

    const importButton = await screen.findByRole("button", {
      name: "deeplink.import",
    });
    fireEvent.click(importButton);

    await waitFor(() => {
      expect(captured.request?.app).toBe("kimicode");
    });

    // Dialog closes after a successful import
    await waitFor(() => {
      expect(screen.queryByText("Kimi Relay")).not.toBeInTheDocument();
    });
  });
});
