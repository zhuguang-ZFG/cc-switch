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

- 备份 `…dsv41f-free-20261015-161205.db`（integrity ok；**该快照已被 supervisor 轮转回收（KEEP=7 只留最新；见 `backup-rotation-lexicographic-bug-2026-10-16.md`）**，该份于 10-16 15:03 的修复后轮转中被回收）。
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
# 整库：改前快照已轮转回收；本变更可**逐字段**回滚（上方 PUT 步骤删掉两个键即可，
#       无快照依赖）。最近的已验证整库快照：newapi-before-motomoto-welfare-20261016-144743.db、
#       new-api-2026-10-16.db（均含此变更后的状态，不能回到改前）
```

## 备注

- ch218 的 p0/w2 地位不变：免费容量只在高档位失败后消耗；现在计费也是
  0，本地 quota 不再虚增。
- 若未来给该名接回真付费腿（如 sensenova 计划升级），需先把比率从 0
  提回，否则付费流量也记 0。

## 能力实测（2026-10-15，用户要求核上下文/思考档位；均对 ch218 上游直连实测）

- **contextWindow 262144 → 1048576（1M）**：阶梯探测 59.6K/254.5K/272.7K/
  345.4K/527.3K/600.0K/745.4K/1045.4K in 全部 200（末尾应答 'fox' 正确），
  1274.8K → 400 明报 `maximum context length is 1048576`。原声明 256K
  低估 4 倍。
- **maxTokens 131072 → 200000**（用户指示）：上游接受 max_tokens=200000
  （finish=stop，参数面 ≥200K；实际生成上限未测，免费腿不值得烧）。
- **thinking efforts low/medium/high/xhigh/max 五档全 200**，reasoning_tok
  随档位递增（16→27）——与 muyuan ch173 拒档不同，opencode-go 全档生效，
  声明原样正确。
- 生效条件不变：models.yml 改动待下次 OMP 重启。
