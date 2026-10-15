# 运行时三警告修复（2026-10-15 晚）：memory review / advisor 回退 / 530

用户报三条运行时警告，逐条归因并修复。

## 警告与根因

### 1. `Memory auto-review failed in both transports. Direct: parse_error.
Subprocess: Unable to resolve a directly executable Pi CLI on Windows`

pi-hermes-memory 插件的 review 模型由 `~/.pi/agent/hermes-memory-config.json`
的 `llmModelOverride` 钉定，现场值 = **`zg-newapi/agnes-2.5-flash`** —— 该名已死
（ch68/ch69/ch220 三腿全暗，alignment 门禁在 quarantine 在册），direct 传输的
模型调用必然失败。subprocess 传输在 Windows 另有独立缺陷：`resolveChildPiInvocation`
要求 argv[1] 是 cli.js / 能 resolve `@earendil-works/pi-coding-agent` / PATH 上有
`pi.exe`——当前 OMP 是编译产物且 PATH 无 pi，三条件全不满足（上游缺陷，已报 issue）。

### 2. `Fallback: zg-newapi/glm-5.3-flash:medium -> zg-newapi-anthropic/claude-opus-4-8`

advisor 链的目标是 `claude-opus-4-8`——全天 0%（ch72 唯一腿 429 Service
Unavailable，ch94/95 status=2），兜底即死路。

### 3. `Advisor request failed: 530 openai_error`

glm-5.3-flash 主选腿 ch118（seeseed，CF 源）530 抖动（在册先例 84×/h）；
**530 不在 `AutomaticRetryStatusCodes=408,500-503`** → 网关不重试池内其他腿
→ OMP 层回退 → 落到上述死链。

## 修复

| # | 变更 | 生效 |
|---|---|---|
| 1 | `~/.pi/agent/hermes-memory-config.json`：`llmModelOverride` `zg-newapi/agnes-2.5-flash` → **`zg-newapi/deepseek-v4.1-flash`**（3 活腿 ch173/ch218/ch208、ratio 0、1M ctx；`llmThinkingOverride: off` 保留） | 下次 OMP 启动（插件 activation 时 loadConfig） |
| 2 | `~/.omp/agent/config.yml`：`fallbackChains.advisor` `[claude-opus-4-8]` → **`[zg-newapi/deepseek-v4.1-flash]`**（advisor 主选白名单不变；链不重复主选、advisor 非推理角色三连门禁全过） | 下次 OMP 启动 |
| 3 | `RetryTimes` **6 → 1**（契约漂移，房规脚本 `update_newapi_retry_budget.py --apply`，自动备份） | **即时**（网关层） |

备份：`hermes-memory-config.json.bak-20261015-182046`、
`newapi-retry-budget-20261015-182057.json`（verified ok）。

## 门禁（修复后实测）

- `test_omp_routes` **40/40 OK**（advisor 链变更后）。
- `newapi-local-smoke`（18:21 轮）→ **`automatic channel recovery` 由 FAIL 转
  OK**（RetryTimes 回归契约值）；FAIL 集合 **8 → 7 类**，余：channels /
  ai.168661 posture / primary opus pool posture / backup opus pool posture /
  pool capacity claude-opus-4-8 / unregistered model ratios / pool answer rate。

## 遗留

1. **subprocess 传输 Windows 解析**：需 PATH 布局（`pi.exe` 或
   `pi.cmd`+`node_modules/@earendil-works/pi-coding-agent/.../cli.js`）或上游修；
   当前以 direct 传输 + 活体 override 规避。
2. **530 是否入 `AutomaticRetryStatusCodes`**（现 `408,500-503`）：加入可让池内
   其他腿承接 CF 530 而非跨模型回退；需同步 smoke 断言，属契约变更，待裁决。
3. 重启 OMP 后复核：memory review 是否恢复（插件日志/`💾 Memory auto-reviewed`
   通知）、advisor 回退是否指向 dsv41f。

## 回滚

```bash
cp ~/.pi/agent/hermes-memory-config.json.bak-20261015-182046 ~/.pi/agent/hermes-memory-config.json
git -C ~/.omp/agent revert <本 commit>          # advisor 链
# RetryTimes：PUT /api/option/ RetryTimes=6（或用备份 json 回填）
```
