# api.abnt.it Claude 渠道接入（2026-10-08，fail-closed）

## 连接

用户提供 `https://api.abnt.it` + key（仅经 `ABNT_KEY` env 传入，不落盘不回显）。
站点为 NewAPI 系（400 错误体带 request id、"manual Claude thinking" 校验语，与
muyuan/42x 同款）。

## 实测面（浏览器 UA 必带：CF 1010 仅 ban python-urllib UA）

- `GET /v1/models` → 200，7 模型：claude-opus-4-8 / 4-7 / 4-6 / 4-5-20251101、
  claude-sonnet-4-5-20250929、claude-haiku-4-5-20251001、`-haiku-4-5-...-thinking`。
- chat 全模型 ×3 轮 + `/v1/messages` + tools 探针 → **确定性 502**（CF 风格纯文本
  错误体、~1s 即返）；`-thinking` 在校验层 400（max_tokens 须 >1024），提额后同 502。
- 结论：控制面活、推理渠道池死。与 ch172 42x.shop 同型（fail-closed 先例）。

## 处置

`scripts/ops/add_abnt_claude_channel.py`（dry-run 默认）→ **ch183 `abnt-claude`**
落库：type=1，7 模型 exact-id，group=default，p20/w1，**status=2 + auto_ban=1**
（禁用态注册、abilities enabled=0、不接客）。DB 快照
`new-api-before-abnt-claude-20261008-194838.db`（35,676,160 B, integrity ok）。
验证：DB readback status=2；abilities 7 行全 enabled=0；smoke `unexpected_disabled=none`。

启用流程（上游复测后）：先带浏览器 UA 直连 chat 复测 200，再
`POST /api/channel/183/status {"status":1}`（admin 头）。注意 claude-opus-4-8 等与
zg-newapi-anthropic 池（3003）模型重叠，启用=加入 claude 池分流，启用前按 42x 同款
流程做网关端到端复测。

## 附带缺陷修复（本次发现并已修）

fork 渠道列表 API 每页**硬顶 100**（`page_size=200/1000` 被钳制）且 `p=0` 是特例页
（与 p=1 同集合）；渠道总数已超 100（现 104）后，原 `p=0&page_size=200` 单页抓取
静默丢 4 渠道（本次为 168/169/170/179），enabled 计数失真（34 报 33）。

- `newapi-local-smoke.py`：改为 p=1 起翻页、total 对账；拉不满即 status=999 →
  channels 检查 FAIL（不静默绿）。变异实证：模拟第二页截断 → `FAIL channels
  bad response: HTTP 999`；复原后 `total=104 enabled=34 OK`。
- `add_abnt_claude_channel.py`：fetch_channels 同款翻页（按名去重可见全量；
  复跑正确报 `already exists (id=183)`）。
- 存量 add_* 脚本仍用单页抓取，>100 渠道后按名去重有同样漏判风险，后续新接入
  一律复制本版 fetch_channels。

## 回归

`test_omp_routes` 40/40 OK；smoke FAIL 集合与存量基线一致
（channel model isolation / fallback channel posture / primary opus pool posture），零新增。
