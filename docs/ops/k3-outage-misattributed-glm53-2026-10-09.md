# k3 断供误判为 glm-5.3 报错 — 诊断与恢复（2026-10-09）

## 现象

用户报告"刚接入的 glm5.3 总是报错"（2026-10-08 深夜～10-09 00:08）。

## 结论（根因）

**glm-5.3（ss2a ch183）全程零失败**；报错全部来自 **k3 无可用渠道**：

- OMP `config.yml` 角色路由：`plan`/`designer`/`task` = `zg-newapi/k3:max`（主
  力工作角色），`advisor`/`default` = glm-5.3 系。
- k3 全部渠道当时处于禁用态（abilities enabled=0）：
  - ch33 `kimi-official-k3`（p50/w10，7 天 7702 行主力）：10-07 03:00 后手改
    禁用（status=2，非自动禁用；Guardian state.json 仅
    `channel_identities.33`（fingerprint+name），不在 disabled/degraded 池）。
  - ch115 `sensenova-k3`：429 `inference exceeds tpm/rpm limit`。
  - ch148 `budsin-apichat`：10-07 15:56 上游额度耗尽（`用户额度不足`）。
  - ch150-170 tokenrhythm 池：禁用。
- NewAPI 对无渠道模型即时返回 503（亚毫秒），stderr 逐条实证：
  `No available channel for model k3 under group default (distributor)`。
  00:07:44–00:08:15 共 12 条，与 glm-5.3/step-5/agnes 成功流交错（同 token
  `local-windows-clients`）→ 用户在 OMP 端看到角色报错，误归因于当晚刚接入
  的 glm-5.3。

### glm-5.3 侧排查（全部通过）

- logs 无任何 glm-5.3 type=1（错误）行；23:47–00:08 ch183 成功 13+ 次，
  含 60–98K prompt 重上下文（advisor 形态），use_time 3–40s。
- ss2a 上游 00:06:57 曾有一次直连 503（`Service temporarily unavailable`），
  ~1 分钟内 3/3 恢复；NewAPI `RetryTimes=1` + `AutomaticRetryStatusCodes=
  408,500-503` 已覆盖，未产生客户端可见失败。
- 网关 glm-5.3 实弹：小请求 + ~24K prompt 200。

## 恢复动作（2026-10-09 00:15）

1. 备份：`new-api-before-k3-ch33-enable-20261009-001559.db`（37187584B，
   integrity=ok）。
2. ch33 管理探针（禁用态可测）：HTTP 200 success（2.5s）→ kimi 官方周额度
   已恢复。ch115 探针 429（仍死，不动）。
3. 启用：`POST /api/channel/33/status {"status":1}`（专用端点；**勿用**
   `PUT /api/channel/` 改状态——本 fork 部分 body PUT 有 clobber 前科，
   实测 detail-shape PUT 返回 HTTP 200 success=false `Invalid parameters`，
   未写入）。
4. 读回：ch33 status=1；abilities 5 模型（k3/kimi-for-coding(-highspeed)/
   k3-256k/zg-k3）enabled=1，p50/w10 未动。

## 验证（端到端）

- 网关 k3 实弹：HTTP 200，`K3_BACK`，93/39 tok，28.0s。
- OMP 用户路径：`omp --model "zg-newapi/k3:max" --print` → `OMP_K3_OK`
  （20.5s）——`:max` effort 剥离 + k3 路由全链路恢复。
- 启用后稳定性：Guardian 巡检 00:15:18 自行探活 ch33 成功（87/16 tok）；
   00:17:12 真实角色流量 18535/58 tok / 6s 成功；ch33 不在 Guardian
   disabled/degraded 池，无回打冲突。

## 遗留 / 观察

- ch33 周额度模式（见 `codex-same-model-failover-2026-09-12.md`）：再次
  耗尽时 k3 将重现同类 503；届时症状=「plan/designer/task 角色报错」，
  与 glm-5.3 无关。判别法：NewAPI stderr 搜 `No available channel for
  model k3`。
- OMP 报错的模型归因以 stderr 请求级日志为准，勿按"最近接入"推断。
