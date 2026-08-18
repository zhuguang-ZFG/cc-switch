const DEFAULT_COMPACTION_CANDIDATES = Object.freeze([
  "zg-newapi/deepseek-v4-flash",
  "zg-newapi/glm-5.2",
  "zg-newapi/zai-glm-5-2",
  "longcat/LongCat-2.0",
]);
const DEFAULT_RECONCILE_INTERVAL_MS = 1000;
const DEFAULT_STALE_AFTER_MS = 10 * 60 * 1000;
const DEFAULT_FAILURE_COOLDOWN_MS = 5 * 60 * 1000;

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

function normalizeCandidates(options) {
  if (Array.isArray(options.candidates) && options.candidates.length > 0) {
    return [...new Set(options.candidates.filter(Boolean))];
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
    } catch (error) {
      result.failed.push({
        selector: selectorOf(model) ?? "unknown/unknown",
        error: error instanceof Error ? error.message : String(error),
      });
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
    error: undefined,
  };
}

function statusSnapshot(status) {
  return { ...status };
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
  const error = status.error ? ` error=${status.error}` : "";
  return `phase=${status.phase} target=${target}${result}${duration}${tokens} images=${status.imageBlocks}${error}`;
}

export function createGlobalCompactionReconciler(options = {}) {
  const candidates = normalizeCandidates(options);
  const intervalMs = options.intervalMs ?? DEFAULT_RECONCILE_INTERVAL_MS;
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_STALE_AFTER_MS;
  const failureCooldownMs =
    options.failureCooldownMs ?? DEFAULT_FAILURE_COOLDOWN_MS;
  const now = options.now ?? Date.now;
  let timerStarted = false;
  let status = initialStatus();
  let lastUnavailableTargetLogAt = 0;
  const unhealthyUntil = new Map();

  function activeCandidates() {
    const currentTime = now();
    const active = candidates.filter(
      (candidate) => (unhealthyUntil.get(candidate) ?? 0) <= currentTime,
    );
    return active.length > 0 ? active : candidates;
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
      error: "no completion event observed before timeout",
    };
    logger?.error?.("global compaction status became stale", {
      target: status.target,
      durationMs: status.durationMs,
    });
  }

  function reconcile(ctx, reason, logger) {
    expireStaleStatus(logger);
    const resolved = resolveCompactionTarget(ctx, activeCandidates());
    if (!resolved.target) {
      const currentTime = now();
      if (currentTime - lastUnavailableTargetLogAt >= staleAfterMs) {
        logger?.error?.("global compaction model unavailable", {
          reason,
          candidates,
        });
        lastUnavailableTargetLogAt = currentTime;
      }
      return {
        target: undefined,
        targetAvailable: false,
        inspected: 0,
        updated: 0,
        alreadyBound: 0,
        failed: [],
      };
    }

    const result = applyGlobalCompactionModel(ctx, resolved.target);
    if (status.phase !== "running") status.target = resolved.target;
    if (result.updated > 0) {
      logger?.debug?.("global compaction model reconciled", {
        reason,
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

  function beginCompaction(event, ctx, logger) {
    const reconciliation = reconcile(ctx, "session_before_compact", logger);
    const startedAt = now();
    const imageBlocks = countImageBlocks(event?.preparation);
    status = {
      ...initialStatus(),
      phase: "running",
      target: reconciliation.target,
      trigger: event?.reason ?? "manual-or-auto",
      startedAt,
      tokensBefore: event?.preparation?.tokensBefore,
      imageBlocks,
    };
    logger?.info?.("global compaction started", {
      target: status.target,
      trigger: status.trigger,
      mainModel: selectorOf(ctx?.model),
      tokensBefore: status.tokensBefore,
      imageBlocks,
      imagePolicy: status.imagePolicy,
    });
    return statusSnapshot(status);
  }

  function completeCompaction(event, logger) {
    const finishedAt = now();
    const startedAt = status.startedAt ?? finishedAt;
    status = {
      ...status,
      phase: "idle",
      finishedAt,
      durationMs: finishedAt - startedAt,
      tokensBefore:
        event?.compactionEntry?.tokensBefore ?? status.tokensBefore,
      result: "success",
      error: undefined,
    };
    logger?.info?.("global compaction completed", {
      target: status.target,
      durationMs: status.durationMs,
      tokensBefore: status.tokensBefore,
      imageBlocks: status.imageBlocks,
      fromExtension: event?.fromExtension === true,
    });
    return statusSnapshot(status);
  }

  function endAutoCompaction(event, logger) {
    if (status.phase !== "running") return statusSnapshot(status);
    if (event?.result && !event?.aborted) {
      return completeCompaction({ compactionEntry: event.result }, logger);
    }

    const finishedAt = now();
    const result = event?.skipped
      ? "skipped"
      : event?.aborted
        ? "aborted"
        : "failed";
    status = {
      ...status,
      phase: "idle",
      finishedAt,
      durationMs: finishedAt - (status.startedAt ?? finishedAt),
      result,
      error: event?.errorMessage,
    };
    if (result === "failed" && status.target) {
      unhealthyUntil.set(status.target, finishedAt + failureCooldownMs);
    }
    const method = result === "failed" ? "error" : "info";
    logger?.[method]?.("global compaction ended without a summary", {
      target: status.target,
      result,
      durationMs: status.durationMs,
      error: status.error,
      cooldownMs: result === "failed" ? failureCooldownMs : undefined,
    });
    return statusSnapshot(status);
  }

  return {
    reconcile,
    start,
    beginCompaction,
    completeCompaction,
    endAutoCompaction,
    getStatus: () => statusSnapshot(status),
  };
}

export default function globalCompactionModel(pi) {
  const reconciler = createGlobalCompactionReconciler();

  pi.on("session_start", (_event, ctx) => {
    reconciler.start(ctx, pi.logger);
  });
  pi.on("before_agent_start", (_event, ctx) => {
    reconciler.reconcile(ctx, "before_agent_start", pi.logger);
  });
  pi.on("session_before_compact", (event, ctx) => {
    reconciler.beginCompaction(event, ctx, pi.logger);
  });
  pi.on("session_compact", (event) => {
    reconciler.completeCompaction(event, pi.logger);
  });
  pi.on("auto_compaction_end", (event) => {
    reconciler.endAutoCompaction(event, pi.logger);
  });
  pi.registerCommand?.("compaction-status", {
    description: "Show the background compaction target and last result",
    handler: async (_args, ctx) => {
      ctx.ui.notify(formatCompactionStatus(reconciler.getStatus()), "info");
    },
  });
}
