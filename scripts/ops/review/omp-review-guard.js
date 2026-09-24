import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

// Loaded explicitly only by the ephemeral reviewer. No index file is present
// in this directory, so ordinary extension discovery must not activate it.
export default function reviewGuard(pi) {
  let calls = 0;
  pi.on("tool_call", (event, ctx) => {
    calls++;
    if (calls > 8)
      return {
        block: true,
        reason: "Review tool budget exhausted. Return findings now.",
      };
    if (!["read", "grep", "glob"].includes(event.toolName)) {
      return {
        block: true,
        reason: "Reviewer is read-only: only read, grep and glob are allowed.",
      };
    }
    try {
      const root = realpathSync(ctx.cwd);
      const input = event.input ?? {};
      const requested = resolve(root, input.path || ".");
      const target = realpathSync(requested);
      const path = relative(root, target);
      if (
        isAbsolute(path) ||
        path === ".." ||
        path.startsWith("../") ||
        path.startsWith("..\\")
      ) {
        return {
          block: true,
          reason: "Reviewer must stay within its workspace.",
        };
      }
      if (
        event.toolName === "glob" &&
        (typeof input.pattern !== "string" ||
          isAbsolute(input.pattern) ||
          /(^|[\\/])\.\.([\\/]|$)|^[A-Za-z]:/.test(input.pattern))
      ) {
        return {
          block: true,
          reason: "Use a relative glob pattern within the workspace.",
        };
      }
      if (
        event.toolName === "read" &&
        (!Number.isInteger(input.limit) || input.limit < 1 || input.limit > 200)
      ) {
        return {
          block: true,
          reason:
            "Reviewer reads require an explicit limit between 1 and 200 lines.",
        };
      }
    } catch {
      return {
        block: true,
        reason:
          "Review path unavailable or invalid; choose an existing workspace path.",
      };
    }
  });
}
