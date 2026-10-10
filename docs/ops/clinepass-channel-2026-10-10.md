# ClinePass 订阅渠道接入 ch203 — 2026-10-10

## 背景

用户提供 ClinePass 订阅 key（2026-10-18 到期），要求接入 NewAPI 渠道。
Key 仅通过 argv 传给 `scripts/ops/add_clinepass_channel.py`，不入库、不落盘。

## 上游语义（2026-10-10 直接探测确认）

- `https://api.cline.bot/api/v1` 认证正常，`/models` 返回 458 个 usage-billing 目录模型。
- 任何非 `cline-pass/` 前缀模型（含 `:free` 变体）都走 Cline Credits，
  本 key 余额 $0.01 → 一律 402 `insufficient_credits`。
- `cline-pass/<model>` 走订阅额度；当前 5 小时滚动窗口耗尽 →
  429 `INFERENCE_CAP_ERROR`（约 19:02 本地重置）。订阅另有 weekly/monthly 上限。
- 订阅目录 12 个模型（docs.cline.bot/getting-started/clinepass）：
  deepseek-v4-pro, deepseek-v4.1-flash, glm-5.3, glm-5.3-flash, kimi-k3,
  mimo-v2.5, mimo-v2.5-pro, minimax-m3, muse-spark-1.3-contributor,
  qwen3.7-max, qwen3.7-plus, qwen3.8-max。
- `cline-pass/deepseek-v4-flash` 不存在（404），正确名是 `deepseek-v4.1-flash`。

## 渠道决策

- ch203 `clinepass`，type=1，base_url `https://api.cline.bot/api`，单 key。
- 对外暴露 pool 规范短名（glm-5.3 等），`model_mapping` 映射到 `cline-pass/*`，
  因此直接并入这些模型的既有聚合池。
- group `default`，priority 30，weight 3：订阅有滚动窗口配额，权重保守，
  避免窗口耗尽时池内兄弟渠道 stampede。
- 429 `INFERENCE_CAP_ERROR` 命中 Guardian 的瞬态限流守卫
  （`TRANSIENT_RATE_LIMIT_MARKERS = 429 / rate limit / rate_limit / too many requests`），
  整条错误扫描被 **skip**，不进隔离计数；`auto_ban=1` 也未被触发。
- 与 ch195/ch196（旧 usage-billing cline.bot key，`vendor/model` 映射）互不影响，
  本次未改动它们。

## 执行与验证

- dup check：86 渠道无 name/base+models 冲突。
- 备份：`~/.new-api-local/backups/new-api-before-clinepass-20261010-143312.db`。
- 创建：POST `/api/channel/` 需 `{"mode":"single","channel":{…}}` 包装
  （裸 channel 报 "channel cannot be empty"，multi_to_single 契约同样成立）。
- 回读：channel 字段全部匹配；abilities 12 行 enabled/30/3 齐。
- `GET /api/channel/test/203?model=glm-5.3-flash`：429 正确透传，status 保持 1。

## 当晚复核（2026-10-10 21:2x，Guardian 日志 + 渠道表直读）

- **原结论"429 池内安全降级"成立**，但机制表述原先写错了：不是"不匹配硬错误
  关键词所以只计软失败、连续 3 次才隔离"，而是命中瞬态限流守卫后 Guardian 直接
  `error scan rate-limited, skipped`。guardian.log 中 ch203 全天 **0 条禁用/恢复行**，
  `status` 始终 1，12 条 abilities 仍 `enabled/p30/w3` → p30/w3 原设计维持。
- 重置时间预测落空：`status=429` 自 14:33 起累计 8 次，最后一次 **21:26** 仍是
  `You have reached your 5-hour Clinepass limit`。5 小时窗口未按"约 19:02"打开，
  订阅另有 weekly/monthly 上限，不能假定短时自愈。
- **新故障形态**：19:06–19:38 出现 4 次 `500 empty response content`
  （`cline-pass/glm-5.3-flash`、`cline-pass/deepseek-v4.1-flash`）——上游返回空内容
  而非配额错误。500 落在 NewAPI 重试码区间，后果是多一跳 failover 时延，同样不禁用。
- ch203 当日 logs 仅 2 行（19:37/19:38，均 kimi-k3，quota 58 与 quota 1 各一行）
  → 真实流量命中极少，池内其余渠道承担主要负载，w=3 的保守权重未造成可观测挤压。
- 记录补漏：ch203 `auto_ban=1`（原文未写）。它与 ch204 的 `auto_ban=0` 相反，
  是有意差异——ClinePass 是可复活的配额型上游，WB 的 bearer 只在内存里、禁用无意义。

## 待办

- [x] 窗口重置后复跑 pinned channel test —— 已跑，glm-5.3-flash 21:26 仍 429，
      隔离判定确认无误（但配额本身未恢复）。
- [ ] 端到端烟测（pool 短名 `glm-5.3` → `cline-pass/glm-5.3`）在配额真正恢复后再做一次；
      当前窗口未打开，跑了也只能证明 failover，不能证明订阅额度可用。
- [ ] 查清 19:06–19:38 的 `500 empty response content` 是 Cline 侧抖动还是
      `cline-pass/` 映射在特定模型上的稳定坏法（若稳定，该腿应从渠道模型里摘掉）。
- [ ] 10-18 到期前替换 key 或下线 ch203（下线时同步清 abilities 行）。
