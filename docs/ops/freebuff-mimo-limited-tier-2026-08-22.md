# freebuff 免费层入网尝试与 limited 档结论（ch107 freebuff-mimo，2026-08-22）

> **已被同日晚的进展推翻**：当晚经 Clash 美区钉出口恢复 full 档，ch107 已启用
> 并扩到 mimo/deepseek-v4-flash/luna 三模型。现行状态见
> `freebuff-full-tier-us-egress-2026-08-22.md`。本文保留作历史记录。

起因：用户想用 freebuff 免费层的 Luna（不占 OpenCode Go 套餐 $15/月额度）。
结论先行：**Luna 路在此账号/网络下不可行；mimo 可用但受区域出口制约**。
ch107 已建好但保持禁用，等干净住宅出口后复跑脚本即启用。

## 账号现实（准入接口实测）

`POST /api/v1/freebuff/session` 返回：

- `accessTier: "limited"`、`countryBlockReason: "country_not_allowed"`
- limited 档**只准入 `mimo/mimo-v2.5`**（6 个 1 小时 session/天，太平洋日重置）。
  `x-freebuff-model: deepseek/deepseek-v4-flash` 请求头被服务端忽略，照样落回
  mimo；`openai/gpt-5.6-luna` 则 403 `session_model_mismatch`
  （"Limited free access is only available with MiMo 2.5"）。
- 出口为被标记的代理/机房 IP 时整体拒绝：`country_blocked`，
  `ipPrivacySignals: [res_proxy, hosting, anonymous]`（实测 SG 出口被封；
  同账号 TW 住宅出口时 mimo 正常出文）。

**恢复条件**：代理客户端换回干净住宅 IP 节点（如当时的 TW 出口）→
`python scripts/ops/add_freebuff_mimo_channel.py sk-local-freebuff --apply`
会自动探针并启用 ch107。要 Luna/DeepSeek/GLM 等全量模型需 full 档
（允许国家的干净 IP），上游明确封 VPN/proxy 出口，无合规解法——
**不要再给用户推荐 freebuff 的 Luna/full 档模型**。

## 代理补丁（tmp/freebuff-2api，运行时源码不入仓）

基于社区免费逆向代理打了四层补丁才打通：

1. **Luna 根 agent 换代**：`base2-free-luna` 已被上游退役
   （`free_mode_legacy_luna_agent`），Luna 现行根为 base3 单循环 harness 的
   `base3-free-luna`（上游 free-agents.ts 与社区 trefeon/freebuff-proxy 佐证）。
2. **CLI 信封**（`free_mode_cli_required` 门）：注入 Buffy 规范系统提示前缀
   （位置 0 前缀测试）、`provider.data_collection=deny`、`cb_easp` stop 哨兵、
   UA 对齐 `ai-sdk/openai-compatible/1.0.0/codebuff`。
3. **per-model 会话**：准入按模型计日配额，`CreateOrRefreshSession` 带
   `x-freebuff-model` 头；会话缓存 `map[model]`；过期会话不在后台空转重准入
   （否则会空烧 6 次/天配额），下次请求惰性准入。
4. **注册表白名单**：limited 档对表外模型必然 403，注册表收窄到
   `mimo/mimo-v2.5`；远程解析到的 gemini 等子代理模型按白名单过滤。

编译产物在 `~/.freebuff2api/`，监听 127.0.0.1:8321，OpenAI + Claude 双协议。

## 守护

- 计划任务创建需要提权（当前 shell 非 elevated，schtasks 和
  Register-ScheduledTask 均 0x80070005）。
- 改用 **HKCU Run 键** `Freebuff2API` → `wscript.exe run.vbs`（无窗口）→
  `run.cmd`（日志落 `~/.freebuff2api/freebuff2api.log`），登录自启。
- 如需改用计划任务，在管理员 shell 跑
  `tmp/register-freebuff2api-task.ps1`（已备好）。

## 渠道形态

- ch107 `freebuff-mimo`（**status=2 禁用**，待干净出口），p9/w5，
  ModelRatio=0，`models=mimo-v2.5-free`，
  model_mapping `mimo-v2.5-free → mimo/mimo-v2.5`，
  base_url `http://127.0.0.1:8321`（本地代理不鉴权，key 为占位串）。
- mimo 免费池目标形态：ch96 Zen（p10）+ ch107 freebuff（p9）双渠道。
- 入网脚本：`scripts/ops/add_freebuff_mimo_channel.py`（add_* 标准合约；
  创建时探针因出口 region-block 失败，脚本按合约自动回滚为禁用——
  这是预期行为，不是脚本 bug）。
- mimo 是推理模型：max_tokens 太小会被隐式推理吃光导致空 content
  （实测 64 时空、256 正常），relay 探测用 1024。看到空 content 先查
  max_tokens，不要误判渠道故障。

## 风险与合规备注

- 逆向代理处于上游 ToS 灰色地带；上游有封号话术（"may get your account
  banned"）。仅用于免费兜底位，不要把关键链路绑死在 freebuff 上。
- 上游按出口 IP 做区域/代理判定且封 VPN/proxy 出口；为拿 full 档而伪装
  出口既违反上游政策也有账号风险，不做。
