# NewAPI 渠道批量入网（nemotron / whyyin / seeseed-qwen / imagic，2026-08-22）

四个来源，全部走完 add_* 标准合约（备份 → 建禁用/扩模型 → 探活 → 启用 →
回读验证 → 3002 relay 逐模型探测）。每次变更前都有整库快照备份，位于
`~/.new-api-local/backups/`。

## Zen 免费新模型：nemotron ×2（ch96 扩模型）

- Zen `/v1/models` 新增 `nemotron-3-ultra-free`、`nemotron-3.5-lightning-free`，
  直连探测（浏览器 UA，裸客户端 CF 1010）均 200 出文。
- ch96 `opencode-zen-free` models 5→7，两模型各自成池，ModelRatio=0。
- `deepseek-v4-flash-free` 也在列表里复活但上游仍坏
  （400 "Model is unavailable"），**不入**。
- 脚本：`scripts/ops/add_opencode_zen_nemotron_models.py`。
- 教训记录：比率修正逻辑必须按"值不等于目标"判断而非"键不存在"——
  nemotron-3-ultra-free 已有非零比率残留，第一版脚本漏改导致回滚
  （已修复并注释）。另一次失败是 Nvidia 上游 502 瞬时过载，重试即过。

## whyyin 聚合站（ch108，新渠道）

- 来源：用户提供 `http://v4.whyyin.cn:28327`。直连探测 7/7 全通
  （OpenAI 兼容，无特殊头）。
- p49/w5，定价未知故不动 ModelRatio（走默认倍率）。
- 池布局：
  - 聚合进现有池做备份：`deepseek-v4-flash-0731`（ch15 p50 主）、
    `glm-5.2`（ch15 p50 主）、`k3`（ch33 kimi-official p50 主）。
  - 新建池：`deepseek-v4-pro-0813`、`glm-5.3`、`kimi-k2.6`、`kimi-k2.7-code`。
- model_mapping 把池内小写名映射回上游大写原名（如 `k3 → Kimi-K3`）。
- 注意：`Kimi-K3` 是慢速推理模型，首 token 约 2 分钟（90s 探测超时，
  240s 成功）；管理探针 test_model 因此用 `kimi-k2.6`，relay 探测 K3
  单独放宽超时。看到 K3 "超时"先区分是首 token 慢还是真故障。
- 脚本：`scripts/ops/add_whyyin_channel.py`。

## seeseed ch89 扩 qwen ×3

- 用户重发同一来源+同一 key（sha256 与库存一致），意图是扩覆盖而非换 key。
- 直连探测：`qwen3.7-max` / `qwen3.7-plus` / `qwen3.8-max` 全 200；
  `gpt-5.6-sol` 401（key 分组无权限）、`gpt-5.6-terra`/`gpt-5.4`/`gpt-5.5`
  500、`longcat-2.0-free` 503 无可用渠道——均不入。
- ch89 `seeseed1ck-hydrogel` models 2→5：qwen3.7-max/plus 为新池，
  qwen3.8-max 成为首个启用渠道（ch31 禁用）。
- 脚本：`scripts/ops/add_seeseed_qwen_models.py`。

## imagic 聚合站（ch109，新渠道）

- 来源：用户提供 `https://newapi.imagic.eu.org`。**必须浏览器 UA**
  （CF 1010），走 `header_override`，同 furry 做法。
- 探测 6/6 全通，p0/w5，定价未知不动 ModelRatio：
  - 新池/首个启用：`grok-4.20-0309-reasoning`、`grok-4.20-multi-agent-0309`
    （ch39 同名但禁用）、`grok-build-0.1`（Grok Build 模型首个可用渠道）、
    `x-preview-f`（付费版 Ox Alpha）。
  - 池内备份：`grok-4.6`（与 ch89 聚合）。
  - `muse-spark-1.2-contributor`：付费名独立成池，**不**并入
    `-free` 免费池（价格未知，避免污染免费池成本核算）。
- 脚本：`scripts/ops/add_imagic_channel.py`。

## 补漏 sweep（同日稍后）

首轮后自查模型列表，补探了漏网模型并补入：

- ch89 seeseed += `qwen3.7-max-normal`（ch89 共 6 模型）。
  弃：`gpt-5.6-luna`（key 有权限但月额度尽，15 天后重置，死渠道不入）、
  `mimo-v2.5-free`（200 可用但定价未知，id 与免费池同名——按 imagic muse
  同款原则不混免费池）、`gpt-oss-120b`/`codex-auto-review`（500）。
- ch109 imagic += `grok-4.20-0309-non-reasoning`、`grok-4.3`、`grok-4.5`
  （后两者为首个启用渠道）、`grok-composer-2.5-fast`（新池）、`mimo-v2.5`
  （入 ch101 池备份）。ch109 共 11 模型。3 个 grok-imagine-image 为图像
  生成模型，聊天 relay 用不上，刻意跳过。
- 脚本：`add_seeseed_qwen_models.py`（幂等重跑）、
  `scripts/ops/add_imagic_extra_models.py`。

## 运维要点

- 所有新渠道已自动进入 Guardian 扫描/恢复队列，无需额外配置。
- 本轮新增四个脚本均支持幂等重跑（已存在则只探测+验证，不动状态/key）。
- whyyin/imagic 这类小聚合站的 500/超时先按"上游抖动"处理，连续失败再
  按摘除流程走；摘除用 `quarantine_newapi_channels.py`，不要手改 DB。
