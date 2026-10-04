# AnyRouter provider 加 fable picker + takeover 投影机制实录（2026-10-04）

**结论：provider 行 `modelPicker.options` 已加 `claude-fable-5-1-reversed`（db 直写，用户授权），env 保持 09-25 直连 fable 全家版。但 takeover 下 db 直写不会自动浮入 live——必须由 ProviderService 保存/切换触发一次投影。**

## takeover 投影机制（源码实证，接替 09-25 runbook 的推论）

1. **启动重申不重投影**（`services/proxy.rs:794-797` 原文实证）：启动恢复中 `has_backup && live_matches_current_proxy` 为真即 `return Ok(())`，live 一字不写；且匹配检查（proxy.rs:2183-2184）只验接管标记+BASE_URL 指向当前代理，**不比对 modelPicker/env**——provider 行后续任何修改，重启都不会带入 live。两次重启 picker 不更新的根因。
2. **backup 恢复路径只管解除接管**：`restore_live_config_for_app_with_fallback_inner`（proxy.rs:1895-1993，尤其 1958-1961）从 `proxy_live_backup` 还原 takeover **前**原配置（本例=直连 fable 全家，`BASE_URL=anyrouter.top`）。**它不是 live 的投影源，编辑它无效且危险**（解除接管时会绕过代理/failover/日志）。
3. **live 重投影唯一触发点**：ProviderService 保存/切换 → `write_live_with_common_config`（provider/live.rs:912,1237），投影当前 provider 行（BASE_URL 重写为 127.0.0.1:15721 + env/modelPicker 整包）。
4. **backup≠投影源实证**：`proxy_live_backup` claude 行 picker=1、env=直连 fable 全家，`backed_up_at=2026-10-04T05:07:40Z` 与 live mtime 同一毫秒——它是接管事件时对 live 前态的快照，内容从来不是 live 当前值。
## 本次变更（全部 db 直写，两次授权）

| 变更 | 内容 | 备份/回滚 |
|---|---|---|
| picker 追加 | options += `claude-fable-5-1-reversed`（label Fable 5.1 (anyrouter)，behavesAs claude-opus-4-5） | `cc-switch.db.20261004-124538-before-fable-picker.bak`；回滚 `json_remove(settings_config,'$.modelPicker.options[1]')` |
| env 对齐又回滚 | 曾误对齐到 live 路由名版（用户裁决：要直连不要 NewAPI 分流），已回滚为 09-25 直连 fable 全家版（byte-identical 验证） | `cc-switch.db.20261004-130148-before-env-align.bak` |

## 路由事实（proxy_request_logs provider_id 归因，6h 窗口）

- `claude-opus-5` → anyrouter 直连 fable 200×53（非桥！8789 桥是 OMP 侧另一系统，此前"桥绕道"归因错误已更正）
- `claude-sonnet-5` → newapi-local k3×64/glm-5.3×6（anyrouter 目录无此模型名，只能 newapi 接）
- opus 档直连 429/503 时 newapi-local failover 顶 200×55——风暴期续命机制
- 1M 门控：anyrouter 全系要求 `anthropic-beta: context-1m-2025-08-07` 头（`[1M]` 后缀仅命名，光后缀不带头照样 400）

## 执行实录（2026-10-04 13:29 完成）

触发路径=**托盘快切来回**（pywinauto UIA 驱动原生托盘菜单：Claude 子菜单 → NewAPI Local → 等 4s → AnyRouter Opus 5.5），免重启、免开 CDP 调试端口。菜单在隐藏图标溢出浮窗（`TopLevelWindowForOverflowXamlIsland`）→ 右键 CC Switch → `Claude · ...` 子菜单；菜单瞬态，跨进程间隙即失效，必须单段连续操作。

验收（live mtime 13:29:54）：BASE_URL 保持 `127.0.0.1:15721` ✓；picker=2 项含 `claude-fable-5-1-reversed` ✓；槽位仍为路由名（takeover 投影对槽位值做路由名变换，picker 从 provider 行直通——行为零变化，sonnet 仍走 newapi 池，要全直连是另一决策）。端到端最后一证：Claude Code `/model` 选 Fable 5.1 后 `proxy_request_logs` 应出现 `request_model=claude-fable-5-1-reversed` 流水。

## 凭据事件（本会话累计，全部入轮换清单）

`~/.claude` anyrouter token（多次部分/近全量）、codex token（proxy_live_backup 打印事故）、OMP models.yml 各 provider key（grep 上下文行）。轮换一律走 UI 编辑保存，ProviderService 自动投影。

## 排障实录（2026-10-04 13:50 用户报"还是不行"）

**初次归因错误已更正**：曾把流水里 400/503 两行当成用户失败（"陈旧会话缺 beta"理论）。复核时间戳后推翻——两发都在 **12:54**（picker 投影进 live 之前 35 分钟，`/model` 里根本还没有 Fable），且都是单发会话，实际是助手自己的探针流量。"陈旧会话"理论撤回。

更正后的事实层：

- fable 请求史上仅 12:54 两发（探针）；**13:20 起代理零流量**；13:50 时本机无 claude 进程——用户的"不行"**未产生任何到达代理的请求**，故障面在 Claude Code 侧（未重启/菜单未出/发送前报错），不在配置。
- live settings.json 验证：合法 JSON、picker 双项含 `claude-fable-5-1-reversed`、BASE_URL=15721。机制有效性有前证：picker 值 `claude-opus-5-5` 在 09-23 有 200×3 真实流水（用户从 `/model` 选中过）。
- 400/503 两行的正确归因：12:54 探针——400=探针未带 `context-1m` beta（透传原样保留客户端 beta，`forwarder.rs:2002-2013`，proxy 不代注入）；503=探针带 beta 撞 anyrouter 风暴。

被否决的修复（同前，维持）：picker 值 `[1M]` 后缀（透传剥离分支不注入 beta，picker 路径未证实）；`ANTHROPIC_CUSTOM_HEADERS`（相对 `ANTHROPIC_BETAS` 零增量，覆盖行为未证实）。

**最终归因（13:58 闭环）**：用户重启的是 **CC Switch**（新 PID 13:57:54，takeover 重申 1s 后重写 live=路由名槽位指纹），"还是直连"= CC Switch UI 里 provider 上游地址 anyrouter.top——设计本意（provider 行存真实上游，代理 15721 在前），且 **CC Switch 源码无任何 modelPicker 渲染代码**（全仓 grep 仅命中本文档），UI 永远不会显示 picker。picker 生效面=Claude Code `/model`。live 扛过 CC Switch 重启（picker 双项+ANTHROPIC_BETAS 均在）。**Fable 有两条已通电路径**：①原生 Fable 档——live `ANTHROPIC_DEFAULT_FABLE_MODEL=claude-fable-5[1M]`，Claude Code 发 `claude-fable-5[1m]`（issue #3980 形态），proxy `model_mapper` 映射到 provider 行 FABLE_MODEL=`claude-fable-5-1-reversed`（`model_mapper.rs:285-299` 测试实证）；②picker 选项——Claude Code 直发 `claude-fable-5-1-reversed`，`matches_configured_upstream`（model_mapper.rs:99-102）保留直通。用户唯一动作=重启 **Claude Code**（非 CC Switch）→ `/model` 选 Fable。
