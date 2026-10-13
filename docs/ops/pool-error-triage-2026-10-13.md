# 聚合池报错归因与修复（2026-10-13 晚）

用户反馈"聚合的经常报错"。以网关 stderr 全量错误普查（今日 8866 条渠道错误行）+ OMP transcript 双源归因，修复三处；其余为上游容量问题（见"遗留"）。

## 根因与修复

### 1. glm-5.3 / glm-5.3-flash / kimi-k3 / minimax-m3 / deepseek-v4.1-flash 池头 ch208（happycoding）钱包耗尽

- 症状：`503 预扣费额度失败, 用户剩余额度: ＄0.003`，今日 ×767（12:00 起持续，19:00 仍发）。**失败规模随请求大小增长**：小探针预扣 $0.004 能过，agent 级大请求需 $0.09+ 必挂——表现为"时好时坏、一发大请求就报错"。
- 503 不在 auto_ban 码表（401,402,403,502）→ 永不自动摘除，每次选中都白烧一跳。
- **修复**：`ch208 priority 30→5`（仍启用；充值后无需回滚，自然被高优先级腿盖过）。备份 `newapi-before-pool-error-fix-20261013-191601.db`（integrity ok）。

### 2. OMP 全量发送 `prompt_cache_key`，严格上游 400 拒参

- 18:13 真实链（RID …z1Gx211X）：glm-5.3-flash 大请求 → ch208 503（预扣费）→ ch183 500（s2api 连接 EOF 偶发）→ **ch173 400 invalid_request → 回吐 OMP**。同族 ch209 今日 13 条 400 明确报 `Unsupported parameter(s): prompt_cache_key`。
- OMP openai-completions 对所有 zg-newapi 请求注入 `prompt_cache_key`+`store`（dump：`~/.omp/logs/http-400-requests/1791886387728-*.json`）。仓库早有同题先例：ch88（runinfra-qwen3-8-27b）param_override 即删此参。
- **修复**：ch173、ch209 加 `param_override={"operations":[{"path":"prompt_cache_key","mode":"delete"}]}`（ch88 格式，直写 DB+`POST /api/channel/fix`，不触 key 列）。
- **验证（19:19-19:23）**：带 `prompt_cache_key`+tools+`reasoning_effort=xhigh`+32K max 的 OMP 形请求 → glm-5.3 ×3 全 200 落 ch209（修复前必 400）；真实 advisor 流量（34-64K prompt）同窗口全 200。

### 3. 已删链/瞬错杂音排除

- ch173 历史 400 多为 `Invalid model: voxtral-mini-*/mistral-embed-*`（8-9 月旧日志，当前 abilities 一致，非现役）。
- ch72 anyrouter 503 风暴（今日 330 条集中 18:xx）与 ch94/95 justwoker `all nodes exhausted`（claude-opus-4-8 池全部 3 腿皆饱和）为上游过载，重试链已承接；19:xx 起 ch72 零错误，观察项。

## 遗留（需用户决策）

| 风险 | 现状 | 选项 |
|---|---|---|
| **kimi-k3 / minimax-m3 单腿 ch208（钱包 $0.003）** | 小请求勉强过，agent 级必挂 | 给 ch208 上游充值；或等今晚 ch223 启用补 kimi-k3 第二腿（minimax-m3 仍单腿）；或再找 k3/m3 免费源 |
| **glm-5.3-flash 有效容量=2 条 5h 窗口福利腿**（ch118 seeseed + ch183 s2api，19:21 实测一次双双 429 到 20:30 重置）| OMP tiny/smol 兜底流量全压这里 | 找非窗口型 glm-flash 腿；或接受间歇 429（链会继续降级） |
| claude-opus-4-8 池 = ch94/95/72 三腿全饱和型 | 慢但可重试 | 观察；持续恶化再降 ch94/95 优先级 |

## 操作记录

- 改动全部经 admin 认证；DB 直写仅 `channels.priority`/`channels.param_override` 三行（PUT 全对象形态在本 fork 报 Invalid parameters，且 GET 回显 key 有掩码回写风险，直写更稳）。
- 网关内存渠道缓存约 1 分钟同步：直写后 `POST /api/channel/fix` 重建 abilities + 65s 后探针确认生效（以行为判别，不假设）。
- 待观察：ch208 若充值回补，p5 自动降级为"只在高优腿故障时兜底"，符合福利腿定位，无需回滚。
