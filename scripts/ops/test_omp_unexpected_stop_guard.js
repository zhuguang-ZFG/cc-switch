import assert from "node:assert/strict";
import test from "node:test";

import unexpectedStopGuard, {
  createUnexpectedStopHandler,
  isExplicitContinuationPromise,
  shouldContinueUnexpectedStop,
} from "./omp-unexpected-stop-guard.js";

function assistant(text, overrides = {}) {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    stopReason: "stop",
    ...overrides,
  };
}

function stopEvent(message, overrides = {}) {
  return {
    session_id: "session-a",
    turn_id: 1,
    stop_hook_active: false,
    last_assistant_message: message,
    signal: { aborted: false },
    ...overrides,
  };
}

test("detects the observed Chinese continuation promise", () => {
  assert.equal(
    isExplicitContinuationPromise(
      "我会继续，结合 MCP 官方规范、Python SDK、GitHub issue/PR 和社区实现重新校准 findings，并区分规范缺陷与测试质量问题。",
    ),
    true,
  );
});

test("detects clear English continuation promises from upstream PR 3695", () => {
  assert.equal(
    isExplicitContinuationPromise(
      "I should apply the same fix to the JS eval worker. Doing that now.",
    ),
    true,
  );
  assert.equal(
    isExplicitContinuationPromise("I will run the scoped tests next."),
    true,
  );
});

test("rejects negated actions and conditional offers", () => {
  assert.equal(
    isExplicitContinuationPromise("I will not run tests next."),
    false,
  );
  assert.equal(
    isExplicitContinuationPromise("I don't need to continue now."),
    false,
  );
  assert.equal(
    isExplicitContinuationPromise(
      "Let me know if you want me to run the tests next.",
    ),
    false,
  );
  assert.equal(
    isExplicitContinuationPromise("如果你希望，我接下来会运行测试。"),
    false,
  );
});

test("rejects blockers that require user action", () => {
  assert.equal(
    isExplicitContinuationPromise(
      "已确认剩余工作正是这四项。请用可写权限重新启动会话；恢复后我会直接继续。",
    ),
    false,
  );
  assert.equal(
    isExplicitContinuationPromise(
      "I cannot continue because this requires your permission. I will continue next.",
    ),
    false,
  );
});

test("rejects final answers and user-directed next steps from issue 6540", () => {
  assert.equal(
    isExplicitContinuationPromise(
      "The work is complete and all tests passed.\n\n## Next steps\n1. Paste the result into GPT Sites.\n2. Bring back what it produces.",
    ),
    false,
  );
  assert.equal(
    isExplicitContinuationPromise("Should I do that for you?"),
    false,
  );
  assert.equal(isExplicitContinuationPromise("请确认是否需要我继续？"), false);
});

test("continues only a normal text-only assistant stop", () => {
  const handler = createUnexpectedStopHandler();

  assert.equal(shouldContinueUnexpectedStop(assistant("我会继续检查。")), true);
  assert.equal(
    shouldContinueUnexpectedStop(
      assistant("我会继续检查。", {
        content: [
          { type: "text", text: "我会继续检查。" },
          { type: "toolCall", name: "read", arguments: {} },
        ],
      }),
    ),
    false,
  );
  assert.equal(
    shouldContinueUnexpectedStop(
      assistant("我会继续检查。", { stopReason: "error" }),
    ),
    false,
  );
  assert.equal(
    handler(
      stopEvent(assistant("我会继续检查。"), {
        signal: { aborted: true },
      }),
    ),
    undefined,
  );
});

test("bounds a continuation chain and resets on a fresh stop", () => {
  const handler = createUnexpectedStopHandler({ maxContinuations: 3 });
  const message = assistant("I will run the tests next.");

  assert.ok(handler(stopEvent(message)));
  assert.ok(
    handler(stopEvent(message, { turn_id: 2, stop_hook_active: true })),
  );
  assert.ok(
    handler(stopEvent(message, { turn_id: 3, stop_hook_active: true })),
  );
  assert.equal(
    handler(stopEvent(message, { turn_id: 4, stop_hook_active: true })),
    undefined,
  );
  assert.ok(
    handler(stopEvent(message, { turn_id: 5, stop_hook_active: false })),
  );
});

test("registers one session_stop handler with OMP", () => {
  const registrations = [];
  unexpectedStopGuard({
    on(event, handler) {
      registrations.push({ event, handler });
    },
    logger: { debug() {} },
  });

  assert.equal(registrations.length, 1);
  assert.equal(registrations[0].event, "session_stop");
  assert.ok(registrations[0].handler(stopEvent(assistant("我会继续检查。"))));
});
