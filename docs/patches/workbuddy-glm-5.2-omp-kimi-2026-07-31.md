# WorkBuddy GLM 5.2 本地代理接入（OMP + Kimi）（2026-07-31）

将已登录的 WorkBuddy Desktop 能力经本机 `codebuddy2openai` 转换器暴露为 OpenAI 兼容端点，并在 OMP 与 Kimi Code CLI 中增加 `glm-5.2` 模型别名。

## 1. 调用链

```text
OMP / Kimi Code CLI
        |
        v
http://127.0.0.1:8787/v1  (codebuddy2openai)
        |
        v
WorkBuddy Desktop 已登录会话
```

- WorkBuddy 快捷方式目标：`C:\Program Files\WorkBuddy\WorkBuddy.exe`。
- 转换器：`C:/Users/zhugu/.kimi-code/proxies/codebuddy2openai/converter.py`。
- 端点仅监听本机回环地址；调用需要该转换器配置的 Bearer key。
- `GET /v1/models` 已列出 `glm-5.2`；凭据、令牌与用户信息不写入仓库。

## 2. OMP 接入

配置文件：`C:/Users/zhugu/.omp/agent/models.yml`。

`codebuddy` provider 使用 `openai-completions` 协议和本机 `http://127.0.0.1:8787/v1` 端点，新增模型：

```yaml
- id: glm-5.2
  name: GLM 5.2 (WorkBuddy)
  reasoning: true
  contextWindow: 1048576
  maxTokens: 32768
```

模型选择：`omp -p --model codebuddy/glm-5.2 "..."`。

## 3. Kimi 接入

配置文件：`C:/Users/zhugu/.kimi-code/config.toml`。

复用既有 `[providers.codebuddy]`，新增模型别名：

```toml
[models."codebuddy/glm-5.2"]
provider = "codebuddy"
model = "glm-5.2"
max_context_size = 1048576
capabilities = [ "thinking" ]
display_name = "GLM 5.2 (WorkBuddy)"
```

模型选择：`kimi -m codebuddy/glm-5.2 -p "..."`。运行中的 TUI 会话需要执行 `/reload`；新会话自动加载。

## 4. 验证

本次接入验证结果：

```text
curl GET /v1/models                         -> 包含 glm-5.2
curl POST /v1/chat/completions (glm-5.2)    -> WORKBUDDY-GLM-OK
omp models codebuddy                        -> glm-5.2, 1M context, 33K max output
kimi doctor config <config.toml>            -> OK
```

## 5. 运行前提与排障

- WorkBuddy Desktop 必须保持已登录，且 `codebuddy2openai` 转换器必须正在监听 `127.0.0.1:8787`。
- `/v1/models` 返回 `401` 时，检查调用是否携带转换器的 Bearer key；这不代表 WorkBuddy 模型不可用。
- 端口未监听时，检查 `C:/Users/zhugu/.kimi-code/proxies/codebuddy2openai/watchdog.ps1` 和 WorkBuddy Desktop 状态。
- 该模型与 Cline Free 的 `cline-glm/glm-5.2`、`cline-free/glm-5.2` 是独立来源；按完整别名选择，避免将两者混淆。
