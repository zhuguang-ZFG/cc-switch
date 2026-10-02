# any 渠道 opus-5-5 入 CC Switch 供应商（2026-09-25）

**结论：CC Switch `providers` 表新增 claude 供应商 `anyrouter-opus55-1790311108648`（「AnyRouter Opus 5.5」），直连 `https://anyrouter.top`，全档模型 `claude-opus-5-5[1M]`，`is_current=0` 手动切换。实弹探针验证 key/模型/beta 全通，503=上游池窗口关闭（已知状态，非配置问题）。**

## 背景

- 用户要求把 any 渠道的 opus5.5 配进 CC Switch。
- 09-24 收口（见 `any-claude-pool-egress-ab-2026-09-23.md`）：Claude Code 实际姿势=**直连 anyrouter.top**（非 8789 桥、非 NewAPI；ch72 治理禁用无 5-5 模型）。
- 本次先按 8789 桥插入，后依证据改为直连定版。

## 关键证据（本次实测）

1. **`settings.json.bak-20260923-direct-test` 的 BASE_URL 实为 `http://127.0.0.1:8789`**——"direct-test" 名字误导，它测的是桥；且其 token 与 `secrets.json` 的 `anyrouter_proxy_key` **完全相同**（len 51）。secrets.json 无第二个 anyrouter key，`anyrouter_proxy_key` 即真 key，不是"桥客户端凭证"。
2. **proxy forwarder 透传客户端 UA**（`src-tauri/src/proxy/forwarder.rs#2172-2183`：provider 无 custom_user_agent 时原样转发），故 Claude Code 真实 claude-cli 指纹经 15721 直达 anyrouter，直连无指纹门问题；`anthropic-beta` 由 forwarder 重建（含 claude-code 标记，#2186-2193）。
3. **canary 直连探针在 takeover 下跳过**（`anyrouter-canary-state.json`: `opus_direct_state=skipped: takeover active`），不能作为直连路径证据；8789 桥探针 5×429、opus_state=closed（2026-09-25T04:40Z）。

## 供应商配置（照抄 direct-test 备份 env，仅改 BASE_URL）

- `ANTHROPIC_BASE_URL=https://anyrouter.top`；`ANTHROPIC_AUTH_TOKEN`=anyrouter_proxy_key
- `ANTHROPIC_MODEL` / OPUS / SONNET / FABLE = `claude-opus-5-5[1M]`；HAIKU / SUBAGENT = `claude-opus-5-5`
- `ANTHROPIC_BETAS=context-1m-2025-08-07`、`CLAUDE_CODE_MAX_RETRIES=15`、`ANTHROPIC_REASONING_MODEL=claude-opus-4-7`、`CLAUDE_CODE_AUTO_COMPACT_WINDOW=272000`、`ENABLE_TOOL_SEARCH=true`
- `modelPicker`：`claude-opus-5-5` behavesAs `claude-opus-4-5`
- `meta.apiFormat=anthropic`（必须：openai_chat 会触发 proxy 格式转换；any 是 anthropic 原生）
- `in_failover_queue=0`；无 provider_endpoints 行

## 实弹验证（2026-09-25，max_tokens=16，claude-cli UA）

| 探针 | 结果 | 含义 |
|---|---|---|
| 直连，model=`claude-opus-5-5`（无 beta） | HTTP 400「1m 上下文已经全量可用，请启用 1m 上下文后重试」 | key 通过（非 401）；裸模型名现强制要求 1M beta |
| 直连，model=`claude-opus-5-5[1M]` + `anthropic-beta: context-1m-2025-08-07` | HTTP 503 Service Unavailable | beta/模型通过，上游池窗口关闭（=canary opus_state） |
| 8789 桥（对照，插入前） | HTTP 429 | 桥路径同样池关 |

**新发现：anyrouter 侧 `claude-opus-5-5` 裸模型名已强制 1M beta（400 文案"1m 上下文已经全量可用"）；直连必须用 `[1M]` 变体或带 context-1m beta——供应商 env 两者皆有。**

## 备份与回滚

- WAL 模式注意：`cc-switch.db-wal` 活跃 4MB，纯文件拷贝主库不含 WAL → 首次 `.bak-20260925-anyrouter-opus55` 不完整，仅留作时间标记。
- 一致快照（sqlite3 backup API，含本行）：`~/.cc-switch/cc-switch.db.bak-20260925-anyrouter-opus55-walsafe`（52,670,464 B，已校验行在内、claude 供应商=10）。
- 回滚=`DELETE FROM providers WHERE id='anyrouter-opus55-1790311108648' AND app_type='claude'`（单行，无需整库回滚）。

## 使用

CC Switch 供应商列表切「AnyRouter Opus 5.5」（UI 未刷新则重启 app；DB 直插行 app 重载后可见）。窗口开时 CLI 内 MAX_RETRIES=15 抽签挤入；开窗告警照旧由 AnyRouter Window Canary 发 Telegram。
