# DeepSeek V4 Pro 双源聚合池接入（2026-08-13）

DeepSeek 官方（ch42）与 opencode-go 套餐（ch48）的 `deepseek-v4-pro` 拉出并聚合成池，完全镜像 flash 的「池 + 官方钉选 + 独立源钉选」三命名模式。本地 NewAPI（127.0.0.1:3002）+ OMP 双侧落地。

## 渠道变更（增量；既有 priority 被 fork 重置，见「更正」节）

| ch | name | models 新增 | model_mapping 新增 | priority / weight |
|----|------|------------|--------------------|-------------------|
| 42 | deepseek-official | `deepseek-v4-pro`, `deepseek-official-v4-pro` | `deepseek-official-v4-pro` → `deepseek-v4-pro` | 50 / 5（未动） |
| 48 | opencode-go-flash | `deepseek-v4-pro`, `opencode-go-pro` | `opencode-go-pro` → `deepseek-v4-pro` | 51 / 20（pro 新行；flash 旧行被本次 PUT 重置为 51，见下方更正） |

- 池名 `deepseek-v4-pro`：ch42 + ch48 双源聚合（ch48 权重高，与 flash 池同构：flash 池 50/5 + 51/20——套餐优先的正式值）。
- 钉选名：`deepseek-official-v4-pro`（仅 ch42）、`opencode-go-pro`（仅 ch48），用于归因/排障。
- 未加 `zg-*` 别名（Cursor BYOK 未提需求）；flash 的 zg 别名模式需要时可照补。
- 接入前上游探活：`GET /api/channel/test/42?model=deepseek-v4-pro` 0.8s OK；`test/48?model=deepseek-v4-pro` 2.7s OK。
- PUT 更新显式携带 DB 原 key（规避空 key 清渠道风险）；`status` 字段不入 body（fork 限制，见 deepseek-flash-persona-prompt-2026-08-06.md）；persona `setting` 原样保留。

## 更正（2026-08-13 复检）：ch48 PUT 重置 flash 优先级 + 用户拍板套餐优先

- 本文件初版写「priority 未动」有误。对 ch48 的 PUT（新增 pro 模型）触发了 fork 的能力行重建：`deepseek-v4-flash` 的 priority 从 49（8-12 修复值）被重置回 51。
- 复检时曾短暂改回 49（备份 `new-api.db.bak-20260813-restore-flash-prio49`，探针 1019ms/ch42 命中）——**随后用户明确要求 opencode-go 套餐优先，不得官方优先**。
- **最终状态（用户决定）**：flash 池与 pro 池均 ch48=51 主 / ch42=50 兜底（备份 `new-api.db.bak-20260813-go-priority-51`）。重启后实测：flash 池 200/1.5s、pro 池 200/1.7s、`opencode-go` 200/1.3s，日志全 `channel_id=48`。8-12 的 5.6s 慢上游已恢复（Go 恢复后自动参与，与当时预期一致）。
- 官方钉选 `deepseek-official-v4-pro`（882ms）仅供归因/排障，不参与池主路由。
- **tool-quirk**：本 fork 的渠道 PUT（改 models）会为该渠道既有模型重建 abilities 行并重置 priority 为默认 51。此后任何渠道 models 编辑后必须复核 abilities 表 priority，勿信「未动」。

## OMP 注册（~/.omp/agent/models.yml，zg-newapi）

| id | 说明 | ctx / maxTok |
|----|------|--------------|
| `zg-newapi/deepseek-v4-pro` | 聚合池 ch42/48 | 380k / 128k（沿 flash 池值） |
| `zg-newapi/deepseek-official-v4-pro` | 官方钉选 | 380k / 128k |
| `zg-newapi/opencode-go-pro` | opencode-go 独立钉选 | 500k / 128k（沿 opencode-go flash 值） |

均 `reasoning: true`（实测 reasoning_content 正常返回）。未接入 modelRoles/fallback 链——仅注册可用，角色接线另议。models.yml 无热重载，已运行的 OMP 进程需重启后可见。

## 验证（2026-08-13 实测）

- 钉选归因（NewAPI 直连，max_tokens=128）：
  - `deepseek-official-v4-pro` → 200，上游 model=deepseek-v4-pro，答 "4"，reasoning 254 字符
  - `opencode-go-pro` → 200，答 "4"，reasoning 173 字符
  - 池 `deepseek-v4-pro` → 200，答 "4"
- 回归：ch42 `deepseek-official-v4-flash` 仍 200 正常（persona system_prompt 仍在注入，prompt_tokens≈971 与改动前一致）。
- OMP 端到端：`omp bench zg-newapi/deepseek-v4-pro` 10/10 OK（TTFT 均 1.6s，TPS 46/s）；`omp bench` 两个钉选各 1 run OK（官方 890ms/53.8tps，opencode-go 1.1s/70.2tps）。

## 角色链接线（2026-08-13 补充，~/.omp/agent/config.yml fallbackChains）

pro 以池名 `zg-newapi/deepseek-v4-pro` 编入旗舰角色回退链（与 opus-5 / k3 同链）：

| 链 | 变更后顺序 |
|----|-----------|
| `zg-newapi/deepseek-v4-pro`（新建模型链，镜像 flash 链） | → `opencode-go-pro` → `deepseek-official-v4-pro` |
| slow | opus-4-8 → k3 → **deepseek-v4-pro** → anyrouter/claude-opus-5 |
| plan | opus-4-8 → k3 → **deepseek-v4-pro** |
| designer | claude-opus-5 → **deepseek-v4-pro** |
| bigctx | k3 → claude-opus-5 → **deepseek-v4-pro** → deepseek-v4-flash |

- **vision 未编入**：pro 为纯文本模型（models.yml 无 image input），进视觉链会静默丢图。
- **default 未动**：当前无显式 default 链，新建会整体替换 OMP 隐式默认链，风险大于收益。
- gpt-5.6-sol 当前不在任何角色/链中（designer 已改 k3:max），「同链」按 opus-5/k3 所在旗舰链理解。
- 验证：编辑后冷启动 `omp bench zg-newapi/deepseek-v4-pro --runs 1` OK（TTFT 4.0s），配置+链加载无错；回退触发实测未做（需制造渠道故障，生产风险，跳过）。
- 备份：`~/.omp/agent/config.yml.20260813-pro-chains.bak`。

## 备份 / 回滚

- DB：`~/.new-api-local/backups/new-api.db.bak-20260813-deepseek-v4-pro`（57,458,688 B，改前）
- OMP：`~/.omp/agent/models.yml.20260813-deepseek-v4-pro.bak`
- 渠道级回滚：PUT 移除本次新增 models/mapping 条目（或还原本渠道 DB 行）；OMP 回滚：还原 models.yml 备份。

## 追加（2026-08-15）：ch84 `teamorouter-deepseek-free`——免费层兜底

用户提供 teamorounter key（仅存 NewAPI 渠道配置，不落文档/仓库）。直连实测
`deepseek-v4-flash-free` / `deepseek-v4-pro-free` 均 200 且有真实内容输出
（reasoning 模型，max_tokens=8 时 content 为空——与 sensenova 同类，预算给足
即正常；OMP 侧经聚合调用不受直接暴露影响）。

| 项 | ch84 |
|----|------|
| type / base_url | 1（OpenAI）/ `https://api.teamorouter.com`（根路径） |
| models | `deepseek-v4-flash,deepseek-v4-pro`（仅聚合名，不直接暴露 -free id，避开新 id 配价） |
| model_mapping | 两个聚合名 → 对应 `-free` 上游 |
| priority / weight | 40 / 5——低于 ch42（50）/ch48（51）主层，仅在主池失效时承接 |
| test_model | `deepseek-v4-flash`（经 mapping 实测上游路径） |

验证：创建 `POST /api/channel/` `{"mode":"single","channel":{...}}`（fork 契约复用）；
abilities 双行 `enabled=1, 40/5`；admin 渠道测试 200；聚合 `deepseek-v4-flash`
请求仍落 ch48（主层不受影响）；创建前整库快照
`new-api-before-teamorouter-20260815-234706.db`（integrity ok）。smoke 新增
`teamorouter free fallback posture` 门禁（允许 auto_ban 降级，锁定 p40/w5 层级）。

## 追加（2026-08-16）：极简模式效应与 Pro 的 OMP 挂载戒律

社区对照实验（同机同模型同档位只换 Harness 模式）：标准 91 / PTC 92 /
极简 99。根因实锤：DSH 官方仓库测试文件 "sends the exact RL prompt and
schemas"——**V4 Pro(0813) RL 训练用的就是极简模式的 prompt+工具 schema**，
首轮塞 20+ 陌生工具会显著劣化（对自家格式过拟合）。**V4 Flash 无此问题**
（各 Harness 下稳定 90–95）。两阶段插件 `dsh-anchored-standard`
（github.com/xiaobright/dsh-anchored-standard）首轮锚定极简格式、首次工具
调用后恢复全工具，98.5 分追平极简——OMP 无此插件机制。

戒律：
- OMP 工具重角色（task/plan/smol 等带工具 inventory 的位）**禁止挂 V4 Pro**；
  Flash 免疫可任意挂（2026-08-16 起 task=deepseek-v4-flash:max）。
- 若需 Pro 峰值做 agentic 执行：走最小工具集自定义子代理（tools 只留
  终端/读文件级），不要直接绑进标准角色。
- 纯 Q&A/无工具调用不受此效应影响（本机 A/B 实测：裸调用 vs 2.5KB 重
  system prompt，CRT+24点 8/8 全对、时延/token 无差异）——效应特指
  **首轮工具 schema**，不是 system prompt 轻重。

