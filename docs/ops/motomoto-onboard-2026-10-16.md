# MotoMoto（motomoto.lol）接入：park 入册（2026-10-16）

用户投递 `newapi_channel_conn`（`https://motomoto.lol` + 一枚 key）。按流程先探上游、
再决定入册形态。结论：**上游 `/v1/*` 处于公告级维护中（无恢复时间表）→ 渠道以
`status=2 + auto_ban=1` 入册（ch225），key 存 SSOT，等公告恢复再开**。

## 上游证据（2026-10-16 14:45-14:48 CST）

| 探针 | 结果 |
|---|---|
| `GET /` | 200，`MotoMoto · 公益 API`；`GET /api/status` 200（`v1.0.0-rc.31-motomoto.49`，41 条公告） |
| `GET /v1/models`（带/不带 key，直连/经 7897） | **503 `{"message":"维护中，请稍后再试","type":"new_api_error"}`**（3 次复探一致） |
| `POST /v1/chat/completions`（带 key） | 同上 503 —— 站点级维护，非请求门 |
| `GET /api/pricing`（公开） | 4 个 id：`codex-auto-review` / `gpt-5.5` / `gpt-5.6-sol` / `gpt-6-astra`（全 openai 端点，default 组） |

公告 #41（2026-10-10，warning，extra=大维护）原文：「接口现在暂停，正在做一次大维护，
暂时无法调用。**这次没有确定的恢复时间**。恢复后会再发公告。」公告 #40 另示：站方对
违规使用直接封号——将来开腿后须保守使用。

因 `/v1/*` 全 503，**key 目前无法完成鉴权**；入册即预置，等恢复后先探活再启用。

## 变更（快照 `newapi-before-motomoto-welfare-20261016-144743.db`，70.4 MB）

- 新渠道 **ch225 `motomoto-welfare`**：type=1（OpenAI 兼容）、base_url
  `https://motomoto.lol`、4 models（上表）、group `default`、priority 10 / weight 1、
  test_model `deepseek-v4.1-flash`、**status=2 + auto_ban=1**。
- 选址逻辑：p10/w1 与同类公益腿（ch223 clinepass-welfare-1013）一致；`auto_ban=1`
  + 手工 `status=2` → Guardian 只导入 `status=3 && auto_ban=1`，此行为惰性，**不会
  产生探测抖动**。
- ModelRatio 未动：4 个 id 本地均已由他渠服务（gpt-6-astra 缺 ratio 属既有待裁项，
  与本渠道无关）；ch225 停用期间无计费面。

## 验证

- 读回：`st=2 auto_ban=1 p10/w1 group=default`、4 models、abilities 4 行 **enabled=0**；
- 无回归实弹：`deepseek-v4.1-flash` 经网关 **`max_tokens=64` → content=`'OK'`（1.5s）**
  ✓；补记：最初 `max_tokens=8` 那次 200 的空 content 是 **reasoning 占满推理预算**的
  假象（`reasoning_tokens=8`，content 空），非"空流"故障——按 house 口径，单独一个
  200/空流都不足为证，故此处以放量后的非空文本为准；
- 烟测（14:48）：ch225 未引入任何新 FAIL 类；`multi-key pool health` OK、
  `intentional channel disables` OK。**FAIL 基线 4→5 类**（10-15 晚 4 类 → 今日 5 类），
  新增的第 5 类 `pool capacity claude-opus-4-8` 与 ch225 无关（ch225 的 4 个 id 不含
  claude-opus-4-8，abilities enabled=0），系 agentrouter claude 池被上游 402 预算
  park 所致——见 `agentrouter-newmodels-2026-10-15.md` 追记。

## UA/出口判别（补做，复审意见）

「全站维护」结论原先只经单一 `curl/8.5.0` UA + 名义直连验证，"裸 UA 被中性文案挡"
这一 house 先例（agentrouter 401）未被排除。补做判别轮：

| UA | `/v1/models` |
|---|---|
| `curl/8.5.0` | 503 维护中（0.5s） |
| Chrome 浏览器 | 503（0.5s） |
| `claude-cli/1.0.43` | 503（0.5s） |
| `OpenAI/Python 1.40.0` | 503（1.2s） |
| `codex_cli_rs/0.9.0` | 503（0.5s） |

→ **UA 门假设证伪**（五种 UA 同文案同速度）。第二出口未取得：`ssh 100.83.32.95`
无免密凭据（Permission denied），即"直连 vs 7897"仍同经本机 Clash——此限制如实记录，
但结合公告 #41（站方自述暂停）+ 控制面 200，维护结论成立；若日后想再排除出口因素，
可从 VPS 侧复探。

## 附带发现（与本渠道无关，供裁决）

- **`codex-auto-review` 与 `gpt-5.5` 本地当前 0 条活腿**（承运渠道 ch70/175/193/194/206
  全 park）——两 id 经网关 503 `model_not_found`。既有缺口，未处置。
- 昨夜回开的 agentrouter claude 池（ch86/134/135/136）已因上游 **402 预算池尽**被
  看护按序 park，连带 `pool capacity claude-opus-4-8` 降到 1<2（冒烟第 5 类 FAIL）。
  详见 `agentrouter-newmodels-2026-10-15.md` 追记节。

## 再启用门（三者同时满足）

1. 公告 #41 被恢复公告取代（查 `/api/status` announcements）；
2. `GET /v1/models` 带本 channel key 返回 200（目录须与 `/api/pricing` 一致）；
3. 管理探针 `POST /api/channel/225/test?model=gpt-5.5` 成功。

然后 `POST /api/channel/225/status {"status":1}`。启用前按待裁清单决定 4 个 id 的
ModelRatio（尤其 gpt-6-astra）。

## 回滚

```bash
# 停用（若已开）
curl -s -X POST "$NEWAPI/api/channel/225/status" -H "Authorization: Bearer $T" \
  -H "New-Api-User: $U" -H 'Content-Type: application/json' -d '{"status":2}'
# 删除渠道：管理台删除 ch225，或从快照恢复
# D:/zhugu-home/home/.new-api-local/backups/newapi-before-motomoto-welfare-20261016-144743.db
```

脚本：`scripts/ops/add_motomoto_welfare_channel.py`（幂等：存在即 verify-only；
key 走 env `MOTOMOTO_KEY`，不入仓）。
