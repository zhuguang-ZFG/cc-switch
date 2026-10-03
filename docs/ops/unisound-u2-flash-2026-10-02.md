# Unisound（云知声）MaaS u2-flash 接入（2026-10-02）

## 结论

用户提供 Unisound MaaS key 与上线公告："U2 Flash 全新上线！限时免费无限量调用
（2026.09.30-2026.10.31）"，base `https://maas-api.unisound.com/v1`。
**ch138** `unisound-u2-flash` 已建并在册启用，网关与 OMP 端到端实弹均 200。

## 直连实证（key 走 env 注入，不写仓库/日志/文档；--apply 后作为渠道凭据
  持久化于 NewAPI DB——渠道 key 的 SSOT）

- `GET /v1/models` → 22 模型：`u2-flash` / `u2` / `u2-med` + 第三方转发档
  （glm-5.x、qwen3.x、kimi-k2.x/k3、deepseek-v4、MiniMax-Mx）。
- `u2-flash` chat 200：**reasoning 模型**，`reasoning_content` 吃 completion 预算
  （max_tokens=64 → finish=length 仅见 'p'，预算要给足）。
- 流式正常：reasoning+content delta 分块，finish=stop（17 chunks）。
- `u2` 同活（finish=stop 'pong'），**但定价未公告**——限免仅限 u2-flash，
  故只接入 u2-flash；u2/u2-med 待定价明确再说。

## 变更

执行脚本：`scripts/ops/add_unisound_u2_flash_channel.py`（key 走 `UNISOUND_KEY`
env，dry-run 默认）。

| 项 | 值 |
|---|---|
| 渠道 | ch138 `unisound-u2-flash`（type=1，base `https://maas-api.unisound.com`） |
| 姿态 | priority 0 / weight 2，auto_ban=1，test_model=u2-flash |
| ModelRatio | `u2-flash` = 0（限免窗口 09.30-10.31） |
| abilities | `(default,1,0,2)` 读回一致 |
| DB 快照 | `new-api-before-unisound-u2-flash-20261002-234924.db`（integrity=ok） |
| OMP models.yml | `u2-flash`（reasoning: true；contextWindow 131072/maxTokens 32768
  为保守标称 **[未实测]**，实弹后可调） |

## 验证

- 网关 `127.0.0.1:3002/v1/chat/completions` 200，usage 110/13，logs 归因 ch138，quota=0。
- OMP 端到端：`omp -p --model zg-newapi/u2-flash` → `U2_OK`（16.4s，logs 11552
  prompt tokens 归因 ch138，quota=0）。**教训**：`omp -p <model> <prompt>` 位置参数
  形式会把模型名当 prompt（会话实录首条 user=模型名、主模型回落 k3 跑题 195s
  进 Recovery）——OMP 探针必须显式 `--model`。

## 到期提醒

**限免 2026-10-31 截止**。11 月前必须复核：
1. u2-flash 是否转为计费 → 调整 ModelRatio 或停用 ch138；
2. 若上游开始余额门控，auto_ban 会自动禁用——届时确认是计费切换而非故障。

## 回滚

`POST /api/channel/138/status {"status":2}` 停用；或删渠道 + `POST /api/channel/fix`
+ models.yml 移除条目（omp-agent 仓还原对应 commit）。
