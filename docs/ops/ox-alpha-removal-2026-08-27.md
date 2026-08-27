# Ox Alpha 全量下线（2026-08-27）

**Status:** Active（取代 `x-preview-f-free-stabilization-2026-08-21.md` 的主力化/稳定化方案；`opencode-zen-free-channels-2026-08-20.md` 的 Ox 聚合各节转历史档）
**Scope:** OMP `models.yml`/`config.yml`、本地 NewAPI 渠道/倍率/abilities、ops 脚本契约

## 背景

用户确认 Ox Alpha 免费档取消；随后确认付费版 Ox Alpha 也已停。公开名
`x-preview-f-free`（免费聚合）与 `x-preview-f`（付费）双双全链路下线。

## 事前状态（2026-08-27 活探针）

- 专用聚合渠道 ch100 `openrouter-ox-alpha` / ch102 `ai-168661-ox-alpha` /
  ch103 `s608885-ox-alpha` / ch104 `opencode-go-oxalpha` **已不存在**（本次操作前即被删除）。
- ch96 `opencode-zen-free` models 仍含 `x-preview-f-free`。
- ch110 `yjs-free` models 仍含 `x-preview-f-free` 且 `model_mapping`
  `x-preview-f-free→ox-alpha`——历史文档漏记的聚合成员，本次全量扫描发现。
- ch109 `imagic` models 仍含付费别名 `x-preview-f`（与 grok 族共享渠道，只剥模型不删渠道）。
- `ModelRatio` 仍有 `x-preview-f-free: 0`。
- OMP 侧：`models.yml` 注册 `x-preview-f-free` 与 `x-preview-f`；
  `config.yml` fallbackChains 10 处引用 `x-preview-f-free`。

## OMP 侧变更

- `~/.omp/agent/models.yml` 删除 `x-preview-f-free` 条目；`config.yml` 删除
  10 处 fallback 引用（omp-sota-claude-opus-5、longcat、muse-spark-free、
  agnes-2.5-flash、claude-haiku-4-5、smol、slow、vision、designer、plan 链）。
- 随后删除付费 `x-preview-f` 条目；`config.yml`/`extensions` 无付费版引用。
- 备份：`models.yml.bak-20260827-181443-ox-free-removal`、
  `config.yml.bak-20260827-181443-ox-free-removal`、
  `models.yml.bak-20260827-181836-ox-paid-removal`。
- 终验：两文件 YAML 解析正常，活配置零 ox 残留。

## NewAPI 侧变更

- 新脚本 `scripts/ops/remove_ox_alpha_newapi.py`（默认 dry-run，`--apply` 生效）：
  整库 SQLite 快照备份 → 全渠道枚举（page_size=200），对 OX 公开名集合
  `{x-preview-f-free, x-preview-f, ox-alpha, ox-alpha-free}` 剥 `models`，
  对 `model_mapping` 按键（OX 公开名）与值（上游名 `ox-alpha`/`stealth/ox-alpha`/
  `ox-alpha-free`）双匹配剥除 → 剥空渠道改 DELETE → 清 ModelRatio 孤儿条目 →
  readback 验证（channels/mapping/ratio/abilities）→ 失败回滚（PUT/POST 复原渠道、
  复原 option）。
- 应用结果：ch96 7→6；ch110 19→18（mapping 剥除）；ch109 11→10；
  `ModelRatio -= x-preview-f-free`。
- 备份：`<NewAPI DB 目录>/backups/new-api-before-ox-alpha-remove-20260827-182122.db`
  （integrity ok）。幂等重跑输出 "verify only"。

## 渠道聚合覆盖（"都做了吗"的答案）

- 移除脚本枚举全部 50 个渠道的 `models` + `model_mapping`，覆盖聚合全表面，
  含历史文档漏记的 ch110；不依赖文档里的池成员名单。
- 专用聚合成员 100/102/103/104 操作前已删；剥除后 abilities 表 readback
  无 enabled 的 ox 行。
- 复活路径切断：`add_opencode_zen_free_channels.py` 的 `MODELS` 去除
  `x-preview-f-free`（契约注释补 removal 记录）；`add_yjs_free_channel.py` 的
  `MAPPED_MODELS` 去除 `x-preview-f-free→ox-alpha`；README 同步。重跑这些
  onboarding 脚本不会把 ox 加回聚合池。
- 其余聚合池（flash/k3/sol/opus/muse 等）未受影响。

## Guardian

- 按活渠道列表扫描，ox 探针自然停止；stale state 由其 reconcile 路径清理。
- daily-cap 墓碑机制为通用机制（关键词匹配），对其余免费渠道继续有效，未改动。

## 取代关系

- `x-preview-f-free-stabilization-2026-08-21.md` 的主力化稳定方案作废。
- `opencode-zen-free-channels-2026-08-20.md` 第二/三/四上游聚合各节转历史档。
- 历史接入脚本（`add_*ox_alpha*`、`add_opencode_go_oxalpha_channel.py`）保留为
  档案，禁止重跑 `--apply`。
