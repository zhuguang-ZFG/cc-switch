# LongCat 2.5 Preview 双源聚合（ch145 主 / ch133 备）（2026-10-03）

## 结论

用户指正：ch133 `longcat-2.5-preview-free`（opencode-go 中继）与 ch145
`LongCat-2.5-Preview`（官方）是**同一模型**的两个来源 id。已聚合为一路由、
双源互备：**官方 ch145 主（priority 0→10）、免费中继 ch133 备（0）**，
两个网关 id 均可达、均默认落 ch145。

## 变更

执行脚本：`scripts/ops/unify_longcat_preview_sources.py`（幂等、带现状守卫；
fork 拒 PUT 渠道更新（09-11 教训），走 sqlite 直写短事务 + channel/fix 重建）。

| 项 | 值 |
|---|---|
| ch133 | models += `LongCat-2.5-Preview`；model_mapping `{"LongCat-2.5-Preview":"longcat-2.5-preview-free"}`；priority 留 0（备） |
| ch145 | models += `longcat-2.5-preview-free`；model_mapping `{"longcat-2.5-preview-free":"LongCat-2.5-Preview"}`；priority 0→10（主，该渠道只载 LongCat，无副作用） |
| abilities | 4 行：两 id × 两渠道，prio 10/0 各二 |
| DB 快照 | `new-api-before-longcat-unify-20261003-181101.db`（integrity=ok） |

## 验证

- abilities 形状：`(LongCat-2.5-Preview,145,10)/(LongCat-2.5-Preview,133,0)/(free,145,10)/(free,133,0)`。
- **映射定向证明**（非破坏性）：admin test `ch133?model=LongCat-2.5-Preview` ✓、
  `ch145?model=longcat-2.5-preview-free` ✓——两个新映射均经 fork 实测。
- 网关实弹：两 id 各 200（12/25、12/32）均归因 ch145（primary）。
- 备援语义（ch145 挂→落 ch133）未做破坏性演练；语义由 NewAPI 优先级/禁用转移保证。

## 备忘

- 此前"两个不同 id、互不聚合"的表述已作废：同一模型、两来源 id、现已互备。
- LongCat 仍未分配 OMP 角色（用户未指定）；双 id 均在 models.yml 注册。
