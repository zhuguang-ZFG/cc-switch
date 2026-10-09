# jojatoken 四 key 池 + longai 聚合接入（2026-10-09）

用户供给 5 个上游连接（4×jojatoken.com + 1×llm.longai.vip，后者 key 有效期
2026-10-20），要求**聚合进现有池**而非独立停泊层。两个上游都是 NewAPI 类聚合站。

## ch188-191 jojatoken（四 key 单 key 渠道池）

- 上游 `https://jojatoken.com`，type=1，**8 模型 exact-id 全量透传**（gpt-5.5 /
  gpt-5.6-sol / gpt-5.6-terra / gpt-5.6-luna / gpt-6-sol / gpt-6-luna /
  gpt-6.1-sol / gpt-6-astra），**p-20/w1 ×4，auto_ban=1**，test_model=gpt-5.6-sol
- 池位：astra 居 asvla ch182 (p20) 之下；terra/luna 居 grok-heavy ch174 (p-10)
  之下；5.5/sol/6.1-sol 与 hubway ch175 (p-20) 1:1 聚合；**gpt-6-luna /
  gpt-6-sol 接入前零活跃渠道，四 key 池为其唯一在营来源**
- 实测：8/8 chat 200（~300 prompt token 上游注入开销）、astra tool_calls 200、
  SSE 200、`/v1/responses` 200（渠道注册为 type=1 chat，与池内其它渠道一致）
- **上游有反探测风控**：短输入探针触发 400
  `Upstream rejected illegal short-input distillation or heartbeat probing`
  （gpt-5.6-terra 实录），连续探测触发 429 `Upstream rate limit exceeded`
  （gpt-6.1-sol 实录）。**影响**：Guardian 错误扫描/管理探针若用短句高频探测
  可能假阳性——`add_jojatoken_channel.py` 的 relay 探针已改自然长度 prompt +
  4s 节流 + 429/5xx/反探测 400/网络超时有界重试（4×15s backoff）
- 定价缺口（只读未改写）：`gpt-6-sol` / `gpt-6.1-sol` / `gpt-6-astra` 无
  ModelRatio，走网关默认倍率（ch175/ch182 既有现状）；`gpt-5.6-luna`/`gpt-6-luna`
  ratio=0
- 前两次 apply 因上述上游风控回滚（合约性回滚，渠道保留禁用态），`--resume`
  模式三次恢复探针+启用；最终 8/8 relay + 严格回读（channel 字段 + abilities×8）
  全过
- 快照：`new-api-before-jojatoken-20261009-163230.db`（首跑）、
  `-163528.db`、`-163916.db`、`-164310.db`（成功跑），integrity=ok
- 回滚：逐渠道 `POST /api/channel/{id}/status {"status":2}`；整库回滚用快照

## ch192 longai（单 key，2026-10-20 到期）

- 上游 `https://llm.longai.vip`（自身为 NewAPI 实例），type=1，**7 模型**
  glm-5.2 / glm-5.3 / glm-5.3-flash / deepseek-v4-pro / kimi-k2.6 /
  kimi-k2.7-code / qwen3.8-max，**p19/w1，auto_ban=1**，test_model=glm-5.3-flash
- 池位：glm-5.3 居 intern-discovery ch140-146 (p40×5) 之下；glm-5.2 居
  ch173 (p20，当晚禁用，见下) 之下；**glm-5.3-flash / deepseek-v4-pro /
  kimi-k2.6 / kimi-k2.7-code / qwen3.8-max 接入前零活跃渠道，ch192 为唯一在营**
- 排除 `qwen3.8-flash`：上游自身 distributor 两次 503 `all selected channels
  are cooling down`，且本地无 ModelRatio（hashneuron/seeseed 只收录实测存活
  模型先例）；上游恢复+补价后可 `--extend` 加入
- glm-5.3 实测首响 ~60s（90s 首探超时、240s 复测 59.8s 200）——relay 预算
  240s；刻意不用它做 test_model；若持续 >60s 由 Guardian 慢渠道检测降权
- 定价：7 模型全部已有 ModelRatio（glm-5.2=2、glm-5.3=0.7、glm-5.3-flash=0.075、
  deepseek-v4-pro/kimi-k2.6/kimi-k2.7-code/qwen3.8-max=0.5），未改写
- 快照：`new-api-before-longai-20261009-165555.db`、`-170234.db`、
  `-171119.db`（成功跑）
- **key 2026-10-20 13:13 到期**：到期后渠道 401，auto_ban/Guardian 会禁用；
  续期 key 后用 `--resume --apply` 恢复

## ch173 muyuan-gongyi 禁用（用户授权）

- longai 接入的 glm-5.2 relay 探针 401 `Invalid token`，判别诊断：deepseek-v4-pro
  （仅 ch192 可路由）得本地 503 路由错误 ⇒ 本地 OMP token 有效；glm-5.2 归因
  ch173；ch173 自身管理探针直连上游 401 复现 ⇒ **上游 key 失效**（两小时内
  从 200 翻转，公益站 key 轮换）
- 用户选择禁用：`POST /api/channel/173/status {"status":2}`（success=true）。
  glm-5.2 由 ch192 (p19) 接管
- 恢复：muyuan.do 控制台刷新 key 后更新渠道 key，再 `POST status=1`
- 注：本机 `AutomaticDisableStatusCodes` 含 401，但 auto-ban 未即时禁用
  ch173（依赖扫描周期）；Guardian 错误扫描预计会独立发现

## 脚本合约（本次新增/硬化）

- `scripts/ops/add_jojatoken_channel.py` / `add_longai_channel.py`：
  `--keys-file`（key 不入 argv/仓）、`--apply`、`--resume`（回滚后恢复：对
  存在但禁用的渠道重探针→通过才启用）、dup 检查、整库在线快照、创建即禁用
  →管理探针→启用→75s 缓存同步→relay 探针→严格回读
- relay 探针硬化（两脚本同）：自然长度 prompt、模型间 4s 节流、429/5xx/
  反探测 400/`TimeoutError`/`OSError`（socket 超时会从 `http_json` 裸抛，
  `http_json` 只捕 `HTTPError`）一律瞬态重试 4×15s
- 归因边界：池级 relay 探针不能证明被更高优先级渠道遮蔽的新渠道；渠道证明
  =管理探针 + abilities 回读，池证明 = relay 200（intern-discovery 先例）

## 回归

- 两脚本 dry-run 均验证 plan/去重/掩码；错误路径（created id 漂移、探针失败、
  relay 失败）实测均走禁用回滚并保留快照（jojatoken 首两跑、longai 前两跑）
- 最终姿态：ch188-191 status=1 p-20/w1；ch192 status=1 p19/w1；ch173 status=2
  （用户授权）；abilities 全部 enabled 与 status 一致
