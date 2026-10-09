# LongCat 官方源接入（ch145：LongCat-2.5-Preview）（2026-10-03）

## 结论

用户提供 LongCat 官方 key（`ak_` 前缀）与 base `https://api.longcat.chat/openai`。
新建 **ch145 `longcat-official`**（type=1），一次性全绿：建渠→abilities→网关
实弹归因→OMP E2E `LC_OK`。与既有 `longcat-2.5-preview-free`（opencode-go ch133）
是**同一模型**的两个来源 id（当日晚些已聚合互备，见 `longcat-preview-unify-2026-10-03.md`）。

## 实证（直连用户 key，env 注入，不落盘）

- `GET /openai/v1/models`：`LongCat-2.5-Preview`（context_window **1048576**、
  max_output_tokens **262144**）、`LongCat-2.0`（1048576/131072，未接入——用户只点名 2.5）。

## 变更

执行脚本：`scripts/ops/add_longcat_official_channel.py`（key 走 `LONGCAT_KEY` env，dry-run 默认）。

| 项 | 值 |
|---|---|
| 渠道 | ch145 `longcat-official`（type=1，base `https://api.longcat.chat/openai`，不带 /v1） |
| 模型 | `LongCat-2.5-Preview` |
| 姿态 | priority 0 / weight 2，auto_ban=1，test_model=LongCat-2.5-Preview |
| abilities | `(default,1,0,2)` 读回一致 |
| 定价 | **ModelRatio 未设**（官方定价未知，open item） |
| DB 快照 | `new-api-before-longcat-official-20261003-173901.db`（integrity=ok） |
| OMP models.yml | +`LongCat-2.5-Preview`（1048576/262144 上游标称，reasoning: true 沿自 ch133 同族条目） |

## 验证

- 网关 chat 200，usage 12/32，日志归因 ch145（脚本硬门一次通过）。
- OMP E2E：`omp -p --model zg-newapi/LongCat-2.5-Preview` → `LC_OK`。
- 未分配角色/回退链（用户未指定岗位；手动 `--model` 点名可用）。

## 风险与备忘

1. 定价 open item：确认后补 ModelRatio/CompletionRatio。
2. 凭据经聊天明文传递，用户裁决**不轮换**（已记录）。
3. ~~ch133 free 变体与 ch145 官方并存但互不聚合~~ → 已更正：同一模型双源，
   当晚经 `unify_longcat_preview_sources.py` 聚合为 ch145 主 / ch133 备（见聚合 runbook）。

## 回滚

禁用 ch145（双表），或还原 `backups/new-api-before-longcat-official-20261003-173901.db`；
models.yml 删条目。
