# 本机 Claude / cc-switch / RTK 对齐（2026-07-26）

本机运维收口（**不含密钥**）。DB/settings 备份只在本机，不进仓库。

## 1. Claude Code `settings.json` 与 ZG 对齐

**File:** `~\.claude\settings.json`  
**Backup:** `~\.claude\settings.json.bak.align-*`

| 变量 | 值 |
|------|-----|
| `ANTHROPIC_BASE_URL` | `http://127.0.0.1:15721`（经 cc-switch） |
| `ANTHROPIC_MODEL` / Opus / Reasoning / Subagent / Fable | `claude-opus-5[1M]` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `glm-5.2[1M]` |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `LongCat-2.0` |
| Theme | `custom:slate-ember` |

**Why:** 曾把 `DEFAULT_OPUS` 钉在 `claude-opus-4-8[1M]`，Auto mode Bash 分类器打 4.8 → `temporarily unavailable` / Cyber Safeguards。Opus 必须全程 5。

## 2. cc-switch FQ#2 `agentrouter-2`

**DB:** `~\.cc-switch\cc-switch.db`  
**Backup:** `~\.cc-switch\cc-switch.db.bak.ar2-opus5-*`

| 项 | 值 |
|----|-----|
| FQ | 1=`zg-gateway-claude` → 2=`agentrouter-2` |
| AR2 全部角色模型 | `claude-opus-5`（**无** `[1M]`，与条目备注一致） |
| 原值 | 全角色 `claude-opus-4-8`（failover 时必踩 Safeguards） |

Proxy knobs（未改）：`FB=25s`，`max_retries=2`，`:15721`。

改 DB 后：cc-switch **重选 ZG 或重启代理** 才热加载 AR2。

## 3. RTK hook 不一致

**Symptom:** `No hook installed`，但 Claude 仍改写 git（因 `@RTK.md` 诱导主动 `rtk git …`）→ 关键 `git log/status` 判断不可靠。

**Fix:**

1. 安装 **rtk-ai/rtk**（勿 `cargo install rtk` → crates.io 假包）：
   ```powershell
   cargo install --git https://github.com/rtk-ai/rtk --force
   ```
   本机现网：`rtk 0.42.4`
2. `rtk init -g --auto-patch` → `PreToolUse` Bash → `rtk hook claude`（`rtk init --show` 应 `[ok]`）
3. `~\.claude\RTK.md` + `CLAUDE.md`：**关键 git 必须**
   ```bash
   rtk proxy git status
   rtk proxy git log --oneline -20
   rtk proxy git branch -vv
   ```

重启 Claude Code 后 hook 生效。

## 4. 非目标（刻意不做）

| 项 | 说明 |
|----|------|
| DeepSeek / MiniMax / MiMo 进 Claude 默认角色 | 网关有渠、偶发显式流量；**不**进 ZG Claude 日常默认 |
| 抠 prompt cache 命中率 | 公益反代 + Agent 负载；已有 cache 字段，延迟瓶颈在上游，不值得当优化目标 |

## Related

- Zhipu `stop` 400：`docs/patches/newapi-dx-zhipu-stop-2026-07-26.md`
- 客户端清理：`docs/patches/local-clients-cleanup-2026-07-26.md`
- 路由：`docs/ops/zg-claude-routing.md`
- Cursor ops：`docs/ops/newapi-dx-cursor-ops.md`
