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
- 429 `INFERENCE_CAP_ERROR` 不匹配 Guardian 硬错误关键词（402/401/502/invalid），
  窗口期只计软失败，连续 3 次才会被隔离，重置后自动恢复。
- 与 ch195/ch196（旧 usage-billing cline.bot key，`vendor/model` 映射）互不影响，
  本次未改动它们。

## 执行与验证

- dup check：86 渠道无 name/base+models 冲突。
- 备份：`~/.new-api-local/backups/new-api-before-clinepass-20261010-143312.db`。
- 创建：POST `/api/channel/` 需 `{"mode":"single","channel":{…}}` 包装
  （裸 channel 报 "channel cannot be empty"，multi_to_single 契约同样成立）。
- 回读：channel 字段全部匹配；abilities 12 行 enabled/30/3 齐。
- `GET /api/channel/test/203?model=glm-5.3-flash`：429 正确透传，status 保持 1。

## 待办

- [ ] 窗口重置（约 19:02）后复跑 test_channel + 本地 3002 端点端到端烟测
      （pool 名 `glm-5.3` → `cline-pass/glm-5.3`）。
- [ ] 10-18 到期前替换 key 或下线 ch203（下线时同步清 abilities 行）。
