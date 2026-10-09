# deepseek-v4-flash 聚合恢复 + ch110 封号排除 — 2026-09-13

## 背景

用户询问 deepseek-v4-flash 聚合状态。该模型是 OMP 大多数 role 的
compactionModel（`add_deepseek_v4_flash_pool.py` docstring），高频调用。
8 月建池设计三档：ch15 sensenova（p50）+ ch118 seeseed 专用渠道（p30/w5）
+ ch110 yjs-free（p6 兜底）。查库时聚合已塌缩为 ch15 单渠道。

## ch118 恢复（22:34）

- 恢复前直探 `test/118?model=deepseek-v4-flash`：**ok**。
- 脚本 `~/.new-api-local/tmp-enable118.py`：全库备份
  （`new-api-before-hashneuron-20260913-223305.db`）→ POST status=1 →
  全对象 PUT（真 key 显式回填，GET masked）sync abilities → 75s 缓存等待 →
  严格 readback（status=1、ability=1）→ 探针。
- 恢复后首探 403（GBK 鉴权类文案），连探 3 次全 ok——共享 key（与 ch89
  同源 `sk-tBf…`）间歇抖动，非配置问题。
- 现聚合：ch15（p50/w1）+ ch118（p30/w2）。

## ch110 定性封号并排除（22:38）

- 直探：`403 User has been banned` —— 上游 yjs-free 账号封禁，
  恢复循环已空转 123 次（`state.json` 实证），本地无复活路径。
- 处置：`guardian.py` `AUTO_BAN_RECOVERY_EXCLUSIONS` 加入 110（注释留
  "上游解封需手工 enable" 口子）。repo 双端同步，195 用例全绿，
  引擎重启（PID 28820）。
- 备用提问：ch15 权重仅 1，ch118 恢复后按 p/w 自然分流即可；如后续
  ch15 容量不足再调。

## 验证

- 探针 ch118 ok ×3；Guardian 引擎重启后单实例，日志循环正常。
