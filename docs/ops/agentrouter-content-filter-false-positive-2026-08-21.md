# agentrouter 内容过滤误伤排查（2026-08-21）

症状：OMP default 角色 `agentrouter/gpt-5.6-sol` 反复 500
`sensitive_words_detected`，重试 3 次耗尽报 "Retry budget exhausted"。
17:46–18:18 连撞十几次，会话永久失败（触发内容留在历史里，每轮重发
必再撞）。

## 排查方法（可复用）

OMP 不转储 500 请求，但会话文件在本地。用
`~/.omp/agent/sessions/**/<session>.jsonl` 提取全部消息文本，切成 8 段
逐段重放到 `http://100.83.32.95:8788/v1/chat/completions`
（`gpt-5.6-sol`，max_tokens=1），命中段内再二分到单条消息、再收敛到
最小触发片段。注意两点：reasoning 模型大段请求 TTFT 可能 >60s（timeout
给 120s）；把片段打印到 GBK 控制台会崩（零宽字符），输出强制 UTF-8。

## 结论：不是敏感词，是编码内容/低熵字符串被一刀切

触发源：hutuji 会话一条 toolResult（读文件结果）里的 **base64 SVG 图标**
（`PHN2Zy...` = `<svg viewBox=...`）。证据矩阵（agentrouter 直连实测）：

| 探针 | 结果 |
|---|---|
| 会话 base64 SVG 片段 | 拦截 |
| `aGVsbG8gd29ybGQ=`（hello world 的 base64） | **拦截** |
| 原始 SVG 文本 | 拦截 |
| JWT header（base64url） | 拦截 |
| 64 个连续 `a` | 拦截 |
| base64 随机句子 / 原始 HTML / `se64,` / `PHN2Zz4=` | 通过 |

过滤器实际拦的是**编码内容/低熵字符串**（反注入启发式，base64 是常见
攻击载荷），与"敏感词"语义无关。且观察到**两个错误码**——500
`sensitive_words_detected`（new_api_error）与 400 `content-blocked`——
agentrouter 多 key 多上游里**至少两个上游各自带内容过滤**，请求随机
落到带过滤的上游即挂，解释了间歇性。

## 处置（2026-08-21 已落地）

1. **fallback 链**（`~/.omp/agent/config.yml`）：
   `agentrouter/gpt-5.6-sol → zg-newapi/k3 → zg-newapi/muse-spark-1.2-contributor-free`。
   注意坑：`zg-newapi/gpt-5.6-sol` 走本地 NewAPI ch45，上游同样是
   agentrouter，**当兜底无效**；anyrouter-sol 按门禁仅手动选用不进链；
   ooioo 当日实测 completions 503 不可用。
2. **中毒会话恢复**：`/shake` 重建上下文甩掉含 base64 的 toolResult，
   或开新会话，或会话级 `/switch` 换模型。
3. **治本**：拿 `aGVsbG8gd29ybGQ=` 被拦的铁证找 agentrouter 报备误伤，
   要求放宽内容过滤/加白。（待用户找客服/社区，AI 无账号登录态。）

## 复发信号

同类故障再发的特征：错误体含 `sensitive_words_detected` 或
`content-blocked`、且会话历史里有 base64/证书/JWT/大段重复字符。
会话一旦中毒不会自愈，必须按第 2 条处理。
