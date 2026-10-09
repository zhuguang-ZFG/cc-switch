# OMP smol 链净化 + commit/smol 角色切 omen-alpha（2026-09-04）

## 背景

qwen3-8-27b "总是故障路由"复盘（见 `qwen3-8-27b-channel-ops-2026-09-04.md`）暴露两件事：

1. commit/smol 角色主力模型 qwen3-8-27b 链路三渠道中两只已死（ch88 余额 402、ch124 groq 拒
   thinking 400），仅 ch112 存活且单点；
2. smol 兜底链里混着 4 条死跳——上游 opencode-zen 免费池（ch96）已劣化
   （hy3-free 401 下架 / big-pickle 429 / muse 500 / nemotron 挂起），这些条目
   零启用渠道，选中即 `no available channel` 空转再跳。

## 变更

### 1. 角色：commit/smol → omen-alpha

```yaml
modelRoles:
  commit: zg-newapi/omen-alpha:high   # 原 zg-newapi/qwen3-8-27b:high
  smol:   zg-newapi/omen-alpha:high   # 原 zg-newapi/qwen3-8-27b:high
  # default 本已是 zg-newapi/omen-alpha:xhigh（更早切换），未动
```

- omen-alpha（ch125 `opencode-go-omen-alpha`）Go 套餐包月边际成本 0，配额远高于
  qwen3-8-27b 免费池；effort 分级经 `:high` 保留。
- qwen3-8-27b 从角色主位退居 smol 链首跳兜底（`zg-newapi/qwen3-8-27b → deepseek-v4-flash`
  链条保留，ch112 单活也仍有 deepseek 垫底）。

### 2. smol 兜底链：9 条 → 5 条

删除 4 条死条目（全部指向 ch96 劣化的 zen 免费池，零启用渠道）：

- `big-pickle`
- `mimo-v2.5-free`
- `nemotron-3-ultra-free`
- `nemotron-3.5-lightning-free`

净化后全链每跳都有活渠道支撑：

```
omen-alpha(ch125) → deepseek-v4-flash(ch118/ch110) → mercury-2(ch61)
→ mimo-v2.5(ch101/ch109/ch111) → muse-spark-free(ch105/ch110) → hy3-free(ch110)
```

### 3. models.yml 四个 zen 条目刻意保留（休眠无害）

链删除 ≠ 模型删除。models.yml 里 4 个模型定义保留：zen 缓过来后 Guardian 探针
救活 ch96 即自动恢复路由，届时想进 smol 链手动加一行即可。回加判据同 bai 纯净化
三条件。

## 验证

- OMP 一发烟测：`omp -p "Reply with exactly OK"` → `OK`（角色解析 + relay 全链 + config
  解析同时验证，41.2s/40.1s 两次经 ch125）。
- smol 链 YAML 结构经编辑回读确认；`~/.omp/agent` 本地仓 commit `e30fbc5`。

## 回滚

- `~/.omp/agent/config.yml.bak-20260904-commit-smol-omen`（角色变更前全量原件）。
- 本地 git 仓 `~/.omp/agent`（禁 remote）可 `git revert`。

## 遗留

- ch96 zen 上游恢复属被动等待（Guardian 恢复队列自动探）。
- qwen3-8-27b 现仅 ch112 单点；yjs 挂则小任务全落 deepseek-v4-flash（可接受，见
  qwen3-8-27b runbook 单点风险节）。
