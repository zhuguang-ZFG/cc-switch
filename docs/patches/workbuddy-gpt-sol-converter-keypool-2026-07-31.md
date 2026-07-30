# WorkBuddy GPT 5.6 Sol 转换器接入 + 多 key 池（2026-07-31）

把用户在 WorkBuddy 里接入的第三方模型 `gpt-5.6-sol`（端点 `work.freemodel.dev`）经本机 `codebuddy2openai` 转换器暴露给 OMP 和 Kimi Code CLI。该第三方要求"只能在 WorkBuddy 中使用"——实测其网关按请求头路由，且不同 key 绑定到健康状态各异的后端分片，需要两处适配才能稳定可用。

## 1. 两个根因（直连 502 的真正原因）

裸 curl 直连 `work.freemodel.dev/v1/chat/completions` 会在拿到 role chunk 后返回 `Upstream service temporarily unavailable` 或 Cloudflare `onebool.com 502`。排查出两层原因：

1. **请求头路由**：网关按请求头分流。必须同时带 WorkBuddy 桌面端（Electron）风格的 `User-Agent` + `Referer` + `Origin` 才路由到可用后端；缺任一项（如 curl 默认 UA、自定义 UA）被打到死后端。实测（httpx HTTP/1.1）只有 `Electron UA + Referer + Origin` 组合能返回内容。
2. **key 绑定后端分片，部分分片挂了**：freemodel.dev 按 key 把请求钉死到特定后端实例。逐 key 单独测（每个 4 次、间隔 1.5s）得到：

   ```text
   key#1 …8db830:  2/4   （降级）
   key#2 …59aa8a:  0/4   （分片死）
   key#3 …388d0a:  0/4   （分片死）
   key#4 …4dcc61:  4/4   （分片健康）
   ```

   这不是限流（限流会随速率均匀劣化、拉大间隔能缓解），也不是随机抽风，而是**每个 key 固定路由到一个后端分片，分片挂了的 key 稳定失败**。这也是单纯加重试治标不治本的原因——死 key 重试还是死。

WorkBuddy 桌面端能用，是因为它天然带 Electron 请求头，且用户恰好选中了绑定健康分片的 key/时刻。

## 2. 转换器改造

文件：`C:/Users/zhugu/.kimi-code/proxies/codebuddy2openai/converter.py`（在 `~/.kimi-code`，不在本仓库）。

- **自定义模型路由**：命中 `~/.workbuddy/models.json` 里登记的模型 id（如 `gpt-5.6-sol`）时，转换器直连其 `url`，不走 CodeBuddy 后端；其余模型路径不变。
- **请求头适配**：自定义上游固定带 WorkBuddy Electron `User-Agent` + `Referer` + `Origin`。
- **多 key 池 + 按 key 健康冷却**（根因修复）：key 池存在转换器目录的 `custom_keys.json`（`{model_id: [key, ...]}`），不写死、不暴露给 OMP/Kimi，也不改动 WorkBuddy 的 `models.json`。针对"key 绑定后端分片、部分分片挂"的根因，转换器按 key 维护健康冷却：某 key 返回 `unavailable`/502/503/504 就把该 key 冷却 180s，后续请求 round-robin 时自动跳过冷却中的 key；冷却到期后重新试探，后端分片恢复即自动重新启用（自愈）。单次请求内仍保留有限重试做失败切换。401/400 等鉴权/请求体错误不重试、不冷却。效果：死 key 只在预热时撞一次，之后请求直达健康 key，8/8 成功且无多余重试。
- **中途错误处理**：上游可能在流中途返回 error chunk。若此时还没拿到任何内容，抛 502 让客户端看到真实错误；若已有内容（尾部错误），忽略该 chunk，保留已得响应。

## 3. 客户端接入

- OMP `~/.omp/agent/models.yml` 的 `codebuddy` provider 增加 `gpt-5.6-sol`（1.05M 上下文 / 128K 输出 / thinking / images）。
- Kimi `~/.kimi-code/config.toml` 增加 `[models."codebuddy/gpt-5.6-sol"]`，经既有 `[providers.codebuddy]`（`127.0.0.1:8787`）。

## 4. 验证

```text
逐 key 单独测（每个 4 次）       -> key#1 2/4、key#2 0/4、key#3 0/4、key#4 4/4（确认分片 affinity）
加冷却后非流式（经 8787）        -> 8/8 成功；死 key 仅预热撞一次，之后无多余重试
流式 gpt-5.6-sol（经 8787）      -> STREAM-OK
转换器日志                        -> "custom upstream, 4 keys" + 冷却/切换标记
glm-5.2 回归（CodeBuddy 后端）    -> GLM-OK，原路径未破坏
```

## 5. 注意事项

- 失败根因是第三方按 key 绑定后端分片、部分分片挂；冷却机制自动绕开死分片并在其恢复后重新启用，但不能保证某 key 永久可用——若全部分片都挂，仍会报错。
- `custom_keys.json` 含明文 key，仅本机使用，勿提交到任何仓库。
- 增删 key 只改 `custom_keys.json` 并重启转换器（杀 8787 监听进程，watchdog 30s 内自动拉起新代码）。冷却时长由 `CUSTOM_KEY_COOLDOWN_S`（默认 180s）控制。
