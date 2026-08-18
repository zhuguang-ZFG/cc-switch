import { createHash } from "node:crypto";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
} from "node:fs";
import { dirname, join } from "node:path";

export const EXTENSION_REVISION = "2026.08.19-routing-r3";
export const ROUTING_LOG_FILENAME = "omp-model-routing.jsonl";
export const ROUTING_LOG_MAX_BYTES = 2 * 1024 * 1024;
export const ROUTING_LOG_MAX_RECORDS = 200;

const WRITER_SYMBOL = Symbol.for("omp.modelRoutingTelemetry.writer");
const ROLE_SUFFIXES = new Set([
  "auto",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
  "off",
]);
const SAFE_SELECTOR = /^[A-Za-z0-9_.:/@+\-]+(?::[A-Za-z0-9_.+\-]+)?$/;
const SAFE_ATOM = /^[A-Za-z0-9_.:@+\-]+$/;
const SAFE_ROUTES = new Set(["task", "scout", "sota", "compaction"]);
const SAFE_RESULTS = new Set(["started", "success", "failed", "aborted", "skipped"]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function safeSelector(value) {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (trimmed.length === 0 || trimmed.length > 256 || trimmed.includes("://")) return undefined;
  return SAFE_SELECTOR.test(trimmed) ? trimmed : undefined;
}

function safeAtom(value, maxLength = 128) {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= maxLength && SAFE_ATOM.test(trimmed)
    ? trimmed
    : undefined;
}

export function stripThinkingSuffix(value) {
  const selector = typeof value === "string" ? value.trim() : "";
  const separator = selector.lastIndexOf(":");
  if (separator <= 0) return selector;
  const suffix = selector.slice(separator + 1).toLowerCase();
  return ROLE_SUFFIXES.has(suffix) ? selector.slice(0, separator) : selector;
}

export function canonicalRoleSnapshot(roles) {
  const entries = Object.entries(isRecord(roles) ? roles : {})
    .filter(([role, selector]) => typeof role === "string" && typeof selector === "string")
    .map(([role, selector]) => [role, selector.trim()])
    .sort(([left], [right]) => left.localeCompare(right));
  return JSON.stringify(Object.fromEntries(entries));
}

export function hashRoleSnapshot(roles) {
  return createHash("sha256").update(canonicalRoleSnapshot(roles)).digest("hex").slice(0, 16);
}

function roleCandidates(value) {
  if (typeof value !== "string") return [];
  return value
    .split(",")
    .map(candidate => candidate.trim())
    .filter(Boolean);
}

export function validateModelRoles(roles, models) {
  const available = new Set(
    (Array.isArray(models) ? models : [])
      .map(model => (isRecord(model) && typeof model.provider === "string" && typeof model.id === "string"
        ? `${model.provider}/${model.id}`
        : undefined))
      .filter(Boolean),
  );
  return Object.entries(isRecord(roles) ? roles : {})
    .filter(([role, selector]) => typeof role === "string" && typeof selector === "string")
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([role, configured]) => {
      const candidates = roleCandidates(configured);
      const exact = candidates.filter(candidate => {
        const base = stripThinkingSuffix(candidate);
        return base.includes("*") || base.includes("?") || base.startsWith("@") ? false : available.has(base);
      });
      const indeterminate = candidates.some(candidate => {
        const base = stripThinkingSuffix(candidate);
        return base.includes("*") || base.includes("?") || base.startsWith("@");
      });
      const state = exact.length > 0 ? "valid" : indeterminate ? "indeterminate" : "unresolved";
      return {
        role,
        configuredSelector: safeSelector(configured) ?? "invalid",
        state,
        resolvedSelector: exact[0] ? safeSelector(stripThinkingSuffix(exact[0])) : undefined,
      };
    });
}

export async function refreshAndValidateRoles(ctx, settings) {
  let refreshState = "skipped";
  let refreshError;
  try {
    if (typeof ctx?.modelRegistry?.refresh === "function") {
      await ctx.modelRegistry.refresh("offline");
      refreshState = "ok";
    } else {
      refreshState = "unsupported";
    }
  } catch {
    refreshState = "failed";
    refreshError = "offline-refresh-failed";
  }

  const roles = typeof settings?.getModelRoles === "function" ? settings.getModelRoles() : {};
  const validation = validateModelRoles(roles, ctx?.modelRegistry?.getAvailable?.());
  return {
    refreshState,
    refreshError,
    roles,
    roleHash: hashRoleSnapshot(roles),
    validation,
    unresolvedCount: validation.filter(item => item.state === "unresolved").length,
    indeterminateCount: validation.filter(item => item.state === "indeterminate").length,
  };
}

export function classifyRouteFailure(result) {
  if (!isRecord(result)) return undefined;
  if (result.aborted === true) return "aborted";
  if (result.retryFailure) return "retry-exhausted";
  if (result.exitCode !== 0 || typeof result.error === "string") {
    const text = `${typeof result.error === "string" ? result.error : ""}`.toLowerCase();
    if (/timeout|timed out/.test(text)) return "timeout";
    if (/rate|429|too many/.test(text)) return "rate-limited";
    if (/credential|api key|unauthori[sz]ed|forbidden/.test(text)) return "auth";
    return "failed";
  }
  return undefined;
}

function usageSnapshot(usage) {
  if (!isRecord(usage)) return undefined;
  const fields = ["input", "output", "cacheRead", "cacheWrite", "totalTokens"];
  const result = {};
  for (const field of fields) {
    const value = finiteNumber(usage[field]);
    if (value !== undefined) result[field] = value;
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

export function extractTaskRouteEvents(details, context = {}) {
  if (!isRecord(details) || !Array.isArray(details.results)) return [];
  return details.results.filter(isRecord).map(result => ({
    revision: EXTENSION_REVISION,
    route: result.agent === "scout" ? "scout" : "task",
    role: typeof result.modelRole === "string" ? result.modelRole : undefined,
    roleHash: context.roleHash,
    configuredSelector: Array.isArray(result.modelOverride)
      ? safeSelector(result.modelOverride[0])
      : safeSelector(result.modelOverride),
    resolvedSelector: safeSelector(result.resolvedModel),
    durationMs: finiteNumber(result.durationMs),
    requests: finiteNumber(result.requests),
    tokens: finiteNumber(result.tokens),
    usage: usageSnapshot(result.usage),
    fallback: result.resolvedModelIsFallback === true,
    result: result.aborted === true ? "aborted" : result.exitCode === 0 && !result.error ? "success" : "failed",
    failureClass: classifyRouteFailure(result),
  }));
}

export function extractTaskDispatchEvents(input, context = {}) {
  const items = Array.isArray(input?.tasks) ? input.tasks : [input];
  return items.filter(isRecord).map(item => {
    const route = item.agent === "scout" ? "scout" : "task";
    return {
      revision: EXTENSION_REVISION,
      route,
      role: route,
      roleHash: context.roleHash,
      result: "started",
    };
  });
}

function normalizeRouteRecord(event) {
  const allowed = [
    "revision",
    "timestamp",
    "route",
    "role",
    "roleHash",
    "configuredSelector",
    "resolvedSelector",
    "durationMs",
    "requests",
    "tokens",
    "usage",
    "fallback",
    "result",
    "failureClass",
    "thresholdPercent",
    "thresholdTokens",
    "contextWindow",
    "ownership",
  ];
  const normalized = { revision: EXTENSION_REVISION, timestamp: Date.now() };
  for (const key of allowed) {
    const value = event?.[key];
    if (value === undefined) continue;
    if (["configuredSelector", "resolvedSelector"].includes(key)) {
      const selector = safeSelector(value);
      if (selector) normalized[key] = selector;
    } else if (["durationMs", "requests", "tokens", "thresholdPercent", "thresholdTokens", "contextWindow"].includes(key)) {
      const number = finiteNumber(value);
      if (number !== undefined) normalized[key] = number;
    } else if (key === "usage") {
      const usage = usageSnapshot(value);
      if (usage) normalized.usage = usage;
    } else if (key === "route") {
      if (SAFE_ROUTES.has(value)) normalized.route = value;
    } else if (key === "result") {
      if (SAFE_RESULTS.has(value)) normalized.result = value;
    } else if (key === "fallback" && typeof value === "boolean") {
      normalized.fallback = value;
    } else if (["revision", "role", "roleHash", "failureClass", "ownership"].includes(key)) {
      const atom = safeAtom(value);
      if (atom) normalized[key] = atom;
    }
  }
  return normalized;
}

export function writeRouteEvent(logPath, event, options = {}) {
  const maxBytes = options.maxBytes ?? ROUTING_LOG_MAX_BYTES;
  const fsApi = options.fsApi ?? { appendFileSync, existsSync, mkdirSync, renameSync, statSync, unlinkSync };
  try {
    fsApi.mkdirSync(dirname(logPath), { recursive: true });
    const line = `${JSON.stringify(normalizeRouteRecord(event))}\n`;
    if (
      fsApi.existsSync(logPath) &&
      fsApi.statSync(logPath).size > 0 &&
      fsApi.statSync(logPath).size + Buffer.byteLength(line, "utf8") > maxBytes
    ) {
      const rotated = `${logPath}.1`;
      if (fsApi.existsSync(rotated)) fsApi.unlinkSync(rotated);
      fsApi.renameSync(logPath, rotated);
    }
    fsApi.appendFileSync(logPath, line, "utf8");
    return true;
  } catch {
    return false;
  }
}

export function readRouteEvents(logPath, maxRecords = ROUTING_LOG_MAX_RECORDS) {
  if (!existsSync(logPath)) return [];
  return readFileSync(logPath, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .slice(-maxRecords)
    .flatMap(line => {
      try {
        const parsed = JSON.parse(line);
        return isRecord(parsed) && typeof parsed.route === "string" ? [parsed] : [];
      } catch {
        return [];
      }
    });
}

export function formatRoutingStatus(records, roleState) {
  const grouped = new Map();
  for (const record of Array.isArray(records) ? records : []) {
    const state = grouped.get(record.route) ?? { count: 0, success: 0, failed: 0, latest: undefined };
    state.count += 1;
    if (record.result === "success") state.success += 1;
    if (record.result === "failed") state.failed += 1;
    state.latest = record;
    grouped.set(record.route, state);
  }
  const refreshState = safeAtom(roleState?.refreshState) ?? "unknown";
  const roleHash = safeAtom(roleState?.roleHash) ?? "unknown";
  const unresolvedCount = finiteNumber(roleState?.unresolvedCount) ?? 0;
  const indeterminateCount = finiteNumber(roleState?.indeterminateCount) ?? 0;
  const lines = [
    `rev=${EXTENSION_REVISION} records=${records?.length ?? 0}`,
    `roles{refresh=${refreshState},hash=${roleHash},unresolved=${unresolvedCount},indeterminate=${indeterminateCount}}`,
  ];
  for (const route of ["task", "scout", "sota", "compaction"]) {
    const state = grouped.get(route);
    lines.push(state ? `${route}{count=${state.count},ok=${state.success},failed=${state.failed},latest=${state.latest.resolvedSelector ?? state.latest.result ?? "unknown"}}` : `${route}{count=0}`);
  }
  return lines.join(" ");
}

export function routeLogPath(agentDir) {
  return join(agentDir, "logs", ROUTING_LOG_FILENAME);
}

export function registerRouteTelemetryWriter() {
  globalThis[WRITER_SYMBOL] = (event, agentDir) => {
    if (!agentDir) return false;
    return writeRouteEvent(routeLogPath(agentDir), event);
  };
}

export function emitRouteEvent(event, agentDir) {
  const writer = globalThis[WRITER_SYMBOL];
  if (typeof writer !== "function") return false;
  return writer(event, agentDir);
}

export default function modelRoutingObservability(pi) {
  registerRouteTelemetryWriter();
  let roleState = { roleHash: undefined, validation: [], refreshState: "not-run" };
  const pending = new Map();
  const completedToolCalls = new Set();
  const settings = pi?.pi?.settings;
  const agentDir = typeof pi?.pi?.getAgentDir === "function" ? pi.pi.getAgentDir() : undefined;
  const logger = pi?.logger;
  let writeFailureReported = false;
  const record = event => {
    if (emitRouteEvent(event, agentDir) || writeFailureReported) return;
    writeFailureReported = true;
    logger?.warn?.("OMP model routing telemetry write unavailable", {
      revision: EXTENSION_REVISION,
      failureClass: "local-write-failed",
    });
  };
  const refresh = async ctx => {
    roleState = await refreshAndValidateRoles(ctx, settings);
    if (roleState.refreshState === "failed" || roleState.unresolvedCount > 0) {
      logger?.warn?.("OMP model role validation incomplete", {
        revision: EXTENSION_REVISION,
        refreshState: roleState.refreshState,
        roleHash: roleState.roleHash,
        unresolvedCount: roleState.unresolvedCount,
      });
    }
    return roleState;
  };

  pi.on("session_start", async (_event, ctx) => {
    await refresh(ctx);
  });
  pi.on("before_agent_start", async (_event, ctx) => {
    await refresh(ctx);
  });
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "task") return;
    const state = await refresh(ctx);
    pending.set(event.toolCallId, { roleHash: state.roleHash, startedAt: Date.now() });
    if (pending.size > 1024) pending.delete(pending.keys().next().value);
    for (const dispatchEvent of extractTaskDispatchEvents(event.input, state)) {
      record(dispatchEvent);
    }
  });
  const consume = event => {
    if (event.toolName !== "task") return;
    if (completedToolCalls.has(event.toolCallId)) return;
    const details = event.result?.details ?? event.details ?? event.result;
    const context = pending.get(event.toolCallId) ?? { roleHash: roleState.roleHash };
    const routeEvents = extractTaskRouteEvents(details, context);
    if (routeEvents.length === 0) return;
    completedToolCalls.add(event.toolCallId);
    if (completedToolCalls.size > 1024) completedToolCalls.delete(completedToolCalls.values().next().value);
    for (const routeEvent of routeEvents) {
      record(routeEvent);
    }
    pending.delete(event.toolCallId);
  };
  pi.on("tool_execution_end", consume);
  pi.on("tool_result", consume);
  pi.registerCommand?.("model-routing-status", {
    description: "Show recent redacted task, scout, SOTA, and compaction routing health",
    handler: async (_args, ctx) => {
      const path = routeLogPath(typeof pi?.pi?.getAgentDir === "function" ? pi.pi.getAgentDir() : agentDir ?? "");
      ctx.ui.notify(formatRoutingStatus(readRouteEvents(path), roleState), "info");
    },
  });
}
