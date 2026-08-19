import { createHash, randomBytes } from "node:crypto";
import {
  appendFileSync,
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

export const EXTENSION_REVISION = "2026.08.19-routing-r5";
export const ROUTING_LOG_FILENAME = "omp-model-routing.jsonl";
export const ROUTING_LOG_MAX_BYTES = 2 * 1024 * 1024;
export const ROUTING_LOG_MAX_RECORDS = 200;
export const COORDINATION_CONTRACT =
  "[OMP coordination contract] Work independently and report only to Main. " +
  "Do not wait for peers, use awaited peer sends, or spawn a coordination tree. " +
  "Yield partial evidence before the runtime or request budget expires.";
export const HUB_WAIT_MAX_MS = 15_000;
export const CANARY_SUCCESS_TTL_MS = 7 * 24 * 60 * 60 * 1000;
export const CANARY_FAILURE_TTL_MS = 30 * 60 * 1000;
export const CANARY_LEASE_STALE_MS = 10 * 60 * 1000;
export const WATCHDOG_WEB_SEARCH_STALL_MS = 2 * 60 * 1000;
export const WATCHDOG_NO_PROGRESS_MS = 5 * 60 * 1000;
export const WATCHDOG_MAX_AGE_MS = 15 * 60 * 1000;

const WRITER_SYMBOL = Symbol.for("omp.modelRoutingTelemetry.writer");
const CANARY_SWEEP_SYMBOL = Symbol.for("omp.modelToolCanary.sweep");
const WATCHDOG_TIMERS_SYMBOL = Symbol.for("omp.agentWatchdog.timers");
const CANARY_ROLES = ["default", "task", "smol"];
const CANARY_DIRECTORY = "model-tool-canary";
const CANARY_STATE_FILENAME = "model-tool-canary-state.json";
const CANARY_LEASE_FILENAME = "model-tool-canary.lock";
const CANARY_PROBE_FILENAME = "omp-model-tool-canary-probe.js";
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
const SAFE_ROUTES = new Set(["task", "scout", "sota", "compaction", "canary", "watchdog"]);
const SAFE_RESULTS = new Set(["started", "success", "failed", "aborted", "skipped", "stale"]);

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

function modelSelector(model) {
  return isRecord(model) && typeof model.provider === "string" && typeof model.id === "string"
    ? safeSelector(`${model.provider}/${model.id}`)
    : undefined;
}

export function discoverCanarySelectors(roles, models) {
  const available = new Set((Array.isArray(models) ? models : []).map(modelSelector).filter(Boolean));
  const selectors = [];
  for (const role of CANARY_ROLES) {
    for (const candidate of roleCandidates(roles?.[role])) {
      const base = safeSelector(stripThinkingSuffix(candidate));
      if (base && available.has(base)) {
        const selector = safeSelector(candidate);
        if (selector) selectors.push(selector);
        break;
      }
    }
  }
  for (const model of Array.isArray(models) ? models : []) {
    if (!isRecord(model) || typeof model.id !== "string" || !model.id.startsWith("omp-sota-")) continue;
    const selector = modelSelector(model);
    if (selector) selectors.push(selector);
  }
  return [...new Set(selectors)];
}

export function isCanaryDue(summary, now = Date.now()) {
  if (!isRecord(summary) || !finiteNumber(summary.checkedAt)) return true;
  const ttl = summary.result === "success" ? CANARY_SUCCESS_TTL_MS : CANARY_FAILURE_TTL_MS;
  return now - summary.checkedAt >= ttl;
}

export function injectCoordinationContract(input) {
  if (!isRecord(input)) return input;
  const prepend = value => {
    if (typeof value !== "string" || value.startsWith(COORDINATION_CONTRACT)) return value;
    return `${COORDINATION_CONTRACT}\n\n${value}`;
  };
  if (Array.isArray(input.tasks)) {
    let changed = false;
    const tasks = input.tasks.map(item => {
      if (!isRecord(item) || typeof item.task !== "string") return item;
      const task = prepend(item.task);
      if (task === item.task) return item;
      changed = true;
      return { ...item, task };
    });
    return changed ? { ...input, tasks } : input;
  }
  if (typeof input.task !== "string") return input;
  const task = prepend(input.task);
  return task === input.task ? input : { ...input, task };
}

export function guardHubInput(input, hasStaleJobs = false) {
  if (!isRecord(input)) return undefined;
  if (input.op === "send" && input.await === true && typeof input.to === "string") {
    return { input: { ...input, await: false } };
  }
  if (input.op !== "wait" || (typeof input.name === "string" && input.name.trim())) return undefined;
  if (typeof input.from === "string" && input.from.trim()) {
    return {
      block: true,
      reason:
        "Peer-specific waits are disabled by the OMP coordination contract. Send a non-blocking update and report to Main.",
    };
  }
  if (hasStaleJobs) {
    return {
      block: true,
      reason:
        "A background agent is stale. Main must inspect with hub jobs, cancel the stale job if needed, preserve partial results, and continue.",
    };
  }
  const timeoutMs = finiteNumber(input.timeoutMs);
  if (timeoutMs === undefined || timeoutMs === 0 || timeoutMs > HUB_WAIT_MAX_MS) {
    return { input: { ...input, timeoutMs: HUB_WAIT_MAX_MS } };
  }
  return undefined;
}

function progressSignature(progress) {
  if (!Array.isArray(progress)) return "none";
  return JSON.stringify(
    progress
      .filter(isRecord)
      .map(item => ({
        id: safeAtom(item.id),
        status: safeAtom(item.status),
        tool: safeAtom(item.currentTool),
        toolStart: finiteNumber(item.currentToolStartMs),
        toolCount: finiteNumber(item.toolCount),
        requests: finiteNumber(item.requests),
      }))
      .sort((left, right) => String(left.id).localeCompare(String(right.id))),
  );
}

function webSearchStartedAt(progress, previous, now) {
  const searches = (Array.isArray(progress) ? progress : []).filter(
    item => isRecord(item) && item.status === "running" && item.currentTool === "web_search",
  );
  if (searches.length === 0) return undefined;
  const starts = searches.map(item => finiteNumber(item.currentToolStartMs)).filter(value => value !== undefined);
  if (starts.length > 0) return Math.min(...starts);
  // OMP 17.3.7 does not forward currentToolStartMs from a detached task.
  return previous ?? now;
}

export function createAgentWatchdog(options = {}) {
  const webSearchStallMs = options.webSearchStallMs ?? WATCHDOG_WEB_SEARCH_STALL_MS;
  const noProgressMs = options.noProgressMs ?? WATCHDOG_NO_PROGRESS_MS;
  const maxAgeMs = options.maxAgeMs ?? WATCHDOG_MAX_AGE_MS;
  const progressByJob = new Map();
  const incidents = new Map();

  function observeTaskProgress(partialResult, now = Date.now()) {
    const details = partialResult?.details ?? partialResult;
    const jobId = safeAtom(details?.async?.jobId);
    if (!jobId) return false;
    const signature = progressSignature(details?.progress);
    const previous = progressByJob.get(jobId);
    const changed = !previous || previous.signature !== signature;
    progressByJob.set(jobId, {
      signature,
      lastChangedAt: changed ? now : previous.lastChangedAt,
      webSearchStartedAt: webSearchStartedAt(details?.progress, previous?.webSearchStartedAt, now),
    });
    if (changed) incidents.delete(jobId);
    return changed;
  }

  function sweep(snapshot, now = Date.now()) {
    const running = Array.isArray(snapshot?.running)
      ? snapshot.running.filter(job => isRecord(job) && job.status === "running" && job.type === "task")
      : [];
    const runningIds = new Set(running.map(job => safeAtom(job.id)).filter(Boolean));
    for (const jobId of [...progressByJob.keys()]) if (!runningIds.has(jobId)) progressByJob.delete(jobId);
    for (const jobId of [...incidents.keys()]) if (!runningIds.has(jobId)) incidents.delete(jobId);

    const fresh = [];
    for (const job of running) {
      const jobId = safeAtom(job.id);
      if (!jobId || incidents.has(jobId)) continue;
      const startedAt = finiteNumber(job.startTime) ?? now;
      const progress = progressByJob.get(jobId);
      let reason;
      let stalledMs;
      if (progress?.webSearchStartedAt && now - progress.webSearchStartedAt >= webSearchStallMs) {
        reason = "web-search-stall";
        stalledMs = now - progress.webSearchStartedAt;
      } else if (now - startedAt >= maxAgeMs) {
        reason = "runtime-budget";
        stalledMs = now - startedAt;
      } else if (now - (progress?.lastChangedAt ?? startedAt) >= noProgressMs) {
        reason = "no-progress";
        stalledMs = now - (progress?.lastChangedAt ?? startedAt);
      }
      if (!reason) continue;
      const incident = { jobId, reason, ageMs: Math.max(0, now - startedAt), stalledMs };
      incidents.set(jobId, incident);
      fresh.push(incident);
    }
    return fresh;
  }

  return {
    observeTaskProgress,
    sweep,
    hasStaleJobs: () => incidents.size > 0,
    getIncidents: () => [...incidents.values()],
  };
}

export function formatWatchdogStatus(incidents) {
  const safe = (Array.isArray(incidents) ? incidents : []).filter(isRecord);
  if (safe.length === 0) return `rev=${EXTENSION_REVISION} stale=0`;
  return `rev=${EXTENSION_REVISION} stale=${safe.length} ${safe
    .map(item => `${safeAtom(item.jobId) ?? "unknown"}{reason=${safeAtom(item.reason) ?? "unknown"},ageMs=${finiteNumber(item.ageMs) ?? 0}}`)
    .join(" ")}`;
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

export function canaryPaths(agentDir) {
  const root = join(agentDir, CANARY_DIRECTORY);
  return {
    root,
    state: join(root, CANARY_STATE_FILENAME),
    lease: join(root, CANARY_LEASE_FILENAME),
  };
}

export function readCanaryState(path) {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return isRecord(parsed) && isRecord(parsed.selectors)
      ? { revision: safeAtom(parsed.revision), updatedAt: finiteNumber(parsed.updatedAt), selectors: parsed.selectors }
      : { selectors: {} };
  } catch {
    return { selectors: {} };
  }
}

export function writeCanaryState(path, state) {
  try {
    mkdirSync(dirname(path), { recursive: true });
    const selectors = {};
    for (const [selector, raw] of Object.entries(isRecord(state?.selectors) ? state.selectors : {}).slice(-64)) {
      const safe = safeSelector(selector);
      if (!safe || !isRecord(raw)) continue;
      selectors[safe] = {
        checkedAt: finiteNumber(raw.checkedAt),
        durationMs: finiteNumber(raw.durationMs),
        result: raw.result === "success" ? "success" : "failed",
        failureClass: safeAtom(raw.failureClass),
        requestIdHash: safeAtom(raw.requestIdHash),
        channelId: safeAtom(raw.channelId),
        gatewayAttribution: safeAtom(raw.gatewayAttribution),
      };
    }
    const payload = JSON.stringify({
      revision: EXTENSION_REVISION,
      updatedAt: Date.now(),
      selectors,
    });
    const temporary = `${path}.${process.pid}.${Date.now()}.tmp`;
    writeFileSync(temporary, `${payload}\n`, "utf8");
    renameSync(temporary, path);
    return true;
  } catch {
    return false;
  }
}

export function acquireCanaryLease(path, now = Date.now()) {
  mkdirSync(dirname(path), { recursive: true });
  const tryAcquire = () => {
    let fd;
    try {
      fd = openSync(path, "wx");
      writeFileSync(fd, `${process.pid} ${now}\n`, "utf8");
      closeSync(fd);
      fd = undefined;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        try {
          unlinkSync(path);
        } catch {}
      };
    } catch (error) {
      if (fd !== undefined) {
        try {
          closeSync(fd);
        } catch {}
        try {
          unlinkSync(path);
        } catch {}
      }
      if (error?.code !== "EEXIST") throw error;
      return undefined;
    }
  };
  let release = tryAcquire();
  if (release) return release;
  try {
    if (now - statSync(path).mtimeMs < CANARY_LEASE_STALE_MS) return undefined;
    unlinkSync(path);
  } catch {
    return undefined;
  }
  release = tryAcquire();
  return release;
}

export function buildCanaryArgs(selector, noncePath, probePath, configPath) {
  const safe = safeSelector(selector);
  if (!safe) throw new Error("invalid-canary-selector");
  return [
    "-p",
    `Use the read tool to read exactly this file: ${noncePath}\nReply with only the file's exact contents.`,
    "--model",
    safe,
    "--no-session",
    "--no-extensions",
    "-e",
    probePath,
    "--config",
    configPath,
    "--no-skills",
    "--no-title",
    "--max-time",
    "2m",
    "--tools",
    "read",
    "--approval-mode",
    "yolo",
  ];
}

function parseCanaryProbe(path) {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

export async function runModelToolCanary(pi, selector, options) {
  const startedAt = Date.now();
  const safe = safeSelector(selector);
  if (!safe) {
    return { checkedAt: startedAt, durationMs: 0, result: "failed", failureClass: "invalid-selector" };
  }
  const root = options.root;
  const probePath = options.probePath;
  if (!existsSync(probePath)) {
    return { checkedAt: startedAt, durationMs: 0, result: "failed", failureClass: "probe-missing" };
  }
  mkdirSync(root, { recursive: true });
  const nonce = `OMP_CANARY_${randomBytes(16).toString("hex")}`;
  const token = randomBytes(8).toString("hex");
  const noncePath = join(root, `omp-model-tool-canary-${token}.txt`);
  const resultPath = `${noncePath}.result.json`;
  const configPath = `${noncePath}.config.yml`;
  try {
    writeFileSync(noncePath, `${nonce}\n`, "utf8");
    writeFileSync(
      configPath,
      "retry:\n  maxRetries: 0\n  modelFallback: false\n",
      { encoding: "utf8", flag: "wx" },
    );
    const result = await pi.exec("omp", buildCanaryArgs(safe, noncePath, probePath, configPath), {
      cwd: options.cwd,
      timeout: options.timeoutMs ?? 130_000,
    });
    const probe = parseCanaryProbe(resultPath);
    const toolProof =
      probe?.readCalled === true &&
      probe?.argsValid === true &&
      probe?.toolResultContainsNonce === true &&
      probe?.finalContainsNonce === true;
    const outputProof = String(result.stdout ?? "").trim().includes(nonce);
    const success = result.code === 0 && result.killed !== true && toolProof && outputProof;
    let failureClass;
    if (!success) {
      if (result.killed === true) failureClass = "timeout";
      else if (!probe) failureClass = "probe-result-missing";
      else if (!probe.readCalled) failureClass = "tool-not-called";
      else if (!probe.argsValid) failureClass = "tool-args-invalid";
      else if (!probe.toolResultContainsNonce) failureClass = "tool-result-invalid";
      else if (!probe.finalContainsNonce || !outputProof) failureClass = "final-output-invalid";
      else failureClass = "child-failed";
    }
    const channelId = safeAtom(probe?.channelId);
    const requestIdHash = safeAtom(probe?.requestIdHash);
    return {
      checkedAt: startedAt,
      durationMs: Math.max(0, Date.now() - startedAt),
      result: success ? "success" : "failed",
      failureClass,
      requestIdHash,
      channelId,
      gatewayAttribution: channelId ? "channel-id" : requestIdHash ? "request-id" : "missing",
    };
  } catch {
    return {
      checkedAt: startedAt,
      durationMs: Math.max(0, Date.now() - startedAt),
      result: "failed",
      failureClass: "local-exec-failed",
      gatewayAttribution: "missing",
    };
  } finally {
    for (const path of [noncePath, resultPath, configPath]) {
      try {
        unlinkSync(path);
      } catch {}
    }
  }
}

export function formatCanaryStatus(state, selectors = []) {
  const entries = isRecord(state?.selectors) ? state.selectors : {};
  const visible = selectors.length > 0 ? selectors : Object.keys(entries);
  if (visible.length === 0) return `rev=${EXTENSION_REVISION} selectors=0`;
  return `rev=${EXTENSION_REVISION} selectors=${visible.length} ${visible
    .map(selector => {
      const summary = entries[selector];
      const result = isRecord(summary) ? safeAtom(summary.result) ?? "unknown" : "never";
      const attribution = isRecord(summary) ? safeAtom(summary.gatewayAttribution) ?? "missing" : "missing";
      return `${safeSelector(selector) ?? "invalid"}{result=${result},gateway=${attribution}}`;
    })
    .join(" ")}`;
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
    "trigger",
    "thresholdPercent",
    "thresholdTokens",
    "contextWindow",
    "ownership",
    "jobId",
    "ageMs",
    "stalledMs",
    "requestIdHash",
    "channelId",
    "gatewayAttribution",
  ];
  const normalized = { revision: EXTENSION_REVISION, timestamp: Date.now() };
  for (const key of allowed) {
    const value = event?.[key];
    if (value === undefined) continue;
    if (["configuredSelector", "resolvedSelector"].includes(key)) {
      const selector = safeSelector(value);
      if (selector) normalized[key] = selector;
    } else if (["durationMs", "requests", "tokens", "thresholdPercent", "thresholdTokens", "contextWindow", "ageMs", "stalledMs"].includes(key)) {
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
    } else if (["revision", "role", "roleHash", "failureClass", "trigger", "ownership", "jobId", "requestIdHash", "channelId", "gatewayAttribution"].includes(key)) {
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
  for (const route of ["task", "scout", "sota", "compaction", "canary", "watchdog"]) {
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
  let lastCanaryRoleHash;
  const pending = new Map();
  const completedToolCalls = new Set();
  const watchdog = createAgentWatchdog();
  const settings = pi?.pi?.settings;
  const agentDir = typeof pi?.pi?.getAgentDir === "function" ? pi.pi.getAgentDir() : undefined;
  const logger = pi?.logger;
  const probePath = agentDir ? join(agentDir, "canary", CANARY_PROBE_FILENAME) : undefined;
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

  const runCanarySweep = async (ctx, options = {}) => {
    if (!agentDir || !probePath) return { selectors: {} };
    const paths = canaryPaths(agentDir);
    const selectors = options.selectors ?? discoverCanarySelectors(roleState.roles, ctx.models.list());
    const existing = globalThis[CANARY_SWEEP_SYMBOL];
    if (existing && typeof existing.then === "function") {
      if (options.manual === true) await existing;
      else return readCanaryState(paths.state);
    }
    const execute = async () => {
      const release = acquireCanaryLease(paths.lease);
      if (!release) return readCanaryState(paths.state);
      try {
        const state = readCanaryState(paths.state);
        for (const selector of selectors) {
          if (options.manual !== true && !isCanaryDue(state.selectors[selector])) continue;
          record({
            route: "canary",
            result: "started",
            resolvedSelector: selector,
            trigger: options.manual === true ? "manual" : "role-refresh",
          });
          const summary = await runModelToolCanary(pi, selector, {
            root: paths.root,
            probePath,
            cwd: ctx.cwd,
          });
          state.selectors[selector] = summary;
          writeCanaryState(paths.state, state);
          record({
            route: "canary",
            result: summary.result,
            resolvedSelector: selector,
            trigger: options.manual === true ? "manual" : "role-refresh",
            durationMs: summary.durationMs,
            failureClass: summary.failureClass,
            requestIdHash: summary.requestIdHash,
            channelId: summary.channelId,
            gatewayAttribution: summary.gatewayAttribution,
          });
        }
        return state;
      } finally {
        release();
      }
    };
    const promise = execute();
    globalThis[CANARY_SWEEP_SYMBOL] = promise;
    try {
      return await promise;
    } finally {
      if (globalThis[CANARY_SWEEP_SYMBOL] === promise) delete globalThis[CANARY_SWEEP_SYMBOL];
    }
  };

  const scheduleCanarySweep = (ctx, force = false) => {
    if (!agentDir || typeof ctx?.setTimeout !== "function" || (!force && lastCanaryRoleHash === roleState.roleHash)) return;
    lastCanaryRoleHash = roleState.roleHash;
    ctx.setTimeout(async () => {
      try {
        await runCanarySweep(ctx);
      } catch {
        logger?.warn?.("OMP model tool canary sweep failed locally", {
          revision: EXTENSION_REVISION,
          failureClass: "local-canary-sweep-failed",
        });
      }
    }, 0);
  };

  const ensureWatchdogTimer = ctx => {
    if (typeof ctx?.setInterval !== "function") return;
    const sessionId = ctx.sessionManager?.getSessionId?.() ?? "unknown";
    const timers = globalThis[WATCHDOG_TIMERS_SYMBOL] instanceof Map
      ? globalThis[WATCHDOG_TIMERS_SYMBOL]
      : new Map();
    globalThis[WATCHDOG_TIMERS_SYMBOL] = timers;
    const previous = timers.get(sessionId);
    if (previous) previous.ctx.clearTimer(previous.timer);
    const timer = ctx.setInterval(() => {
      const incidents = watchdog.sweep(ctx.getAsyncJobSnapshot());
      if (incidents.length === 0) return;
      for (const incident of incidents) {
        record({ route: "watchdog", result: "stale", failureClass: incident.reason, ...incident });
      }
      const ids = incidents.map(incident => incident.jobId).join(",");
      pi.sendMessage(
        {
          customType: "agent-watchdog",
          content:
            `OMP watchdog detected stale task jobs: ${ids}. ` +
            "Main must inspect with hub jobs, cancel stale jobs if needed, preserve partial results, and continue the work.",
          display: false,
          details: { revision: EXTENSION_REVISION, jobIds: incidents.map(item => item.jobId) },
        },
        { triggerTurn: true, deliverAs: "steer" },
      );
    }, 15_000);
    timers.set(sessionId, { ctx, timer });
  };

  pi.on("session_start", async (_event, ctx) => {
    await refresh(ctx);
    ensureWatchdogTimer(ctx);
    scheduleCanarySweep(ctx, true);
  });
  pi.on("before_agent_start", async (_event, ctx) => {
    await refresh(ctx);
    scheduleCanarySweep(ctx);
  });
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "hub") return guardHubInput(event.input, watchdog.hasStaleJobs());
    if (event.toolName !== "task") return undefined;
    const guardedInput = injectCoordinationContract(event.input);
    const state = await refresh(ctx);
    pending.set(event.toolCallId, { roleHash: state.roleHash, startedAt: Date.now() });
    if (pending.size > 1024) pending.delete(pending.keys().next().value);
    for (const dispatchEvent of extractTaskDispatchEvents(guardedInput, state)) {
      record(dispatchEvent);
    }
    scheduleCanarySweep(ctx);
    return guardedInput === event.input ? undefined : { input: guardedInput };
  });
  pi.on("tool_execution_update", event => {
    if (event.toolName === "task") watchdog.observeTaskProgress(event.partialResult);
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
  pi.on("session_shutdown", (_event, ctx) => {
    const sessionId = ctx.sessionManager?.getSessionId?.() ?? "unknown";
    const timers = globalThis[WATCHDOG_TIMERS_SYMBOL];
    const current = timers instanceof Map ? timers.get(sessionId) : undefined;
    if (current) current.ctx.clearTimer(current.timer);
    if (timers instanceof Map) timers.delete(sessionId);
  });
  pi.registerCommand?.("model-routing-status", {
    description: "Show recent redacted task, canary, watchdog, SOTA, and compaction routing health",
    handler: async (_args, ctx) => {
      const path = routeLogPath(typeof pi?.pi?.getAgentDir === "function" ? pi.pi.getAgentDir() : agentDir ?? "");
      ctx.ui.notify(formatRoutingStatus(readRouteEvents(path), roleState), "info");
    },
  });
  pi.registerCommand?.("model-tool-canary", {
    description: "Run bounded real-tool canaries for one selector or all managed roles",
    handler: async (args, ctx) => {
      await refresh(ctx);
      const requested = typeof args === "string" ? args.trim() : "";
      let selectors;
      if (requested) {
        const resolved = ctx.models.resolve(requested);
        const selector = modelSelector(resolved);
        if (!selector) {
          ctx.ui.notify("model-tool-canary: selector is unavailable", "error");
          return;
        }
        selectors = [selector];
      } else {
        selectors = discoverCanarySelectors(roleState.roles, ctx.models.list());
      }
      const state = await runCanarySweep(ctx, { selectors, manual: true });
      ctx.ui.notify(formatCanaryStatus(state, selectors), "info");
    },
  });
  pi.registerCommand?.("model-tool-canary-status", {
    description: "Show cached redacted model tool-canary results",
    handler: async (_args, ctx) => {
      const selectors = discoverCanarySelectors(roleState.roles, ctx.models.list());
      const state = agentDir ? readCanaryState(canaryPaths(agentDir).state) : { selectors: {} };
      ctx.ui.notify(formatCanaryStatus(state, selectors), "info");
    },
  });
  pi.registerCommand?.("agent-watchdog-status", {
    description: "Show stale background task jobs detected by the OMP watchdog",
    handler: async (_args, ctx) => {
      watchdog.sweep(ctx.getAsyncJobSnapshot());
      ctx.ui.notify(formatWatchdogStatus(watchdog.getIncidents()), "info");
    },
  });
}
