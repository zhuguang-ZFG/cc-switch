# opencode-go 套餐变更与 space-bunny 改名处置（2026-10-06）

## 结论
- **套餐**：opencode.ai/zen/go 维持 Go $10/月并新增 **Go Plus $40/月**；用量限额改为 **per-model 月度美元额度**（Go $15–$60、Plus $60–$240；窗口 5h=20% / 周=50% / 月=100%）。对本栈在役模型无回收（现役 6 模型线上 `/v1/models` 全部在册；`omen-alpha` 未列入 docs 清单但线上可用）。
- **改名**：上游将 `space-bunny-free` 改名为 `space-bunny`（旧 id 返回 400 `Model is unavailable`）。官方 Go 定价表现口径：**Space Bunny $0.15/$0.60、计入 $30/月额度（不再标 Free）**——改名与"免费→计费"是否因果上游无说明，仅按观察记录（Zen 页仍列 Free 的 `space-bunny-free`，属另一档）。ch130 已改名并同步 OMP。
- **计费**：改名会丢弃 `ModelRatio` 旧 key → 新名一度按 37.5 计费。已补 `ModelRatio["space-bunny"]=0`（Go 包月零边际成本先例，2026-10-02 口径）；改名前 7 行误计费共 **2,475,076 quota（≈$4.95）**（log id 233024、233089–233095），**未擅自退还，待用户确认**。
- **兼容别名（临时桥）**：ch130 `models="space-bunny,space-bunny-free"` + `model_mapping {"space-bunny-free":"space-bunny"}`——桥接启动期缓存旧名的运行中 OMP 会话（旧名 503 自 19:10:52 起归零；19:10:57 起旧名 200 归因 ch130、quota=0）。
- **Guardian 假阳性根因修复**：通用 chat 探针对 responses-only 模型（ch131/132/137）必假失败 → `CHANNEL_TEST_PATH_OVERRIDES` 补 3 渠道（`&endpoint_type=openai-response&stream=true`，同 48/91/92/128 口径）→ ch132 由 Guardian 自身回池（3/3）、ch131 权重复原、10-05 误降记录清除。

## 时间线（证据）
| 时间 | 事件 |
|---|---|
| 10-05 09:49:54 | ch131 被 Guardian 误降权（2→1，chat 探针假失败） |
| 13:46:20 | ch130 最后一次旧名成功（oneapi 日志） |
| 13:46:42 起 | OMP advisor 旧名请求开始 400（`~/.omp/logs/http-400-requests/`） |
| 17:24:45 | ch132 被 Guardian 误禁（同根因） |
| 18:41–18:50 | 直连上游实测：`space-bunny` 200 / 旧名 400；`/v1/models` 36 个含新名不含旧名 |
| 18:52:17 | ch130 改名落地（脚本 + DB 快照 + abilities + 网关实弹 200 归因 ch130） |
| 19:03:52 | Guardian 自愈恢复 ch132（weight=2、joined pool、3/3 checks） |
| 19:10:52 | 最后一条旧名 503（18:54:04 起共 96 条，均为运行中会话 config.yml 启动期缓存） |
| 19:10:57 / 19:12:37 | 别名上线后旧名 200（quota=0）归因 ch130 |
| 19:1x | `ModelRatio["space-bunny"]=0` + 别名 PUT + `channel/fix`；两 id 网关实弹均 200 / quota=0 |

## 变更与回滚
| 项 | 内容 | 回滚 |
|---|---|---|
| NewAPI ch130 | 改名 `opencode-go-space-bunny`、models/test_model `space-bunny`、tag `limited-time-free`（19:5x 更正为 `go-included`）；随后加别名 models `space-bunny,space-bunny-free` + mapping；`ModelRatio[space-bunny]=0` | 快照 `~/.new-api-local/backups/new-api-before-opencode-go-spacebunny-20261006-185217.db`；字段级 `opencode-go-spacebunny-rename-pre-20261006-185217.json` |
| OMP | `models.yml` id `space-bunny`（1M / reasoning / compaction omen-alpha）；`config.yml` `advisor: zg-newapi/space-bunny` | `.bak-20261006-spacebunny`（models/config/test 三件） |
| `scripts/ops/test_omp_routes.py` | advisor 允许集换 `zg-newapi/space-bunny`（**验证输入变更，待用户评审建基线**） | 同上 `.bak-20261006-spacebunny` |
| `guardian.py` | `CHANNEL_TEST_PATH_OVERRIDES` += 131/132/137 | `.bak-20261006-spacebunny` |
| guardian state | ASCII 安全手术：重置 ch132 恢复字段、清 ch131 误降记录 | `state.json.bak-20261006-spacebunny`（注意：写 state.json 必须 ASCII，见下） |

执行脚本：`scripts/ops/fix_opencode_go_space_bunny_20261006.py`（dry-run 默认）。

## 已知副作用（勿追查）
- Guardian `pool_legs` 现含 `space-bunny-free: 1`（别名存在的自然结果，可能触发单腿提示；按下方流程下架别名后消失）。
- ch130 identity fingerprint 两度变更，guardian 日志 `identity changed; cleared stale recovery state`（ch130 不在恢复队列，无害）。
- `state.json` 必须以 **ASCII**（`json.dumps(st, indent=2)`）写 state.json + state.json.last-good 两份；guardian 以 cp936 默认编码读取，UTF-8 中文会致 `UnicodeDecodeError → 静默回滚 last-good`（实测 18:57 手术被回滚；corrupt 件 `state.json.corrupt-20261006185719350269-21264`）。

## 别名下架流程（用户下次重启 OMP 会话后）
1. `PUT /api/channel/`：ch130 `models="space-bunny"`、`model_mapping=""`；
2. `POST /api/channel/fix`；
3. 确认 `space-bunny-free` ability 行消失、`pool_legs` 不再含旧名。

## 验证
- 探针面（打补丁前先验证）：`/api/channel/test/{131,132,137}?...&endpoint_type=openai-response&stream=true` 均 success；重启后 ch132 测试日志 `RelayFormat: openai_responses`，18:54:07 后无 13x 协议错误。
- ch130：改名后与别名后两轮网关实弹均 200 归因 ch130；abilities 两 id 均 `(1,0,5)`。
- 旧名 503：96 条（18:54:04–19:10:52）→ 别名后 0 条；旧名 19:10:57 起 200 / quota=0。
- Guardian：ch132 回池 `stability_checks=3 fails=0`；ch131/ch132 均 `(1,2)` 且误降记录已清。
- OMP：`omp -p --model zg-newapi/space-bunny` → `BUNNY_OK`；`test_omp_routes.py` 12 passed。
- smoke gate：FAIL 项均为在办其它事项（ch72 隔离、ch45 已删、ch3/9/18 opus 姿态、ch149 零输出流），非本变更面。

## 遗留
- 旧会话彻底切新名需重启 OMP（config.yml 启动期缓存；别名已保证期间可用）。
- 误计费 2,475,076 quota（≈$4.95）：待用户确认是否退还。
- 官方 Go 定价表已更新为计费口径（$0.15/$0.60，计入 $30/月额度；不再标 Free / limited time）——"限时免费"表述已在 ch130 tag（`go-included`）与 models.yml name/注释更正；`ModelRatio=0` 在订阅制下仍正确（该表为订阅内额度速率，非额外扣费）。
