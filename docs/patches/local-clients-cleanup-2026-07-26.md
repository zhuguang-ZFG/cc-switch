# 本机客户端清理（2026-07-26）

本机运维收口：去掉闲置 Agent/CLI 栈，日常只保留 **Claude Code → cc-switch → ZG NewAPI**（及 Cursor IDE BYOK）。**不改**仓库内 Reasonix/Pi 产品代码与用户手册——那些仍是 cc-switch 功能；此处只记本机安装态。

## 已卸载 / 删除

| 组件 | 处理 |
|------|------|
| **A2A 舰队** | Cursor MCP `jdmt-a2a`、Claude/Kimi `a2a-bridge`、计划任务、bridge 进程；工作区 `~\repos\A2A`、`~\jdmt` 等 |
| **Reasonix + Atom Code** | 全局 npm `reasonix`；`Local\AtomCode`、`.atomcode`、`.reasonix`；源码克隆 `DeepSeek-Reasonix`；cc-switch DB `app_type=reasonix` providers + `proxy_config` |
| **Pi** | `npm uninstall -g @earendil-works/pi-coding-agent`；`~\.pi`、项目 `.pi`；npm shim `pi(.cmd/.ps1)`；DB `zg-newapi-pi` / `agnes-pi` + `proxy_config(pi)` + 相关日志 |

## 刻意保留

| 项 | 说明 |
|----|------|
| Claude Code | 日常主路径；current=`zg-gateway-claude` |
| cc-switch | 本机代理 `:15721`；FQ = ZG → `agentrouter-2` |
| Cursor IDE BYOK | `zg-*` → NewAPI（见 `docs/ops/newapi-dx-cursor-ops.md`） |
| Cursor Agent CLI | 仅云端模型表；默认勿用 Opus 4.8 |
| Kimi / Codex / Grok 等 providers | DB 内其它 `app_type` 未动 |

## Claude Code 主题

| 项 | 值 |
|----|-----|
| Theme | `custom:slate-ember`（Slate Ember） |
| 文件 | `~\.claude\themes\slate-ember.json` |
| settings | `~\.claude\settings.json` → `"theme": "custom:slate-ember"` |
| 外观 | `base=dark`；琥珀强调（非默认紫）；fullscreen 消息底 `#1a1f28` |

新建 `themes/` 后需**重启一次** Claude Code；之后改 JSON 可热重载。会话内可用 `/theme` 切换。

## DB 备份（本机，勿提交）

- Reasonix 清理前：`~\.cc-switch\cc-switch.db.bak.*`（若当时有）
- Pi 清理：`~\.cc-switch\cc-switch.db.bak.pi-uninstall-*`

## Related

- 路由快照：`docs/ops/zg-claude-routing.md`
- Cursor/NewAPI 客户端矩阵：`docs/ops/newapi-dx-cursor-ops.md`
