# 42x.shop DeepSeek 渠道接入（禁用态 fail-closed）（2026-10-06）

## 结论
- 用户提供 `https://api.42x.shop` + key（`sk-` 格式）。上游为 NewAPI 系站点（`/api/status` 正常）。
- 实测该 token 可见模型 3 个：`deepseek-v4-flash-0731` 与 `deepseek-v4-flash-0731-free`（**均 502 上游故障**，19:0x 时段多轮复现，non-stream/stream 一致）；`deepseek-flash-free`（本 token **403 无权限**）。
- 以 **禁用态** 落库：ch172 `42x-deepseek-v4-flash-0731`（p20/w1、auto_ban=1、tag `42x-deepseek`），abilities 两行 enabled=0，网关请求 fail-closed 503（`No available channel ... under group default`）。**不接客**。
- 启用前置（全部满足再开）：① 上游 `deepseek-v4-flash-0731` 复测 200；② 明确 ModelRatio 定价（`-free` 预期 0；付费版按站点费率）；③ 若换/加 key，复核该 token 对目标模型的权限。

## 操作
- 落库脚本：`scripts/ops/add_42x_deepseek_channel.py`（dry-run 默认；key 经 env `FORTYTWOX_KEY` 传入，不落盘不回显；key 仅入 NewAPI DB 渠道字段）。
- DB 快照：`~/.new-api-local/backups/new-api-before-42x-deepseek-20261006-190648.db`。
- 启用（上线时执行）：
  `POST /api/channel/172/status {"status":1}`（带 admin 头）+ 定价 PUT；随后网关实弹必须归因 ch172。
- 回滚：保持/改回 status=2，或删除渠道（快照可整库恢复）。

## 证据
- `/v1/models`：3 模型；`deepseek-v4-flash-0731` 与 `-free` chat 均 502（≥6 发多轮复现）；`deepseek-flash-free` 403。
- ch172：DB `status=2`、`auto_ban=1`、abilities 两行 `enabled=0`；网关 503 `No available channel for model deepseek-v4-flash-0731 under group default`。
- 未扩模型表：只注册实测存在的 2 个 id；`deepseek-flash-free` 因 403 不入列（**不要凭模型名盲加**）。

## 遗留
- 上游恢复 + 定价决策前勿启用；启用时同批补 `ModelRatio`（含 `-free`=0）。
- key 轮换/权限变化时，先跑脚本 dry-run 或直连复测再动渠道。
