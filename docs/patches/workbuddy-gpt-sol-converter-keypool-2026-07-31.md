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
- 增删 key 只改 `custom_keys.json`，转换器按 mtime 热加载（无需重启）。冷却时长由 `CUSTOM_KEY_COOLDOWN_S`（默认 180s）控制。

## 6. 安全加固（2026-07-31，代码审查后修复）

审查转换器后发现若干可靠性与安全问题，已逐项修复（均在 `~/.kimi-code/proxies/codebuddy2openai/converter.py`，不在本仓库）：

1. **流式错误分类统一**：原流式路径对任何 error chunk 都重试并冷却 key（包括 400/401/403/404）。改为提取 error chunk 的 status code，与非流式共用 `_is_retryable_code_text`——仅 502/503/504 或文案含 unavailable/temporarily/bad gateway 才重试；不可重试错误原样转发给客户端且不冷却 key。
2. **日志与内存限制**：原会把完整 prompt、工具参数、模型输出、原始 SSE 写入日志，且 `raw_parts` 无界累积、`timeout=None`。改为只记元数据（模型/messages/tools/finish/tokens），删除完整 dump 与 `raw_parts`，流式读取改为有限超时（connect 15s / read idle 120s）。
3. **冷却并发竞态**：原"晚到的成功"会清除"更晚请求"刚设置的冷却。`_mark_key_success` 改为带 `req_epoch`，只清除不晚于本次请求开始时间的失败状态（`_KEY_FAIL_AT`）。
4. **URL 校验**：新增 `_validate_custom_url`，强制 https、拒绝 loopback/私网/link-local/`.local`、用 `urlsplit` 正确处理 query string。防止 `models.json` 被篡改后转换器被诱导向任意地址。
5. **custom_keys.json 无效值回退**：原全无效值会覆盖 `models.json` 的有效单 key。改为过滤+去重后非空才覆盖。
6. **空流不丢 chunk**：流正常结束但无 content 时，原会吞掉 role/finish/`[DONE]`。改为转发 `pending`。
7. **配置热加载**：原注释称"动态加载"实则只启动读一次。改为 `get_custom_models()` 按 mtime 热加载，并清理失效模型的冷却状态。
8. **/health 脱敏 + 鉴权**：原无鉴权且返回 auth 文件路径/nickname/企业名/token 过期时间。改为要求 API key、只返回 `logged_in`/`token_expired`/`custom_models`。
9. **/v1/models 含自定义模型**：原只列硬编码 `DEFAULT_MODELS`。改为合并自定义模型（去重保序）。

加固后验证：`gpt-5.6-sol` 非流式 5/5、流式 OK、`glm-5.2` 回归正常、`/health` 无 key→401 且无敏感字段、错误分类单元检查 400/401/403/404/429/500 均不重试。

## 7. 补充修复（2026-08-03 晚间，WorkBuddy sol 不能用）

**现象**：桌面 WorkBuddy（快捷方式 `C:\Users\Public\Desktop\WorkBuddy.lnk` → `C:\Program Files\WorkBuddy\WorkBuddy.exe`）里 sol 请求无响应。

**根因**：`~/.workbuddy/models.json` 的 `apiKey` 是 4 key 池中唯一死的那把（key#1 `fe_oa_05e8...`）。WorkBuddy 桌面端直连 `models.json` 的 `url`（work.freemodel.dev），固定用该 key → 请求超时。4 key 逐把实测（Electron UA/Referer/Origin 头，非流式+流式）：

```text
key#1 fe_oa_05e8... 超时（死）
key#2 fe_oa_16e0... 200 15.7s / 流式 18.7s
key#3 fe_oa_69a9... 200 26.7s / 流式 6.0s
key#4 fe_oa_2502... 200 13.5s / 流式 2.5s
```

**修复**：`models.json` 的 `apiKey` 改为 key#4（流式最快 2.5s）；`url` 保持 `https://work.freemodel.dev/v1/chat/completions` 不动；`custom_keys.json` 4 key 池不动（转换器 8787 侧仍按池轮询+冷却）。备份：`models.json.20260803-2258.bak`（原 freemodel+死key）、`models.json.20260803-alternate.bak`（曾试切 aliyun 的版本）。

**验证**：转换器 8787 端到端 sol 流式 200（7.7s，4 chunks + DONE）；3 个活 key 直连 freemodel 流式全 OK。

**注意**：WorkBuddy 主程序启动时缓存 models.json，需重启桌面应用才生效（当前进程受保护无法从外部杀掉，需托盘退出或任务管理器结束）。

**重启后核对（2026-08-03 23:30）**：

- WorkBuddy 已重启（新 PID + 新日志），模型列表含 `custom-local:gpt-...`（sol）
- **OMP models.yml 的 codebuddy 块缺 `gpt-5.6-sol`**（文档第 34 行说加了，实际漏了）——已补：`contextWindow 262144 / maxTokens 32768 / reasoning / images`（对齐 workbuddy-sol-context-fix 的 262K 实测工作区）；`omp models` 已确认注册（262K/33K/thinking/images）
- 转换器 8787 sol 非流式 → `OK`；Kimi CLI `kimi -m codebuddy/gpt-5.6-sol` → `OK`；NewAPI 6 渠道 test → 全 OK（3.3-6.3s）
- OMP 重启后 `codebuddy/gpt-5.6-sol` 已解析
