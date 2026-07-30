# 本机 Cline 清理，仅保留 VPS 池（2026-07-31）

本机不再维护 Cline CLI 或 `127.0.0.1:3457` 直连代理。OMP 和 Kimi Code CLI 的 Cline 免费模型统一经 NewAPI 的 VPS Cline 账号池访问；WorkBuddy `codebuddy2openai` 本地代理继续保留在 `127.0.0.1:8787`。

## 已移除

- 全局 npm `cline` CLI（原版本 `3.0.47`）。
- 本机 Cline 账户目录 `C:/Users/zhugu/.cline`。
- 本机代理目录 `C:/Users/zhugu/.kimi-code/proxies/cline-glm-proxy`。
- 监听 `127.0.0.1:3457` 的 Node 代理及其 PowerShell watchdog。
- 启动项中的 `ClineGLMWatchdog`；启动项保留 `CodebuddyWatchdog`，继续守护 WorkBuddy 转换器。

删除前已在同目录创建带时间戳的 OMP、Kimi 和启动项备份。

## 客户端配置

### OMP

`C:/Users/zhugu/.omp/agent/models.yml` 已删除本机 `cline` provider。VPS 池仍挂在 `zg-newapi` provider 下，使用 wire 模型名：

- `cline-free/glm-5.2` - `Cline Pool GLM 5.2 (NewAPI/VPS)`
- `poolside/laguna-s-2.1:free` - `Cline Pool Laguna S 2.1 (NewAPI/VPS)`
- `deepseek/deepseek-v4-flash` - `Cline Pool DeepSeek V4 Flash (NewAPI/VPS)`
- `stepfun/step-3.7-flash` - `Cline Pool Step 3.7 Flash (NewAPI/VPS)`

OMP 发送的模型 `id` 就是上游 wire 模型名，因此不能改成仅客户端可见的别名；通过显示名区分来源。

### Kimi Code CLI

`C:/Users/zhugu/.kimi-code/config.toml` 已删除 `[providers.cline-glm]` 和全部 `cline-glm/*` 本机模型。VPS 池保留为：

- `zg-newapi/cline-glm-5.2`
- `zg-newapi/cline-laguna-s-2.1`
- `zg-newapi/cline-deepseek-v4-flash`
- `zg-newapi/cline-step-3.7-flash`

这些别名仍经 `[providers.zg-newapi]` 访问 NewAPI/VPS 账号池。

## 验证

```text
OMP models.yml YAML 解析通过
kimi doctor config <config.toml> -> OK
127.0.0.1:3457 无监听
cline 命令不可用
```

WorkBuddy `127.0.0.1:8787` 路径不属于本次清理；其 `codebuddy/glm-5.2` 模型仍保留。
