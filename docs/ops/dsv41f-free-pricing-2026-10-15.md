# deepseek-v4.1-flash 免费化（2026-10-15）

用户指示「opencode 白嫖 DeepSeek V4.1 Flash，搞一下 free 模型」。
opencode-go 免费腿 ch218 早在 2026-10-12 已接入
（`add_opencode_go_deepseek_v41_flash_channel.py`，p0/w2 免费底层；
当时刻意不动 ModelRatio——共享 id，价表牵动付费腿）。本轮把计费钉零。

## 现状证据（2026-10-15）

- 24h 实流分布：ch218（opencode-go 免费）30 行 / ch173（muyuan 公益）3 行
  / ch208（已禁用）1 行——**免费腿承担全部实流**。
- 付费腿 admin test：ch15 sensenova → 403 `model is not available in the
  current token plan`（该名在 sensenova 计划外，auto_ban=0 故常驻但不干活）；
  ch173 muyuan 200（公益）；ch218 opencode-go 200（1.1s）。
- 结论：该名现存全部活腿皆免费，钉 0/0 不改变任何付费计费事实。

## 变更

- 备份 `new-api-before-dsv41f-free-20261015-161205.db`（integrity ok）。
- 服务端断言原名未注册 → `PUT /api/option/` 全量 JSON 钉
  `ModelRatio[deepseek-v4.1-flash]=0`、`CompletionRatio[...]=0`；
  服务端读回 0/0；邻居 glm-5.3=0.7 / glm-5.3-flash=0.075 未动。
- OMP `models.yml` name 改标 `(opencode-go ch218 FREE p0; muyuan ch173;
  sensenova ch15 403)`。

## 门禁

- `test_omp_routes` 40/40 OK。
- `newapi-local-smoke`：unregistered-ratio 违规列表中
  `deepseek-v4.1-flash` 消失（改动前 x31 quota=52964 ratio=37.5 在册）；
  其余违规名为存量基线。

## 回滚

```bash
# 比率：PUT /api/option/ 全量 JSON 删 deepseek-v4.1-flash 键（两端）
# 整库：~/.new-api-local/backups/new-api-before-dsv41f-free-20261015-161205.db
```

## 备注

- ch218 的 p0/w2 地位不变：免费容量只在高档位失败后消耗；现在计费也是
  0，本地 quota 不再虚增。
- 若未来给该名接回真付费腿（如 sensenova 计划升级），需先把比率从 0
  提回，否则付费流量也记 0。
