# NewAPI 渠道核查清单（2026-08-02）

> 本文档是 2026-08-01 聚合池快照（`newapi-aggregation-pools-2026-08-01.md`）之后的**待实测核查清单**。
> **核查完成（2026-08-02，经 NewAPI 管理 API 实测）**：`GET /api/channel/` 全量 37 渠道 + `GET /api/log/` 近 100 条命中日志。
> 实测结论已回填，勾选项即实测结果。文档矛盾修正见文末「实测引发的文档更正」。
> 完成核查后请同步更新 `newapi-aggregation-pools-2026-08-01.md` 并在本文档打勾。

## 核查方法（通用判定标准）

| 检查项 | 判定标准 |
|---|---|
| 渠道是否在路由池 | `GET /api/channel/` 列表；**路由过滤看 abilities 表**（每模型独立 weight/priority），channels.weight 只是默认值 |
| 渠道是否真禁用 | 必须双保险：`status=2`（ManuallyDisabled）+ `abilities.enabled=0`。**status=0 是 Unknown 非 Disabled，从未生效**（ch16/25/43 教训） |
| 渠道是否被路由选中 | 查 `logs` 表近窗口命中数；或隔离测试该模型直连渠道 |
| 渠道健康 | `POST /api/channel/test/{id}`；或查 logs 最近 success/failure |
| 僵尸渠道特征 | enabled 但不在任何聚合池 models 列表、近 24h 零命中、abilities 残留 |

> 注：`/api/ability/` 端点不存在（404），abilities 表无管理 API，只能查 `GET /api/channel/{id}`（不含 abilities 字段）或直接查 DB。实际路由权重以日志命中分布为准。

## P0 —— 渠道去向不明（实测完成）

### 1. ch40（0v0.club）—— ✅ 已查清：渠道已删除，无文档记录
- **文档记录**：07-31 创建为 glm-5.2 第四源（w5），后扩挂 deepseek-v4-flash（`newapi-0v0-glm-backup-2026-07-31.md`）；08-01 快照两池均无；无删除/禁用记录。
- **实测结果**：
  - [x] `GET /api/channel/` 全量列表**无 ch40** → 渠道已被删除（删除动作未记录到文档）
  - [x] 近 100 条日志无 ch40 命中
- **结论**：无隐患（已删干净），但文档缺删除记录。聚合池快照无需补条目，只需在 CHANGELOG 记一句"ch40 已删除（日期/原因未知，实测确认不在）"。

### 2. ch41（zzzcoding.org）—— ✅ 已查清：渠道已删除，无文档记录
- **文档记录**：07-31 创建 w5，挂 gpt 六模型（`newapi-zzzcoding-gpt-backup-2026-07-31.md`）；08-01 快照 gpt 池无 ch41，且断言"gpt-5.6-luna/terra 摘除后零源"。
- **实测结果**：
  - [x] 全量列表**无 ch41** → 渠道已被删除
  - [x] 近 100 条日志无 ch41 命中
- **结论**：与 ch40 同——已删干净，文档缺记录。快照"luna 零源"说法在 ch41 已删的前提下**成立**（luna/terra 确实零源，仅 centos 曾挂，已禁）。

### 3. ch24（welfare-0xpsyche-responses）—— ✅ 已查清：DISABLED（status=2）
- **文档记录**：07-29 enabled；07-30 仍"在线运行"（zzzcoding-investigation）；08-01 快照消失，无移除记录。
- **实测结果**：
  - [x] ch24 **status=2（ManuallyDisabled）**，w2 pri40，models `welfare-codex-gpt-5.6-sol`，base_url `https://welfare.0xpsyche.me`
  - [x] 近 100 条日志无 ch24 命中
- **结论**：已被手动禁用（时间/原因无记录）。若 Kimi Code 的 `openai_responses` provider 仍引用它，会报无可用渠道——需确认客户端是否已改配。**注意：`GET /api/channel/24` 详情无 abilities 字段，无法确认 abilities.enabled=0，建议在 DB 侧核一眼**。

## P1 —— 禁用方式存疑（实测完成）

### 4. ch16/ch25（centos 两线路）—— ✅ 已查清：status=2 有效禁用
- **文档记录**：08-01 快照第 48 行写"status=0 + abilities enabled=0 摘除"；但同文档第 59 行教训 status=0 从未生效。
- **实测结果**：
  - [x] ch16 **status=2**、ch25 **status=2**（均为有效 ManuallyDisabled，非 status=0）
  - [x] 近 100 条日志无 ch16/25 命中
- **结论**：实际就是 status=2，文档第 48 行的"status=0"表述错误（应为 status=2），快照需更正一行。无隐患。

## P2 —— 文档过时残留（实测完成）

### 5. 快照第 5 节"其他单源模型" —— ✅ 已核实：确有过时行，需更正
- **实测依据**：gpt-5.6-luna/terra 实际零源（ch16/25 禁用、ch41 删除）；claude-sonnet-5 实际 ch57 有（models 含 claude-sonnet-5）；gpt-5.5 实际 ch2/16/25 全禁、仅 ch30（+ch41 已删）。
- **结论**：第 5 节与第 3/4 节矛盾属实，需重写第 5 节（见「实测引发的文档更正」）。

### 6. ch122-126（Agnes/hongshi/GPT123458/VyceAI 旧体系）—— ✅ 已查清：全部不存在
- [x] 全量列表无 ch122/123/124/125/126 → 旧体系渠道全部已移除
- [x] 近 100 条日志无命中
- **结论**：07-31 longcat-official / deepseek-official 文档中的"ch122 Agnes""ch126 vyceai"引用**均已失效**，需清理。

### 7. ch2（ai.centos.hk-gpt）—— ✅ 已查清：DISABLED（status=2）
- [x] ch2 **status=2**，models `gpt-5.5,gpt-5.6-luna,gpt-5.6-sol`
- **结论**：已禁用，状态无断层（文档缺一条禁用记录，可忽略或补记）。

## 实测新发现（08-01 快照之后的重要事实）

### 🔴 ch9/ch18（linxi-k40 + backup）实际仍 ENABLED 且在服务！
- **快照第 58 行记录**："ch3/9/18（baibei/linxi-k40）直测全部 503 `All available accounts exhausted`（上游账户耗尽），与旧 ch26/27/28 一并禁用（status=2 + abilities enabled=0）"。
- **实测**：
  - ch3（baibei-100xlabs）：status=2 ✅ 已禁用（符合记录）
  - **ch9（linxi-k40）：status=1 ENABLED**，w20 pri57，auto_ban=1
  - **ch18（linxi-k40-opus5-backup）：status=1 ENABLED**，w10 pri54，auto_ban=1
  - 近 100 条日志：**ch9 成功 12 次 / 0 失败、ch18 成功 1 次 / 0 失败**，且全部为 claude-opus-5/4-7 请求，最近命中在 2026-08-01T16:07（即核查当时仍在路由）
- **推断**：ch3 是 status=2 手动禁用得以保留；ch9/18 当时很可能被 auto-ban（status=3），被 `auto-ban-revive.py` 守护脚本（赦免 `status=3 AND auto_ban=1` → 1）**赦免回 enabled**——且上游后来恢复了（日志 0 失败，全部成功），所以现在 ch9/18 实际是**健康活跃**的 claude 源。
- **结论**：文档记录"ch9/18 已禁用"**错误/过时**。实测它们是 enabled 且健康，claude 池实际是 **ch45 + ch57 + ch9 + ch18 四源**（ch9 w20 pri57 甚至权重最高）。这不是隐患，但快照第 58 行、CHANGELOG "claude 聚合池七源→二源重组"条目的描述均需更正；同时确认 ch9/18 是否需要保留（若上游可靠，保留反而增加 claude 冗余）。

### 🟡 channels 层权重与快照/CHANGELOG 记录不一致（多处）
实测 `GET /api/channel/` 的 weight 与文档记录对比：

| 渠道 | 文档记录 | 实测 channels.weight | 说明 |
|---|---|---|---|
| ch42 deepseek-official | w8（CHANGELOG 提权 3→8） | **w1** | 可能 abilities 层是 8，channels 层未改；或又被降回 |
| ch48 opencode-go | w8（"从 22 降权"） | **w22** | 同上，降权可能只在 abilities 层 |
| ch46/47 bazaarlink | w2 / w12 | **w3 / w3** | 与文档均不符 |
| ch53 atomcode-bridge | w8（提权 3→8） | **w10** | — |
| ch55 inferx-deepseek-b | w1 | **w5** | — |
| ch49/50 inferx | w5 / w1 | **w5 / w5** | ch50 与文档不符 |

- **解释**：NewAPI 路由按 **abilities 表** weight/priority（文档教训早已写明），`channels.weight` 只是新建渠道的默认值；文档记录的"权重"很可能指 abilities 层，而实测的 channels 层是另一套值。**无法从管理 API 验证 abilities 层**（无 /api/ability 端点）。建议：若想核对真实路由权重，查 DB `abilities` 表或直接看日志命中比例（近 100 条：ch31:63、ch9:12、ch44:8、ch45:7、ch57:4、ch18:1、ch35:1、ch42:1——注意该窗口以 qwen/claude 为主，deepseek 池命中少）。
- **结论**：非故障，但文档与 channels 层数据漂移是事实；若希望文档与 API 一致，可把 channels.weight 同步成 abilities 层值（低优先级）。

### 🟡 ch0 日志条目
- 近 100 条中 ch0 有 3 条 type=3（channel.status_update）与 type=7（登录），非请求失败，无隐患。

## 实测引发的文档更正（待执行）

1. **`newapi-aggregation-pools-2026-08-01.md`**：
   - 第 58 行：更正为 "ch3 已禁用（status=2）；**ch9/18 实测仍 enabled 且健康**（上游恢复，claude 池实际四源：ch45/ch57/ch9/ch18）"
   - 第 48 行：ch16/25 禁用方式 "status=0" → "status=2"（实测确认）
   - 第 5 节：重写——gpt-5.5 现源 ch30（单源）；gpt-5.6-luna/terra **零源**；claude-sonnet-5 现源 ch57（ch26/27 已合并禁用）；删除 ch16/25 引用
   - 第 2 节 glm 池：实测与文档一致（ch14/35/37/38/44/49/54）✅ 无需改
   - 第 1 节 deepseek 池：ch40 已删（文档已不含，✅）；channels 权重漂移项加注
2. **CHANGELOG.md [Unreleased]**：追加本次核查条目（ch9/18 实况、ch40/41/24 实测状态、channels 权重漂移说明）。
3. **残留引用清理**：`docs/patches/longcat-official-omp-kimi-2026-07-31.md`（ch122 Agnes 引用）、`docs/patches/newapi-deepseek-official-channel-2026-07-31.md`（ch126 vyceai 引用）——标注已失效或删除。
4. **客户端侧确认**：Kimi Code `openai_responses` provider 若仍引用 `zg-newapi/welfare-codex-gpt-5.6-sol`（ch24 已禁）需改配；OMP/Kimi 中 gpt-5.6-luna/terra 条目已移除（文档记录过）✅。

## 待办（仅需人工在 DB/客户端侧确认）

- [ ] 确认 ch24 abilities.enabled=0（管理 API 查不到，需 DB）
- [ ] 确认 ch9/18 保留意图（上游已恢复，健康；若保留需把快照/CHANGELOG 描述改对）
- [ ] 若需核对真实路由权重：查 DB `abilities` 表 weight/priority

## 补充核查：deepseek-v4-flash 各源版本（2026-08-02）

背景：官方 2026-07-31 发布 `DeepSeek-V4-Flash-0731`（同一 model ID `deepseek-v4-flash` 后端静默升级，官方措辞 "public beta"、非 GA 字样；V4-Pro 仍是 Preview）。核查各聚合源挂的是否为 0731 正式版。

### ✅ 已确认 0731 正式版

| 渠道 | 证据 |
|---|---|
| ch42 deepseek-official | 官方直连（用户指示不查，按官方语义即最新版） |
| ch48 opencode-go | 用户指示不查 |
| ch56 hf-deepseek-0731 | **实测** `/v1/models` 真实 ID `deepseek-ai/DeepSeek-V4-Flash-0731`（key 任意可查） |
| ch50 / ch55 inferx | model_mapping → `deepseek-v4-flash-0731`；CHANGELOG 记录接入时即挂 0731 |
| **ch35 cline-free** | **用户确认（2026-08-02）**：cline 网关的 `deepseek/deepseek-v4-flash` 为正式版 |
| **ch58 hf.space** | **用户确认（2026-08-02）**：share-api 的 `deepseek-v4-flash` 为正式版 |
| **ch15 sensenova** | **用户确认（2026-08-02）**：裸名透传，正式版 |
| **ch37 / ch38 tokenrhythm** | **用户确认（2026-08-02）**：裸名透传，正式版 |
| **ch44 codebuddy** | **用户确认（2026-08-02）**：裸名透传，正式版 |
| **ch46 / ch47 bazaarlink** | **用户确认（2026-08-02）**：裸名透传，正式版 |
| **ch53 atomcode-bridge** | **用户确认（2026-08-02）**：裸名透传，正式版 |

> **结论（2026-08-02）**：deepseek-v4-flash 聚合池**全部 14 源均为 0731 正式版**（官方语义：同一 model ID 静默升级，裸名透传即最新版；ch56 实测、ch50/55 mapping、ch35/58/其余 5 源用户确认）。

> 判断依据：官方升级是**同一 model ID 静默换模型**（"simply use deepseek-v4-flash to access the latest version"），故凡透传官方 API 的中转源（tokenrhythm/bazaarlink/codebuddy 等）裸名即最新版；仅独立部署快照型上游（如 HF 单独部署 Preview 权重）可能停在旧版。

## 安全

- 本文档不含任何 API key。
- 本次核查仅用管理 API 只读请求（`GET /api/channel/`、`GET /api/log/`、`GET /v1/models`），未做任何写操作。
- 访问令牌仅存在于会话环境变量，未落盘、未提交。
