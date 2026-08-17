# opencode go 套餐 luna 切换（2026-08-17）

## 背景与决策

用户指令：opencode go 套餐禁止使用 DeepSeek V4 Flash，只能使用 gpt-5.6-luna；
OMP 中 luna 接替 V4Flash 的全部角色位。上游管理端实测（`GET /api/channel/test/48?model=`）：
`gpt-5.6-luna` 2.8s OK；`deepseek-v4-flash`/`deepseek-v4-pro` 当时仍可通，但按套餐政策停用。

## 变更内容

### NewAPI ch48（opencode-go-flash → opencode-go-luna）

- `models`: `opencode-go,deepseek-v4-flash,deepseek-v4-pro,opencode-go-pro` → `gpt-5.6-luna`（唯一）。
- `model_mapping` → `{}`（别名 `opencode-go`/`opencode-go-pro`/`zg-opencode-go` 全部退役）。
- `setting.system_prompt` 清空：原 DeepSeek flash 专用 "Just-Lisa" persona 注入（3328 字符，
  2026-08-06 加入）随 flash 角色一并移除。
- `param_override` 曾临时加 `delete prompt_cache_key`（仿 ch88），查明不需要后移除（见下）。
- priority/weight 保持 51/20；name → `opencode-go-luna`。
- abilities 终态：`gpt-5.6-luna` = ch48(51/20, enabled) + ch82(51/5, disabled，7758 仍禁用)；
  `deepseek-v4-flash` = 仅 ch42(50/5)；`deepseek-v4-pro` = 仅 ch42(50/5)。

### OMP（~/.omp/agent/）

- modelRoles：`tiny` → `zg-newapi/gpt-5.6-luna`；`task` → `zg-newapi/gpt-5.6-luna:max`。
- fallbackChains：
  - 新增 `zg-newapi/gpt-5.6-luna` 链（尾部 = 原 flash 链尾部：official-flash → fengwind-flash → sensenova）。
  - `zg-newapi/deepseek-v4-flash` 链删 `opencode-go` 头；`deepseek-v4-pro` 链删 `opencode-go-pro` 头。
  - `claude-haiku-4-5` / `k3` / `bigctx` 链中的 `deepseek-v4-flash` 位 → `gpt-5.6-luna`；`smol` 链头 `opencode-go` → `gpt-5.6-luna`。
- models.yml：删 `opencode-go`/`opencode-go-pro` 条目；`gpt-5.6-luna` 名称改 "(opencode-go ch48)"；
  flash/pro 名称改 "(官方 ch42)"。
- **需重启 OMP 生效**（models.yml/fallbackChains 无热重载）。

### 监控同步（scripts/ops/）

- newapi-local-smoke.py：`SMOKE_MODELS` opencode-go→gpt-5.6-luna；
  `CRITICAL_ABILITY_POSTURES` 删 ch48 四行旧模型、加 `(48,"gpt-5.6-luna"):(51,20)`。
- test_smoke.py posture 用例、test_omp_cache_optimizer_model_registry.ts 夹具同步
  （后者 DS 族用例改用 `deepseek-official-v4-flash`）。
- guardian state.json 无 ch48 joined/disabled 残留，无需清理；池归属按渠道声明模型自动收敛。

## 排障记录：Cloudflare 1010 假阳性

 relay 带 `prompt_cache_key` 探针一度 403（上游 body=`error code: 1010`），疑似 WAF 拦 pck 字段。
 逐步隔离结论：

- 字段隔离：pck 非空→403，pck 空/垃圾字段/stream_options/reasoning_effort→200。
- 直连上游（curl，任意 UA）带 pck → 200，排除 zen/go WAF 拦字段。
- param_override delete 对 ch88 有效（实证 200），对 ch48 表象无效——实为触发条件不在 body。
- **根因：fork 在 pck 触发 channel_affinity 的路径会把客户端 User-Agent 透传上游；
  `Python-urllib/3.13` 签名被 CF 1010 封禁。无 UA / undici / node / Go UA / Chrome UA 全部 200。**
  OMP 生产形状（无 UA 或 node 系）不受影响，param_override 不需要，已移除。

## 验证矩阵

| 检查 | 结果 |
|---|---|
| ch48 readback（models/mapping/prio/weight/setting） | ✅ DB+API 一致 |
| abilities 终态（luna ch48 / flash·pro 仅 ch42） | ✅ |
| relay luna 非流式 + reasoning_effort max/xhigh/high/minimal/none | ✅ 全 200 |
| relay luna 生产形状（stream+pck+stream_options+max，无 UA） | ✅ 200 SSE 完整 |
| relay flash/pro 归因 logs.channel_id=42 | ✅ |
| config.yml/models.yml YAML 解析 | ✅ |
| pytest test_smoke.py | ✅ 33 passed |
| bun test_omp_cache_optimizer_model_registry.ts | ✅ PASS |
| newapi-local-smoke.py 全量 | ✅ ALL OK（含 luna relay 200、posture 无违例） |

## 备份

- `~/.new-api-local/backups/new-api.db.bak-20260817-172620-ch48-luna`（79.7MB）
- `~/.new-api-local/backups/channel-48-20260817-172620.json`
- `~/.omp/agent/config.yml.bak-20260817-172620-luna`、`models.yml.bak-20260817-172620-luna`

## 残留风险

- gpt-5.6-luna 在 NewAPI 仅 ch48 单渠道（ch82/7758 禁用中）；ch48 挂则走 OMP 链
  fengwind-flash→sensenova（跨族降级）。
- luna 272k ctx 低于原 opencode-go 声明的 500k；task/tiny 超长上下文场景注意。
- 旧 runbook 中 opencode-go=flash 的记载（deepseek-v4-pro-pool、cursor-newapi-byok 等）自此过时，
  以本文为准。

## 第二阶段：deepseek 官方全系撤出 NewAPI（2026-08-17 17:5x，用户指令「官方的从newapi撤下，pro也撤」）

- ch42（deepseek 官方，flash+pro+两个 official 别名）已从 NewAPI 删除——渠道行与 abilities
  行均不存在，relay `deepseek-v4-flash`/`deepseek-v4-pro` 返回 503 model_not_found（预期）。
  用户侧执行删除，本侧核验并收尾。
- OMP 链同步：pro 角色位（slow/plan/designer/bigctx/k3 链）→ `fengwind/deepseek-v4-pro`；
  luna/smol 链删 `zg-newapi/deepseek-official-v4-flash`；`zg-newapi/deepseek-v4-flash` 与
  `zg-newapi/deepseek-v4-pro` 两条链头整体删除。models.yml 再删 4 个 zg-newapi deepseek 条目
  （累计 6 个死条目清除）。deepseek 在 OMP 仅余 fengwind 直连。
- smoke 脚本：`MIN_ENABLED_CRITICAL_MODELS` 删 deepseek-v4-flash；`CRITICAL_ABILITY_POSTURES`
  删 4 行 ch42；test_smoke.py missing-row 用例改 (45,gpt-5.6-sol)；cache-optimizer 夹具改
  fengwind/deepseek-v4-flash。
- 验证：pytest test_smoke 33 passed；cache-optimizer PASS；newapi-local-smoke 全量 ALL OK；
  test_omp_routes 33 passed + 1 个**既有**失败（default=k3 与 k3 链共存违反 hard-fail 门禁，
  改动前备份配置同样违反——非本次引入，k3 链服务 plan/bigctx/designer，去留待用户决策）。
- 记忆库已整理：project 90%（4529/5000）、failure 87%（8752/10000），本次变更与 CF 1010
  quirk 均已落盘。

## 第三阶段：NewAPI↔OMP 模型盘点（2026-08-17 18:1x，用户问「有些模型没配置到omp中」）

- 全量 diff：22 个 NewAPI enabled 模型不在 OMP，其中 15 个为**故意不接**（zg-* 别名、
  claude OpenAI 兼容重复、haiku 日期/1M 变体、LongCat/agnes/intern 直连或 anthropic 网关已有）。
- ch89 seeseed 复扫：grok-4.6/grok-chat-fast 活；GLM-5.3 上游 401（本 key 在 free 组、模型仅
  default 组）、mimo-v2.5 上游下架——已从 ch89 摘除（备份 channels-89-20260817-181629.json）。
- OMP models.yml 新增 `zg-newapi/grok-4.6`（262k/32k reasoning）与 `zg-newapi/grok-chat-fast`
  （131k/16k），规格为保守声明。k3-256k/kimi-for-coding-highspeed/opus-thinking 变体属冗余未接。
- 验证：relay 两 grok 200；newapi-local-smoke 全量 ALL OK。
