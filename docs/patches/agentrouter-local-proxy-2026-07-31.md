# agentrouter 本地代理接入 OMP/Kimi（claude-opus-5）（2026-07-31）

把 agentrouter（含新上线的 Claude Opus 5）经本机代理暴露给 OMP 和 Kimi Code CLI。agentrouter 无法接入 NewAPI（VPS 不可达），只能本机代理转发。

## 1. 为何不能接 NewAPI（VPS 不可达）

从 NewAPI 所在的阿里云 VPS（47.112.162.80）实测：

- `agentrouter.org`（原域名）：**网络不可达**（大陆封）。
- `ps.air-outer.com`（备用域名）：可达但被**阿里云 WAF 反爬挑战**挡（返回 JS 挑战页 `aliyun_waf_aa/bb`，非 JSON）；带浏览器 UA 也过不了，是需执行 JS 拿 cookie 的挑战，数据中心 IP 被拦。

故 NewAPI（VPS）接不了 agentrouter。本机（住宅 IP）两个域名都可达。

## 2. 客户端身份校验（关键坑）

agentrouter 按 User-Agent 校验客户端身份：缺 claude-cli 格式 UA 返回 `401 unauthorized client detected`。必须带：

```
User-Agent: claude-cli/1.0.0 (external, cli)
```

UA 格式要精确匹配（少了 `(external, cli)` 后缀即被拒）。与 freemodel.dev 要 WorkBuddy Electron UA 同一套路。

## 3. 代理设计

文件：`C:/Users/zhugu/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py`，监听 `127.0.0.1:8788`，OpenAI 兼容。

- **UA 注入**：所有上游请求注入 claude-cli UA + Bearer key。
- **多 key 池 + 429 冷却**（`keys.json`，4 个 key）：agentrouter 单 key 限流激进（429 "token limit ... retry after Ns"，消息夸大但确实在限）。round-robin 轮询，某 key 撞 429 就按 retry-after 冷却并切下一个 key；全 key 冷却时等待最早到期的再试（外层循环 key 数 × 2 轮，熬过限流）。
- **域名 failover**：`agentrouter.org` 主、`ps.air-outer.com` 备，网络/5xx 时切换。
- **SSE 帧规范化**：agentrouter 流式会发 `data: null` 行，Kimi 等严格解析器读 `null.id` 会崩（`Cannot read properties of null`）；代理过滤 `data: null` 并把每个 data 事件规范化为 `data: ...\n\n` 标准帧。
- **加固**：元数据日志（不落完整 payload/响应）、有限超时（connect 15s / read 300s）、`/health` 鉴权。

## 4. OMP / Kimi 接入

- OMP `~/.omp/agent/models.yml`：`agentrouter` provider（`http://127.0.0.1:8788/v1`，openai-completions，本地端点无鉴权 apiKey 任意），挂 `claude-opus-5` / `claude-opus-4-8`（200K 上下文 / 128K 输出 / thinking）。
- Kimi `~/.kimi-code/config.toml`：`[providers.agentrouter]`（type=openai，同端点）+ `[models."agentrouter/claude-opus-5"]` / `claude-opus-4-8`（capabilities=["thinking"]，Kimi openai provider 自动识别 reasoning_content）。

## 5. 自启动

- watchdog：`agentrouter-proxy/watchdog.ps1`（每 30s 探测 `/health`，死则 pythonw 拉起，stdout/stderr 分开重定向）。
- 启动项 `cline-glm-proxy.bat` 增加 `AgentrouterWatchdog`（与 CodebuddyWatchdog 并列）。实测杀代理后 ~30s 自动恢复。

## 6. 验证

```text
非流式 claude-opus-5（经 8788）        -> content 正常返回
流式 claude-opus-5                     -> 规范化帧，Kimi/OMP 均解析成功
4 key 抗限流                           -> 非流式 6/6；密集后流式熬过冷却成功
Kimi -m agentrouter/claude-opus-5      -> "我是 Claude，Anthropic 开发的 AI 助手。"
OMP --model agentrouter/claude-opus-5  -> "我是 Claude,由 Anthropic 开发的 AI 助手。"
watchdog 自动拉起                       -> 杀代理后 ~30s 恢复，4 key
```

## 7. 注意事项

- **限流**：agentrouter 单 key 限流激进，4 key 池 + 冷却已大幅缓解，但极端突发下仍可能瞬时全 key 限流（代理会等待恢复）。
- **内容审核**：agentrouter 偶发返回 `content-blocked`（上游审核，与代理无关）；某些测试式 prompt（如 "Reply with exactly: X"）更易触发，自然对话较少。
- 代理仅本机可用（VPS 接不了），开机自启依赖本机 Windows 启动项。
- `keys.json` 含明文 key，仅本机使用，勿提交仓库。

> 安全：本文档不含 agentrouter API key、VPS 密码。
