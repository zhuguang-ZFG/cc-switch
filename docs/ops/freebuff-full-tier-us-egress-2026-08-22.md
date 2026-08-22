# freebuff full 档打通：Clash 美区钉出口 + 三模型入网（2026-08-22 晚）

接 `freebuff-mimo-limited-tier-2026-08-22.md`：该文结论（limited 档只有 mimo、
ch107 禁用、不要推荐 Luna）已于当晚被推翻。本文件为现行状态。

## 出口方案：Clash 按域名钉美国节点

- 上游按出口 IP 判定 `accessTier`；被标记 `res_proxy/hosting/anonymous` 的
  出口直接 `country_blocked`。实测悍刀行订阅 4 个美国节点中
  **🇺🇸[三网]美国1 仍是 limited**（anonymous_network，已排除），
  **🇺🇸[Hy2]美国1 / 🇺🇸[三网]美国2 / 🇺🇸[Hy2]美国2 为 full**。
- 做法：Clash Verge 给悍刀行 profile 的专属 merge 文件
  `profiles/mMv4S8X7mC8v.yaml` 写入
  `prepend.proxy-groups: Freebuff-US`（select，三个 full 节点）+
  `prepend.rules: DOMAIN-SUFFIX,codebuff.com,Freebuff-US`；主代理组不动。
  备份 `*.bak-freebuff-20260822`。
- 热重载：`PUT http://127.0.0.1:9097/configs?force=true`，
  body 必须带 `"payload": ""`（缺这个字段会 400 Body invalid）。
- 运行时 mixed-port 是 **7897**（不是默认 7890）；`~/.freebuff2api/config.json`
  的 `HTTP_PROXY` 指向 `http://127.0.0.1:7897`。
- 选 per-profile merge 而非全局 Merge.yaml：换订阅/改名时优雅降级
  （钉出口失效），不会搞崩 mihomo。**订阅更新重新生成运行时配置后 merge
  是否生效未实测**——若 freebuff 突然 403 country_blocked，先查
  clash-verge.yaml 里 Freebuff-US 组和规则还在不在。

## run.cmd 中文注释 GBK bug（守护静默失败根因）

`~/.freebuff2api/run.cmd` 是 UTF-8 含中文注释；cmd 按 GBK 解析时中文字节
吞掉换行，注释行与 `cd /d` 黏成一条非法命令 → `cd` 从未执行 → 相对路径
`config.json` 找不到 → exe 静默退出。表现为 vbs/HKCU 自启"跑了个寂寞"。
修复：run.cmd 改纯英文注释 + 全绝对路径。**Windows 上 cmd 批处理文件
要么纯 ASCII 要么存 GBK，永远不要 UTF-8 中文注释。**

## freebuff2api 补丁（tmp/freebuff-2api，运行时源码不入仓）

在 limited 档四层补丁之上新增：

5. **白名单放三模型**：`mimo/mimo-v2.5`、`openai/gpt-5.6-luna`
   （根 `base3-free-luna`）、`deepseek/deepseek-v4-flash`
   （根 `base3-free-deepseek-flash`，上游 2026-08 已全量切 base3 一模型一根）。
   白名单语义从"limited 档防 403"变为"只放行实测可用的免费模型"。
6. **model_locked 自动换模型**：上游同账号同时只锁一个模型，切换模型时
   准入返回 `409 {"status":"model_locked","currentModel":...}`。
   `createSessionWithUnlock` 捕获后自动 EndSession 解锁、清掉本地其他模型
   的缓存会话、按新模型重新准入。三模型互切实测全通。
7. **预热两个新根**（只占根 run 不占配额）；`Acquire` 失败现在会打日志
   （原来吞错只回 502 "no healthy upstream auth token available"，无法定位）。

旧 exe 备份 `~/.freebuff2api/freebuff2api.exe.bak-20260822-models`。

## NewAPI 侧

- ch107 `freebuff-mimo` 已启用（status 1），p9/w5，
  models `mimo-v2.5-free,deepseek-v4-flash-free,gpt-5.6-luna-free`，
  映射到对应上游 ID，三模型 ModelRatio=0。
- **`AutomaticRetryStatusCodes` 加入 429**（原 `408,500-503`）：
  mimo-v2.5-free 池 ch96 Zen p10 + ch107 p9，ch96 撞 Zen 限速 429 时
  原本不转移直接吐给调用方；现在自动落到 ch107（日志
  `use_channel: ["96","107"]` 佐证）。RetryTimes=1 不变。
- 备份：`new-api-before-freebuff-mimo-20260822-211755.db`（启用）、
  `-213727.db`（扩模型）。

## 使用注意

- **互切烧会话**：单模型锁意味着 mimo/deepseek/luna 互切会 EndSession
  上一个模型。luna 只有 6 session/天——把 luna 放固定角色，**不要进高频
  fallback 链**，否则一天额度几次切换就烧完。
- mimo 和 deepseek-v4-flash 是官方 unlimited 非 Premium 模型，可放开用。
- PUT `/api/channel/` 请求体**不能带 `status` 字段**（否则 200 +
  Invalid parameters）；key 字段 list API 不回显，更新前需从 DB 补水。

## 合规备注（不变）

上游明确封 VPN/proxy 出口，靠未被标记的节点拿到 full 档仍在 ToS 灰色
地带，封号风险自担。freebuff 只作免费兜底位，关键链路不要绑死在它上面。
