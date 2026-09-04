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

## 变更（最终态）

- ch111 models 5→2：`mimo-v2.5,hy3`。摘：mimo-v2.5-pro（503 陈旧）、
  hy4-preview + minimax-m2.7（余额门槛）、deepseek-v4-flash +
  deepseek-v4-flash-vision-exp（实测扣费）。p30/w5/status/key 不动。
- ModelRatio：删池专属键（mimo-v2.5-pro、hy4-preview、minimax-m2.7）。
  **deepseek-v4-flash 全局 0.5 比率保留**——ch110/ch118 同样在服务
  该模型，比率是共享的（脚本以此区分 REMOVE_MODELS 与
  POOL_ONLY_RATIO_MODELS）；vision-exp 无比率键。
  mimo-v2.5=0、hy3=0.5 历史值不动。
- 封闭式 bai 单模型渠道不动：ch113 qwen3.8-27b、ch121 glm-5.3-flash、
  ch122 qwen3.8-flash（探活 OK，但**免费性未过哨兵验证**——后续若
  出现余额异常优先怀疑它们）。

## 验证（独立 DB 回读 + relay）

- ch111=`mimo-v2.5,hy3` p30/w5 status=1；5 个摘除模型 abilities 零残留；
  池专属比率键全消失；`deepseek-v4-flash=0.5` 仍在，ch110/ch118
  abilities enabled 不变。
- 3002 relay：mimo-v2.5 200 出文 OK、hy3 200 出文 OK、
  deepseek-v4-flash 200（走 ch110/ch118，计费不受本次影响）。
- 整库快照：`new-api-before-bai-free-sync-20260904-194802.db`
  （154,865,664 B，integrity=ok）；失败全量回滚；幂等
  （重跑 verify-only no-op）。

**回加条件（任何被摘模型）**：b.ai 充值 + 新一轮直连 200 +
哨兵消耗检查（探针前后余额不变），三者缺一不可。

脚本：`scripts/ops/sync_bai_free_models.py`。
