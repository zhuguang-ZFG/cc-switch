import { describe, expect, it } from "vitest";
import {
  APP_PROFILE_SCOPE,
  hasScopeSnapshot,
} from "@/components/profiles/scope";
import type { Profile } from "@/lib/api/profiles";

const emptySlots = {
  claude: null,
  "claude-desktop": null,
  codex: null,
  kimicode: null,
  reasonix: null,
};

describe("Kimi Code profile scope", () => {
  it("maps the Kimi Code app to its own profile scope", () => {
    expect(APP_PROFILE_SCOPE.kimicode).toBe("kimicode");
  });

  it("detects only Kimi Code snapshot slots", () => {
    const profile: Profile = {
      id: "project-1",
      name: "Project 1",
      payload: {
        providers: { ...emptySlots, kimicode: "kimi-provider" },
        mcp: { ...emptySlots },
        skills: { ...emptySlots },
        prompts: { ...emptySlots },
      },
    };

    expect(hasScopeSnapshot(profile, "kimicode")).toBe(true);
    expect(hasScopeSnapshot(profile, "claude")).toBe(false);
    expect(hasScopeSnapshot(profile, "codex")).toBe(false);
    expect(hasScopeSnapshot(profile, "reasonix")).toBe(false);
  });
});

describe("Reasonix profile scope", () => {
  it("maps the Reasonix app to its own profile scope", () => {
    expect(APP_PROFILE_SCOPE.reasonix).toBe("reasonix");
  });

  it("detects only Reasonix snapshot slots", () => {
    const profile: Profile = {
      id: "project-rx",
      name: "Reasonix Project",
      payload: {
        providers: { ...emptySlots, reasonix: "rx-provider" },
        mcp: { ...emptySlots },
        skills: { ...emptySlots },
        prompts: { ...emptySlots },
      },
    };

    expect(hasScopeSnapshot(profile, "reasonix")).toBe(true);
    expect(hasScopeSnapshot(profile, "kimicode")).toBe(false);
    expect(hasScopeSnapshot(profile, "claude")).toBe(false);
  });
});
