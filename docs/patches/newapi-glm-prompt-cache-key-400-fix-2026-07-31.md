# NewAPI glm-5.2 prompt_cache_key 400 修复（2026-07-31）

glm-5.2 经 NewAPI 调用约半数请求报 `400 未知请求字段：prompt_cache_key`，表现为"间歇性用不了"。真根因：**客户端（kimi/omp 开缓存）在请求体里直接发 `prompt_cache_key` 字段，NewAPI 原样透传给上游；tokenrhythm 上游（无问芯穹）严格拒绝该非标准字段。** 修复：在 tokenrhythm 两个渠道用 param_override 高级模式 `mode:delete` 剥离该字段。

## 1. 现象与定位

错误日志（NewAPI logs 表）：

```text
channel error (channel #37/#38, status code: 400): 未知请求字段：prompt_cache_key   token_name=cc-switch modelName=glm-5.2
```

只在 ch37/38（tokenrhythm）出现，ch14（wintoken）从不报错。三渠道按权重约 50:25:25 分流，故约一半请求命中 ch37/38 就 400。400 从 12:04（正是 ch37/38 接入时刻）开始，是接入 tokenrhythm 的回归。

### 关键判定：字段来自客户端透传，不是亲和注入

直连 tokenrhythm 上游 `https://tokenrhythm.studio/v1` 对照：

```text
无 prompt_cache_key        -> 200 OK
prompt_cache_key: "x"      -> 400 未知请求字段
prompt_cache_key: null     -> 400（值为 null 也拒，只要字段名在就报）
```

经网关连打（带 `prompt_cache_key`）逐条关联 logs 表：命中 ch14→200，命中 ch37/38→400。不带字段连打 6/6 全 200。证明字段确实到达上游、且由客户端发出——`glm trace` 亲和规则的 `key_sources` 此时已只剩 `User-Agent`（读作亲和 key 与透传是两回事），字段仍到上游，故与亲和规则无关。

## 2. 试过无效的手段（勿再走）

- **移除 `glm trace` 的 `prompt_cache_key` key_source**：只影响亲和 key 选取，不阻止 body 透传。字段仍到上游，400 依旧。（本文件早前一版把这条当根因，已推翻。）
- **`param_override = {"prompt_cache_key": null}`（简单覆盖模式）**：set-value 语义，只把值设为 null，不删键；tokenrhythm 照拒。ch38 实测无效。

## 3. 修复（param_override 高级模式 delete）

NewAPI param_override 高级操作模式支持删字段（[官方文档](https://doc.newapi.pro/guide/console/channel-management/)）。在 ch37 与 ch38 的 `channels.param_override` 列写入：

```json
{"operations":[{"path":"prompt_cache_key","mode":"delete"}]}
```

sqlite 直写（rc.21 的 `POST /api/channel` 会 panic），改完 `podman restart new-api`（`MEMORY_CACHE_ENABLED=true`，必须重启才生效）。改前备份 `one-api.before-pck-override-<ts>.db`。

**落库坑**：JSON 含双引号，务必用引号定界的 heredoc 写 sql 文件再灌 sqlite，否则 shell 吞掉内层双引号→落库成 `{operations:[...]}` 非法 JSON→rc.21 解析失败静默跳过 override→字段照样透传（本次踩过，`quote(param_override)` 核对字节可发现）。

## 4. 验证

带 `prompt_cache_key` 连打 18 次，逐条关联 logs：

```text
仅 ch38 设 override  -> 400 只来自 ch37（ch38 已不报）
ch37+ch38 都设 override -> 18/18 http200，logs 零 prompt_cache_key 400
```

## 5. 注意

- 只修了 glm-5.2/tokenrhythm。其他 tokenrhythm 同类严格上游若后续也挂同款字段透传问题，需同样加 delete override。
- `prompt_cache_key` 是 OpenAI 部分模型的非标准缓存提示字段（客户端开 prompt caching 时发出），非所有上游都支持；严格上游会拒。根治点在上游侧剥离，而非改客户端。
- delete override 是幂等的：字段不存在时 delete 无副作用，故不影响不带该字段的请求。

> 安全：本文档不含 API key、VPS 密码。
