# agentrouter 上新接入（2026-10-15 晚）：gpt-6.1-sol + claude-opus-5-5

用户转来群公告：「gpt-6.1-sol（推荐使用）、claude-opus-5-5 已上线，同时增加了
资源投放量」。

## 上游证据（管理探针经 NewAPI；key 从 SSOT 进程内读，不落日志）

- `ch180`（agentrouter-deepseek-v4-flash）对 `gpt-6.1-sol` → **200**；
- `ch86` 对 `claude-opus-5-5`：429（并发限流）→ 500 → **200**（间隔重试）；
- ch127（agentrouter-codex-gpt）的 key 上游已失效（401 invalid api key）→ 不回开；
- 8788 代理的 keys.json 全部失效（401）——需轮换；
- ch86/134/135/136 的上游池已不再有 `claude-opus-5`（503 无可用渠道）——只剩
  `-5-5`。

## 变更（快照 `new-api-before-agentrouter-newmodels-20261015-212818.db`）

- `ch180` models += `gpt-6.1-sol`（p51，优于余额近空的 jojatoken p-20）；
- `ch86/134/135/136` models += `claude-opus-5-5`、`test_model` 改指该名、
  **回开**（status=1）→ 恢复 OMP 已注册但唯一活腿 ch3（超时）死掉的
  `claude-opus-5-5`；
- readback：4 渠道 status=1 + 各自 ability enabled=1 ✓。

## 网关实弹

- `gpt-6.1-sol` → **200，落 ch180**；它是推理模型，小 max_tokens 探测全喂
  reasoning（content 空属探测预算坑，非故障）；
- `claude-opus-5-5` → **200**（37.3s，落 **ch135**）；其间 120s 超时 ×2 是
  队列拥塞时段，非不可用。

## 遗留

- 8788 代理 keys.json 失效（401）→ 需轮换（影响所有走 8788 的直连路径）；
- ch127 key 上游失效（401）→ 待新 key 再回开；
- ch86-136 仍挂 `claude-opus-5`/`opus-4-8`（上游池已无该名）——若上游彻底下架，
  按聚合裁剪那套摘掉；
- OMP 重启后生效（config/扩展），渠道改动即时生效。

## 回滚

```bash
# 整库：cp 备份（new-api-before-agentrouter-newmodels-20261015-212818.db，停机）
# 逐渠道：models 摘除 + status 回 2（runbook 契约）
```
