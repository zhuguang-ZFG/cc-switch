const DEFAULT_MAX_CONTINUATIONS = 3;

const ENGLISH_ACTION =
  "run|apply|fix|check|test|update|change|inspect|investigate|continue|implement|verify|review|deploy|commit|edit|write|read";

const ENGLISH_PROMISE_PATTERNS = [
  /\b(?:doing|running|applying|fixing|checking|testing|updating|changing|investigating|inspecting)\s+(?:that|this|it)\s+now\b/i,
  new RegExp(
    `\\b(?:let me|i(?:'ll| will| am going to| should| need to))\\b[^.!?]{0,160}\\b(?:${ENGLISH_ACTION})\\b[^.!?]{0,80}\\b(?:now|next|immediately)\\b`,
    "i",
  ),
  /\bi(?:'ll| will)\s+continue\b/i,
];

const CHINESE_PROMISE_PATTERNS = [
  /我(?:会|将)(?:直接|立即|马上|继续)*继续/u,
  /(?:我)?(?:现在|接下来|随后)(?:会|将|来|准备)?(?:立即|马上|直接)?(?:执行|处理|检查|测试|更新|修改|调查|核对|验证|实现|修复|部署|提交|编辑|读取|继续)/u,
  /继续(?:执行|处理|检查|测试|更新|修改|调查|核对|验证|实现|修复|部署|提交)/u,
];

const NEGATED_ACTION_PATTERNS = [
  new RegExp(
    `\\b(?:not|never|won't|will not|can't|cannot|don't|do not|should not|need not|am not|isn't|aren't)\\b[^.!?]{0,120}\\b(?:${ENGLISH_ACTION})\\b`,
    "i",
  ),
  /(?:不会|不再|不能|无法|无需|不需要|不要|未能)(?:继续|执行|处理|检查|测试|更新|修改|调查|核对|验证|实现|修复|部署|提交)/u,
];

const CONDITIONAL_OFFER_PATTERNS = [
  /\b(?:let me know|if you (?:want|would like|need) me to|if you'd like me to|would you like me to|should i|can i)\b/i,
  /(?:如果|若是|若你|如需|你(?:想|希望|需要)我|是否需要我|要不要我|可以的话|告诉我)/u,
];

const BLOCKER_PATTERNS = [
  /\b(?:unable to|cannot continue|can't continue|blocked by|waiting for (?:you|the user)|requires? (?:your|user) (?:input|approval|permission))\b/i,
  /(?:无法继续|不能继续|被阻塞|受阻|等待(?:你|用户)|需要(?:你|用户)|请(?:你|用)|写权限|重新启动会话|恢复后)/u,
];

const COMPLETION_PATTERNS = [
  /\b(?:the (?:task|work|implementation|fix) is (?:complete|completed|done|finished)|everything is done|all tests pass(?:ed)?)\b/i,
  /(?:任务|工作|实现|修复)(?:已经|已)(?:完成|结束)|全部(?:已经|已)?完成|所有测试(?:均|都)?通过/u,
];

const USER_RESPONSE_CUE_PATTERNS = [
  /^(?:please\s+)?(?:confirm|reply|choose|pick|decide|advise|answer|tell me|let me know)\b/i,
  /^(?:请)?(?:确认|回复|选择|决定|告知|告诉我|回答)/u,
];

function messageBlocks(message) {
  return Array.isArray(message?.content) ? message.content : [];
}

export function assistantText(message) {
  return messageBlocks(message)
    .filter((block) => block?.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n")
    .trim();
}

export function hasToolCall(message) {
  return messageBlocks(message).some(
    (block) => block?.type === "toolCall" || block?.type === "tool_use",
  );
}

function lastNonEmptyLine(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .at(-1);
}

function matchesAny(text, patterns) {
  return patterns.some((pattern) => pattern.test(text));
}

export function isExplicitContinuationPromise(text) {
  const normalized = text.trim();
  if (!normalized) return false;

  const lastLine = lastNonEmptyLine(normalized) ?? "";
  if (/[?？]\s*$/.test(lastLine)) return false;
  if (matchesAny(lastLine, USER_RESPONSE_CUE_PATTERNS)) return false;
  if (matchesAny(normalized, BLOCKER_PATTERNS)) return false;
  if (matchesAny(normalized, CONDITIONAL_OFFER_PATTERNS)) return false;
  if (matchesAny(normalized, NEGATED_ACTION_PATTERNS)) return false;
  if (matchesAny(normalized, COMPLETION_PATTERNS)) return false;

  return (
    matchesAny(normalized, ENGLISH_PROMISE_PATTERNS) ||
    matchesAny(normalized, CHINESE_PROMISE_PATTERNS)
  );
}

export function shouldContinueUnexpectedStop(message) {
  if (!message || message.role !== "assistant") return false;
  if (message.stopReason !== "stop") return false;
  if (hasToolCall(message)) return false;
  return isExplicitContinuationPromise(assistantText(message));
}

export function createUnexpectedStopHandler(options = {}) {
  const maxContinuations =
    options.maxContinuations ?? DEFAULT_MAX_CONTINUATIONS;
  const continuationCounts = new Map();

  return function handleUnexpectedStop(event) {
    if (event?.signal?.aborted) return undefined;

    const sessionId = event?.session_id ?? "unknown";
    if (!event?.stop_hook_active) continuationCounts.set(sessionId, 0);

    if (!shouldContinueUnexpectedStop(event?.last_assistant_message)) {
      continuationCounts.delete(sessionId);
      return undefined;
    }

    const count = continuationCounts.get(sessionId) ?? 0;
    if (count >= maxContinuations) return undefined;
    continuationCounts.set(sessionId, count + 1);

    return {
      continue: true,
      additionalContext:
        "You explicitly promised to continue but ended without taking the promised action. " +
        "Continue now by performing the next concrete tool call or repository action. " +
        "Do not narrate another intention to continue. If execution is genuinely blocked, " +
        "state the concrete blocker and request only the input needed to unblock it.",
    };
  };
}

export default function unexpectedStopGuard(pi) {
  const handleUnexpectedStop = createUnexpectedStopHandler();

  pi.on("session_stop", (event) => {
    const result = handleUnexpectedStop(event);
    if (result) {
      pi.logger?.debug?.("unexpected-stop guard requested continuation", {
        sessionId: event.session_id,
        turnId: event.turn_id,
      });
    }
    return result;
  });
}
