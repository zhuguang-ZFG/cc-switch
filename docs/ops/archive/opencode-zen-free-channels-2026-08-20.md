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

**已下线**：

- `deepseek-v4-flash-free` —— 2026-08-21 上游返回 401 ModelError
  "Free promotion has ended"（硬错误非额度 429），已从 ch96 摘除
- `laguna-s-2.1-free` —— 2026-08-21 起持续 503 "Endpoint is unavailable"
  （接入时 200，未注册 OMP），同日按用户决策摘除

摘除工具：`scripts/ops/remove_opencode_zen_free_model.py <model> --apply`
（通用版；注意额度型 429 FreeUsageLimitError 会自愈，不是摘除理由）。

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
`scripts/ops/remove_opencode_zen_free_model.py --apply`（备份 → ch96
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

**第二上游聚合（2026-08-21）**：Ox Alpha 同时上架 OpenRouter
（`stealth/ox-alpha`，定价 0，models.dev 标注 1048576 上下文）。已建
ch100 `openrouter-ox-alpha`（type=1，base_url=`https://openrouter.ai/api`，
key argv 传入不入仓），`model_mapping` 把公开名 `x-preview-f-free` 映射到
`stealth/ox-alpha`，与 ch96 **聚合同名**——Zen 直连（p10）优先，OpenRouter
（p5/w5）在 Zen 免费日额度耗尽时垫底接管，OMP 侧零改动。实施：
`scripts/ops/add_openrouter_ox_alpha_channel.py <OR_KEY> --apply`（备份 →
禁用创建 → 管理探针验证 mapping → 启用 → 75s 缓存同步 → relay 探针 3002
ok → 回读验证渠道/mapping/abilities/倍率）。OpenRouter 不在 Cloudflare 后，
无需浏览器 UA；该 key 为付费档账号（无免费层每日上限），免费模型本身
上游定价 0。备份 `new-api-before-openrouter-ox-alpha-20260821-143300.db`。

**思维链 effort 矩阵（2026-08-21 实测，逐个档位探测）**：Zen 免费模型的
`reasoning_effort` 白名单**按模型不同**，发不支持的档位会 400 [1210]
"This model always engages in thinking and cannot..."：

| 模型 | low | high | xhigh | max | minimal/medium |
|---|---|---|---|---|---|
| x-preview-f-free | ✓ | ✓ | ✗ 1210 | ✓ | ✗ 1210 |
| hy3-free | ✓ | ✓ | ✓ | ✗ 400 | （未测） |
| muse-spark-1.2-contributor-free | ✓ | ✓ | （未测） | ✓ | （未测） |

muse-spark 特别注意（2026-08-21 实测）：三档全 200 不拒收，但所有档位
`reasoning_tokens` 均为 None——muse-free 走 /v1/responses 直通，effort
参数被上游静默吞掉，**声明档位无实际推理效果**。models.yml 已补
`efforts: [low,high,max]` 白名单仅为行为显式化（防未来上游收紧变 4xx），
不要指望 `:max` 让 muse 真的多想。

big-pickle / mimo-v2.5-free 当时额度耗尽未测。OMP models.yml 已按实测
声明 `thinking.efforts`（x-preview `[low,high,max]`、hy3 `[low,high,xhigh]`），
从源头避免 `:xhigh` 这类不支持档位被发到上游（故障实例：2026-08-21
OMP 以 `x-preview-f-free:xhigh` 调 172 条消息会话，上游 1210 拒收，
dump 见 `~/.omp/logs/http-400-requests/1787294055247-*.json`）。

**Go 套餐 mimo-v2.5（2026-08-21）**：Go 订阅本身活着（muse 之外 6 个模型
实测全 200），官方额度表里**可用模型中月用量最大的是 MiMo-V2.5**
（~150,400 req/月典型、$60 内含额度档；Muse Contributor 更高但训练数据
条款+上游已收回，不可用）。已建 ch101 `opencode-go-mimo`（type=1，
base_url=`https://opencode.ai/zen/go`，复用 Go key，浏览器 UA 必需），
ModelRatio=0（订阅制边际成本为零；**注意首次接入时 NewAPI 已有非零
mimo-v2.5 倍率残留，脚本已改为"值不等即纠正"，不能只查存在性**）。
与 ch96 的 `mimo-v2.5-free` 不同：Go 档是 zero-retention 正式档，可进
通用 fallback。OMP：models.yml 注册（200000/32000 照 models.dev），
smol 链插在免费池之前、agnes（tiny）链尾追加。实施：
`scripts/ops/add_opencode_go_mimo_channel.py <GO_KEY> --apply`。
备份 `new-api-before-opencode-go-mimo-20260821-151200.db`。

**第三上游聚合（2026-08-21）**：Ox Alpha 第三个兜底源
`https://ai.168661.xyz`（一 key 一模型族，/v1/models 只列 `ox-alpha`）。
已建 ch102 `ai-168661-ox-alpha`（type=1，base_url 不带 /v1，NewAPI 自拼，
key argv 传入不入仓），`model_mapping` 把公开名 `x-preview-f-free` 映射
到 `ox-alpha`，与 ch96/ch100 **聚合同名**：Zen 直连（p10）> 168661
（p7/w5）> OpenRouter（p5/w5），OMP 侧零改动。实测该上游**免费**
（usage `cost: 0`、`upstream_inference_cost: 0`），聚到 ModelRatio=0 的
公开名下计费依然真实。注意两点：站点在 Cloudflare 后，浏览器 UA
header_override 必需；非流式请求存在冷启动（首次 >90s，脚本管理探针
timeout 已放宽到 100s，复测 1.7s）。实施：
`scripts/ops/add_ai168661_ox_alpha_channel.py <KEY> --apply`（备份 →
禁用创建 → 管理探针 → 启用 → 75s 缓存同步 → relay 探针 3002 ok）。
备份 `new-api-before-168661-ox-alpha-20260821-152325.db`。

**第四上游聚合（2026-08-21）**：`https://api.608885.xyz`。已建 ch103
`s608885-ox-alpha`（type=1，base_url 不带 /v1），`model_mapping` 把公开名
`x-preview-f-free` 映射到 `stealth/ox-alpha`，**p6/w5** 落在 168661（p7）
与 OpenRouter（p5）之间：Zen p10 > 168661 p7 > 608885 p6 > OpenRouter p5。
实测免费（`cost: 0`）、非流式 1.8s / 流式 3.6s 无冷启动，故排在付费档账号
的 OpenRouter 之前。**key 形态坑**：站点发放的是 base64 编码 key
（`c2st...` = base64("sk-...")），原样调用 401，解码后才可用；脚本对
`c2st` 前缀自动解码。该上游目录共 5 模型（grok-4.5/4.6、kimi-k3、glm-5.2、
ox-alpha），本渠道只映射 ox-alpha。实施：
`scripts/ops/add_608885_ox_alpha_channel.py <KEY> --apply`。
备份 `new-api-before-608885-ox-alpha-20260821-162828.db`。
