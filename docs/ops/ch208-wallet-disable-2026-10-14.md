# ch208 happycoding 欠费禁用（2026-10-14）

用户报「欠费的没 ban」并贴出现场：OMP advisor（glm-5.3）经 ch208 回
`503 预扣费额度失败, 用户剩余额度: $0.024808`。10-13 runbook 已裁决
「降权后禁用」但只落地了降权（p5），禁用从未执行。

## 证据

- ch208 happycoding：3h 内 221× `do request failed` + 明报预扣费失败
  （14:30 余额 $0.126 → 14:58 $0.0248，28 分钟烧 ~$0.10）；同窗口仍有
  286 笔小额成功 → auto_ban 不触发（500/预扣费不在禁用码表 + 未全死）。
- 排除项：ch220 lucky0625 41 错为福利 key 5 RPM 饱和（非欠费，
  0 成功/3h 但策略保留）；ch173 的 402 仅 6h 内 2 次瞬态。
- ch208 承载：glm-5.3（**sole**）、kimi-k3（sole）、minimax-m3（sole）、
  glm-5.3-flash（3 条备胎腿）、deepseek-v4.1-flash（3 条备胎腿）。

## 变更

- 备份 `new-api-before-ch208-disable-20261014-230149.db`（integrity ok）。
- `POST /api/channel/208/status {"status":2}` → 读回 status=2。
- fail-closed 验证：网关 glm-5.3 → 503 `model_not_found`（不再烧预扣费
  重试）。
- `config.yml` 角色重指（glm-5.3 全域无活腿， abilities 12 行全禁用）：
  - `plan` / `designer` / `task`：`glm-5.3:max` → `glm-5.2:max`
    （旗舰同级、1M ctx、efforts low..max；活腿 ch220 p-20，间歇饱和）；
  - `slow` 兜底链：`glm-5.3:max` → `glm-5.2:max`；
  - `advisor` **保持 glm-5.3 未动**——门禁白名单
    {glm-5.3, omen-alpha, space-bunny} 全部无活腿（omen-alpha 零
    abilities 行、space-bunny ch178 桥死），改名单需用户批准。
    **advisor 当前是暗路由，待用户裁决。**
  - 用户会话期间又将 default 切为 `gemini-3.8-flash:high`（保留不动；
    此前补的 kimi-for-coding 审批链闲置但无害）。
- `verify_omp_newapi_alignment.py` QUARANTINE 注册表：
  - 新增 `glm-5.3`（ch208 欠费禁用，充值后回开）；
  - 修正 `glm-5.2` 旧条目（10-12 muyuan 401 已过时，现 ch220 活）。
- 端到端：glm-5.2 带 reasoning budget 网关 200 归因 ch220。

## 门禁

- `test_omp_routes` 40/40 OK；`verify_omp_newapi_alignment` verdict 回到
  1 orphan（存量 Atria）；glm-5.3 走 QUARANTINE 不计孤儿。

## 遗留

1. **advisor 暗路由**：三个批准路由全灭。候选：批 `glm-5.3-flash`
   （4 腿池、NewAPI 价 0.075/3.333 近免费档）进白名单，或批 glm-5.2，
   或等 ch208 充值。用户决定后改 `test_advisor_role_stays_on_approved_
   low_cost_routes` 白名单 + config。
2. **ch220 单腿风险**：plan/designer/task 现在全系 ch220 一条福利腿
   （间歇 5 RPM 饱和）。ch119/192 的 glm-5.2 腿都 status=2。建议观察，
   饱和频繁时考虑给 glm-5.2 找第二条腿。
3. **kimi-k3 / minimax-m3**：ch208 禁用后 NewAPI 无活腿，但 OMP
   models.yml 未注册这两个名（alignment 未报孤儿）——若用户用到会
   503，充值回开即可。
4. ch208 充值后回开：`POST /api/channel/208/status {"status":1}` +
   复跑双门禁 + 角色可按需回指 glm-5.3。

## 回滚

```bash
cp ~/.new-api-local/backups/new-api-before-ch208-disable-20261014-230149.db ~/.new-api-local/new-api.db  # +重启服务
cp ~/.omp/agent/config.yml.bak-20261014-muyuan-sync ~/.omp/agent/config.yml  # 会丢失 muyuan 同步与本轮角色改动
```
