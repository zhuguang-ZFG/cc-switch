# 本机 Claude MCP / Skills 精简（2026-07-26）

本机运维收口（**不含密钥**）。`~\.claude.json` 备份只在本机。

## Claude MCP：13 → 6

**File:** `~\.claude.json` → `mcpServers`  
**Backup:** `~\.claude.json.bak.mcp-slim-*`

| 保留 | 用途 |
|------|------|
| `github` | 代码/PR |
| `context7` | 库文档 |
| `filesystem` | 本机目录（Documents/Desktop/Downloads） |
| `fz-sim` | Grbl/QWEN 仿真（硬件相关） |
| `gitnexus` | 仓图谱 |
| `agent-inspect` | agent 检查 |

| 已删 | 原因 |
|------|------|
| `context7-1` | 与 `context7` 重复 |
| `context-mode` / `linux-do` / `fetch` / `kimi-mneme` / `code-rag` / `headroom` | Kimi 时代/低频；挤占工具表 |

**未改：** Cursor `~\.cursor\mcp.json`（仍为 github / context7 / agentkey / filesystem(`QWEN3.0`) / fz-sim）。A2A 此前已卸。

改完需**重启 Claude Code**。

## Skills

| 动作 | 说明 |
|------|------|
| 删 `~\.claude\skills-archive-bulk-*` | 旧归档 |
| 删 `grill-me` | 桩 skill，保留 `grilling` |
| 清 `~\.kimi-code\.tmp_a2a_*` | A2A 残留草稿 |

Cursor / `.agents` skills 未动（ESP workbench 等按硬件场景保留）。

## Related

- 客户端清理：`docs/patches/local-clients-cleanup-2026-07-26.md`
- Opus5 / RTK：`docs/patches/local-claude-rtk-align-2026-07-26.md`
- Cursor ops：`docs/ops/newapi-dx-cursor-ops.md`
