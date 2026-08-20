# tabitoken 多 key 拆分为单 key 渠道（2026-08-20）

## 问题

ch75 `tabitoken` 是 3 key 轮询的多 key 渠道（type=14，见
`docs/ops/tabitoken-channel-2026-08-09.md`）。2026-08-20 key#2 余额耗尽
（$0.219 < 预扣 $0.8），其 403 预扣费失败触发 auto_ban，**整个渠道被禁用**，
尽管 key#1/key#3 健康——本 fork 的多 key 机制不会跳过欠费 key，而是整渠道
陪葬（与 t1qq ch90 同类坑，`docs/ops/t1qq-sol-channel-2026-08-16.md`）。

直接后果：`claude-opus-5` 精确模型池无可用渠道（smoke `pool capacity
claude-opus-5` FAIL）。

## 诊断证据（2026-08-20 直连实测）

逐 key 直连 `POST https://tabitoken.com/v1/chat/completions`：

| key | max_tokens=1 | max_tokens=8192（真实预扣） |
|---|---|---|
| #1 sk-2mHNj…Wxfp | 200 | **200 solvent** |
| #2 sk-U2W8s…ba73 | 200 | **403 预扣费失败，余额 $0.219** |
| #3 sk-IR7tO…PrJW | 200 | **200 solvent** |

**关键教训：欠费 key 小探针能假活**——max_tokens=1 的预扣极小，余额 $0.22
也能过；真实流量（预扣 $0.8）才暴露。Guardian 的恢复探针/管理测试都是小请求，
不足以判定偿付能力，所以偿付探测必须带真实档 max_tokens。

## 修复：拆分为三个单 key 渠道

| Channel | Name | Key | 状态 |
|---|---|---|---|
| ch97 | tabitoken-1 | #1 | enabled p50/w8 |
| ch98 | tabitoken-2 | #2 | **created DISABLED（欠费，充值前停放）** |
| ch99 | tabitoken-3 | #3 | enabled p50/w8 |

配置逐项复制 ch75：type=14、base_url=`https://tabitoken.com`、models=
`claude-opus-5,claude-opus-5-thinking,claude-opus-4-8,claude-opus-4-8-thinking,zg-claude-opus-5`、
model_mapping=`{"zg-claude-opus-5":"claude-opus-5"}`、test_model=claude-opus-5、
auto_ban=1、group=default。单 key 渠道不再有"一枚欠费拖垮全部"的失效模式。

ch75 保留为禁用 tombstone（使用日志归因不丢），**绝不可复活**：轮询会再撞
欠费 key 再触发整渠道 auto_ban，形成禁用-恢复循环。已加入
`guardian.py AUTO_BAN_RECOVERY_EXCLUSIONS` 和
`newapi-local-smoke.py KNOWN_BROKEN_CHANNELS`（两集合同步有
test_smoke.py 断言守护，204 测试通过）。ch98 同样入两个集合：小探针假活会
让 Guardian 误恢复成"半活"状态，充值后须手工 enable + 重跑拆分脚本验证。

## 操作入口

```bash
# dry-run（默认）
python scripts/ops/split_tabitoken_channel.py

# 实施：备份 → 建 3 个禁用渠道 → 逐 key 真实预扣偿付探测 → 管理探针 →
# 仅启用有偿付能力的渠道 → 回读验证
python scripts/ops/split_tabitoken_channel.py --apply
```

key 从 ch75 的 DB 行读取，不走 argv 不打印。重跑幂等：已存在渠道只
探针+回读，不重建、不动 status、不换 key。key#2 充值后的恢复路径：
`POST /api/channel/98/status {"status":1}`，然后把 98 移出两个排除集。

## 验证证据

- DB 快照：`~/.new-api-local/backups/new-api-before-tabitoken-split-20260820-153616.db`
  （90,271,944 bytes，integrity=ok）
- ch97/ch99：偿付探测 200 → 管理探针 claude-opus-5 ok → 启用 → 75s 缓存同步
  后渠道+abilities 回读全对（5 模型 × enabled × p50/w8）
- ch98：创建即禁用，偿付探测复现 403 预扣费失败（余额 $0.219），保持禁用
- 双锁补齐：ch75/ch98 的 channels.weight 与 abilities.weight 均置 0
  （ch75 多 key 禁 PUT，走 DB 直写；smoke `intentional channel disables` 通过）
- smoke 回归：`pool capacity claude-opus-5` 恢复（enabled=2，ids=[97,99]），
  渠道/隔离/姿态/多 key 健康全部 OK
- 唯一残留 FAIL 与本次无关：`smoke completions` 超时 = ch15 sensenova 上游
  挂起（Guardian 15:33 已记"无响应"软失败 1/3，14:18 也曾报慢），归
  Guardian 降权/禁用闭环处理

## 其余多 key 渠道现状

ch3 baibei-100xlabs、ch9 linxi-k40、ch14 wintoken-glm、ch91
jianzhile-gpt-5.6-sol 均为多 key 且已全部处于禁用状态，暂无同类急性风险；
若日后要重新启用，应同样拆分为单 key 渠道。

## 代理层多 key 审计（2026-08-20，结论：健康，无需动作）

"单 key 欠费拖垮整体"是 **NewAPI 渠道层**多 key 的机制病；本地代理层的
多 key 是另一套实现，已自带 per-key 隔离：

- **agentrouter-proxy (8788)**：3 key 池。402/403 命中额度关键词
  （quota/额度/余额…）→ `_mark_fail` 只冷却该 key 180s，`_pick_key` 轮询
  跳过冷却中的 key，同请求自动换 key 重试；认证类 403 快速失败不换 key。
  单 key 欠费 = 自己进冷却，池不受拖累。ch45 `agentrouter`（NewAPI）指向
  8788，间接受益。已知小瑕疵：冷却固定 180s，欠费 key 每 180s 被重试一次
  （一次上游空调用），可改指数退避，属优化非 bug。
- **ch86 `agentrouter-claude`**：直连 `ps.air-outer.com` 的单 key 渠道，
  不涉多 key（其 w0 隔离是独立事件）。
- **anyrouter-proxy (8789)**：无 key 池（device_id 单账号机制），不涉此问题。

## 回滚

```powershell
# 禁用拆分渠道
POST /api/channel/97/status {"status":2}
POST /api/channel/99/status {"status":2}
# 若需回到多 key 形态：从两个排除集移除 75，POST /api/channel/75/status {"status":1}
# （key#2 充值前不建议——会立刻再触发整渠道禁用）
```
