# obitmc-relay 渠道接入（ch171：共享免费 27B 备份位）（2026-10-04）

## 结论

用户提供论坛共享连接信息（`relay.obitmc.com`，楼主自报并发限制）。新建 **ch171
`obitmc-relay`**（type=1），**纯备份姿态 p20/w1**，正典 `qwen3-8-27b` 经
`→unsloth/Qwen3.8-27B-GGUF` 映射接入，abilities 读回一致，admin 渠道自测 200
（日志 223847 归因 ch171），链健康 200 归因 ch124 主腿未抢流量，政策门禁
FAIL 组与 19:25 计划运行逐字节相同 = 零新增违规。OMP/models.yml 零改动。

## 上游实证（只读探测，key 经 base64 解码仅过内存，不落盘不回显）

- 帖子首版 base64 **截断**（93 字符，93 mod 4 = 1 不可能合法）；用户补发完整
  96 字符 → `sk-live-`+64hex。截断部分未做爆破猜测（越权，拒）。
- **CF 1010 坑**：裸 python-urllib UA 403 `error code: 1010`（有无 auth 同码
  → 边缘拦截非鉴权失败）；curl 默认 UA 200、浏览器 UA 200。NewAPI Go 客户端
  由 admin 渠道测试实证通过（见下）。
- **格式**：仅 OpenAI chat（`/v1/messages` 404 "No such endpoint on this
  relay"），不可入 Claude 系路由。
- **reasoning 坑**：`unsloth/Qwen3.8-27B-GGUF` 返回 `reasoning_content`；
  `max_tokens=16` 时预算全耗在思考上 `content=null`（finish=length 假象）。
  探测/判活须给足 max_tokens（≥48）。
- `internal/qwen3.8-27b-nie`：首调 503 `first_chunk_timeout`（14s 中继截断），
  60s 长超时重试 200（3.6s）= **冷启动非死**。**未入列**（同中继同类模型，
  v1 单映射减半 guardian 探针负载；并发限制未压测，按政策不打压测）。

## 变更

执行脚本：`scripts/ops/add_obitmc_relay_channel.py`（key 走 `OBITMC_KEY`
env，dry-run 默认，幂等 resume：配置一致复用、漂移拒绝）。

| 项 | 值 |
|---|---|
| 渠道 | ch171 `obitmc-relay`（type=1，base `https://relay.obitmc.com` 不带 /v1） |
| 模型 | `qwen3-8-27b` exact-id 正典 |
| 映射 | `{"qwen3-8-27b": "unsloth/Qwen3.8-27B-GGUF"}` |
| 姿态 | **priority 20 / weight 1**（池在营 ch124 p50、ch88 p49、ch140-143 p40
  全 w1 → 在营最小 40，备份严格低于；≤50 政策满足）；auto_ban=1，
  test_model=qwen3-8-27b |
| abilities | `(default,1,20,1)` 读回一致 |
| 定价 | 只读对账未改写：qwen3-8-27b 无 ModelRatio 条目，走网关默认倍率，
  与现有承运渠道平价 |
| DB 快照 | `backups/new-api-before-obitmc-relay-20261004-192803.db`
  （16,601,088 bytes，integrity=ok） |
| 池深变化 | qwen3-8-27b 在营 6→7 |
| OMP/models.yml | **零改动**（exact-id 透明冗余） |

## 开发过程教训（留痕）

1. **edit 自动修复吞 def 行**：对 `fetch_channels` 的 PUT 触发 auto-repair
   去重时删掉了 def 行，孤儿缩进块留在 `online_backup` 尾部 `return` 之后
   ——**py_compile 通过**（合法不可达代码），运行时才 NameError。对策已执行：
   import 级结构断言（callable 全套 + 源内无孤儿引用）后再 --apply。
2. **git-bash `VAR=$(…)` 不 export**：子进程 `os.environ` 拿不到，首跑
   `OBITMC_KEY env var required`。须 `export VAR=$(…)`。
3. **凭猜补全省略代码必错**：`http_json()` 无 `params` kwarg（分页走 URL
   query），模板省略段不得臆造——先看源再写。

## 验证

- admin 渠道测试：HTTP 200 success，4.603s，日志 `(223847, ch171,
  qwen3-8-27b, 11/16 tokens)` —— **Go 路径 CF 门实证通过**，映射生效。
- 链健康：网关 qwen3-8-27b chat 200（17/2），日志 223848 归因 **ch124**
  （p50 主腿）——ch171 未抢流量，备份姿态行为级确认。（脚本 30s 轮询窗口内
  日志未落盘报 chNone，事后 DB 直查闭环。）
- 政策门禁 `newapi-local-smoke.py`：FAIL 组 `channels / channel model
  isolation / primary opus pool posture` 与 19:25:02 计划运行**逐字节相同**
  = 存量（ch3/9 禁用、ch72 anyrouter 隔离、opus 池姿态），零新增。
- abilities 前后对账：ch171 单行 `(default,1,20,1)`，其余池行不变。

## 风险与备忘

1. **语义分叉**：正典 `qwen3-8-27b` 在 models.yml 声明 `reasoning: false`
   （ch88 腿行为），而本腿发 `reasoning_content`。备份位可接受（OMP 正常
   max_tokens 下 content 照常完成）；若未来提拔为主力或直挂 models.yml，
   条目须改 `reasoning: true` + thinking 配置，否则小 max_tokens 调用方
   会看到空 content。
2. **共享免费渠道**：楼主自报并发限制，配额/稳定性/留存未背书 → 纯备份位
   正合适。观察 logs 中 ch171 出现频率，常态服役 = 主池退化信号（查主池，
   勿提拔 ch171）。
3. **guardian 交互**：reasoning 模型 + guardian 探针若 max_tokens 过小可能
   content=null 假失败；guardian 有 `probe-incompatible, skipped` 机制兜底。
   观察点：下次 full scan 后 guardian.log 中 ch171 是否出现
   probe-incompatible 或 auto-ban 事件。
4. 凭据经论坛明文公开（楼主主动共享），多人共用，key 随时可能被撤。
5. **独立存量事件（非本次引入）**：opus 主渠道 ch3 `baibei-100xlabs`、
   ch9 `linxi-k40` 今日 15:25–19:25 之间被禁（19:25 计划运行首现
   `channels` FAIL 组），opus 池姿态 FAIL 持续中——与本渠道无关，另行排查。

## 回滚

禁用 ch171（channels.status=2 + abilities.enabled=0 双表，或删渠道后
POST /api/channel/fix）；或还原
`backups/new-api-before-obitmc-relay-20261004-192803.db`（会同时回滚
19:28 之后的所有变更）。OMP 零改动无需回滚。
