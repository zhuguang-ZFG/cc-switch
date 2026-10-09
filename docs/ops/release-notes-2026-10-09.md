# Release Notes — d85deaf5 (2026-10-09)

**ops: NewAPI 渠道批量接入 + smoke auth 修复 + 死渠道清理 + OMP 瘦身**

## 新渠道接入（13 个 onboarding 脚本）

| 渠道 | 脚本 | 模型 | 角色 | 状态 |
|------|------|------|------|------|
| ss2a.top | `add_ss2a_channel.py` | glm-5.3 | 主档 p50 | disabled (ch183) |
| ss2a kimi | `add_ss2a_kimi_channel.py` | kimi 系 | 备份 | 脚本入库，未创建渠道 |
| 0v0.club | `add_0v0_channel.py` | glm-4.5-air, glm-4.6v | 新增 | enabled (ch185) |
| asvla | `add_asvla_channel.py` | gpt-6-astra, claude 系 | 主档 | enabled (ch182) / disabled (ch177) |
| hubway | `add_hubway_channel.py` | gpt-5.6-terra, gpt-6-sol 等 | p-20 备份 | enabled (ch175) |
| vsakura | `add_vsakura_gpt_channel.py` | gpt-5.6-terra, gpt-6-sol 等 | p-20 备份 | disabled (ch176) |
| grok-heavy | `add_grok_heavy_channel.py` | gpt-5.6-luna, gpt-6-astra | 主档 | enabled (ch174) |
| muyuan-gongyi | `add_muyuan_gongyi_channel.py` | 15 个 mistral/glm/qwen 系 | 新增 | enabled (ch173) |
| 42x-deepseek | `add_42x_deepseek_channel.py` | deepseek 系 | 新增 | 脚本入库，未创建渠道 |
| 103.39.64.76 relay | `add_103_39_64_76_relay_channel.py` | glm-5.3-flash 备份 | 备份 | disabled (ch179) |
| zen-free-bridge | `add_zen_free_bridge_channel.py` | 多个 FREE 模型 | 免费层 | enabled (ch178) |
| opencode-go step-5 | `add_opencode_go_step5preview_channel.py` | step-5-preview-free | 限时免费 | enabled (ch184) |
| jev-systemone | `add_jev_systemone_channel.py` | system-one 系 | 新增 | enabled (ch181) |

**渠道状态汇总：** 8 enabled / 4 disabled / 2 仅脚本入库（共 14 个渠道条目）

## Smoke 关键修复

- **admin auth 断链修复**：Guardian token 非 JWT → 401 后不再 fail-closed，改为 drop cache + 回退密码登录，admin API 检查恢复
- **ch45 残留清理**：从 `FALLBACK_CHANNEL_POSTURES` 和 `DEGRADED_ACCEPTED_DISABLED` 移除已删渠道
- **ch72 模型隔离**：移除 anyrouter 多余模型 `claude-fable-5-1-reversed`
- **ch3/ch9 opus 权重恢复**：Guardian 降级权重回滚至契约值 (p52/w20, p52/w16)
- **新增 8 渠道 posture contracts**：ch173/174/175/178/181/182/184/185 入 `BACKUP_CHANNEL_POSTURES`，漂移检测覆盖

## 死渠道清理

- 渠道总数清理至 77（enabled 33 / disabled 42 / other 2）
- 死模型（所有渠道 disabled）48 → 33（-31%）
- 交叉校验 posture contracts 避免误删（ch83/ch91 Sol 契约渠道误删后从备份恢复）

## OMP 瘦身

- `models.yml`：移除 7 个无 key provider（fengwind/ooioo/mistral-official/sotamodel-canary/arcdent/lijinmu/seeseed-hydrogel），34 个模型条目删除，945 → 681 行
- `config.yml`：清理 `maxInFlightRequests` 和 `fallbackChains` 中对应引用
- advisor 路由 `space-bunny-free` → `space-bunny` 换指

## 辅助工具

- `turnstile_solver.py`：验证码自动解题
- `jev_systemone_bridge.mjs` + `jev_ops_triage.py`：JEV SystemOne 桥接与分诊
- `zen_free_bridge.mjs`：zen 免费层桥接
- `ch3-100xlabs-reprobe.py`：ch3 重探测
- `park_zombie_channels_20261007.py`：僵尸渠道停泊

## Runbook 文档（19 篇）

覆盖 10-06 至 10-09 所有渠道接入、故障处置、清理操作的完整记录。已归档至 `docs/ops/archive/`。

## 统计

- 47 files changed, +9543 / -6 lines (初始提交 d85deaf5)
- 157 files changed (归档提交 f8a192a7：156 runbook → docs/ops/archive/)
