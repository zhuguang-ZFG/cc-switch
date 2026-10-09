# 顺手清单批量处理（#8–#13）+ 单腿池治理与两起上游死亡事件（2026-10-04 凌晨）

## 结论

六项全落地。过程中发现两起上游死亡（ch118 seeseed 下架 deepseek-v4-flash、
ch89 seeseed-hydrogel 补全端全灭），按用户裁决完成姿态调整；fork 路由模型
实证为**渠道优先级**（abilities 为物化副本，per-ability 调整不 survive
channel/fix）；ch148 全局压至 **-10** 成为全池最底层备份。

## 逐项

### #8 glm-5.3-flash（bai ch121）

- 唯一承载渠道 ch121 status=2 → 网关 503 `No available channel`（三档
  max_tokens 实测）。**死选择子**，models.yml 条目已删（agent repo，slim 后
  新 hash），恢复=ch121 复活 + 历史回捞。
- agentrouter 直连 glm-5.3 条目保留：账户级 402 死亡，随充值复活（决策项 #4）。

### #9 intern qwen 映射审计

- ch140-143 全部：`qwen3-8-27b` 在 models + `model_mapping→qwen3.8-27b` 在册，
  abilities×4 p40 enabled——**一致无缺口**；池六腿（Groq50>runinfra49>intern×4 40）。
- 扩池 runbook 的"死映射待统一"为同日增补轮**之前**的描述，已标修（本仓提交）。

### #10 官方定价落库（admin API，备份 `new-api-before-pricing-20261004-000625.db`）

| 模型 | ModelRatio | CompletionRatio | 来源 |
|---|---|---|---|
| step-3.7-flash | 0（残留）→ **0.1** | 3（残留）→ **5.75** | 官方 $0.20/$1.15 per 1M（OpenRouter/lmmarketcap 一致） |
| LongCat-2.5-Preview | 无 → **0.14** | 无 → **4** | 官方 ¥2/¥8 per 1M（限时折扣，longcat.chat 定价页） |

读回验证 200/170 条。`longcat-2.5-preview-free` 保持 0（免费变体）。

### #11 单腿池备份位（核心教训：fork 路由=渠道优先级）

- **实证**：per-ability priority/weight 直改不 survive `channel/fix`（物化副本被
  渠道行重建）；qwen runbook 的"ch124 40→50"生效是因为单模型渠道=渠道级修改。
- **ch89 w0 排除**：w0 渠道在权重选择中被排除，即使独占最高层也塌穿到下一层。
  ch89 渠道 weight 不可动（会把 grok-4.6 从 ch109 w5 独载改成 10:5 混载）。
- 终态（备份 `new-api-before-singleleg-backup-20261004-000658.db`）：
  - **ch148 priority 20→-10**（sqlite 直写 + fix），六池 abilities 全部
    `(…,1,-10,1)` 沉底；主渠道配置**零改动**（w0/优先级原样）。
  - **step-3.7-flash**：ch144(p0,w2) 主 / ch148(-10) 备，归因实证 ch144 ✓。
  - **qwen3.8-max**：ch89 上游死亡（见下），用户裁决**挂 ch148 常态承载**
    （手动微流量；ch89 w0 本就不可抢），归因实证 ch148 ✓。
  - **deepseek-v4-flash**：ch118 死后 ch148 承载（见下），归因实证 ch148 ✓。

### 上游死亡事件 A：ch118 seeseed 下架模型

- 网关 400 `unknown provider for model deepseek-v4-flash`；admin 渠道自测同款
  → 上游目录删除该模型（不可自愈）。**双表禁用 ch118**（备份
  `new-api-before-ch118-disable-*.db`），池由 ch148 按备份设计承载。
  smoke 门禁：auto_ban 渠道禁用属 automation-owned，未触发 unexpected_disabled。

### 上游死亡事件 B：ch89 seeseed-hydrogel 补全端全灭

- 传输层活（/v1/models 200/0.9s，目录 46 模型含 qwen3.8-max）；chat 全灭：
  grok-4.6 直连 500 `do_request failed`（`new_api_error`，**它上游的上游死**），
  qwen3.8-max 直连 60s 挂零字节。grok 无感（ch109 独载）；qwen3.7-max 同款
  ch89 独腿亦死。恢复取决于 seeseed，无本地动作。

### #13 atria 耗尽行为（不烧额度）

- ch139 status=1、**auto_ban=1**：1 亿 token 耗尽→上游 4xx→auto_ban 自动禁用=
  既定 fail-closed。当前用量 19,162 tokens（0.02%），耗尽不近。行为已定义，
  不做燃烧测试。

### #12 ~/.omp/agent .git 瘦身

- 备份：`D:/Temp/User/omp-agent-pre-slim-20261004.bundle`（486MB，`git bundle
  verify` 完整历史）。
- `git filter-repo --path cache/tiny-models --invert-paths --force`：
  **.git 471M → 379K**。历史 hash 重写；`commit-map-pre-slim-20261004.txt`
  已入库（agent repo 根）。本仓引用的旧 hash（1593471 等）经该 map 解析。
- 坑：filter-repo 重置工作树，吞掉了未提交的 flash 条目删除——已重放并提交。
- fsck 干净、log 连续、`omp -p` SLIM_OK。

## 验证矩阵（全部实测归因）

| 模型 | 归因 | 期望 |
|---|---|---|
| qwen3.8-max | ch148 | ch148（用户裁决常态承载） |
| step-3.7-flash | ch144 | ch144 主 |
| deepseek-v4-flash | ch148 | ch148（ch118 死后备份承载） |
| glm-5.3 | ch140/142 | intern 池主 |
| k3 | ch33 | 官方主 |
| 门禁 | smoke 仅存量 opus-posture FAIL；route gate 40/40（早前） | — |

### auto_ban 未兑现 + ch89 手工禁用（00:34，评审指正后补证）

- 早前记录"auto_ban=1 自动接住"是**未验证假设**。补证：00:16–00:20 多次
  生产/admin 500（do_request_failed）后 20+ 分钟 ch89 仍 status=1、零日志行——
  **auto_ban 对 500 类上游死亡的触发覆盖未兑现**（至少不及时）。含义：
  Guardian/本仓依赖 auto_ban 做 fail-closed 的假设对 5xx 类失效不成立，
  此类死亡须手工禁用或 Guardian 侧强制（备份
  `new-api-before-ch89-disable-20261004-003429.db`，smoke 复绿=仅存量 FAIL，
  auto_ban 渠道禁用被 accepted 逻辑接纳）。
- **已手工双表禁用 ch89**（可逆，seeseed 补全端恢复后回捞）。连带：
  qwen3.7-max/normal/plus（ch89 独腿 ×3，OMP 可选）转硬 503——死选择子，
  是否按 qwen3.8-max 同款挂 ch148 待用户裁决（budsin 有 qwen3.7-max/plus
  exact id）。

## 回滚

- 定价：还原 `new-api-before-pricing-20261004-000625.db` 或 admin API 改回。
- 单腿/ch118：还原 `new-api-before-singleleg-backup-000658` /
  `new-api-before-ch118-disable-*`；ch148 回 p20 = sqlite + fix。
- slim：`git clone D:/Temp/User/omp-agent-pre-slim-20261004.bundle` 完整恢复。
