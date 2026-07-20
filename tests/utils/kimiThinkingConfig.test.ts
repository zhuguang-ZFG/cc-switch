import { describe, expect, it } from "vitest";
import {
  canEditKimiThinkingStructured,
  mergeKimiThinkingState,
  parseKimiThinkingState,
} from "@/utils/kimiThinkingConfig";

describe("kimiThinkingConfig", () => {
  it("parses empty snippet as unset thinking", () => {
    expect(parseKimiThinkingState("")).toEqual({
      enabled: null,
      effort: "",
    });
  });

  it("parses effort-only [thinking] table", () => {
    expect(
      parseKimiThinkingState(`[thinking]\neffort = "max"\n`),
    ).toEqual({ enabled: null, effort: "max" });
  });

  it("parses enabled + effort", () => {
    expect(
      parseKimiThinkingState(
        `[thinking]\nenabled = true\neffort = "high"\n`,
      ),
    ).toEqual({ enabled: true, effort: "high" });
  });

  it("merges effort into empty snippet", () => {
    const next = mergeKimiThinkingState("", {
      enabled: null,
      effort: "max",
    });
    expect(next).toContain("[thinking]");
    expect(next).toContain('effort = "max"');
    expect(parseKimiThinkingState(next ?? "")).toEqual({
      enabled: null,
      effort: "max",
    });
  });

  it("preserves unrelated tables when updating thinking", () => {
    const base = `[hooks]
enabled = true

[thinking]
effort = "low"
`;
    const next = mergeKimiThinkingState(base, {
      enabled: true,
      effort: "max",
    });
    expect(next).toContain("[hooks]");
    expect(next).toContain("enabled = true");
    expect(parseKimiThinkingState(next ?? "")).toEqual({
      enabled: true,
      effort: "max",
    });
  });

  it("removes [thinking] when both controls are cleared", () => {
    const next = mergeKimiThinkingState(
      `[thinking]\nenabled = true\neffort = "high"\n\n[hooks]\nx = 1\n`,
      { enabled: null, effort: "" },
    );
    expect(next).not.toContain("[thinking]");
    expect(next).toContain("[hooks]");
  });

  it("returns null for invalid TOML on merge", () => {
    expect(
      mergeKimiThinkingState("[broken", { enabled: null, effort: "max" }),
    ).toBeNull();
    expect(canEditKimiThinkingStructured("[broken")).toBe(false);
  });
});
