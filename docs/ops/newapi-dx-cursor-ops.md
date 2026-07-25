# Cursor 运维 NewAPI 开发体验

**Owner:** Cursor agent（定时/按需）  
**VPS script:** `/opt/new-api/analyze_newapi_dx.py`  
**Local entry:** `scripts/ops/newapi-dx-analyze.py`  
**Reports:** `/opt/new-api/reports/dx-*.md`

## 职责

Cursor 负责闭环：

1. 拉证据（soft_* journal + Opus 延迟）
2. 安全带内自动改：软截断阈值、`channels/abilities` 权重
3. 冒烟（glm / Haiku / Opus）
4. 写报告 + `STATUS.md`；越界只 escalate

## 一键执行

```powershell
python scripts/ops/newapi-dx-analyze.py           # 实改
python scripts/ops/newapi-dx-analyze.py --dry-run # 只报告
```

凭据：本机 `D:\Downloads\VPS.txt`（勿提交）。

## 建议定时

- **已创建** Windows 计划任务：`CCSwitch-NewAPI-DX-Ops`（每天 **09:00**）
  - 入口：`scripts/ops/newapi-dx-analyze.bat`
  - 日志：`D:\Users\cc-switch\.tmp-newapi-dx-ops.log`
  - 查询：`schtasks /Query /TN CCSwitch-NewAPI-DX-Ops`
  - 删除：`schtasks /Delete /TN CCSwitch-NewAPI-DX-Ops /F`
- 亦可 Cursor loop 按需跑：`python scripts/ops/newapi-dx-analyze.py`
- 冷却：权重默认 6h 内相同建议不重复写
- 回看窗口：24h；渠道最少 10 样本才参与排名

## 安全带

| 项 | 值 |
|----|-----|
| weight | 1–50；单次 \|Δ\|≤15 |
| Opus 主池权重 | 当前 **`#9/#10/#20` = 50/40/32**（pri45）；`#60` w8；单次 \|Δ\|≤15 |
| `#81` | Opus/Fable 已摘（models 空 + abilities off）；渠 pri15；改完 **必须** `podman restart new-api`；**勿** `/status` 弹回 |
| SHORT_OUT | 16–64（当前 **64**） |
| TEXT_HEUR | `KIRO_GUARD_TEXT_HEUR=1`：未闭合 fence / 句尾开放标点 / 有 tools 却只说「我将」无 tool_use |
| SOFT_RETRY_BACKOFF_MS | **700**（Kiro-Go #143；即时重试易同溃） |
| empty tool | `input:{}` / `tool_use` 无 block → soft（kiro-gateway #56） |
| soft journal | `/opt/new-api/kiro-guard-soft.jsonl`；进程内 `/metrics` |
| AR guard | `kiro-guard-ar-8410/11/12`；`KIRO_GUARD_PROXY=http://127.0.0.1:7890`；`#118–120` base=`127.0.0.1:841x` |
| AR 关键词 | `KIRO_GUARD_CONTENT_BLOCK_FAILOVER=1`：`sensitive_words*` / content policy / 405 → **立即 502** 切渠（不软重试） |
| AR Cyrillic | `KIRO_GUARD_CYRILLIC_BYPASS=1`（仅 `kiro-guard-ar-841*`）：`c`→`с` 打散词表；响应还原；勿开到百倍/k40 |
| SOFT_LIMIT | `KIRO_GUARD_SOFT_LIMIT=1`：空/半截 tool → Bash 提示继续拆分（非 502） |
| RetryTimes | NewAPI options **3**（勿回 5；与 guard soft-retry 叠乘） |
| `#11` / `#60` | `#11` status=2 + abilities off；health **勿复活**（`AUTO_REACTIVATE_EXCLUDE`∋11 + SKIP-REENABLE）；`#60` pri45/**w8** |
| health quota | 仅计费语义才 DISABLE-QUOTA；503/no-accounts/disk ≠ 额度 |
| 本机 FQ | ZG → `agentrouter-2`；`max_retries=2`；`ANTHROPIC_MODEL=claude-opus-5[1M]`（林夕已撤） |
| 软截断自动改 | journal soft_* 或日志短 completion ≥20 事件 |

## 回滚

- 权重：`/opt/new-api/backups/one-api.before-dx-weights-*.db`
- 软截断：`/opt/new-api/kiro-guard.env` 改回后 `systemctl restart kiro-guard*`
- Guard 代码：`/opt/new-api/kiro_guard.py.bak.p0-*` → `cp` 回 `kiro_guard.py` 后重启 units

## AgentRouter / AnyRouter

- **AgentRouter**（已复活）：`#118-120` → AR-guard `:841x`（proxy+Cyrillic 在 guard）；本机 FQ 仅 `agentrouter-2`（无林夕）。勿给上游加 `[1m]`。
- 纪要：`docs/patches/newapi-dx-2026-07-26-night.md`
- **AnyRouter FC `#52`**：配置已就位，站方 503 时保持 `status=2`。冒烟绿后：`POST /api/channel/52/status {"status":1}` 并 `UPDATE abilities SET enabled=1 WHERE channel_id=52`。
- **anyrouter.top**：若 403「无权访问 …[1m]」，在控制台**重建令牌且模型限制留空**，再写回本机 provider。
