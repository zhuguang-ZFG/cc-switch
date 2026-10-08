# OMP A2/A3/D/F 挂账清理 — 2026-10-08

## 背景

OMP 全量体检（A–F 分级）后，A1（compactionModel 全局换轨）与 B（16 死条目摘除）已于当晚执行。
剩余挂账：A2（tiny 角色 agnes-2.5-pro-alpha 上游 403）、A3（advisor 角色 space-bunny 无承载 503）、
D（ch170 k3 唯一承载 402）、F（zg-newapi-anthropic 5 条目 asvla key 余额尽 503）。

## 处置

### D（ch170 停泊）

- ch170 `tokenrhythm-k21`（k3 唯一承载）账户余额尽，402 直透（NewAPI 对 402 不重试）。
- 按 zombie-park 先例（`docs/ops/zombie-park-ch127-ch9-2026-10-07.md`）：
  `POST /api/channel/170/status {"status": 2}` → HTTP 200，verify status=2 auto_ban=1。
- k3 模型条目保留在 models.yml（fallback 链仍引用），但渠道已禁，请求将 fail-closed。

### A2（tiny 角色换指）

- 原 `tiny: zg-newapi/agnes-2.5-pro-alpha`（ch68/69 上游 403 预扣费失败）。
- 换指为 `tiny: zg-newapi/agnes-2.5-flash`（同族，实弹 200）。
- 从 models.yml 摘除 `agnes-2.5-pro-alpha` 条目（zg-newapi 66→64）。

### A3（advisor 角色换指）

- 原 `advisor: zg-newapi/space-bunny`（ch130 已 park status=2，503 无可用渠道）。
- 换指为 `advisor: zg-newapi/glm-5.3`（实弹 200，reasoning 模型）。
- 从 models.yml 摘除 `space-bunny` 条目（保留 `space-bunny-free`，zen-free-bridge ch178 仍活）。
- 同步修 advisor fallback 链：删 `zg-newapi/glm-5.3:max`（避免与主角色重复，闸门 test_role_fallbacks_do_not_repeat_their_primary_model 要求）。

### F（zg-newapi-anthropic 5 死条目摘除）

- asvla ch177 key 余额尽，5 条目 503：`claude-opus-4-6`、`claude-opus-4-7`、`claude-sonnet-4-6`、`claude-sonnet-5`、`claude-fable-5`。
- 按 B 类模式摘除（zg-newapi-anthropic 10→5 条目）。
- 保留 `claude-fable-5-1`（ch3 主，仍活）与 `claude-opus-4-8`/`claude-opus-5`/`claude-sonnet-4-5-20250929`/`claude-haiku-4-5-20251001`（justwoker ch94/95 在营，但 claude-opus-5 流式丢 content 缺陷未愈——F 类存量，本轮未动）。

## 验证

- `python3 -m unittest scripts.ops.test_omp_routes` → **Ran 40 tests, OK**。
- YAML 解析通过；zg-newapi 64、zg-newapi-anthropic 5，无重复 id。
- 全链键/值 + 11 个 modelRoles 选择器零未解析（unresolved = []）。
- 实弹（3002 网关）：`agnes-2.5-flash` 200、`glm-5.3` 200（reasoning_content 有内容，content=null 为 reasoning 模型预期行为）。

## 备份

- `models.yml.bak-20261008-211715-A2A3F-purge`（摘除前）
- `config.yml.bak-…`（换指前，时间戳见文件）

## 仍挂账

- F 类存量：`claude-opus-5` 流式丢 content（justwoker 上游 Anthropic 面 SSE 缺陷，8790 透明管道无责；
  待 8790 SSE 合成补丁或上游修复，OMP 恒流式 → 暂不可用于 OMP 对话）。
- ch125/ch130 opencode-go 窗口限额 429 自愈观察（auto_ban 中，可能随窗口重置恢复）。
- key3（vsakura 严格后端）是否摘除待观察。
- 桥修复是否镜像 scripts/ops 待决策。
