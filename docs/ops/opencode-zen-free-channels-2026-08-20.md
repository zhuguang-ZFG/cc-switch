# opencode-zen 免费池渠道 ch96（2026-08-20）

> 2026-08-21 池变动：新增 `x-preview-f-free`（Ox Alpha Free，zero-retention），
> 摘除 `deepseek-v4-flash-free`（免费促销结束，上游硬 401）。见文末"池变动"。

## Scope

把 OpenCode Zen 的免费模型池接入本地 NewAPI，供 OMP 兜底使用：

| Channel | Name | Models | Priority/Weight | Role |
|---|---|---|---|---|
| ch96 | opencode-zen-free | 免费模型池（见下，随上游促销变动） | 10 / 5 | 免费兜底池（best-effort） |

关键发现：**OpenCode Go 的 key（ch48 `opencode-go-muse` 同一把）对 Zen 免费端点
直接有效**。官方文档把 Go（`https://opencode.ai/zen/go`）和 Zen
（`https://opencode.ai/zen`）描述为两条产品线，但 2026-08-20 实测 Go key 在
Zen `/v1/chat/completions` 与 `/v1/responses` 上认证通过，免费模型可用。

## 收录模型（6 个）

文档收录（[opencode.ai/docs/zen](https://opencode.ai/docs/zen/)，全部 Free，
隐私条款明确）：

- `big-pickle`（stealth，chat/completions）
- `mimo-v2.5-free`（chat/completions）
- `hy3-free`（chat/completions）
- `muse-spark-1.2-contributor-free`（**仅 /v1/responses**）

`/v1/models` 列出但文档尚未收录（数据政策未明，默认不注册进 OMP）：

- `laguna-s-2.1-free`（chat/completions，接入时 200；2026-08-21 起 503
  "Endpoint is unavailable"，未注册 OMP，留在渠道上等恢复）

**已下线**：`deepseek-v4-flash-free` —— 2026-08-21 上游返回 401 ModelError
"Free promotion has ended"（硬错误非额度 429），已从 ch96 摘除
（`scripts/ops/remove_opencode_deepseek_flash_free.py`）。

**刻意排除**：`nemotron-3-ultra-free` / `nemotron-3.5-lightning-free` ——
NVIDIA trial 条款更严格（"do not submit personal or confidential data" +
NVIDIA API Trial Terms），不适合自动化 agent 流量。

## 直连实测证据（2026-08-20，Go key + 浏览器 UA）

| 模型 | 端点 | 结果 |
|---|---|---|
| hy3-free | chat/completions | 200 |
| laguna-s-2.1-free | chat/completions | 200 |
| muse-spark-1.2-contributor-free | responses | 200 |
| big-pickle | chat/completions | 429 FreeUsageLimitError |
| mimo-v2.5-free | chat/completions | 429 FreeUsageLimitError |
| deepseek-v4-flash-free | chat/completions | 429 FreeUsageLimitError |

**429 FreeUsageLimitError ≠ 故障**：它证明认证和路由都通，只是免费每日额度
暂时耗尽。这正是建渠道的意义——额度重置后自动可用。脚本的 management probe
把 429 FreeUsageLimitError 视为 pass-with-warning。

## 渠道参数

- type=1（OpenAI），base_url=`https://opencode.ai/zen` —— 不带 `/v1`
- key：复用 ch48 的 Go key（argv 传入，不入仓）
- test_model: `hy3-free`（接入时唯一 200 的 chat 模型）
- priority 10 / weight 5，group `default`，auto_ban=1
- **header_override `User-Agent` = 浏览器 UA 必需**：与 Go/justwoker/gorouter
  一样在 Cloudflare 后，非浏览器 UA 报 1010
- **muse-free 无需 chat→responses 转换策略**：OMP 侧该模型声明
  `api: openai-responses`，原生发 /v1/responses，NewAPI type-1 渠道直通
  （与 ch48 相同的机制；`global.chat_completions_to_responses_policy` 未动）
- **ModelRatio=0**：池内模型名是 Zen 独占且上游免费，倍率置 0 保证计费日志
  真实（接入时含把已有的 `mimo-v2.5-free`/`deepseek-v4-flash-free` 0.5
  纠正为 0；后者下线时其条目已一并删除）

## OMP 侧接线

`~/.omp/agent/models.yml`（zg-newapi provider 下新增 4 条，文档收录的免费模型）：

- `muse-spark-1.2-contributor-free`：`api: openai-responses`，context/maxTokens
  照抄付费版 ch48 条目（1048576/131072，text+image）
- `big-pickle` / `mimo-v2.5-free` / `hy3-free` / `x-preview-f-free`：
  openai-completions（provider 默认），contextWindow 保守取 131072 /
  maxTokens 16384（官方未公布，低估只损失截断头部空间）

`~/.omp/agent/config.yml` fallbackChains：

- `zg-newapi/muse-spark-1.2-contributor` 链首插入
  `zg-newapi/muse-spark-1.2-contributor-free` —— ch48 当前 RegionError 禁用中，
  task 角色立即可落到同族免费版
- `smol` 链尾追加 big-pickle / mimo-v2.5-free / hy3-free —— 最后手段免费池
- （2026-08-21）`zg-newapi/omp-sota-claude-opus-5` 链尾与 `smol` 链尾追加
  `x-preview-f-free` —— 零数据保留免费模型，sota 日额度耗尽后的兜底之一

主力 modelRoles 未动。

## 隐私注意

- 大部分免费模型在免费期内"collected data may be used to improve the model"
  （Zen 隐私章节明示）；muse contributor free 条款是"用 prompt/completion
  换折扣/免费"。脚本 `--apply` 强制要求 `--accept-zen-free-data-policy`。
- **例外：`x-preview-f-free`（Ox Alpha Free）官方明示 zero-retention，
  不用于训练**（Zen 隐私章节），是池内隐私姿态最好的免费模型。
- 不要把机密代码/密钥推进走免费池的会话；它定位是兜底，不是主力。

## 验证证据

- DB 快照：`~/.new-api-local/backups/new-api-before-opencode-zen-free-20260820-144832.db`
  （90,066,944 bytes，integrity=ok）
- ch96 创建为禁用 → 管理探针 `hy3-free` ok（证明 header_override 突破 CF）→
  ModelRatio 置 0 → 启用 → 75s 缓存同步 → **relay 探针**
  `POST 127.0.0.1:3002/v1/responses` model=`muse-spark-1.2-contributor-free`
  用 OMP 自己的 zg-newapi token：ok（证明 OMP 实际调用路径端到端通）
- 回读验证：渠道字段 + 6 条 abilities（p10/w5/enabled）+ 6 条 ModelRatio=0
- OMP 侧 YAML 解析 + 引用完整性校验：4 个新模型注册成功，全部 fallback 链
  条目可解析

## 操作入口

```bash
# dry-run（默认，只出计划）
python scripts/ops/add_opencode_zen_free_channels.py <GO_KEY>

# 实施（备份 → 禁用创建 → 探针 → 倍率 → 启用 → relay 探针 → 回读）
python scripts/ops/add_opencode_zen_free_channels.py <GO_KEY> --apply \
  --accept-zen-free-data-policy
```

重跑幂等：渠道已存在时只探针+回读验证，不重建、不动 status、不换 key。

## 回滚

```powershell
# 禁用渠道
POST /api/channel/96/status {"status": 2}
# OMP 侧：git -C ~/.omp/agent checkout -- models.yml config.yml（或回退对应 commit）
# ModelRatio：从备份快照恢复 options 行，或手工删除 6 个免费模型条目
```

## 顺带清理

`global.chat_completions_to_responses_policy` 的 `channel_ids` 原有残留引用
`142`（渠道已删除，当时 max id=95）。2026-08-20 已摘除，现为 `[91, 92]`，
model_patterns 与 enabled 未动；备份
`new-api-before-policy-ch142-cleanup-20260820-145831.db`。

## 池变动（2026-08-21）

**新增 `x-preview-f-free`（Ox Alpha Free）**——OpenCode 新上线的 stealth
免费模型，chat/completions 端点，官方明示 **zero-retention、不用于训练**
（池内隐私姿态最好）。实测（Go key + 浏览器 UA 直连）：/v1/models 在列，
chat/completions 200（reasoning 模型，16 max_tokens 全花在
reasoning_tokens 上空 content 属预期）。实施：
`scripts/ops/add_opencode_ox_alpha_model.py --apply`（备份 → ch96 models
+= → 管理探针 ok → ModelRatio=0 → 75s 缓存同步 → relay 探针 3002 ok →
回读验证渠道/abilities/倍率）。OMP 侧 models.yml 注册（131072/16384 保守
窗口），config.yml `omp-sota-claude-opus-5` 链尾与 `smol` 链尾追加。备份
`new-api-before-opencode-ox-alpha-20260821-112505.db`。Ox Alpha 是
zero-retention，无需 `--accept-zen-free-data-policy` 门禁。

**摘除 `deepseek-v4-flash-free`**——免费促销结束，/v1/models 仍在列但调用
硬 401 ModelError "Free promotion has ended for DeepSeek V4 Flash Free"
（非额度 429，不会自愈）。实施：
`scripts/ops/remove_opencode_deepseek_flash_free.py --apply`（备份 → ch96
models -= → 删除孤儿 ModelRatio 条目 → 回读验证 abilities 行失效）。该模型
从未注册进 OMP，OMP 侧无改动。备份
`new-api-before-opencode-dsflash-free-removal-20260821-113057.db`。

**观察**：`laguna-s-2.1-free` 同日起 503 "Endpoint is unavailable"（接入时
200）。未注册 OMP，留在渠道上等恢复；若持续 503 可按同一摘除流程处理。

`add_opencode_zen_free_channels.py` 的 `MODELS` 常量已同步为现状（6 个），
重跑幂等验证通过（2026-08-21，ch96 status=1 untouched，probe ok）。

**限额修正（2026-08-21，数据源 models.dev —— opencode 官方元数据源）**：
OMP models.yml 四条免费条目从保守占位 131072/16384 改为实测/官方值——
`x-preview-f-free` 1000000/131072（input text+image，图片输入已实测：8x8
红色 PNG 正确回答 "Red"）、`big-pickle` 200000/32000（注意 models.dev 标注
input 上限 160000）、`mimo-v2.5-free` 200000/32000、`hy3-free`
190000/64000。`reasoning_effort` 已实测被上游接受：low→reasoning_tokens=0
（3.1s），high→推理参与（4.7s），两者答案均正确，OMP 的 `:effort` 后缀
机制对该模型有效。
