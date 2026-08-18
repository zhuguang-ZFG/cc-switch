export const EXTENSION_REVISION = "2026.08.19-r4";

const DEFAULT_COMPACTION_CANDIDATES = Object.freeze([
  "zg-newapi/deepseek-v4-flash",
  "zg-newapi/glm-5.2",
  "zg-newapi/zai-glm-5-2",
  "longcat/LongCat-2.0",
]);
const DEFAULT_RECONCILE_INTERVAL_MS = 1000;
const DEFAULT_STALE_AFTER_MS = 10 * 60 * 1000;
const DEFAULT_FAILURE_COOLDOWN_MS = 5 * 60 * 1000;
const DEFAULT_RETRY_DELAY_MS = 50;
const THRESHOLD_PERCENT_KEY = "compaction.thresholdPercent";
const THRESHOLD_TOKENS_KEY = "compaction.thresholdTokens";
const THRESHOLD_STATE_SYMBOL = Symbol.for("omp.compaction.threshold.states");
const ROUTE_WRITER_SYMBOL = Symbol.for("omp.modelRoutingTelemetry.writer");

const LOCAL_FAILURE_PATTERN =
  /\b(?:abort(?:ed)?|cancel(?:led|ed)?|already compacted|nothing to compact|session too small|no model selected|no available model|usable credentials|api key|required credentials|unauthori[sz]ed|forbidden|invalid (?:configuration|request|model)|compaction already in progress)\b/i;
const PROVIDER_TRANSPORT_PATTERN =
  /\b(?:timed? out|timeout|connection (?:error|reset|refused|closed)|econn(?:reset|refused|aborted)|enotfound|dns|tls|socket|network error|fetch failed|stream (?:error|closed|terminated|read error)|unexpected eof)\b/i;
const LABELED_RETRYABLE_HTTP_STATUS_PATTERN =
  /\b(?:http(?: status)?|status(?: code)?|error code)\s*[:=]?\s*(408|409|425|429|5\d\d)\b/i;
const LEADING_RETRYABLE_HTTP_STATUS_PATTERN =
  /^\s*(408|409|425|429|5\d\d)\s+(?:request timeout|conflict|too early|too many requests|internal server error|bad gateway|service unavailable|gateway timeout)\b/i;

function availableModels(ctx) {
  if (typeof ctx?.models?.list === "function") return ctx.models.list();
  if (typeof ctx?.modelRegistry?.getAvailable === "function") {
    return ctx.modelRegistry.getAvailable();
  }
  return [];
}

function selectorOf(model) {
  if (!model || typeof model !== "object") return undefined;
  if (!model.provider || !model.id) return undefined;
  return `${model.provider}/${model.id}`;
}

export function resolveCompactionThresholdPolicy(model) {
  const contextWindow = Number(model?.contextWindow);
  if (!Number.isFinite(contextWindow) || contextWindow <= 0) {
    return {
      state: "unavailable",
      contextWindow: undefined,
      thresholdPercent: undefined,
      thresholdTokens: undefined,
    };
  }
  const thresholdPercent =
    contextWindow <= 272_000
      ? 70
      : contextWindow <= 400_000
        ? 78
        : contextWindow <= 512_000
          ? 82
          : 85;
  return {
    state: "managed",
    contextWindow,
    thresholdPercent,
    thresholdTokens: Math.floor((contextWindow * thresholdPercent) / 100),
  };
}

function thresholdStateStore() {
  if (!(globalThis[THRESHOLD_STATE_SYMBOL] instanceof WeakMap)) {
    globalThis[THRESHOLD_STATE_SYMBOL] = new WeakMap();
  }
  return globalThis[THRESHOLD_STATE_SYMBOL];
}

export function createCompactionThresholdManager(settings, options = {}) {
  const store = options.store ?? thresholdStateStore();
  let state = settings && store.get(settings);
  if (!state) {
    state = {
      revision: EXTENSION_REVISION,
      ownership: "uninitialized",
      appliedTokens: undefined,
      result: "not-run",
      selector: undefined,
      contextWindow: undefined,
      thresholdPercent: undefined,
      thresholdTokens: undefined,
    };
    if (settings) store.set(settings, state);
  }

  function setState(next) {
    state = next;
    if (settings) store.set(settings, state);
  }

  function snapshot() {
    return { ...state };
  }

  function reconcile(model, reason = "unknown", logger) {
    if (
      !settings ||
      typeof settings.get !== "function" ||
      typeof settings.override !== "function" ||
      typeof settings.clearOverride !== "function"
    ) {
      setState({
        ...state,
        ownership: "unsupported",
        result: "settings-api-unavailable",
        selector: selectorOf(model),
      });
      return snapshot();
    }

    const currentTokens = settings.get(THRESHOLD_TOKENS_KEY);
    if (state.ownership === "managed" && currentTokens === state.appliedTokens) {
      settings.clearOverride(THRESHOLD_TOKENS_KEY);
    } else if (state.ownership === "managed" && currentTokens !== state.appliedTokens) {
      setState({ ...state, ownership: "external-override", appliedTokens: undefined });
    }

    const basePercent = settings.get(THRESHOLD_PERCENT_KEY);
    const baseTokens = settings.get(THRESHOLD_TOKENS_KEY);
    const policy = resolveCompactionThresholdPolicy(model);
    if (basePercent !== -1 || baseTokens !== -1) {
      setState({
        ...state,
        ownership: "user-configured",
        result: "explicit-setting-preserved",
        selector: selectorOf(model),
        contextWindow: policy.contextWindow,
        thresholdPercent: basePercent,
        thresholdTokens: baseTokens,
        appliedTokens: undefined,
      });
      return snapshot();
    }
    if (policy.state !== "managed") {
      setState({
        ...state,
        ownership: "unavailable",
        result: "context-window-unavailable",
        selector: selectorOf(model),
        contextWindow: undefined,
        thresholdPercent: undefined,
        thresholdTokens: undefined,
        appliedTokens: undefined,
      });
      return snapshot();
    }

    settings.override(THRESHOLD_TOKENS_KEY, policy.thresholdTokens);
    setState({
      ...state,
      revision: EXTENSION_REVISION,
      ownership: "managed",
      result: "applied",
      reason,
      selector: selectorOf(model),
      contextWindow: policy.contextWindow,
      thresholdPercent: policy.thresholdPercent,
      thresholdTokens: policy.thresholdTokens,
      appliedTokens: policy.thresholdTokens,
    });
    logger?.debug?.("model-specific compaction threshold reconciled", {
      revision: EXTENSION_REVISION,
      reason,
      selector: state.selector,
      contextWindow: state.contextWindow,
      thresholdPercent: state.thresholdPercent,
      thresholdTokens: state.thresholdTokens,
    });
    return snapshot();
  }

  return { reconcile, getStatus: snapshot };
}

function normalizeCandidates(options) {
  if (Array.isArray(options.candidates) && options.candidates.length > 0) {
    return [
      ...new Set(
        options.candidates
          .filter((candidate) => typeof candidate === "string")
          .map((candidate) => candidate.trim())
          .filter(Boolean),
      ),
    ];
  }
  if (options.target) {
    return [
      options.target,
      ...DEFAULT_COMPACTION_CANDIDATES.filter(
        (candidate) => candidate !== options.target,
      ),
    ];
  }
  return [...DEFAULT_COMPACTION_CANDIDATES];
}

function metadataWriteFailure(model) {
  return {
    selector: selectorOf(model) ?? "unknown/unknown",
    errorClass: "metadata-write-failed",
  };
}

export function classifyCompactionFailure(errorMessage) {
  const message = typeof errorMessage === "string" ? errorMessage : "";
  if (!message || LOCAL_FAILURE_PATTERN.test(message)) {
    return { kind: message ? "local" : "unknown", retryable: false };
  }

  const statusMatch =
    LABELED_RETRYABLE_HTTP_STATUS_PATTERN.exec(message) ??
    LEADING_RETRYABLE_HTTP_STATUS_PATTERN.exec(message);
  if (statusMatch) {
    return {
      kind: "provider-http",
      status: Number.parseInt(statusMatch[1], 10),
      retryable: true,
    };
  }
  if (PROVIDER_TRANSPORT_PATTERN.test(message)) {
    return { kind: "provider-transport", retryable: true };
  }
  return { kind: "unknown", retryable: false };
}

export function resolveCompactionTarget(
  ctx,
  candidates = DEFAULT_COMPACTION_CANDIDATES,
) {
  const available = new Map();
  for (const model of availableModels(ctx)) {
    const selector = selectorOf(model);
    if (selector) available.set(selector, model);
  }
  for (const candidate of candidates) {
    if (available.has(candidate)) {
      return { target: candidate, model: available.get(candidate) };
    }
  }
  return { target: undefined, model: undefined };
}

export function applyGlobalCompactionModel(
  ctx,
  target = DEFAULT_COMPACTION_CANDIDATES[0],
) {
  const models = new Set(availableModels(ctx));
  if (ctx?.model) models.add(ctx.model);

  const result = { inspected: 0, updated: 0, alreadyBound: 0, failed: [] };
  if (!target) return result;

  for (const model of models) {
    if (!model || typeof model !== "object") continue;
    result.inspected += 1;
    if (model.compactionModel === target) {
      result.alreadyBound += 1;
      continue;
    }

    try {
      model.compactionModel = target;
      if (model.compactionModel !== target) {
        throw new Error("model metadata is not writable");
      }
      result.updated += 1;
    } catch {
      result.failed.push(metadataWriteFailure(model));
    }
  }
  return result;
}

export function clearGlobalCompactionModel(ctx, managedTargets) {
  const targets = new Set(managedTargets);
  const models = new Set(availableModels(ctx));
  if (ctx?.model) models.add(ctx.model);

  const result = { inspected: 0, cleared: 0, untouched: 0, failed: [] };
  for (const model of models) {
    if (!model || typeof model !== "object") continue;
    result.inspected += 1;
    if (!targets.has(model.compactionModel)) {
      result.untouched += 1;
      continue;
    }
    try {
      model.compactionModel = undefined;
      if (model.compactionModel !== undefined) {
        throw new Error("model metadata is not writable");
      }
      result.cleared += 1;
    } catch {
      result.failed.push(metadataWriteFailure(model));
    }
  }
  return result;
}

export function countImageBlocks(preparation) {
  let count = 0;
  const messages = [
    ...(preparation?.messagesToSummarize ?? []),
    ...(preparation?.turnPrefixMessages ?? []),
  ];
  for (const message of messages) {
    if (!Array.isArray(message?.content)) continue;
    for (const block of message.content) {
      if (block?.type === "image" || block?.type === "image_url") count += 1;
    }
  }
  return count;
}

function initialStatus() {
  return {
    phase: "idle",
    target: undefined,
    trigger: undefined,
    startedAt: undefined,
    finishedAt: undefined,
    durationMs: undefined,
    tokensBefore: undefined,
    imageBlocks: 0,
    imagePolicy: "text-only-serialization",
    result: undefined,
    errorClass: undefined,
    errorStatus: undefined,
    lastRetryResult: undefined,
  };
}

export function formatCompactionStatus(status) {
  const target = status.target ?? "unavailable";
  const result = status.result ? ` result=${status.result}` : "";
  const duration = Number.isFinite(status.durationMs)
    ? ` duration=${status.durationMs}ms`
    : "";
  const tokens = Number.isFinite(status.tokensBefore)
    ? ` tokensBefore=${status.tokensBefore}`
    : "";
  const error = status.errorClass
    ? ` error=${status.errorClass}${status.errorStatus ? `:${status.errorStatus}` : ""}`
    : "";
  const candidateText = (status.candidates ?? [])
    .map((candidate) => {
      const cooldown = candidate.cooldownRemainingMs
        ? `cooldown:${candidate.cooldownRemainingMs}ms`
        : candidate.state;
      return `${candidate.selector}{${cooldown},a=${candidate.attempts},s=${candidate.successes},f=${candidate.failures},r=${candidate.retryAttempts}}`;
    })
    .join(";");
  const threshold = status.threshold
    ? ` threshold=${status.threshold.ownership}:${status.threshold.thresholdTokens ?? "default"}@${status.threshold.thresholdPercent ?? "default"}%/${status.threshold.contextWindow ?? "unknown"}`
    : "";
  return `rev=${status.revision ?? "unknown"} phase=${status.phase} target=${target}${result} retry=${status.retryState ?? "idle"} extensionRetries=${status.extensionRetries ?? 0}${duration}${tokens} images=${status.imageBlocks}${error}${threshold} candidates=[${candidateText}]`;
}

export function createGlobalCompactionReconciler(options = {}) {
  const candidates = normalizeCandidates(options);
  const intervalMs = options.intervalMs ?? DEFAULT_RECONCILE_INTERVAL_MS;
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_STALE_AFTER_MS;
  const failureCooldownMs =
    options.failureCooldownMs ?? DEFAULT_FAILURE_COOLDOWN_MS;
  const retryDelayMs = options.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS;
  const extensionRetryEnabled = options.extensionRetryEnabled !== false;
  const now = options.now ?? Date.now;
  let timerStarted = false;
  let status = initialStatus();
  let lastUnavailableTargetLogAt = Number.NEGATIVE_INFINITY;
  let autoCompactionActive = false;
  let autoCompactionReason;
  let retryPending = false;
  let retryInProgress = false;
  let retryUsedForCurrentAuto = false;
  let extensionRetries = 0;
  const health = new Map(
    candidates.map((candidate) => [
      candidate,
      {
        available: false,
        cooldownUntil: 0,
        attempts: 0,
        successes: 0,
        failures: 0,
        retryAttempts: 0,
        lastResult: undefined,
        lastFailureClass: undefined,
      },
    ]),
  );

  function updateAvailability(ctx) {
    const selectors = new Set(availableModels(ctx).map(selectorOf).filter(Boolean));
    for (const candidate of candidates) {
      health.get(candidate).available = selectors.has(candidate);
    }
  }

  function activeCandidates(ctx) {
    updateAvailability(ctx);
    const currentTime = now();
    return candidates.filter((candidate) => {
      const candidateHealth = health.get(candidate);
      return (
        candidateHealth.available &&
        candidateHealth.cooldownUntil <= currentTime
      );
    });
  }

  function candidateSnapshots() {
    const currentTime = now();
    return candidates.map((candidate) => {
      const candidateHealth = health.get(candidate);
      const cooldownRemainingMs = Math.max(
        0,
        candidateHealth.cooldownUntil - currentTime,
      );
      return {
        selector: candidate,
        available: candidateHealth.available,
        state: !candidateHealth.available
          ? "unavailable"
          : cooldownRemainingMs > 0
            ? "cooldown"
            : "ready",
        cooldownRemainingMs,
        attempts: candidateHealth.attempts,
        successes: candidateHealth.successes,
        failures: candidateHealth.failures,
        retryAttempts: candidateHealth.retryAttempts,
        lastResult: candidateHealth.lastResult,
        lastFailureClass: candidateHealth.lastFailureClass,
      };
    });
  }

  function retryState() {
    if (retryInProgress) return "running";
    if (retryPending) return "pending";
    return status.lastRetryResult ?? "idle";
  }

  function statusSnapshot() {
    return {
      ...status,
      revision: EXTENSION_REVISION,
      retryState: retryState(),
      extensionRetries,
      candidates: candidateSnapshots(),
    };
  }

  function expireStaleStatus(logger) {
    if (
      status.phase !== "running" ||
      !Number.isFinite(status.startedAt) ||
      now() - status.startedAt < staleAfterMs
    ) {
      return;
    }
    status = {
      ...status,
      phase: "unknown",
      finishedAt: now(),
      durationMs: now() - status.startedAt,
      result: "stale",
      errorClass: "completion-timeout",
      errorStatus: undefined,
    };
    logger?.error?.("global compaction status became stale", {
      target: status.target,
      durationMs: status.durationMs,
      errorClass: status.errorClass,
    });
  }

  function reconcile(ctx, reason, logger) {
    expireStaleStatus(logger);
    const eligible = activeCandidates(ctx);
    const resolved = resolveCompactionTarget(ctx, eligible);
    if (!resolved.target) {
      const cleared = clearGlobalCompactionModel(ctx, candidates);
      if (status.phase !== "running") status.target = undefined;
      const currentTime = now();
      if (currentTime - lastUnavailableTargetLogAt >= staleAfterMs) {
        logger?.error?.("global compaction model unavailable", {
          reason,
          revision: EXTENSION_REVISION,
          candidateCount: candidates.length,
          availableCount: candidateSnapshots().filter(
            (candidate) => candidate.available,
          ).length,
          coolingCount: candidateSnapshots().filter(
            (candidate) => candidate.state === "cooldown",
          ).length,
          cleared: cleared.cleared,
        });
        lastUnavailableTargetLogAt = currentTime;
      }
      return {
        target: undefined,
        targetAvailable: false,
        inspected: cleared.inspected,
        updated: 0,
        cleared: cleared.cleared,
        alreadyBound: 0,
        failed: cleared.failed,
      };
    }

    const result = applyGlobalCompactionModel(ctx, resolved.target);
    if (status.phase !== "running") status.target = resolved.target;
    if (result.updated > 0) {
      logger?.debug?.("global compaction model reconciled", {
        reason,
        revision: EXTENSION_REVISION,
        target: resolved.target,
        inspected: result.inspected,
        updated: result.updated,
      });
    }
    if (result.failed.length > 0) {
      logger?.error?.("global compaction model reconciliation failed", {
        reason,
        target: resolved.target,
        failures: result.failed,
      });
    }
    return {
      target: resolved.target,
      targetAvailable: true,
      cleared: 0,
      ...result,
    };
  }

  function start(ctx, logger) {
    const result = reconcile(ctx, "session_start", logger);
    if (!timerStarted && typeof ctx?.setInterval === "function") {
      ctx.setInterval(
        () => reconcile(ctx, "interval", logger),
        intervalMs,
      );
      timerStarted = true;
    }
    return result;
  }

  function beginAutoCompaction(event) {
    if (retryPending || retryInProgress) return statusSnapshot();
    autoCompactionActive = true;
    autoCompactionReason = event?.reason ?? "auto";
    retryUsedForCurrentAuto = false;
    status.lastRetryResult = undefined;
    return statusSnapshot();
  }

  function beginCompaction(event, ctx, logger) {
    if (retryPending && !retryInProgress) {
      status = {
        ...status,
        phase: "idle",
        result: "retry-pending",
        errorClass: undefined,
        errorStatus: undefined,
      };
      return { ...statusSnapshot(), cancel: true };
    }
    const reconciliation = reconcile(ctx, "session_before_compact", logger);
    const startedAt = now();
    const imageBlocks = countImageBlocks(event?.preparation);
    if (!reconciliation.target) {
      status = {
        ...initialStatus(),
        phase: "idle",
        trigger: retryInProgress
          ? "fallback-retry"
          : autoCompactionReason ?? "manual",
        finishedAt: startedAt,
        tokensBefore: event?.preparation?.tokensBefore,
        imageBlocks,
        result: "unavailable",
        lastRetryResult: retryInProgress ? "failed" : status.lastRetryResult,
      };
      return { ...statusSnapshot(), cancel: autoCompactionActive || retryInProgress };
    }

    const trigger = retryInProgress
      ? "fallback-retry"
      : autoCompactionActive
        ? autoCompactionReason
        : "manual";
    status = {
      ...initialStatus(),
      phase: "running",
      target: reconciliation.target,
      trigger,
      startedAt,
      tokensBefore: event?.preparation?.tokensBefore,
      imageBlocks,
      lastRetryResult: status.lastRetryResult,
    };
    const targetHealth = health.get(status.target);
    targetHealth.attempts += 1;
    if (retryInProgress) targetHealth.retryAttempts += 1;
    logger?.info?.("global compaction started", {
      revision: EXTENSION_REVISION,
      target: status.target,
      trigger: status.trigger,
      mainModel: selectorOf(ctx?.model),
      tokensBefore: status.tokensBefore,
      imageBlocks,
      imagePolicy: status.imagePolicy,
    });
    return { ...statusSnapshot(), cancel: false };
  }

  function completeCompaction(event, logger) {
    if (status.phase !== "running") return statusSnapshot();
    const finishedAt = now();
    const startedAt = status.startedAt ?? finishedAt;
    const wasRetry = status.trigger === "fallback-retry";
    const targetHealth = status.target ? health.get(status.target) : undefined;
    if (targetHealth) {
      targetHealth.successes += 1;
      targetHealth.lastResult = "success";
      targetHealth.lastFailureClass = undefined;
    }
    status = {
      ...status,
      phase: "idle",
      finishedAt,
      durationMs: finishedAt - startedAt,
      tokensBefore:
        event?.compactionEntry?.tokensBefore ?? status.tokensBefore,
      result: wasRetry ? "fallback-success" : "success",
      errorClass: undefined,
      errorStatus: undefined,
      lastRetryResult: wasRetry ? "success" : status.lastRetryResult,
    };
    logger?.info?.("global compaction completed", {
      revision: EXTENSION_REVISION,
      target: status.target,
      result: status.result,
      durationMs: status.durationMs,
      tokensBefore: status.tokensBefore,
      imageBlocks: status.imageBlocks,
      fromExtension: event?.fromExtension === true,
    });
    return statusSnapshot();
  }

  function recordProviderFailure(target, classification, finishedAt) {
    if (!target || !classification.retryable) return;
    const targetHealth = health.get(target);
    if (!targetHealth) return;
    targetHealth.failures += 1;
    targetHealth.lastResult = "failed";
    targetHealth.lastFailureClass = classification.kind;
    targetHealth.cooldownUntil = finishedAt + failureCooldownMs;
  }

  function finishRetryFailure(error, ctx, logger) {
    const finishedAt = now();
    const classification = classifyCompactionFailure(
      error instanceof Error ? error.message : String(error ?? ""),
    );
    const target = status.target;
    recordProviderFailure(target, classification, finishedAt);
    status = {
      ...status,
      phase: "idle",
      finishedAt,
      durationMs: Number.isFinite(status.startedAt)
        ? finishedAt - status.startedAt
        : undefined,
      result: "fallback-failed",
      errorClass: classification.kind,
      errorStatus: classification.status,
      lastRetryResult: "failed",
    };
    logger?.error?.("global compaction fallback ended without a summary", {
      revision: EXTENSION_REVISION,
      target,
      result: status.result,
      errorClass: classification.kind,
      errorStatus: classification.status,
      cooldownMs: classification.retryable
        ? failureCooldownMs
        : undefined,
    });
    reconcile(ctx, "fallback_failure", logger);
    return statusSnapshot();
  }

  function scheduleFallback(ctx, logger) {
    const fallback = reconcile(ctx, "schedule_fallback", logger);
    if (
      !extensionRetryEnabled ||
      retryUsedForCurrentAuto ||
      retryPending ||
      retryInProgress ||
      typeof ctx?.setTimeout !== "function" ||
      typeof ctx?.compact !== "function"
    ) {
      return false;
    }
    if (!fallback.target) return false;

    retryUsedForCurrentAuto = true;
    retryPending = true;
    ctx.setTimeout(async () => {
      retryPending = false;
      retryInProgress = true;
      extensionRetries += 1;
      try {
        await ctx.compact();
        if (status.phase === "running") {
          finishRetryFailure(
            new Error("compaction completed without a completion event"),
            ctx,
            logger,
          );
        }
      } catch (error) {
        finishRetryFailure(error, ctx, logger);
      } finally {
        retryInProgress = false;
      }
    }, retryDelayMs);
    return true;
  }

  function endAutoCompaction(event, ctx, logger) {
    autoCompactionActive = false;
    autoCompactionReason = undefined;
    if (status.phase !== "running") return statusSnapshot();
    if (event?.result && !event?.aborted) {
      return completeCompaction({ compactionEntry: event.result }, logger);
    }

    const finishedAt = now();
    const result = event?.skipped
      ? "skipped"
      : event?.aborted
        ? "aborted"
        : "failed";
    const classification =
      result === "failed"
        ? classifyCompactionFailure(event?.errorMessage)
        : { kind: result, retryable: false };
    const failedTarget = status.target;
    status = {
      ...status,
      phase: "idle",
      finishedAt,
      durationMs: finishedAt - (status.startedAt ?? finishedAt),
      result,
      errorClass: result === "failed" ? classification.kind : undefined,
      errorStatus: classification.status,
    };
    recordProviderFailure(failedTarget, classification, finishedAt);
    const method = result === "failed" ? "error" : "info";
    logger?.[method]?.("global compaction ended without a summary", {
      revision: EXTENSION_REVISION,
      target: failedTarget,
      result,
      durationMs: status.durationMs,
      errorClass: status.errorClass,
      errorStatus: status.errorStatus,
      cooldownMs: classification.retryable
        ? failureCooldownMs
        : undefined,
    });

    if (classification.retryable) scheduleFallback(ctx, logger);
    return statusSnapshot();
  }

  return {
    reconcile,
    start,
    beginAutoCompaction,
    beginCompaction,
    completeCompaction,
    endAutoCompaction,
    getStatus: statusSnapshot,
  };
}

export default function globalCompactionModel(pi) {
  const reconciler = createGlobalCompactionReconciler();
  const settings = pi?.pi?.settings;
  const thresholdManager = createCompactionThresholdManager(settings);
  const agentDir = typeof pi?.pi?.getAgentDir === "function" ? pi.pi.getAgentDir() : undefined;
  const emitRoute = event => {
    const writer = globalThis[ROUTE_WRITER_SYMBOL];
    if (typeof writer !== "function" || !agentDir) return false;
    try {
      return writer(event, agentDir) === true;
    } catch {
      return false;
    }
  };

  pi.on("session_start", (_event, ctx) => {
    thresholdManager.reconcile(ctx.model, "session_start", pi.logger);
    reconciler.start(ctx, pi.logger);
  });
  pi.on("before_agent_start", (_event, ctx) => {
    thresholdManager.reconcile(ctx.model, "before_agent_start", pi.logger);
    reconciler.reconcile(ctx, "before_agent_start", pi.logger);
  });
  pi.on("auto_compaction_start", (event) => {
    reconciler.beginAutoCompaction(event);
  });
  pi.on("session_before_compact", (event, ctx) => {
    const started = reconciler.beginCompaction(event, ctx, pi.logger);
    const threshold = thresholdManager.getStatus();
    emitRoute({
      revision: EXTENSION_REVISION,
      route: "compaction",
      resolvedSelector: started.target,
      result: started.cancel ? "failed" : "started",
      failureClass: started.cancel ? "unavailable" : undefined,
      contextWindow: threshold.contextWindow,
      thresholdPercent: threshold.thresholdPercent,
      thresholdTokens: threshold.thresholdTokens,
      ownership: threshold.ownership,
    });
    return started.cancel ? { cancel: true } : undefined;
  });
  pi.on("session_compact", (event) => {
    const wasRunning = reconciler.getStatus().phase === "running";
    const completed = reconciler.completeCompaction(event, pi.logger);
    if (!wasRunning) return;
    emitRoute({
      revision: EXTENSION_REVISION,
      route: "compaction",
      resolvedSelector: completed.target,
      result: "success",
      durationMs: completed.durationMs,
      fallback: completed.result === "fallback-success",
    });
  });
  pi.on("auto_compaction_end", (event, ctx) => {
    const ended = reconciler.endAutoCompaction(event, ctx, pi.logger);
    if (ended.result && ended.result !== "success" && ended.result !== "fallback-success") {
      emitRoute({
        revision: EXTENSION_REVISION,
        route: "compaction",
        resolvedSelector: ended.target,
        result: "failed",
        durationMs: ended.durationMs,
        failureClass: ended.errorClass ?? ended.result,
      });
    }
  });
  pi.registerCommand?.("compaction-status", {
    description: "Show background compaction health and last result",
    handler: async (_args, ctx) => {
      ctx.ui.notify(
        formatCompactionStatus({
          ...reconciler.getStatus(),
          threshold: thresholdManager.getStatus(),
        }),
        "info",
      );
    },
  });
}
