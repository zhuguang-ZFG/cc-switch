# supervisor 快照轮转 bug：按文件名字典序删除（2026-10-16）

**症状**：两份被 runbook 引用为回滚路径的快照消失——
`new-api-before-agentrouter-newmodels-20261015-212818.db`、
`new-api-before-ch136-reenable-20261015-222306.db`；而更老的
（10-15 16:12/17:36/18:09/19:00）全部存活。

## 根因（已定位，非推测）

`scripts/ops/proxies-supervisor.py` → `backup_newapi_db()`：

```python
backups = sorted(BACKUP_DIR.glob("new-api-*.db"))   # ← 无 key= ：按【文件名】字典序
for old in backups[:-BACKUP_KEEP]:                  # BACKUP_KEEP = 7
    old.unlink()
```

字典序把"名字"当"年龄"：`new-api-before-a…` < `…-ch1…` < `…-chs…`（chstatus）<
`…-d…`（dsv41f）< `…-r…`（restore-ch39）< `…-z…`（zen）。当时 9 个匹配文件，
`[:-7]` 恰好删掉前 2 个 = agentrouter + ch136 —— **与实际消失集合逐一吻合**；
更老的 chstatus/dsv41f 因名字在后而幸存。

**附带损伤**：日均文件名 `new-api-<date>.db` 的 `2` < `b`，恒排最前 → 每次备份
写完后立刻被自己删掉（磁盘上现无任何 `new-api-2026-*.db`）——日均备份链实为断裂。

## 修复

```python
backups = sorted(BACKUP_DIR.glob("new-api-*.db"), key=lambda p: p.stat().st_mtime)
```

与同目录既有做法一致（`newapi_backup_restore_drill.py`、`system-health-check.py`
均按 mtime 排序）。

## 回归证据（新测试，先红后绿）

`scripts/ops/test_proxies_supervisor_backup_rotation.py`：构造名字序与 mtime 序
**相反**的 7+2 份快照（`zzz` 最旧、`aaa` 最新），调用 `backup_newapi_db()` 后断言
「恰好保留 BACKUP_KEEP 份、最新的 `aaa` 与当日 `new-api-2026-10-16.db` 存活、
最旧的 `zzz` 被删」。

| 版本 | 结果 |
|---|---|
| 回退修复（旧字典序） | **FAIL**：`'new-api-2026-10-16.db' not found in [… m00…m05, zzz]` —— 当日备份与最新快照被删、最旧全部留下 |
| 修复后 | 1 passed（既有 supervisor 测试 17 passed 无回归） |

## 部署与重启

- 在机副本（`C:\Users\zhugu\.omp\guardian\proxies-supervisor.py`）先备份为
  `.bak-20261016-pre-mtime-fix`，再布入修复（`Select-String` 校验含新排序键）；
- 重启：核验 pid 7296 确为 python + `proxies-supervisor.py` 后 kill → 按 watchdog
  同法（ProcessStartInfo、隐藏窗口）启动 pid 10684；
- 复位核验：心跳 14:58:32 新鲜、`last_backup=2026-10-16`（不重复备份）、10 项受管
  服务全 healthy、端口 8788/3003/15999/16000/16001 同 PID 仍在听（8788 的 401 为
  既知客户端鉴权门）；旧 pid 进程消失。
- 回滚：还原 `.bak-20261016-pre-mtime-fix` 并重启（修复本身也可 `git revert`）。

随后按 house 路径补做正规化（15:06）：

- 实测 live 三件套（`proxies-supervisor.py` / `anyrouter-window-canary.py` /
  `omp-model-routing-observability.js`）字节**均等于 `8c5a6643`**，而门禁基线
  `BASE = "0ebb556a^"` 已过期（对三件套全会拦）→ 按「BASE 对齐 live 字节」语义推进为
  `BASE = "8c5a6643"`；
- `python scripts/ops/deploy-stability-scripts.py` 通过门禁并落档：
  `{"manifest": "…/guardian/backups/stability-20261016-150620-15224/deployment.json",
  "verified_files": 3, "restart_performed": false}`；部署前后 live 字节逐一相同
  （1ea7a0ef9f8b / db62d7aa588c / 0f7210baa219）。

## 遗留（供裁决）

轮转只覆盖 `new-api-*.db`；`add_*` 脚本产出的 `newapi-*.db`（含今日 motomoto 三份）
**不在其管辖内** → 该族无界增长。选项：把 glob 扩为两族（并可能上调 BACKUP_KEEP），
或统一命名。未擅动（会改变历史保留深度）。

## 日志佐证（supervisor 自述，非推断）

```
[2026-10-15 03:00:03] new-api.db 备份完成: new-api-2026-10-15.db (62.1MB)，清理旧备份 8 份
[2026-10-16 03:00:15] new-api.db 备份完成: new-api-2026-10-16.db (66.2MB)，清理旧备份 6 份
```

10-16 03:00:15 那次删掉的 6 份按字典序恰为：`new-api-2026-10-15.db`、`new-api-2026-10-16.db`
（日均两份，`2`<`b`）+ 当夜四份（`agentrouter-newmodels`、`catalog-trim`、`ch127-trim`、
`ch136-reenable`）——与盘上残留 7 份（chstatus×2、dsv41f×2、restore-ch39×2、zen）逐一对上。
**删除时刻是 10-16 03:00，不是 10-15 19:2x**（"19:23 分界"是巧合：19:32 之后的文件名恰好都排前）。

## 实盘验证（修复后，15:03）

对真实 backups 目录跑修复版 `backup_newapi_db("2026-10-16")`：

- 补回断裂的日均链：`new-api-2026-10-16.db`（67.4 MB）✓；
- 轮转只删 mtime 最旧一份：`new-api-before-dsv41f-free-20261015-161205.db`（16:12）——
  **与修复语义一致**（保留最新 7 份）；
- 余 7 份按 mtime 全为最新（10-15 16:39 起）；`dsv41f-free-pricing-2026-10-15.md`
  的回滚引用已在同一轮改为逐字段方案。

## 悬空引用全量清点（同日扫描）

`docs/ops/*.md`（非 archive）中对 `new-api-before-*.db` 的引用共 43 处，其中 **35 处
已悬空**（横跨约 20 份文档，最早的 10-09）；archive 与 patches 中另有大量历史引用同样
已不存在。根因不是偶发：**`backups/` 是 KEEP=7 的轮转缓存，不是归档**——任何"引用某份
快照作长期回滚路径"的写法都会随轮转失效。

制度结论（已按此改写本次涉及的文档）：
- 回滚路径**优先写逐字段/逐命令步骤**（快照只是加速器，不是前提）；
- 引用快照时必须写明"若仍存在"，或注明其受 KEEP=7 约束；
- 需要长期留存的快照应放**独立归档目录**（如 `backups/archive/`），不混在轮转集里
  ——是否建归档、是否扩 glob 覆盖 `newapi-*` 族，待用户裁决。

## 影响面复盘

- 数据无损失（删除的是快照副本，非库本体）；
- 代价是**回滚可达性**：两份 runbook 的整库回滚路径失效 → 已改为
  「逐字段步骤 + 最近已验证快照 `newapi-before-motomoto-welfare-20261016-144743.db`」；
- 教训写进流程：引用快照前先 `ls` 核实存在，且引用后不得假定其长期存活
  （自动化轮转会清理）。
