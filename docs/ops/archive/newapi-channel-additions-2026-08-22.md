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

## OMP 侧注册（同日收尾）

NewAPI 聚合完成后，把本轮 19 个新模型注册进 OMP
（`~/.omp/agent/models.yml` 的 `zg-newapi` provider 块末尾，
`qwen3-8-27b` 之后、`omp-sota-claude-opus-5` 之前）：

- 免费：`nemotron-3-ultra-free`、`nemotron-3.5-lightning-free`（ch96）。
- 付费/定价未知：`deepseek-v4-pro-0813`、`glm-5.3`、`kimi-k2.6`、
  `kimi-k2.7-code`（ch108）；`qwen3.7-max`、`qwen3.7-max-normal`、
  `qwen3.7-plus`（ch89）；`grok-4.20-0309-reasoning`、
  `grok-4.20-0309-non-reasoning`、`grok-4.20-multi-agent-0309`、
  `grok-build-0.1`、`grok-4.3`、`grok-4.5`、`grok-4.6`、
  `grok-composer-2.5-fast`、`muse-spark-1.2-contributor`、`x-preview-f`
  （ch109，后两者为付费池）。

`config.yml` 的 `retry.fallbackChains.smol` 链末尾只追加了两个免费
nemotron（`x-preview-f-free` 之后）；付费/定价未知模型不进任何兜底链
（链是手工策划的，避免免费链路烧付费额度）。

注意：

- 新条目的 `contextWindow`/`maxTokens` 是按同系模型估的保守值
  （nemotron 262144/32768、grok 系 262144/65536、qwen3.7 系
  262144/65536、whyyin 大杯 1000000/131072），**未逐一实测**，遇到
  截断/超限再按实测值收紧。
- 改动前备份：`~/.omp/agent/models.yml.bak-20260822-new-channels`、
  `config.yml.bak-20260822-new-channels`。
- 验证：`omp -p --model zg-newapi/nemotron-3-ultra-free` 冒烟出文 OK，
  证明 models.yml 可被 OMP 解析且 ch96 端到端通。OMP 配置按进程启动时
  加载，已打开的交互会话若认不出新模型需重启 OMP。

## yjs.im（JasperAPI，ch110 `yjs-free`）

分组制 NewAPI 站。踩坑：用户拿到的第一个 key 绑 **Free-Lite** 组（5k 上
下文轻量区），该组当前渠道全空——42 个模型全部 503
`No available channel ... under group Free-Lite`。完整报错里的组名是关
键线索（截断的报错看不到）。换绑 **Free** 组的 key 后 19/22 探通。

分组地形（/api/pricing 的 `enable_groups` + `group_ratio`）：

- **Free**（ratio 0，真免费）20 模型：deepseek-v4-flash×3 版本、ox-alpha、
  kimi-k3、glm-5.2、mimo-v2.5、muse-spark-1.2-contributor、hy3、
  minimax-m3、big-pickle、agnes-2.0/2.5-flash 等。
- **unlimited**（0 倍率，5k 上下文）10 个小模型；**Codex-Plus/Pro/Team**
  付费号池装 gpt-5.6-sol/gpt-5.4/5.5/5.6-terra/5.6-luna；**Grok-Super**
  付费装 grok-4.5/4.6。sol 在这站摸不到免费的。

ch110 配置：p6/w5，auto_ban，浏览器 UA header，19 个模型——

- 映射进现有免费池：`x-preview-f-free`→ox-alpha（第四路上游）、
  `muse-spark-1.2-contributor-free`、`hy3-free`→hy3。
- 进现有付费池当免费备份（ModelRatio 不动，高估成本是保守方向）：
  `k3`→kimi-k3、`glm-5.2`、`deepseek-v4-flash`、`-0731`、agnes×2。
- 新建 10 个 yjs 独有池（ModelRatio+=0）：`dots-3-note-preview`、
  `inkling`、`sensenova-6.8-flash-lite`、`step-3.7-flash`、
  `diffusiongemma-26b-a4b-it`、`gpt-oss-20b`、`glm-4.5-flash`、
  `minimax-m3`、`nemotron-3-ultra-550b-a55b`、`deepseek-v4-flash-preview`。
- 排除：`big-pickle`/`mimo-v2.5`（上游持续 429，zen 池里已有）、
  `gemma-4-31b-it`（读超时 ×2）。`glm-5.2` 管理探针 quota 警告（瞬态
  限流，路由已证通）。

脚本：`scripts/ops/add_yjs_free_channel.py`（幂等重跑）。注意首跑在
relay 验证阶段撞上 `hy3-free` 池的**本地** FreeUsageLimitError 429
（OMP relay 用户当日该池免费额度尽，与渠道无关），脚本按失败回滚禁用
了 ch110；随后手动续跑（重新启用 + 恢复 ModelRatio 合并 + 用 yjs 独有
池 `dots-3-note-preview` 做 relay 验证）完成入网。**教训：池级 relay
探针失败要先分辨是渠道问题还是本地配额/池内其他成员问题，再决定回滚。**

OMP 侧无需改动：进现有池的模型自动增强现有兜底链；10 个新池模型偏
小/ niche，未注册 OMP，需要时按 models.yml 既有格式补。

## 运维要点

- 所有新渠道已自动进入 Guardian 扫描/恢复队列，无需额外配置。
- 本轮新增四个脚本均支持幂等重跑（已存在则只探测+验证，不动状态/key）。
- whyyin/imagic 这类小聚合站的 500/超时先按"上游抖动"处理，连续失败再
  按摘除流程走；摘除用 `quarantine_newapi_channels.py`，不要手改 DB。
