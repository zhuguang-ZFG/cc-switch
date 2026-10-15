# NewAPI 门禁归因清理（2026-10-15 晚）：ch57/68/69 入册 + ch39 复原 + 备份轮转

用户令「同意」三项可执行项（A 归因入册 / B ch39 posture / C 备份轮转）。
冒烟 FAIL 基线 **7 → 5 类**。

## A. ch57/68/69 归因入册（清 `channels` 类）

`DEGRADED_ACCEPTED_DISABLED` 增三条（原仅 ch72/ch123），并在桶注释里补一句
"手工停车、无自动路径的条目须在 reason 里写明再启用条件"（该桶原语义写的是
"expected to auto-recover"，对这三条不成立——Guardian 只导入
`status=3 && auto_ban=1`，手工 `status=2` 不在其队列）：

| 渠道 | 证据 |
|---|---|
| 57 gorouter | **Guardian 从未碰过它**（guardian.log 无记录）；ops README:229：余额不足自 2026-08-20 保持 status=2，Cloudflare 1010 的浏览器 UA header_override 已预置；再启用=余额恢复 |
| 68 agnes-com-haiku | 10-15 admin test：`do request failed`（上游死）；其模型在 OMP quarantine 在册 |
| 69 agnes-cn-haiku | 10-15 admin test：403 账号异常活动；同上 |

## B. ch39（ai-168661-grok）复原（清 `ai.168661 posture` 类）

**溯源判定**（先查删除动机，再决定复原还是删契约）：

- 10-11 11:42 快照：ch39 仍在（`status=2 weight=0 keylen=67`，10 条 abilities
  **全 enabled=0**）；10-12 23:50 快照起缺失 → **删除窗口 = 10-11 11:42 ~
  10-12 23:50**；
- 该窗口的 10-11/10-12 commits、docs（无 10-11/10-12 文件）、guardian.log
  **均无 ch39/168661/grok 删除记载**；
- 而契约注释明说 "Keep the recovery point quarantined"、`KNOWN_BROKEN` 注释记
  `39/78: ai.168661 账号侧死 key 恢复点` → 判定为**无记载的误删**，按契约复原
  （不删契约、不改 `test_smoke.py`——该测试断言契约表非空，正确做法是让行存在）。

**操作**：快照 `new-api-before-restore-ch39-20261015-1900{00,23}.db` → 从备份
整行 INSERT（30 列；`group` 是保留字须加引号）+ 10 条 abilities（enabled=0）→
readback `39:ai-168661-grok status=2 weight=0 p55 auto_ban=1 keylen=67
base=https://ai.168661.xyz test=grok-4.5` + `quick_check=ok`。

**验证**：冒烟 `ai.168661 channel posture → violations=none`；alignment 仍
**CLEAN**（ch39 隔离态不产生路由）。逐模型复核：ch39 的 10 个 grok 名中仅
`grok-4.5` 有 enabled 路由，且在 **ch118**（非 ch39）；其余 9 名 enabled 路由
= NONE。→ **复原未新增任何路由**（total 103 中 ch39 为隔离行）。

> 后注（防再误修）：**ch39 是 10-11 11:42 之后被无记载误删的隔离恢复点，已按
> 该时刻快照 `new-api-before-yanglechu-20261011-114217.db` 逐字段复原**
> （name/models/mapping/test_model/priority=55/weight=0/status=2/auto_ban=1/
> base_url）；它是**有意存在**的，不是漂移——除非用户明示不再保留，勿再当
> 异常删除或"清理死行"。

**风险/回滚**：若该删除其实是有意的（如不再保留 Grok 恢复点），回滚=删行，
快照在册。

**如实记录一处小事故**：备份轮转时 keep 集合的路径写法不匹配 Windows 路径，
**误删了溯源件 `new-api-before-yanglechu-20261011-114217.db`**（其余含 ch39 的
副本本就已被旧的轮转淘汰）→ 现存 12 份备份均不含 ch39；ch39 行本身已在库、
契约校验通过，故影响限于"溯源件少一份"。

## C. DB 备份轮转（回收 1162 MB）

- 删除：13 份旧 `backups/*.db`（10-07 19:59 ~ 10-13 10:58）+ 7 份主目录
  legacy `new-api.db.bak-*`（8-12 ~ 9-07；含一份 157 MB 的 ch45-glm53）；
- 保留：12 份（10-13 11:20 ~ 10-15 19:00，733 MB）——覆盖最近三天的每次
  变更前快照；
- manifest 见上（删除清单在 commit message 摘要中；精确文件名按 mtime+大小
  可复述）。

## 门禁（终态实测）

- `newapi-local-smoke`：FAIL **5 类**（余：primary opus pool posture /
  backup opus pool posture / pool capacity claude-opus-4-8 /
  unregistered model ratios / pool answer rate——全部为上游死因或待裁决值）；
  `channels` 与 `ai.168661 posture` 均 **OK**。
- `test_smoke.py` **47/47 OK**；`verify_omp_newapi_alignment` **CLEAN**。

## 回滚

```bash
# 归因/契约：git revert <本 commit>
# ch39：DELETE FROM channels WHERE id=39; DELETE FROM abilities WHERE channel_id=39;
# 备份：不可回滚（manifest 在册）
```
