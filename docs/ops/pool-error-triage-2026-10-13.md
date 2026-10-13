# 聚合池报错归因与修复（2026-10-13 晚）

用户反馈"聚合的经常报错"。以网关 stderr 全量错误普查（今日 8866 条渠道错误行）+ OMP transcript + **上游直连判别探针**归因；修复三类（ch208 摘除、ch173/ch209 参数与档位 remap），其余为上游容量问题（见"遗留"）。

## 根因与修复

### 1. glm-5.3 / glm-5.3-flash / kimi-k3 / minimax-m3 / deepseek-v4.1-flash 池头 ch208（happycoding）钱包耗尽

- 症状：`503 预扣费额度失败, 用户剩余额度: ＄0.003`，今日 ×767（12:00 起持续）。**失败随请求规模增长**：小请求预扣 <$0.003 能过，agent 级需 $0.09+ 必挂——表现为"时好时坏、大请求必报错"。至 19:5x 上游余额转负（`＄-0.000078`）。
- **该消息是上游实例的用户配额报错，不是本网关的用户配额**（已判别：本网关 master token `unlimited_quota=1` 余 983 亿单位、admin quota 9091 亿，同期 advisor 34-106K 计费请求全 200；且错误按 `channel error (channel #208, status code: 503)` 记账=上游 HTTP 503 透传，网关自身配额拒绝发生在渠道循环之前不会按渠道记账。同源代码族=同文案，勿混淆）。
- 503 不在 auto_ban 码表（401,402,403,502）→ 永不自动摘除，每次选中都白烧一跳。
- **修复**：先 `priority 30→5`；余额转负证实全尺寸皆死后 **status=2 禁用**（19:56，`POST /api/channel/208/status`，abilities 自动 enabled=0）。充值回补后重新启用即可，建议启用时保持低优先级。备份 `newapi-before-pool-error-fix-20261013-191601.db`（integrity ok）。

### 2. OMP 全量发送 `prompt_cache_key`，严格上游 400 拒参

- 18:13 真实链（RID …z1Gx211X）：glm-5.3-flash 大请求 → ch208 503（预扣费）→ ch183 500（s2api 连接 EOF 偶发）→ **ch173 400 invalid_request → 回吐 OMP**。
- **直连上游逐参数判别（初稿归因已修正）**：ch173 收 `prompt_cache_key` **不拒**（200）；真正拒的是 **`reasoning_effort=xhigh` 和 `medium`**——返回与 OMP 收到的字节级同形 `{"message":"invalid_request","type":"upstream_error",...}`。ch173 对 glm-5.3-flash 接受枚举={low,high,max}；对 qwen3.8-flash-next 则 xhigh/max 均可（模型×档位异构）。
- 各腿 effort 档位普查（直连上游，medium/xhigh/max）：**ch173** 400/400/200；**ch209**(glm-5.3) 503 empty response/503/200；**ch183** 全 200；ch118 窗口打满未测。**OMP 自动升档（thinking auto→high/xhigh）正踩雷区**——这就是"聚合时好时坏"的又一层。
- `prompt_cache_key`：OMP 对 zg-newapi 全量注入（dump `~/.omp/logs/http-400-requests/1791886387728-*.json`），**ch209 实锤拒参**（13 条 400 明示 `Unsupported parameter(s)`），ch88 早同题。
- **修复（ch88/ch15 先例格式，条件 set）**：ch173=删 pck + {medium,xhigh}→high；ch209=删 pck + {medium,xhigh}→max。**验证**：ch208 禁用后 glm-5.3/xhigh、/medium 经网关 200（唯一活腿=ch209 ⇒ remap 兑现）；ch173 remap 因 p20 第三梯队未被选中暂无网关级直接命中（若未生效最坏=维持现状 400，无副作用）。advisor 真实流量同窗全 200。

### 3. 已删链/瞬错杂音排除

- ch173 历史 400 多为 `Invalid model: voxtral-mini-*/mistral-embed-*`（8-9 月旧日志，今日零发生；abilities 与其声明一致，管理端逐模型测 glm-5.3-flash/qwen3.8-flash-next/deepseek-v4.1-flash/qwen3-8-27b 全绿——假腿嫌疑排除）。ch173 今日另有 500×16（`do request failed`，muyuan 网络偶发）。
- ch72 anyrouter 503 风暴（今日 330 条集中 18:xx）与 ch94/95 justwoker `all nodes exhausted`（claude-opus-4-8 池全部 3 腿皆饱和）为上游过载，重试链已承接；19:xx 起 ch72 零错误，观察项。

## 遗留（需用户决策）

| 风险 | 现状 | 选项 |
|---|---|---|
| **kimi-k3 / minimax-m3 现无腿**（ch208 已禁用，上游钱包负余额） | 确定性 503（诚实失败，不再假 200） | 给 ch208 上游充值后重启；今晚 ch223 启用补 kimi-k3 第二腿；minimax-m3 需另找源 |
| **glm-5.3-flash 有效容量=2 条 5h 窗口福利腿**（ch118 seeseed + ch183 s2api，19:21/19:5x 两次实测窗口打满 429）| OMP tiny/smol 兜底流量全压这里 | 找非窗口型 glm-flash 腿；或接受间歇 429（链继续降级到 ch173）|
| claude-opus-4-8 池 = ch94/95/72 三腿全饱和型 | 慢但可重试 | 观察；持续恶化再降 ch94/95 优先级 |

## 操作记录

- 改动全部经 admin 认证或 admin API；DB 直写仅 `channels.priority`/`channels.param_override`（PUT 全对象在本 fork 报 Invalid parameters，且 GET 回显 key 有掩码回写风险，直写更稳）；禁用走 `POST /api/channel/208/status`。
- 网关内存渠道缓存约 1 分钟同步：直写后 `POST /api/channel/fix` 重建 abilities + 65-70s 后以**行为判别探针**确认生效（不假设）。
- 归因纪律教训：上游与网关同源代码族 → **同文案不同主体**（"用户额度不足/预扣费失败"两侧都会发）；判别=①查本侧 users/tokens 配额 ②看是否按 `channel error (channel #N)` 记账（记账=上游透传）③同窗计费成功样本。
