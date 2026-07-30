# Cline Free 多模型代理接入（omp + kimi）（2026-07-30）

把 cline CLI（npm `cline` v3.0.47）账户的**免费层**模型代理成本地 OpenAI 端点，给 omp（oh-my-pi）和 kimi code CLI 用，省 NewAPI 额度。

## 1. cline 免费层模型清单

通过 `GET https://api.cline.bot/api/v1/ai/cline/recommended-models`（带 cline 专属 headers）拉到三组：`recommended`（计费）、`free`（免费）、`clinePass`（订阅）。**只白嫖 free 组**，共 4 个，全部实测 200：

| wire 模型名 | 后端 | 上下文 | reasoning | 冒烟 |
|---|---|---|---|---|
| `cline-free/glm-5.2` | fireworks `zai/glm-5.2`（多 fallback） | 1M | ✅ xhigh | stop |
| `poolside/laguna-s-2.1:free` | poolside | 200K | ❌ | stop |
| `deepseek/deepseek-v4-flash` | deepseek | 1M | ❌ | stop |
| `stepfun/step-3.7-flash` | 阶跃星辰（带视觉） | 1M | ❌（带 xhigh 会 500） | stop |

`recommended` 层（claude-opus-5/grok-4.5/gpt-5.6-sol/kimi-k3/glm-5.2）要计费，`clinePass/*` 要订阅，都不接。

## 2. 凭据位置

`~/.cline/data/settings/providers.json`，`providers.cline.settings.auth`：
- `accessToken`：`workos:eyJ...`（JWT，会过期）
- `refreshToken`：调 `POST https://api.cline.bot/api/v1/auth/refresh` body `{refreshToken, grantType:"refresh_token"}` 续期，返回 `data.accessToken`+`data.expiresAt`，token = `workos:` + accessToken。

本机只有 `cline` provider（没有 `cline-pass`，源版脚本写的 `cline-pass` 要改成 `cline`）。

## 3. proxy 实现

`C:/Users/zhugu/.kimi-code/proxies/cline-glm-proxy/cline-glm-proxy.mjs`（来源 `D:\Downloads\source.7z` 的 `cline-glm-proxy.mjs`，扩展为多模型 + 改 refreshToken 来源）。监听 `127.0.0.1:3457/v1`，OpenAI 兼容，key 任意。

### 3.1 三个关键坑（踩过，别重犯）

1. **cline gateway 端点是 `/api/v1/chat/completions`（`/api/v1/` 前缀，不是 `/v1/`），且必须带 cline 专属 headers**——缺任一 gateway 直接 **404 Not Found**（不是 401），裸 curl 会误以为端点不存在：
   ```
   User-Agent: Cline/3.0.47
   HTTP-Referer: https://cline.bot
   X-Title: Cline
   X-IS-MULTIROOT: false
   X-CLIENT-TYPE: cline-sdk
   X-CLIENT-VERSION: 3.0.47
   X-PLATFORM: terminal
   X-PLATFORM-VERSION: 3.0.47
   X-CORE-VERSION: 0.0.66
   X-Task-ID: <session_id>
   ```

2. **`step-3.7-flash` 带 `reasoning_effort=xhigh` 会 500 `empty response`**——proxy 只对 `cline-free/glm-5.2` 强写 xhigh，其余模型仅当客户端显式传 `reasoning_effort` 时才带。

3. **proxy 必须绑 `127.0.0.1`（IPv4）**——绑 `localhost` 时 node 只绑 `::1`，kimi/omp 走 IPv4 `127.0.0.1` 连不上，报 `provider.connection_error`。

### 3.2 proxy 行为

- `FREE_MODELS` 白名单 4 个，不在白名单的 fallback 到 `cline-free/glm-5.2`（防误用计费模型）。
- `max_tokens` 用客户端传入 fallback 8192（源版写死 131072 太大）。
- cline gateway 响应外包 `{data:{...}}`，proxy 解开外层返回裸 OpenAI 格式。
- token 自动 refresh（到期前 60s），401 清缓存重试 1 次。
- `/v1/health` 返回 `{status, tokenExpires, models}`；`/v1/models` 列 4 个；`/v1/chat/completions` 透传（支持 stream）。

### 3.3 自启动

`C:/Users/zhugu/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/cline-glm-proxy.bat`（startup 文件夹，无需管理员；开机 `start /min node ...`）。Write 工具对该路径 EPERM，用 Bash heredoc 写。

## 4. omp 接入

`C:/Users/zhugu/.omp/agent/models.yml` 加 provider `cline`（`api: openai-completions`，`baseUrl: http://127.0.0.1:3457/v1`，`apiKey: any`，`authHeader: true`），挂 4 个模型，`id` = wire 名。glm-5.2 标 `reasoning: true`，其余不标。

冒烟：
```
omp -p --model cline-free/glm-5.2 "..."         → OK-CLINEGLM  stop
omp -p --model poolside/laguna-s-2.1:free "..." → OK           stop
omp -p --model deepseek/deepseek-v4-flash "..." → OK           stop
omp -p --model stepfun/step-3.7-flash "..."     → OK           stop
```

⚠️ omp schema 无 wire 模型名覆盖字段，`id` 即 wire 名；改模型映射只能改 `id` 本身。

## 5. kimi 接入

`C:/Users/zhugu/.kimi-code/config.toml`：

```toml
[providers.cline-glm]
type = "openai"
base_url = "http://127.0.0.1:3457/v1"
api_key = "cline-local"

[models."cline-glm/glm-5.2"]
provider = "cline-glm"
model = "cline-free/glm-5.2"
max_context_size = 1048576
capabilities = [ "thinking" ]
display_name = "GLM 5.2 (Cline Free)"

[models."cline-glm/laguna-s-2.1"]
provider = "cline-glm"
model = "poolside/laguna-s-2.1:free"
max_context_size = 200000
max_output_size = 32000
capabilities = []
display_name = "Laguna S 2.1 (Cline Free)"

[models."cline-glm/deepseek-v4-flash"]
provider = "cline-glm"
model = "deepseek/deepseek-v4-flash"
max_context_size = 1048576
capabilities = []
display_name = "DeepSeek V4 Flash (Cline Free)"

[models."cline-glm/step-3.7-flash"]
provider = "cline-glm"
model = "stepfun/step-3.7-flash"
max_context_size = 1000000
max_output_size = 65536
capabilities = [ "image_in" ]
display_name = "Step 3.7 Flash (Cline Free)"
```

tomllib 校验通过。冒烟：
```
kimi -m cline-glm/glm-5.2 -p "..."           → OK-KIMI      stop
kimi -m cline-glm/laguna-s-2.1 -p "..."      → OK-LAGUNA    stop
kimi -m cline-glm/deepseek-v4-flash -p "..." → OK-DEEPSEEK  stop
kimi -m cline-glm/step-3.7-flash -p "..."    → OK-STEP      stop
```

## 6. 注意事项

- glm-5.2 自带思维链，小 `max_tokens`（如 40）会被思维链占满导致 `finish_reason=length`，实际使用时 `max_tokens` 给足（≥4096）。
- cline free tier 有速率/额度限制（cline 账户级），适合日常主力 + 溢出兜底，不适合高并发烧。
- `default_model` 仍是 `zg-newapi/claude-opus-4-8`（本机 config.toml 第 1 行），cline-glm/* 仅作 `-m` 临时切换/省钱用，不作默认。
- 这套与 NewAPI 完全独立——cline gateway 不经 NewAPI，是 cline 账户直连，额度走 cline 不走 zg-newapi。

## 7. 排障

| 现象 | 原因 | 修法 |
|---|---|---|
| `404 Not Found` | 没带 cline 专属 headers | proxy 的 `clineHeaders()` 必须完整 |
| `500 empty response`（step-3.7） | 带 reasoning_effort | proxy 只对 glm-5.2 强写 xhigh |
| `provider.connection_error`（kimi/omp） | proxy 绑 localhost 只绑 ::1 | 改绑 `127.0.0.1` |
| `401 Unauthorized` | JWT 过期 | proxy 自动 refresh；手动 `curl http://127.0.0.1:3457/v1/health` 看 tokenExpires |
| `finish_reason=length`（glm-5.2） | max_tokens 太小被思维链占满 | max_tokens ≥ 4096 |
