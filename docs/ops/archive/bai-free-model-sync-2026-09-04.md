# ch111 bai-free 免费池纯化（2026-09-04）

用户要求对齐 b.ai 上游免费目录并**剔除不免费模型**。最终 ch111 只保留
哨兵法验证零消耗的真免费模型：`mimo-v2.5,hy3`。

## 探测分类（首轮，`/v1/models` 47 模型逐个 chat 探测）

- **200 OK**：mimo-v2.5、hy3、deepseek-v4-flash、
  deepseek-v4-flash-vision-exp、glm-5.3-flash、qwen3.8-27b、qwen3.8-flash、
  hy4-preview、minimax-m2.7。
- **503 "no available channel under group mi"**：mimo-v2.5-pro → 陈旧，摘。
- **403 "Deposit required"**：claude-*/gemini-*/gpt-*/kimi-*、
  deepseek-v4-pro、glm-5.1/5.2、minimax-m3、qwen3.8-max → 付费档。
- **仅 429（未见 200，不盲收）**：glm-5.3、gpt-5.6-luna。

## 核心教训：200 OK ≠ 免费

首轮入池后数小时内暴露两种"假免费"，全部由**哨兵法**抓出
（hy4-preview 必 400 且报实时账户余额，用它夹逼每个模型：探模型 →
再探 hy4 读余额差）：

1. **余额门槛型**：hy4-preview（要求 ≥5010，账户 ~2600）、
   minimax-m2.7（余额 ~2500+ 时 200，跌到 2362 后每请求 400）——
   余额在门槛上方时正常出文，跌破后全部 400。首轮 200 只是
   当时余额尚可。
2. **隐性扣费型**：deepseek-v4-flash（小探针每次 **-9** quota）、
   deepseek-v4-flash-vision-exp（**-30**）——余额实测递减。真实流量
   会抽干 b.ai 账户，届时门槛/余额归零连真免费模型一起打死。

哨兵验证零消耗：mimo-v2.5（2401→2401）、hy3（2401→2401）。
方法局限（诚实记录）：上游 `/api/pricing`（NewAPI fork 的权威
model_ratio/group_ratio 源）对裸 GET/Bearer/浏览器 UA 均回 403 面板级
门控，比率无法直接核验；余额差哨兵对上游异步 post-consume/预扣退款的
归因存在理论上的滞后混淆窗口（mimo/hy3 前后窗口 delta 精确为 0，
两个扣费窗分别 -9/-30，异步滞后需恰好落在扣费窗内才能解释，但未获
权威源佐证）。摘除决策按非对称风险处理：deepseek-v4-flash 本就由
ch110/ch118 服务，摘 ch111 仅失去冗余备份源；若误摘（实际免费），
回加条件（充值+直连200+哨兵零消耗）可低成本恢复。minimax/hy4 归类
为门槛型（400 响应自带 required 值，服务端计价直接证据）。

## 变更（最终态）

- ch111 models 5→2：`mimo-v2.5,hy3`。摘：mimo-v2.5-pro（503 陈旧）、
  hy4-preview + minimax-m2.7（余额门槛）、deepseek-v4-flash +
  deepseek-v4-flash-vision-exp（实测扣费）。p30/w5/status/key 不动。
- ModelRatio：删池专属键（mimo-v2.5-pro、hy4-preview、minimax-m2.7）。
  **deepseek-v4-flash 全局 0.5 比率保留**——ch110/ch118 同样在服务
  该模型，比率是共享的（脚本以此区分 REMOVE_MODELS 与
  POOL_ONLY_RATIO_MODELS）；vision-exp 无比率键。
  mimo-v2.5=0、hy3=0.5 历史值不动。
- OMP 侧清死条目（同日追补）：`~/.omp/agent/models.yml` zg-newapi 摘
  `mimo-v2.5-pro`、`deepseek-v4-flash-vision-exp`（abilities 已零渠道，
  选中即 no available channel；config.yml 零引用，YAML 回读 52 模型
  可解析）。备份 `models.yml.bak-20260904-bai-dead-entries`；按 OMP
  进程启动加载惯例，重启生效。
- 封闭式 bai 单模型渠道哨兵复验（同日，余额基线 2362）：
  **ch121 glm-5.3-flash、ch122 qwen3.8-flash 均哨兵验证真免费**
  （200 出文，探针前后余额 2362→2362 零消耗），保持启用；
  **ch113 上游 qwen3.8-27b 实证门槛型非免费**
  （400 `balance=2362 required=3202`，`code=insufficient_user_quota`）
  ——ch113 此前已禁用（status=2，abilities enabled=0），**保持禁用**；
  该模型对外覆盖由 ch88/ch112/ch124 继续（ch113 映射
  `qwen3-8-27b→qwen3.8-27b`，非 bai 独源）。ch113 回加条件与其他
  被摘模型一致（充值+直连200+哨兵零消耗）。
- **test_model 修正（同日追补）**：纯化后 test_model 残留
  `deepseek-v4-flash`（auto_ban=1）——该模型已不在 ch111 models，
  Guardian 错误扫描（每 5 分钟 test_channel）与 NewAPI 自测每轮必失败，
  3 次软失败即隔离整个免费池。已改 `mimo-v2.5`（API PUT 快照
  `new-api-before-ch111-testmodel-20260904-195757.db` + 回读 + 管理探针
  200），models/abilities/status/p30/w5 原样。脚本已加 **test_model
  自愈**：`sync_bai_free_models.py` 摘除模型若命中 test_model，同一
  PUT 改指首个存活模型；`verify()` 断言 test_model ∈ 存活列表；全库
  校验确认无其他启用渠道存在同类违规。

## 验证（独立 DB 回读 + relay）

- ch111 models=`mimo-v2.5,hy3` p30/w5 status=1；5 个摘除模型 abilities
  零残留；池专属比率键全消失；`deepseek-v4-flash=0.5` 仍在，ch110/ch118
  abilities enabled 不变。
- test_model 修正回读：`mimo-v2.5` 管理探针 200；models/status/p30/w5/
  abilities 原样；全库启用渠道 test_model ∈ models 校验通过；
  脚本 `verify()` 正/负用例通过（错值 test_model 正确 raise）、
  dry-run 幂等。
- 3002 relay：mimo-v2.5 200 出文 OK、hy3 200 出文 OK、
  deepseek-v4-flash 200（走 ch110/ch118，计费不受本次影响）。
- 纯化后当前余额（2362）下哨兵复验：mimo-v2.5、hy3 relay 200 出文，
  直连哨兵余额 2362→2362（delta=0）——真免费结论在当前余额成立。
  deepseek-v4-flash 现路由形态：ch118 p30/w3 主 + ch110 p6/w5 备 +
  ch15 p50 禁用（比率 0.5 全局共享）。
- 整库快照：`new-api-before-bai-free-sync-20260904-194802.db`
  （154,865,664 B，integrity=ok）；失败全量回滚；幂等
  （重跑 verify-only no-op）。

**回加条件（任何被摘模型）**：b.ai 充值 + 新一轮直连 200 +
哨兵消耗检查（探针前后余额不变），三者缺一不可。

脚本：`scripts/ops/sync_bai_free_models.py`。
