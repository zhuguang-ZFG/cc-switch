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

## 排障实录（2026-10-04 13:47 用户首试 Fable 失败）

现象：`/model` 选 Fable 5.1 后请求失败。流水两行归因：

- **400**（session d68db095，362ms）：上游报错"1m 上下文已经全量可用，请启用 1m 上下文后重试"= 该会话未发送 `context-1m-2025-08-07` beta。proxy 透传原样保留客户端 beta（`forwarder.rs:2002-2013`），所以根因=**陈旧 Claude Code 会话**（启动时 live env 尚无 `ANTHROPIC_BETAS`），非配置错误。
- **503**（session de0e8105，3008ms）：**过了 1m 校验**才撞 anyrouter 容量风暴（Service Unavailable）——证明现行配置对新会话正确；failover 队列不伺候此模型 id，风暴期失败直接浮出水面。

被否决的修复：picker 值加 `[1M]` 后缀（已写入后又回滚，备份 `cc-switch.db.20261004-1355-before-fable-1m.bak`）。理由：客户端 `[1M]` 处理仅对 env 槽位有 200×53 实证，picker 路径未证实；且透传路径剥离后缀的分支（`forwarder.rs:1346,1675-1681`）不注入 beta（`anthropic_bridge_one_m` 仅 Codex/Reasonix 分支置位），picker 值不被识别时反而必 400。`ANTHROPIC_CUSTOM_HEADERS` 方案同样被否：相对已验证的 `ANTHROPIC_BETAS` 零增量（都需重启会话），且 beta 头覆盖行为未证实。

结论：配置零变更（db=live 一致）。用户动作=**完全退出所有 Claude Code 会话再重启**，重选 Fable 5.1；残留风险=anyrouter 风暴期 503（容量问题，非配置）。
