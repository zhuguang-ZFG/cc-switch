# NewAPI 渠道核查清单（2026-08-02）

> 本文档是 2026-08-01 聚合池快照（`newapi-aggregation-pools-2026-08-01.md`）之后的**待实测核查清单**。
> 仓库文档交叉核查发现若干渠道去向不明或文档矛盾，以下条目均需在 NewAPI 管理端（`https://aliyun.donglicao.com`）实测确认后回填状态。
> 完成核查后请同步更新 `newapi-aggregation-pools-2026-08-01.md` 并在本文档打勾。

## 核查方法（通用判定标准）

| 检查项 | 判定标准 |
|---|---|
| 渠道是否在路由池 | `GET /api/channel/` 列表；**路由过滤看 abilities 表**（每模型独立 weight/priority），channels.weight 只是默认值 |
| 渠道是否真禁用 | 必须双保险：`status=2`（ManuallyDisabled）+ `abilities.enabled=0`。**status=0 是 Unknown 非 Disabled，从未生效**（ch16/25/43 教训） |
| 渠道是否被路由选中 | 查 `logs` 表近窗口命中数；或隔离测试该模型直连渠道 |
| 渠道健康 | `POST /api/channel/test/{id}`；或查 logs 最近 success/failure |
| 僵尸渠道特征 | enabled 但不在任何聚合池 models 列表、近 24h 零命中、abilities 残留 |

## P0 —— 渠道去向不明（最可能产生隐患）

### 1. ch40（0v0.club）
- **文档记录**：07-31 创建为 glm-5.2 第四源（w5），后扩挂 deepseek-v4-flash（`newapi-0v0-glm-backup-2026-07-31.md`）；08-01 快照两池均无；无删除/禁用记录。deepseek 官方文档显示其 deepseek ability 曾 pri=0 w=0。
- **待核查**：
  - [ ] ch40 当前 status？（1=enabled / 2=disabled / 3=auto-ban）
  - [ ] ch40 models 列表？abilities 表状态？
  - [ ] 近 24h logs 是否有命中？
  - [ ] 若 enabled 且零命中 → 判定僵尸渠道，禁掉（status=2 + abilities.enabled=0）或补回池
  - [ ] 若已禁用 → 在快照补一行记录，消除"去向不明"

### 2. ch41（zzzcoding.org）
- **文档记录**：07-31 创建 w5，挂 gpt-5.5/5.6-sol/5.6-luna/5.6-terra/5.4/5.4-mini 六模型（`newapi-zzzcoding-gpt-backup-2026-07-31.md`）；08-01 快照 gpt 池无 ch41，且断言"gpt-5.6-luna/terra 摘除后零源"——**与 ch41 记录直接冲突**（若 ch41 在，luna 应为 ch41 单源或双源）。
- **待核查**：
  - [ ] ch41 当前 status？
  - [ ] ch41 models / abilities？
  - [ ] 若 enabled → 快照"luna 零源"说法错误，需更正快照并补 luna/terra 路由记录
  - [ ] 若已禁用/删除 → 无记录，补记录

### 3. ch24（welfare-0xpsyche-responses）
- **文档记录**：07-29 enabled（Responses-only，别名 `welfare-codex-gpt-5.6-sol`，w2 pri40）；07-30 仍"在线运行"（zzzcoding-investigation）；08-01 快照消失，无移除记录。
- **待核查**：
  - [ ] ch24 当前 status？
  - [ ] 若 enabled → 是否仍被 Kimi Code `openai_responses` provider 使用？确认后补回快照
  - [ ] 若已禁用 → 补记录（禁用原因？）

## P1 —— 禁用方式存疑（可能实际未生效）

### 4. ch16/ch25（centos 两线路）
- **文档记录**：08-01 快照第 48 行写"status=0 + abilities enabled=0 摘除"（centos 欠费 403）；但同一文档第 59 行教训明确 **status=0 从未生效**（ch16/25/43 正是反面案例，须 status=2）。
- **待核查**：
  - [ ] ch16、ch25 当前 status 值？（0=Unknown 无效 / 2=ManuallyDisabled 有效）
  - [ ] abilities.enabled 是否 = 0？
  - [ ] 若 status 仍是 0 → 改成 2，否则 403 渠道仍可能被路由选中

## P2 —— 文档过时残留（需清理/确认）

### 5. 快照第 5 节"其他单源模型"整体过时
- 仍写 gpt-5.5 "ch16/25/30 三源"、gpt-5.6-luna "ch16/25 二源"、gpt-5.6-terra "ch25 单源"、claude-sonnet-5 "ch26/27 二源"——与第 3 节（ch16/25 禁用）和第 4 节（ch26/27 并入 ch57）自相矛盾。
- **待处理**：核对各模型当前真实路由源后更新第 5 节（或删除并入前几节）。

### 6. ch122-126（Agnes/hongshi/GPT123458/VyceAI 旧体系）
- 07-31 文档仍称"Claude Haiku 走 ch122 Agnes"（longcat-official）、"聚合裸名源含 ch126 vyceai"（deepseek-official），但 07-28 极简重建后的全渠道清单均无这些渠道。
- **待核查**：
  - [ ] ch122/ch123/ch124/ch125/ch126 是否真的不存在（`GET /api/channel/` 确认）？
  - [ ] 若已不存在 → 清理 longcat-official / deepseek-official / CHANGELOG 中残留引用

### 7. ch2（ai.centos.hk-gpt）
- **状态断层**：07-29 审计为 gpt 主源（healthy）；07-29 下午 504×186；07-31 zzzcoding 文档显示其 ability 已 disabled；08-01 快照消失。中间无正式禁用记录。
- **待核查**：
  - [ ] ch2 当前 status？abilities？
  - [ ] 若已禁用 → 补记录；若 enabled → 确认是否 gpt 池遗漏源

## 核查完成后需同步更新的文档

- [ ] `docs/patches/newapi-aggregation-pools-2026-08-01.md`：ch40/ch41/ch24 状态、ch16/25 真实禁用方式、第 5 节刷新
- [ ] `CHANGELOG.md` [Unreleased]：追加本次核查结论条目
- [ ] 相关残留引用清理（longcat-official / deepseek-official 等）

## 安全

- 本文档不含任何 API key。
- 所有核查动作遵守 `docs/ops/do-not-modify-cc-switch.md`：不动 cc-switch 代码/数据库，仅 NewAPI 运维操作。
