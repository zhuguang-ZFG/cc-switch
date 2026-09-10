# agentrouter DeepSeek 敏感词误杀——内容词表实锤（2026-09-10）

**Status:** 事件已自愈（19:43:05），无配置变更
**Scope:** 纯诊断。修正 `omp-model-config-review-2026-08-03.md` 中「短流式形态触发」的结论。

## 1. 事件

- 19:24:40–19:38:20 `agentrouter/deepseek-v4-flash`（OMP default 角色，经 8788 Tailscale 桥直连 agentrouter.org）62 次 500 `sensitive words detected`（含用户报错 request id `2026…Fp7BnV` @19:34:58），OMP 重试无退避。
- 19:43:05 同会话 msgs=89（+1 条）流式 7.0s **成功**；19:54–19:55 新会话 msgs=10/14/17 流式全通。

## 2. 根因（实测）

触发条件 = **会话内容命中上游 deepseek 渠道敏感词词表**，非请求形态、非本地组件：

| 对照 | 结果 |
|---|---|
| 同桥同时段 glm-5.3 msgs=88–130 流式 | 全 200（形状相同） |
| 合成 trivial / medium / 88 轮良性会话（~30KB）/ 单条 200KB，stream=True 与 False | 全 200 |
| 失败的真实 msgs=88 会话 stream=True（重试 15+ 次） | 确定性 500，重试无效 |

- 过滤在 agentrouter.org 上游，本地无法安全洗词；8788 桥保持原状。

## 3. 关联态

- ch15（zg-newapi deepseek 主路）同窗 429 `tpm/rpm limit` 为瞬时限流，70s 后复探 200（非额度耗尽）。
- NewAPI 池内 deepseek 备选全灭：ch108/118/107/110 均 status=2。
- OMP 对 500 的重试无退避（62 次）属风暴放大器，未修，另行立项。

## 4. 建议（未执行，待示下）

- DeepSeek 长会话固定走 `zg-newapi/deepseek-v4-flash`（ch15 主路；efforts `[low, medium, high, xhigh]`，现役 `:max` 后缀在两处条目均悬空，建议 `:high`）；`agentrouter/deepseek-v4-flash` 留作新会话/短请求备用。
- compaction fallback（`omp-global-compaction-model.js` L7）仍指向 agentrouter/deepseek，继承同一 WAF 风险，未动。
- config.yml `default:` 未改（09-09 用户定向配置，维持）。
