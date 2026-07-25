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
| `#81` | 末档（~1–5） |
| SHORT_OUT | 16–64 |
| 软截断自动改 | journal soft_* 或日志短 completion ≥20 事件 |

## 回滚

- 权重：`/opt/new-api/backups/one-api.before-dx-weights-*.db`
- 软截断：`/opt/new-api/kiro-guard.env.bak.*` + `systemctl restart kiro-guard*`
- Guard 代码：`kiro_guard.py.bak.*`
