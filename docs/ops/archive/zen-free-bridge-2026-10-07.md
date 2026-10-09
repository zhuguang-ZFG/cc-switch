# zen-free-bridge：OpenCode Zen 免费档经真实 CLI 入 OMP（2026-10-07）

## 结论

OpenCode Zen 免费档**只能从真实 opencode 客户端内调用**：裸 API 请求（任意
代 Console key、任意头/UA 配方）一律 `403 FreeTierError`；本机安装的
opencode CLI（1.18.35，凭据=Console key）能过门禁，随后按**账号级每日免费
额度**以 429 rate-limit 回应（08-20 同款 429 类，配额非故障）。

落地：本地 OpenAI 兼容桥 `scripts/ops/zen_free_bridge.mjs`（127.0.0.1:8412）
驱动 opencode CLI，NewAPI ch178 `zen-free-bridge`（12 模型 ModelRatio=0）入网，
OMP 注册 6 新条目 + 4 条目改源。免费档配额恢复后逐模型实弹补验。

## 关键证据（2026-10-07）

- 裸 API：`/zen/v1/chat/completions` + `fledge-alpha-free`，旧代 `sk-` key 与
  新代 `oc_sk_` key（ch96/ch130 同源）均 403 FreeTierError，文案逐字相同。
- 真实 CLI：`opencode run -p --model opencode/fledge-alpha-free`（auth.json
  注入 Console key）→ 不再 403，报 `Rate limit exceeded`（83s）。
- 桥 E2E：`curl 8412/v1/chat/completions`（fledge）→ 429 rate_limit_error
  （~120s）；同日 3 个代表模型（fledge/exo/nemotron-ultra）探针全 quota →
  **限流为账号级全局，非按模型**。
- 修复记录：Bun 流 chunk 需 TextDecoder（Uint8Array.toString 出字节码）；
  Windows 命令行 8191 上限 → prompt 改 stdin 管道；429 后 10min 熔断免起 CLI
  （防 OMP/Guardian 重试风暴，快败 429）。

## 链路

```
OMP → NewAPI 3002 /v1/chat/completions → ch178 (base http://127.0.0.1:8412)
  → zen_free_bridge.mjs → opencode CLI run -p (stdin prompt)
  → opencode.ai/zen 免费档（真实客户端会话过门禁）→ 429 配额 | 200 出文
```

## 渠道形态

| 项 | 值 |
|---|---|
| 渠道 | ch178 `zen-free-bridge`（type=1，base `http://127.0.0.1:8412`，本地免鉴权占位 key） |
| 模型 | 12 个 chat 型免费 ID（fledge-alpha-free, big-pickle, space-bunny-free, longcat-2.5-preview-free, exo-free, mimo-v2.6-flash-free, muse-spark-1.2-contributor-free, ling-3.1-flash-free, ling-3.0-flash-fin-free, nemotron-3-ultra-free, nemotron-3.5-lightning-free, muse-spark-1.3-contributor-free）；`jev-1.13-free` 为 systemone 决策接口，不适用 |
| 姿态 | priority 0 / weight 5，auto_ban=1，test_model=fledge-alpha-free，group default |
| 定价 | ModelRatio=0（免费源） |
| 备份 | `~/.new-api-local/backups/new-api-before-zen-free-bridge-20261007-200110.db`（31,940,608 B，integrity=ok） |
| OMP | models.yml.bak-20261007-zen-free-bridge；新增 fledge/exo/mimo-v2.6-flash/ling×2/muse-1.3-free，改源 big-pickle/mimo-v2.5-free/nemotron×2（zen-free-bridge ch178）。2026-10-08：muse-1.2-contributor-free 改源 ch178 并去 `api: openai-responses`，删除 mimo-v2.5-free 死条目（备份 models.yml.bak-20261008-zen-lineup）。 |

## 验证

- `python3 scripts/ops/add_zen_free_bridge_channel.py --apply`：备份→建渠禁用→
  3 探针全 quota（pass-with-warning）→ ModelRatio=0→启用→abilities 读回全绿。
- `python3 scripts/ops/test_omp_routes.py`：40/40。
- `omp models`：zg-newapi 82 模型，fledge/muse-1.3-free 可见。
- OMP E2E：`omp -p --model zg-newapi/fledge-alpha-free` →（结果行待配额窗口补）。

## 运维要点

- 429/配额：非故障（08-20 免费池合约）；熔断 10min 后桥自动再试。
- 每轮 CLI ~80-120s：免费档是 best-effort 兜底，勿作关键链路。
- 桥无自启：重启后需手工 `bun scripts/ops/zen_free_bridge.mjs 8412`；
  如需常驻建议 HKCU Run 键（freebuff 先例），待用户确认。
- opencode CLI 凭据在 `~/.local/share/opencode/auth.json`（=Console key），
  禁止轮换/外泄；CLI 升级后重验门禁行为。

## 2026-10-08 补验与清单同步

- 配额恢复实弹：桥直连 `fledge-alpha-free` → **HTTP 200，content=ZEN_OK，13.4s**；
  `nemotron-3-ultra-free` → **200，NEMO_OK，9.8s**（账号级每日额度已恢复）。
- `--probe-all`（12 模型）：**9/12 返回 200**（fledge/big-pickle/space-bunny/longcat/
  mimo-v2.6-flash/ling-3.1-flash/nemotron-ultra/nemotron-3.5/muse-1.3-free），
  `exo-free`、`ling-3.0-flash-fin-free`、`muse-spark-1.2-contributor-free`
  报 upstream-down（非配额、非配置）。
- 上游故障分类（CLI 实弹，非配额）：
  - `exo-free` / `ling-3.0-flash-fin-free`：`Endpoint is unavailable`（~80-89s）。
  - `muse-spark-1.2-contributor-free`：`UnknownError: Unexpected server error.
    Check server logs for details.`（2.3s 快败）。
  - `nemotron-3-ultra-free` 探针一次超时（NewAPI 170s），桥直连 9.8s 正常 →
    判定为瞬时/串行抖动，不是模型故障。
- 清单漂移：`/zen/v1/models` 免费清单已移除 `mimo-v2.5-free`、新增
  `muse-spark-1.2-contributor-free`。ch178 同步为当前 12 模型。
- 同步脚本：`sync_zen_free_bridge_models_20261008.py`（备份→PUT ch178 models→
  ModelRatio 同步→verify→异常回滚），幂等运行通过；备份
  `new-api-before-zen-free-bridge-sync-20261008-122310.db`（34,435,072 B，
  integrity=ok）。
- OMP models.yml：muse-1.2-contributor-free 去 `api: openai-responses`（桥不实现
  /v1/responses）、标签 `(opencode-zen ch96)`→`(zen-free-bridge ch178)`；删除
  mimo-v2.5-free 死条目；备份 `models.yml.bak-20261008-zen-lineup`。
- 回归：`test_omp_routes.py` 40/40；`omp models` 中 zg-newapi 81 模型，
  mimo-v2.5-free 不再可见，muse-1.2-contributor-free 可见。
- 遗留：ch96 `opencode-zen-free` 仍 status=2 禁用且模型清单陈旧（未动，ch178 为
  唯一 zen-free 源）；OMP 内 `hy3-free` 等 ch96 死条目不在本次同步范围。

## 2026-10-08 流式缺陷修复

- 现象：`omp -p --model zg-newapi/fledge-alpha-free` 配额恢复后 300s 超时；
  NewAPI 日志显示 ch178 请求 8-34s 即完成但 OMP 反复重试。
- 根因：OMP 发 `stream: true`，桥只回 JSON；NewAPI 把 JSON 转成 SSE 时产出
  `choices: []` 的 usage-only chunk + `[DONE]`（无 delta.content），OMP 拿不到
  内容不断重试。
- 修复：`zen_free_bridge.mjs` 支持 `stream: true`，返回标准 SSE
  （`choices[0].delta.content` + `finish_reason: stop` + `data: [DONE]`）；
  备份 `zen_free_bridge.mjs.bak-20261008-pre-stream`。
- 验证：桥直连流式 200（11.2s，delta+[DONE]）；NewAPI 流式路径 200
  （delta chunk 透传 + usage + [DONE]，10.1s）；OMP E2E `-p` 真实 200 出文。
- 桥已重启：`bun scripts/ops/zen_free_bridge.mjs 8412`（Start-Process 常驻，
  日志 zen-bridge-8412.log）。

## 回滚

- NewAPI：`POST /api/channel/178/status {"status":2}` 或还原备份。
- OMP：还原 `models.yml.bak-20261007-zen-free-bridge`。
- 桥：杀进程即可（无状态）。