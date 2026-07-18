import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppVisibilitySettings } from "@/components/settings/AppVisibilitySettings";
import type { SettingsFormState } from "@/hooks/useSettings";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
  }),
}));

const baseVisibleApps = {
  claude: true,
  "claude-desktop": true,
  codex: true,
  grokbuild: true,
  opencode: true,
  openclaw: true,
};

describe("AppVisibilitySettings", () => {
  it("saves Kimi visibility without the legacy Hermes alias", () => {
    const onChange = vi.fn();
    const settings = {
      visibleApps: { ...baseVisibleApps, kimicode: false },
    } as SettingsFormState;

    render(<AppVisibilitySettings settings={settings} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /Kimi Code$/ }));

    expect(onChange).toHaveBeenCalledOnce();
    const update = onChange.mock.calls[0][0];
    expect(update.visibleApps.kimicode).toBe(true);
    expect(update.visibleApps).not.toHaveProperty("hermes");
  });

  it("canonicalizes legacy Hermes visibility when another app changes", () => {
    const onChange = vi.fn();
    const settings = {
      visibleApps: { ...baseVisibleApps, hermes: false },
    } as SettingsFormState;

    render(<AppVisibilitySettings settings={settings} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /Claude Code$/ }));

    const update = onChange.mock.calls[0][0];
    expect(update.visibleApps.kimicode).toBe(false);
    expect(update.visibleApps).not.toHaveProperty("hermes");
  });
});
