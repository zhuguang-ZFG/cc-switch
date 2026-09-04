# ch111 bai-free 免费池模型同步（2026-09-04）

用户要求对齐 b.ai 上游免费目录。对 `https://api.b.ai/v1/models`（47 模型）
逐模型用 ch111 key + 浏览器 UA 探测 chat completions 分类：

- **200 OK（免费可用）**：mimo-v2.5、hy3、deepseek-v4-flash、
  deepseek-v4-flash-vision-exp、glm-5.3-flash、qwen3.8-27b、qwen3.8-flash、
  hy4-preview（初探）、minimax-m2.7。deepseek-v4-flash 并行批 429、
  串行重试 200 = 瞬时限流，保留。
- **503 "no available channel under group mi"**：mimo-v2.5-pro → 陈旧，摘。
- **403 "Deposit required"（付费档不入）**：claude-*/gemini-*/gpt-*/kimi-*、
  deepseek-v4-pro、glm-5.1/5.2、minimax-m3、qwen3.8-max。
- **仅 429（未见 200，不盲收）**：glm-5.3、gpt-5.6-luna。

## hy4-preview 追杀（同日）

首轮入池后复测：hy4-preview 直连与 relay 全部
`400 "credit insufficient balance: balance~2600 required=5010"`——
上游对该模型有 ~5010 credit 余额门槛，当前 b.ai 账户余额 ~2600 不达标。
首轮 200 早于门槛生效（或余额此后跌破阈值）。每请求必 400 的目录模型是
纯陷阱 → 同日摘除。**回加条件：b.ai 充值 + 新一轮直连 200 探测，缺一不可。**

## 变更（最终态）

1. ch111 `bai-free` models 6→5：`mimo-v2.5,hy3,deepseek-v4-flash,
   deepseek-v4-flash-vision-exp,minimax-m2.7`。摘 mimo-v2.5-pro（503 陈旧）
   与 hy4-preview（余额门槛 400）；增 minimax-m2.7。
   priority/weight/status/key 不动。
2. ModelRatio：minimax-m2.7 → 0（免费）；删 mimo-v2.5-pro、hy4-preview
   键。既有比率（hy3 0.5、deepseek-v4-flash 0.5、mimo-v2.5 0）为历史
   保守高估，只多收不少收，影响其他池 → 不动。
3. 封闭式 bai 单模型渠道不动（全部探测 OK）：ch113 qwen3.8-27b、
   ch121 glm-5.3-flash、ch122 qwen3.8-flash。

## 验证（DB 独立回读 + relay）

- ch111 models/status/p30/w5 与上一致；hy4-preview、mimo-v2.5-pro 的
  abilities 行零残留；ModelRatio 无两陈旧键、minimax-m2.7=0。
- 3002 网关 relay：minimax-m2.7 200 出文；mimo-v2.5 200（保留模型健在）。
  hy4-preview 直连 400 余额门槛（归因证据，见上）。
- 整库在线快照：`new-api-before-bai-free-sync-20260904-194019.db`
  （154,804,224 B，integrity=ok），失败全量回滚（渠道 models +
  ModelRatio），幂等（重跑 verify-only no-op）。

脚本：`scripts/ops/sync_bai_free_models.py`。
