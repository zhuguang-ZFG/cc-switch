# NewAPI 聚合池总览（2026-08-01）

本文件是 NewAPI 各模型聚合池的**当前事实快照**，防止文档漂移。任何渠道增删/权重调整/状态变化都应同步更新本文件。

## 1. deepseek-v4-flash 聚合池（十四源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch37 | tokenrhythm-1（基元） | 付费中转 | 20 | 1 | 快源（avg 5s），主源 |
| ch38 | tokenrhythm-2（基元） | 付费中转 | 20 | 1 | 快源（avg 6s），主源 |
| ch35 | cline-free 多账号池 | 免费 | 18 | 1 | 快源（avg 7s）；`model_mapping: deepseek-v4-flash→deepseek/deepseek-v4-flash` |
| ch47 | bazaarlink-flash-2 | 免费 | 12 | 0 | 快源（avg 5s）；base_url `https://bazaarlink.ai/api`（NewAPI 自动补 /v1） |
| ch15 | sensenova（商汤日日新） | 付费中转 | 10 | 1 | 中速（avg 19s） |
| ch48 | opencode-go-flash | 订阅 | 8 | 0 | `https://opencode.ai/zen/go`（带 /v1 会 404）；OpenCode Go $10/月订阅；中速（avg 21s），从 22 降权 |
| ch42 | DeepSeek 官方直连 | 官方 | 8 | 1 | 官方稳定（avg 22s）；models 含裸名，官方别名走 `deepseek-official-v4-flash`；从 1 提权（用户要求） |
| ch56 | hf-deepseek-0731 | 免费 | 5 | 0 | `huggingface.co` 端点 `deepseek-ai/DeepSeek-V4-Flash-0731`；key 任意；IP 限流 20 突发/12 每分钟 |
| ch53 | atomcode-bridge | 免费 | 8 | 0 | 中速（avg 11s，2-22s）；本机 `atomgit-opencode-bridge`（Tailscale 100.83.32.95:9457，base_url 不带 /v1）；CodingPlan Lite 额度；从 3 提权（比 sensenova/opencode 快） |
| ch58 | hfspace-deepseek | 免费 | 2 | 0 | `2c2ch1u11-share-api-0.hf.space`（base_url 不带 /v1）；120 RPM/key 限流；上游慢（冷启动 60s+）；上下文非 1M |
| ch46 | bazaarlink-flash-1 | 免费 | 2 | 0 | 慢源（avg 32s）降权；10 RPM/150 每日加权扣量 |
| ch44 | codebuddy（WorkBuddy 本机） | 桌面依赖 | 2 | 0 | 慢源（avg 28s）降权；Tailscale 100.83.32.95:8787 |
| ch55 | inferx-deepseek-b | 免费 | 1 | 0 | 最慢（avg 65s）兜底；`model.inferx.net/endpoints`（不带 /v1）；每 100 万 token 免费 |
| ch50 | inferx-deepseek | 免费 | 1 | 0 | 最慢（avg 38s）兜底；同上第一 key |

> **ch43（旧 Python 代理）已于 2026-08-01 删除**，被本机 `atomgit-opencode-bridge` 替代（ch53）。旧代理签名算法不对被上游拒，bridge 用正确 `atomcode-signing-v1` HMAC 签名 + 真实 UA，成功过上游验证。

## 2. glm-5.2 聚合池（七源）

| 渠道 | 来源 | 类型 | 权重 | auto_ban | 备注 |
|------|------|------|------|----------|------|
| ch14 | wintoken | 付费中转 | 20 | 1 | 主源权重翻倍 |
| ch35 | cline-free 多账号池 | 免费 | 10 | 1 | `model_mapping: glm-5.2→cline-free/glm-5.2` |
| ch37 | tokenrhythm-1 | 付费中转 | 10 | 1 | |
| ch38 | tokenrhythm-2 | 付费中转 | 10 | 1 | |
| ch44 | codebuddy（本机） | 桌面依赖 | 5 | 0 | |
| ch49 | inferx-glm52 | 免费 | 5 | 0 | `model.inferx.net/endpoints`（不带 /v1）；`glm-52`（上游 `cyankiwi/GLM-5.2-AWQ-INT4`）免费 |
| ch54 | inferx-glm52-b | 免费 | 5 | 0 | 同上，第二 key；与 ch49 轮换分摊容量 |

## 3. gpt 聚合池（centos 摘除后）

| 模型 | 渠道 | 权重 | auto_ban | 备注 |
|------|------|------|----------|------|
| gpt-5.6-sol | ch30 fastaitoken | 5→20（修复） | 1 | 2026-08-01 修复：abilities priority 10→50、weight 5→20（此前被降级致付费主源零命中，流量全压免费本机源） |
| gpt-5.6-sol | ch44 codebuddy（本机） | 5 | 0 | |
| gpt-5.6-sol | ch45 agentrouter（本机） | 5 | 0 | |
| gpt-5.5 | ch30 fastaitoken | 20 | 1 | 单源 |
| gpt-image-2 | ch30 fastaitoken | 20 | 1 | 单源 |

> ch16/ch25（centos.hk 同上游两 key）已于 2026-08-01 禁用：centos 上游账户欠费返 403「预扣费额度失败 用户剩余额度 ¥0.09」（该"用户"指 centos 账户，**非**本地无限钱包），且 NewAPI 对上游 403 默认不 failover，故双保险摘除（status=2 + abilities enabled=0）。
> **副作用**：gpt-5.6-luna / gpt-5.6-terra 仅挂 centos，摘除后零源，已从 OMP/Kimi 配置移除（避免选到报"无可用渠道"）；centos 充值/换 key 后可恢复 ch16/25。

## 4. claude-opus-5 / claude-opus-4-8 / claude-opus-4-7 聚合池（二源）

| 渠道 | 来源 | 类型 | 权重 | priority | auto_ban | 备注 |
|------|------|------|------|----------|----------|------|
| ch45 | agentrouter（本机） | 代理池 | 15 | 50 | 0 | Tailscale 100.83.32.95:8788；主源（权重 15/19 ≈ 79%） |
| ch57 | gorouter 合并（三 key） | 付费中转 | 4 | 40 | 0 | `https://gorouter.app`；ch26/27/28 三把 key 合并换行分隔；备源（权重 4/19 ≈ 21%）。**多 key 正确姿势**：key 真实换行（0x0A）分隔 + `channel_info` 以 **bytes** 写入（`is_multi_key:true, multi_key_size:3, multi_key_status_list:{"0":1,"1":1,"2":1}, multi_key_mode:"polling"`）——str 写入致 GORM 二次编码、`multi_key_mode` 写数字、key 写 `\n` 字面量，三种坑都导致渠道不可路由 |

> **2026-08-01 整合**：原七源 → 二源。ch26/27/28（gorouter 三把 key 同上游）合并为 ch57；ch45 权重 5→15 成主源；ch3/9/18（baibei/linxi-k40）直测全部 503 `All available accounts exhausted`（上游账户耗尽），ch3 已禁用（status=2 + abilities enabled=0）。
> **2026-08-02 实测更正**：ch9/ch18 实际**仍 enabled 且健康**（`GET /api/channel/` 实测 status=1；日志 12/12、1/1 成功零失败，claude-opus-5/4-7 流量继续命中）——推断当时为 auto-ban（status=3）后被 `auto-ban-revive.py` 赦免回 1，且上游已恢复。claude 池**实际四源**：ch45 + ch57 + ch9 + ch18。
> **重要教训**：NewAPI 渠道禁用**必须双保险**——`status=2`（ManuallyDisabled）+ `abilities.enabled=0`。只改 status 不生效（路由过滤看 abilities 表；status=0 是 Unknown 非 Disabled，更无效）。之前 ch16/25/43 的「status=0 禁用」实际从未生效，靠 abilities 兜底。
> **保守后备守护**：`/opt/new-api/auto-ban-revive.py` + systemd timer `k40-baibei-revive.timer`（每分钟）赦免**所有**被 auto_ban 的渠道（`status=3 AND auto_ban=1`→1；替代原仅覆盖 ch3/9/18 的 k40-baibei-revive.py）。SQL 带 `AND status=3` 故手动禁用（status=2）不被误赦免；`auto_ban=0` 渠道（免费源）不赦免（有意不禁用）。判活权交 NewAPI 下轮定时测试，零误判。改 DB 后依赖 NewAPI channels sync goroutine（~1-2min）拉入内存。

## 5. 其他单源模型（非聚合）

| 模型 | 渠道 |
|------|------|
| sensenova-6.7-flash-lite | ch15 |
| grok-4.5 | ch17 (w10) / ch29 (w20) / ch39 (w10) 三源 |
| gpt-5.5 | ch30 单源（ch2/16/25 已禁、ch41 已删） |
| gpt-5.6-luna | **零源**（仅 centos ch16/25 曾挂，已禁；ch41 已删） |
| gpt-5.6-terra | **零源**（仅 centos ch25 曾挂，已禁） |
| claude-sonnet-5 | ch57（ch26/27 已合并禁用） |
| qwen3.8-max-preview | ch31 |
| k3/kimi-for-coding | ch33 |
| step-router-v1 | ch36 |

> **2026-08-02 实测更正**：本节原写 gpt-5.5 三源 ch16/25/30、luna 二源 ch16/25、terra 单源 ch25、sonnet-5 二源 ch26/27——均过时（ch16/25 已禁、ch41 已删、ch26/27 已并入 ch57）。以上为实测后版本。

## 6. 路由与亲和策略（2026-08-01 生效）

- **claude 亲和性已移除**：`channel_affinity_setting.rules` 删除 `claude cli trace`，Claude 模型纯按权重轮询（agentrouter ch45 主源 + gorouter ch57 备源）。
- **亲和系统全局关闭**：`channel_affinity_setting.enabled=false`、`switch_on_success=false`（保留 codex/glm/grok/deepseek/longcat/qwen 六条规则定义但未启用）。
- **auto_ban 策略**：本机源（ch44/45）+ 免费紧 RPM 源（ch46/47 bazaarlink 10 RPM）auto_ban=0（桌面断连/突发 429 是常态，误杀得不偿失）；VPS/付费源全部 auto_ban=1（稳定源真挂该禁）。
- **gorouter 三源合并 + claude 池重组**：ch26/27/28（gorouter 同上游三 key）合并为 ch57，weight=4；ch45 权重 5→15 成主源；ch3/9/18（baibei/linxi-k40）直测 503 `All available accounts exhausted` 禁用，与旧 ch26/27/28 一并 status=2 + abilities enabled=0。
- **k40/baibei 已禁用**：ch3/9/18 上游账户全耗尽（503→自动摘除），不再需要保守后备守护。
- **402 加入重试状态码**（2026-08-01）：`AutomaticRetryStatusCodes` 由 `100-199,300-399,409-499,500-504,505-599` 改为 `100-199,300-399,402,409-499,500-504,505-599`。根因：bazaarlink（ch46/47）免费额度打满时上游返回 402 `Insufficient credits`，但 402 不在重试范围 → NewAPI 不 failover 直接返给客户端 → OMP 报 `402 Insufficient credits`。修复后额度打满的源 402 触发重试/切其他源，不再报错。402 是网关层快速失败（额度检查毫秒级），重试代价可忽略；ch46/47 auto_ban=0 保持（免费源突发 429 常态，见上）。
- **auto_ban 阈值 50→3**（2026-08-01）：`ChannelDisableThreshold` 由 50 降 3——免费/月度配额打满源（sensenova、bazaarlink 等）返 429/402 时 3 次即自动禁用，`AutomaticEnableChannelEnabled=true` 额度回血自动拉回。代价：任何渠道连续 3 次失败会被临时禁（网络闪断可能导致），靠自动恢复机制拉回。
- **ch43 atomcode 根因纠正 + 本机 bridge 替代（ch53）**（2026-08-01）：旧 Python 代理（`atomcode-open-api`）签名算法不对——`atomcode-signing-v1` 实现错误，被上游 `llm-api.atomgit.com` 拒（`SIG_MISSING`）；旧网关 `api-ai.gitcode.com` 则对 deepseek 返业务 403 `not enabled for codingplan 'Lite'`（伪装"每月token额度已不足"）。**根因非额度**（status-v2 显示 calls_used:0）。改用 GitHub 开源项目 `Small-tailqwq/atomgit-opencode-bridge`：正确 `atomcode-signing-v1` HMAC 签名（HKDF-SHA256 + master key）+ 真实 UA + 自动 token 续命，本机（Windows）跑 `node proxy.js` 监听 0.0.0.0:9457，读本机 `~/.atomcode/auth.toml`。ch53 经 Tailscale 100.83.32.95:9457 接 NewAPI，验证 `finish:stop content:BRIDGE-NEWAPI-OK`。旧 ch43 已删除。Qwen3-VL 纯文本可用，图片仍被"独享"校验拦截（未进池）。

## 7. 验证记录

- **deepseek 十二源均衡命中验证**（2026-08-01）：ch15:8 ch42:6 ch37/38:5 ch44:5 ch47:5 ch46:3 ch48:2 ch53:2 ch55:2 ch50:1（权重 10/1/10/10/5/3/3/22/5/5/5 比例吻合）。
- **ch43 退出后 deepseek 池验证**：连打 5 发全 `finish:stop`（length 为 12 token 推理被吃），近 2min ch43 命中 0 —— 退出生效，deepseek 靠其他 9 源正常。
- **ch49/50 inferx 接入验证**：glm-5.2 `finish:stop content:GLM-IX-OK`；deepseek `finish:stop content:DS-IX-OK`。
- **ch53 本机 bridge 验证**：`finish:stop content:BRIDGE-NEWAPI-OK`（本机 atomgit-opencode-bridge 经 Tailscale 接入）。
- **新渠道不参与路由的三坑修复**（2026-08-01）：新增渠道（ch49/50/53/54/55）创建时 abilities 行 `group` 为空（不在 default 组）、`priority=30`（主渠道为 50）、base_url 带 `/v1`（NewAPI type=1 自动补 `/v1` 拼成 `/v1/v1/...` 404）。修复后 deepseek 十二源全渠道均衡命中（ch15:8 ch42:6 ch37/38:5 ch44:5 ch47:5 ch46:3 ch48:2 ch53:2 ch55:2 ch50:1）。
- claude-opus-5 Anthropic 格式 10 发：ch26/27/28/45 全部分流。
- deepseek-v4-flash 隔离验证 ch46/47：base_url 初设 `https://bazaarlink.ai/api/v1` 致 NewAPI 拼成 `/api/v1/v1/...` 返 404，改 `https://bazaarlink.ai/api` 后 `finish:stop content:BZ-OK` 通过。九源全 enabled。
- ch18 禁用后近 2min 零错误（此前每分钟 10+ 502）。
- k40/baibei 守护脚本端到端验证：模拟 ch3 status=3 → 脚本输出 `赦免 auto_ban: ch3(baibei-100xlabs)` → 回 status=1；手动 status=2 不被赦免。

> 安全：本文档不含任何 API key。
