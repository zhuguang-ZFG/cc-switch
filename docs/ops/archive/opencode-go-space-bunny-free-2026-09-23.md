# Space Bunny 免费周接入（2026-09-23）

## 结论

opencode.ai/zen Go 平台新模型 **`space-bunny-free`**（用户公告：限时免费一周、
1M 上下文、多模态、不消耗 Go 额度）已接入本地生产链路并实弹验证 200。
OMP 用法：`zg-newapi/space-bunny-free`。

## 关键发现（zen 端点语义）

- 上游 `https://opencode.ai/zen/go/v1/chat/completions` 强制要求
  `x-opencode-session` 头（缺失 → 400 `MissingSessionID`），与 UA 无关；
  非浏览器 UA（如 python-urllib）先被 CF 403。
- 本地 NewAPI 3002 的 ch125（opencode-go-omen-alpha）靠 **`header_override`**
  注入 Chrome UA + 静态 session `newapi-local-relay` 实现免头转发——新渠道照抄。
- 实测上游 `/v1/models` 带 Go key 返回 34 模型，含 `space-bunny-free`。

## 变更（可回滚）

1. **新建专用渠道**（不动生产 ch125）：
   - id=130 `opencode-go-space-bunny-free`，type=1，base_url 同 ch125，
     key 复用 ch125 SSOT，models=`space-bunny-free`，group=default，
     priority=0，weight=5，`header_override` 照抄 ch125，tag=`promo-free-week`。
   - 回滚：`POST /api/channel/130/status {"status":2}`（或删除渠道）。
2. **OMP models.yml**：zg-newapi 组加 entry
   `space-bunny-free`（reasoning: true，contextWindow 1000000，maxTokens 32768，
   compactionModel zg-newapi/omen-alpha）。
   备份：`~/.omp/agent/models.yml.bak-20260923-spacebunny`。
3. 变更前快照：`~/.new-api-local/backups/space-bunny-wiring-20260923.json`。

## 验证

- 直连上游（opencode UA + 动态 session）：200，"SPACE BUNNY"，
  reasoning_tokens=28（有思考模式）。
- 生产路径（3002/v1，探针 key）：`/v1/models` 44 个（+1）；
  chat 200 "ROUTE OK"。
- **多模态实测失败**：data-URI image_url → 上游 400 invalid params (2013)。
  免费档疑似纯文本（或需 OpenCode 客户端原生格式）。models.yml 暂声明
  text-only；图片需求出现时再测（可加回 input: [text, image]）。

## 维护提示

- 免费周结束（约 2026-09-30）后：模型可能 404 或恢复计费——若失效，
  禁用 ch130 + 从 models.yml 删 entry（两个动作都有备份）。
- Go 平台未来加新模型：同法新建专用渠道 + 照抄 header_override，
  不要把模型塞进 ch125（隔离生产 omen 渠道定义）。
