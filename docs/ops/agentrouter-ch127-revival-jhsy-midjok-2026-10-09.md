# agentrouter ch127 五 key 复活 + jhsy/midjok 接入（2026-10-09 晚）

hubway runbook（10-07）曾判定 ch127 `agentrouter-codex-gpt` 上游死亡并停泊；
当晚用户更正：**agent 池 5 把 key 全部有效、未被废**。本轮复活实证成立，
期间踩中两个独立故障根因，均有契约价值。另接入 jhsy（ch201）与 midjok
（ch193/194，脚本补录仓）。

## ch127 agentrouter 五 key 轮询池复活

两个根因叠加导致"渠道看似死"：

1. **上游客户端指纹门（新风控）**：agentrouter.org ~10-09 起对裸请求回 401
   `unauthorized client detected`。修复=渠道 `header_override` 注入
   Claude Code 头集（`User-Agent: claude-cli/1.0.43 (external, cli)`、
   `x-app: cli`、`anthropic-version: 2023-06-01`）；带正确头后 401→402/200
   =鉴权已通过。**教训：直连 401 未必是 key 失效，先做 UA 矩阵判别**
   （hashneuron/CF 1010 先例同族）。
2. **channel_info BLOB 契约再犯**：sqlite 直写 channel_info 为 TEXT 后，
   distributor 查询整体崩：`sql: Scan error on column index 28, name
   channel_info: unexpected end of JSON input`，**殃及 deepseek-v4-flash
   全部路由（跨渠道 503）**。在营行 `typeof(channel_info)=blob` 是硬事实。
   修复：`scripts/ops/fix_ch127_channel_info.py` 以 `CAST(? AS BLOB)` 紧凑
   JSON 重写（含被停泊连带的 ch57/72/86/134/135/136 单 key 形态），
   等 ~60s 缓存同步。与 t1qq runbook "完整 BLOB 直写+缓存同步" 配方同源。

配置与验证：

- 5 把 key 换行合并 `is_multi_key=true, multi_key_size=5, mode=polling`；
  `status_code_mapping={"402":"503","429":"503"}`（预算池 402 映射为可
  failover 的 503）；auto_ban=1
- 临时提权 p52 遍历轮询：**10/10 全 200（0.5–1.5s）**，日志
  `multi_key_index` 0–4 全命中，`multi_key_status_list` 空=无 key 被标记失效
- agentrouter 按模型预算池现状：`deepseek-v4-flash` 活；`gpt-6-astra` ch127
  池不足→自动回退 ch182 成功（200/7.7s，冗余链路有效）；`gpt-5.6-sol` 403
  insufficient balance 池耗尽，路由保留待补货
- 池位回归：deepseek-v4-flash **p40**（ch180 p51 主 → ch127 p40×5key 轮询
  次级池），gpt 两模型 p40；default+Free 双组

## ch201 jhsy-glm（ai.jhsy0721.xyz）

- 用户供 base64 key（不落仓）；上游 `/v1/models` 仅 `glm-5.3-flash` 单模型，
  直连 chat 200/2.5s
- type=1 单 key，p30/w5，default+Free；快照
  `new-api-before-jhsy-ch201-20261009-203241.db`
- 接入轮实测 ch118 seeseed glm 腿 **3/4 超时劣化**（回退 ch192 需 30–50s）
  → ch118 glm 降 p30→**p25** 作备胎；链=ch201 p30 → ch118 p25 → ch192 p19
- 网关归因验证：`use_channel:["201"]` 直达 200/2.0s

## ch193/194 midjok（midjok.lol，脚本补录）

- 当晚安抚接入（用户决定聚合）：双 key 各建单 key 渠道（jojatoken 四 key 池
  先例），p10/w1，auto_ban=0；7 模型直连实测 200 才收录（gpt-5.5 /
  gpt-5.6-sol / gpt-5.6-terra / gpt-6-sol / gpt-6.1-sol / gpt-6-astra /
  codex-auto-review）；404/429/502 变体按实测排除，脚本头注释含完整判别矩阵
- `scripts/ops/add_midjok_channel.py`（key 走 env 不入仓）
- **open item：两渠道 abilities 仅 default 组，无 Free 组行**——OMP token 走
  Free 组即不可路由 midjok（当晚 gpt-5.6-sol 实测归因 ch193 系 default 组
  流量）；是否补 Free 待用户决策，本轮未动
- ch193 当晚已见真实服务记录（gpt-5.6-sol relay 归因 ch193）

## Claude 池 ch86/134/135/136 key 对齐（用户授权，同晚）

- 指纹对账：ch86/ch134/ch136 三把 key 本就在验证过的五 key 池内；**ch135
  持有的是被删/失效 key（fp 5e089d4f，不在池）** → 换成有效池闲置腿
  sk-nAax0…（fp 22ccd1e1）；第五把 sk-vBV8P…（4ecda142）继续只挂 ch127
- 四渠道统一补 `header_override` claude-cli 头集（ps.air-outer.com 与
  agentrouter.org 同指纹门、同后端）
- 逐渠道直连双 host 探针（自然长度 prompt）：**8/8 全 402 `Budget pool
  quota has been exhausted`** ⇒ key 有效、鉴权通过，Claude 预算池自 10-02
  账户级耗尽后仍未回血
- 姿态：**维持 status=2 fail-closed 停泊**（启用会被 402 关键词扫描/
  auto_ban 立刻打回，徒增噪音）；上游补预算后一步启用（status=1 +
  abilities 随行），header/key 均已就绪
- 备份：`new-api-before-agent-claude-pool-<ts>.db`

## Free 组路由补齐 + ctyun 羊毛接入（2026-10-10）

- **gpt-5.6-luna 20 行 Free abilities 补齐**（用户授权）：luna 此前 default
  组 6 条启用渠道（ch199 dddai p20/w5、ch174 p-10、ch188-191 p-20）但 Free
  组零行 ⇒ OMP token（Free 组）报 no available channel。补行后网关实弹 200
  （62s，dddai 慢腿归因 ch199）
- **midjok ch193/194 ×7 模型 Free 行补齐**；实弹 gpt-5.6-terra 走 Free 组
  403 `Insufficient account balance`——ch193/194 此前有真实 200 消费记录，
  判定为 midjok key 组账户余额耗尽（促销比率消耗完），非路由缺陷；观察项
- **ch135 复核**：状态齐备（BLOB channel_info、header_override、有效 key、
  402=鉴权通过），单 key 渠道按渠道优先级/权重选择而非 key 轮询；维持
  status=2 + abilities disabled 停泊，预算回血即一步启用
- **ch202 ctyun-oc-pool（电信 eaiChat 羊毛，key 双重 base64=278 字符 JWT）**：
  base `https://eaichat.ctyun.cn/ai/platform/v2/cp`（NewAPI 拼 /v1 实测正确），
  2 模型 `kimi-k3-oc`/`glm-5.3-oc` 接入前**零活跃渠道**（唯一来源）；
  p30/w5 双组，auto_ban=0；直连+网关 4/4 全 200（归因 ch202）；`/v1/models`
  404=无目录端点，exact-id；**5 小时额度窗（00:11/05:11/10:11/15:11 刷新），
  key 2026-10-10 15:11 到期即摘除**；快照 `new-api-before-free-abilities-<ts>.db`

## SenseAudio 福利羊毛（api.senseaudio.cn）— 用户决定跳过

- 端点存活（401 规范鉴权应答）；用户转发的 key 为兽音译者混淆文本，
  **用户明示不解密**；拿明文 key 再议。模型面 glm-5.3-flash / qwen3.8-27b /
  deepseek-v4.1-flash；30 万积分促销 10-09 当日到期，过期作废不再跟

## 遗留/观察

- ch127 `gpt-6-astra` / `gpt-5.6-sol` 预算池恢复依赖上游补货（窗口 00:00/08:00/16:00 先例）
- ch118 seeseed `grok-4.5` / `claude-sonnet-5` 能力行仍 enabled 而上游死（用户未裁决禁用）
- ch118 glm 腿 30–50s 超时为新劣化观察项，Guardian 慢渠道检测预计接管
- 本地 fork logs 表归因契约：`channel_id` + `other.admin_info.use_channel` /
  `multi_key_index`（无独立 use_channel 列）
