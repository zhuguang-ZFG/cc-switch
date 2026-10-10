# OMP 配置漂移修复 + deepseek-v4-flash 首选回切 ch180（2026-10-10）

## 一、OMP 角色链/注册漂移修复（test_omp_routes 9 FAIL → 40/40）

根因：10-09 13:2x 的 governance/瘦身改动（另一会话）从 models.yml 删了 40 个注册
（含 `zg-newapi/qwen3.8-max`），并把 k3→glm-5.3 的角色链改写时引入了
**未注册选择器** `zg-newapi/claude-opus-4-8`（该模型只注册在
`zg-newapi-anthropic`）与**六条链首重复主模型**——门禁契约未随行，漂移 9 项。

修复（改动均先行备份 `config.yml.bak-20261010-ompdriftfix` / `models.yml.bak-20261010-ompdriftfix`）：

- models.yml：按门禁契约重注册 `qwen3.8-max`（reasoning, text+image,
  1M/131072；条目模板取自 `models.yml.bak-20261009`）
- config.yml 七条链规范化：tiny/vision/slow/plan/advisor/task 去链首重复主模型；
  slow/designer/task 的 `zg-newapi/claude-opus-4-8` 改指已注册的
  `zg-newapi-anthropic/claude-opus-4-8`（advisor 链同款先例）
- 验证：`python3 -m unittest scripts.ops.test_omp_routes` → 40/40 OK

**遗留（上游侧，非本地缺陷）**：qwen3.8-max 两个池当前均坏——
ch118 seeseed 站面 relay 500（见下）、ch192 longai key 已死
（渠道测试 401 `无效的令牌`，10-09 17:17 还正常出账）。注册保留（门禁契约），
但实弹 `qwen3.8-max` 会失败直至上游补货/换 key。

## 二、deepseek-v4-flash 频繁报错 → 首选回切 agentrouter

**症状**：OMP 用户侧 deepseek-v4-flash 高频报错（用户报障"没法使用"）。

**根因**：10-09 用户裁决把 seeseed ch118 提为首选（p51）后，**seeseed 站自身
relay 坏了**：key 有效（`/v1/models` 200、目录含 deepseek-v4-flash），但 chat
确定性 500 `upstream error: do request failed`（站→其上游断链，3/3 复现），
glm 腿 530、qwen3.8-max 同站同坏。且 ch118 `auto_ban=0` 不会被摘出池——主跳
恒定先失败，靠 NewAPI 500 重试码兜底才成功：非流式多吞一跳时延，流式首包前
失败尚可 failover、首包后失败即断流。

**处置**（非禁用，仅路由回退；seeseed 站恢复后可再升回首选）：

| 渠道 | 动作 | 终态 |
|---|---|---|
| ch180 agentrouter | channels.priority + abilities(deepseek-v4-flash) → **51** | 主 |
| ch118 seeseed | → **25**（保留 default+Free 行，status=1 入池） | 备胎 |

备份：`new-api-before-dsv4f-head-back-to-ch180-20261010-091127.db`（backup API，WAL-safe）。
注意 abilities 更新按 `model='deepseek-v4-flash'` 定向，不重演 10-09 的
`WHERE channel_id` 连坐坑。

**验证**（OMP token 经 3002）：

- 非流式 burst 并发 6：**6/6 200，5.4–5.9s**；DB 归因 `use_channel:["180"]` 直落（无 ch118 失败跳）
- 流式：首包正常，41 chunks / 1.9s
- `newapi-local-smoke.py`：FAIL 由 3 → **1**（仅剩 `72:anyrouter test_model=None` 存量项）
- ch15/ch127 对照渠道测试同步 success（备链完好）

## 回滚

- 路由：把上表 priority 反向写回（ch118=51 / ch180=30）+ 60s 缓存同步
- OMP 配置：还原 `*.bak-20261010-ompdriftfix` 两份文件
