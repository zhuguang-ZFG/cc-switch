const DEFAULT_COMPACTION_MODEL = "zg-newapi/deepseek-v4-flash";
const DEFAULT_RECONCILE_INTERVAL_MS = 1000;

function availableModels(ctx) {
  if (typeof ctx?.models?.list === "function") return ctx.models.list();
  if (typeof ctx?.modelRegistry?.getAvailable === "function") {
    return ctx.modelRegistry.getAvailable();
  }
  return [];
}
export function applyGlobalCompactionModel(
  ctx,
  target = DEFAULT_COMPACTION_MODEL,
) {
  const models = new Set(availableModels(ctx));
  if (ctx?.model) models.add(ctx.model);

  const result = { inspected: 0, updated: 0, alreadyBound: 0, failed: [] };
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
        selector: `${model.provider ?? "unknown"}/${model.id ?? "unknown"}`,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return result;
}

export function createGlobalCompactionReconciler(options = {}) {
  const target = options.target ?? DEFAULT_COMPACTION_MODEL;
  const intervalMs = options.intervalMs ?? DEFAULT_RECONCILE_INTERVAL_MS;
  let timerStarted = false;

  function reconcile(ctx, reason, logger) {
    const result = applyGlobalCompactionModel(ctx, target);
    if (result.updated > 0) {
      logger?.debug?.("global compaction model reconciled", {
        reason,
        target,
        inspected: result.inspected,
        updated: result.updated,
      });
    }
    if (result.failed.length > 0) {
      logger?.error?.("global compaction model reconciliation failed", {
        reason,
        target,
        failures: result.failed,
      });
    }
    return result;
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

  return { reconcile, start };
}

export default function globalCompactionModel(pi) {
  const reconciler = createGlobalCompactionReconciler();

  pi.on("session_start", (_event, ctx) => {
    reconciler.start(ctx, pi.logger);
  });
  pi.on("before_agent_start", (_event, ctx) => {
    reconciler.reconcile(ctx, "before_agent_start", pi.logger);
  });
  pi.on("session_before_compact", (_event, ctx) => {
    reconciler.reconcile(ctx, "session_before_compact", pi.logger);
  });
}
