# ClinePass 福利双 key 接入（2026-10-13，ch223 fail-closed）

## 来源

用户在福利群分享两个 Cline Pass 临期 key（自取贴）；key 仅在会话 env 传递，未写入仓库：

| key | 尾号(脱敏) | 过期（北京时间） | 月额度 |
|---|---|---|---|
| key1 | `sk_9696…44be` | 2026-10-14 17:48 | 50% |
| key2 | `sk_4de8…ec84` | 2026-10-17 15:03 | 60% |

## 探测事实（17:55 CST）

- **OpenAI 兼容端点确认**：`POST https://api.cline.bot/api/v1/chat/completions` + `Bearer sk_…`（符合"尽量 OpenAI/codex 格式"偏好）。404 形态排除 `/v1/…`、`/api/v1/messages`。
- **两 key 均活着但都在 5 小时滚动窗口打满**：429 `INFERENCE_CAP_ERROR`（key1 "resets in 4h 1m"、key2 "3h 59m"→ 约 **21:55 CST** 回血）。鉴权通过（非 401）＝key 有效；公开群贴出即被薅穿是本 fork 福利 key 常态（lucky0625 同 pattern）。
- 目录（`recommended-models` `.clinePass`）**14 id**：`add_clinepass_channel.py` 的 12 个映射 + 新增 `mimo-v2.6-flash` / `mimo-v2.6-pro`。
- 5h 窗口耗尽返回 429，不在 `AutomaticDisableStatusCodes=401,402,403,502` → 池内优雅降级；key 过期（401）触发 auto_ban 自动摘除。

## 入库 ch223（fail-closed，待回血激活）

- 脚本：`scripts/ops/add_clinepass_welfare_1013.py`（env 传 key，幂等 verify-only 重入；创建→定价→multi-key 修复→abilities→503 证明一次成型）。
- 渠道 `clinepass-welfare-1013`：type=1，`base_url=https://api.cline.bot/api`（fork 自拼 `/v1/chat/completions`），14 池名映射 `cline-pass/*`，双 key 换行 + `channel_info` SQL 修复 `polling size=2`（ch221 先例，修复后不得再 PUT）。
- 姿态 **p10/w1 auto_ban=1**：额度不可控福利腿，绝不当池头。
- **status=2 保持禁用**——当前无法绿测（5h 窗口），已用 `mimo-v2.6-pro → 503` 证明 fail-closed 无污染。
- 定价：`mimo-v2.6-flash/pro` 补 0/0（新池名）；其余 12 池名沿用现价。
- ch203 `clinepass`（旧 key，周限 10-16 重置）**未动**；ch195/196（usage-billing 旧键）未动。
- 备份：`newapi-before-clinepass-welfare-1013-20261013-175605.db`（integrity ok）。

## 待办（激活程序）

1. ≈21:55 CST 后 management probe：`GET /api/channel/test/223?model=glm-5.3-flash` 绿（200 success）。
2. 绿则 `POST /api/channel/223/status {"status":1}`；若 429 仍未回血，一小时后复测（勿连打）。
3. key1 10-14 17:48 过期后预期 401 → auto_ban 会整渠道禁用（双 key 轮询下同渠道连坐是本 fork 行为）；届时禁用等 key2 补给或整渠道重配。

## 附带发现（同日外部操作潮）

- **ch175 hubway** 12:48、**ch220 lucky0625** **15:37**（`status_time=1791877019` 换算复核，早先误记 20:17 已更正）先后被 `manual operation` 外部禁用（本会话未调用其 status 接口；ch220 禁用前最后消费 13:16 成功、其间无任何失败日志，非 auto_ban）。
- 影响：`codex-auto-review`、`gemini-3.8-flash`（tiny 主）失去唯一活腿 → 均入 QUARANTINE；tiny 由兜底链落 4 腿 `glm-5.3-flash` 继续服务。**E2E 实证（18:26）**：叠加配置令 default=gemini-3.8-flash + 链 `[glm-5.3-flash]` 跑 `omp -p`，网关归因日志 266488 `glm-5.3-flash` ch183 token=master（25.6K prompt 真 agent 请求）→ 链在真实网关兑现。注意 gemini 直连的 `503 model_not_found` 是 relay 前拒绝，**不产生渠道日志行**——排查时勿以"无日志"当作"未尝试"。
- 若禁用非您本意，恢复命令：`POST /api/channel/{175|220}/status {"status":1}`。
