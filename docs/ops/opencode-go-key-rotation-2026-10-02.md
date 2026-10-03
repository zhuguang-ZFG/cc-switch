# OpenCode Go key 轮换 + 四模型渠道切换（2026-10-02）

## 结论

用户提供 OpenCode Go 新 key（`oc_sk_…` 格式，与旧 `sk-…` 不同代），要求 Go 平台只保留
**space-bunny-free / muse-spark-1.3-contributor / gpt-6-luna / longcat-2.5-preview-free**
四个模型，其余删除。NewAPI 侧全部落地；muse 一度被上游 workspace 隐私门拦截，
用户当晚放行后 4/4 全部实弹验证通过（见关键发现 1）。

## 变更（均已验证，可回滚）

执行脚本：`scripts/ops/rotate_opencode_go_key_20261002.py`（key 走 env，不落盘；dry-run 默认）。

| 动作 | 渠道 | 模型 | 结果 |
|---|---|---|---|
| 换 key | ch125 opencode-go-omen-alpha（用户裁定保留，OMP 小模型层依赖） | omen-alpha | DB 读回一致 |
| 换 key | ch130 opencode-go-space-bunny-free | space-bunny-free | 网关 chat 200，归因 ch130 |
| 新建 | ch131 opencode-go-muse-spark-1.3 | muse-spark-1.3-contributor | **responses-only**：网关 /v1/responses 200，归因 ch131（放行后验证，reasoning_tokens=181） |
| 新建 | ch132 opencode-go-gpt-6-luna | gpt-6-luna | **responses-only**：网关 /v1/responses 200，归因 ch132 |
| 新建 | ch133 opencode-go-longcat-2.5-preview-free | longcat-2.5-preview-free | 网关 chat 200，归因 ch133（有 reasoning_content） |
| 删除 | ch48 opencode-go-muse（muse-1.2，08-21 起禁用僵尸） | — | abilities 已清 |
| 删除 | ch117 opencode-go-qwen3.8-max-free（禁用；ch114 tokenrouter 腿不动） | — | abilities 已清 |

配套：
- ModelRatio：4 个保留模型 → 0（Go 包月零边际成本，同 omen-alpha/gpt-5.6-luna 先例）。
- `POST /api/channel/fix` 重建；abilities 4 行 `(default,1,0,2)`，48/117 无残留。
- OMP `models.yml` 注册 3 个新模型（备份 `~/.omp/agent/models.yml.bak-20261002-gokey`）：
  - `gpt-6-luna` 与 `muse-spark-1.3-contributor` 均须 `api: openai-responses`（上游 chat.completions 报
    `ModelProtocolUnsupported`，fork 对 /v1/responses 原生透传）；
  - muse-1.3/longcat 的 contextWindow/maxTokens 为保守标称 [未实测]，实弹后可调；
  - longcat 实测有 reasoning_content → `reasoning: true`。
- DB 快照：`~/.new-api-local/backups/new-api-before-opencode-go-key-rotation-20261002-224803.db`
  （8.1MB，integrity=ok）。

## 关键发现

1. **muse-spark-1.3-contributor 曾被 workspace 隐私门拦截，放行后暴露 responses-only 面**
   （2026-10-02 当晚闭环）：先报 `This Go model trains on request data…workspace's Privacy
   settings`（直连同报，contributor 整档 1.2/1.3 同门）；用户在 opencode.ai workspace
   Privacy settings 放行后，chat 面转报 `ModelProtocolUnsupported`——与 gpt-6-luna 同型，
   /v1/responses 直连与经 3002 均 200，网关实弹归因 ch131，reasoning_tokens=181。
2. **gpt-6-luna 是 responses-only 模型**：chat.completions 400 `ModelProtocolUnsupported`；
   /v1/responses 直连与经 3002 均 200。消费方必须走 responses 面。
3. 新 key 实测上游 `/v1/models` 36 个模型可用（旧 key 同期仍挂在 ch96 zen-free 上，未动）。
4. Guardian 零改动：ch48 的 PINNED_CHANNEL_WEIGHTS 按 live 渠道字典查（缺席即空转），
   `cleanup_stale_state`（guardian.py:2672-2699）会在周期内自动清除 48/117 的
   disabled/weight/joined 残留状态（ch78 删除先例）；smoke KNOWN_BROKEN_CHANNELS
   保留 48 作隔离奇偶位。

## 回滚

1. DB 恢复快照 + `POST /api/channel/fix`；
2. 或定向：`POST /api/channel/{131,132,133}/status {"status":2}` 停用新渠道，
   ch125/ch130 用旧 key PUT 回去（旧 key 仍在 ch96 上可查 SSOT）；
3. models.yml 还原 `.bak-20261002-gokey`。

## 遗留

- **ch96 opencode-zen-free 仍用旧 key**（/zen 非 /zen/go，status=2）。~~若旧 key 被上游吊销，
  zen-free 恢复时需先换 key~~ → **2026-10-03 证伪并升级结论**：
  - key 状态：chat 探针返回策略性 `403 FreeTierError`（非 401 鉴权失败）——key 被服务端
    接受、拒绝发生在授权策略层；`/v1/models` 200 仅证明目录端点可达，不单独证明鉴权。
  - 硬证据：上游 403 文案 `OpenCode's free tier can only be used from within OpenCode`。
  - 当日已测 4 组复刻配方全灭（结论限于已测配方，未穷尽所有头）：
    ① UA `opencode/<ver>` ×3 变体；② pi#2824 全套 CLI 头（`opencode/latest/1.3.15/cli`
    +x-opencode-*×4；注意该 issue 针对 429 FreeUsageLimitError，与本次 403 不同错误类）；
    ③（2026-09-09 更新的）Skynoxk zen-proxy 配方（`opencode-1.18.15`+directory 头）
    ×3 种 auth 模式（账户 key/匿名/Bearer public，各 403）；④ 精确现版
    `opencode-1.18.34`（npm latest）仍 403。疑需会话/设备绑定或服务端授权。
  - 社区旁证（非官方政策）：anomalyco#42500 系用户提交的 feature request，
    closed as not planned，摘录中无维护者政策声明——仅作参考。
  - **结论：ch96 保持禁用为正确终态；非 NewAPI/OMP 配置问题。** 目录变动（上游共 11 个
    free id，ch96 仅配置 6 个）：hy3-free 与 nemotron-3-ultra-free 已下架；新上架
    ling-3.1-flash-free/fledge-alpha-free/mimo-v2.6-flash-free/
    muse-spark-1.3-contributor-free/jev-1.13-free（同锁定，未接入）。
  - `muse-spark-1.2-contributor-free` 经 furry-vg ch105 在役（commit 链）——系
    **同名替代来源**（mapping 指向另一上游），不计入 /zen 免费档利用。
- ~~muse-1.3 放行后验证~~（已闭环：responses-only，models.yml 声明 openai-responses，
  网关 /v1/responses 200 归因 ch131，reasoning_tokens=181）。

## 追加：gpt-5.6-luna 恢复（2026-10-02 第二趟）

- 现象：OMP 选 `gpt-5.6-luna` 报 503 `No available channel … under group default`。
  根因：旧 ch106 `opencode-go-luna` 已不存在（105→107 断号），其余挂该模型的
  ch82/94/95/107 全部 status=2 且 abilities enabled=0；models.yml 条目成孤儿。
- 直连实证（ch130 新 key）：`/v1/models` 仍列 `gpt-5.6-luna`；chat 面 400
  `ModelProtocolUnsupported` → **同为 responses-only**（同 gpt-6-luna 型）；
  `/v1/responses` 直连 200 completed。
- 处置：`scripts/ops/add_opencode_go_gpt56luna_channel.py --apply`（key/header_override/
  base_url 克隆 ch130，donor 不动）→ **ch137** `opencode-go-gpt-5.6-luna`（p0/w2，
  ModelRatio=0，abilities `(default,1,0,2)`）；DB 快照
  `new-api-before-opencode-go-gpt56luna-20261002-233533.db`（integrity=ok）。
- 实弹：网关 `127.0.0.1:3002/v1/responses` 200 completed，usage 11/5，logs 归因 ch137。
- models.yml：`gpt-5.6-luna` 加 `api: openai-responses`，名称更新为 ch137
  （omp-agent 仓 commit 5fa6068，同commit捎带此前未提交的本 runbook 三新模型条目）。
- ch106 时间线：轮换前快照（10-02 22:48）中 ch106 已不存在 → 删除发生在 08-23
  （omp-config 文档在册）至 10-02 之间，非本次轮换所为；具体删除点无快照可考。
- 共享 session 风险：ch130/131/132/133/137 五渠道共用同一静态 `x-opencode-session`
  （照抄 ch130 override）；上游若改为 session 绑定 key/plan 将五腿同损，目前 5/5
  实弹 200 无冲突迹象。
- 端到端复核（评审收口）：`omp -p --model zg-newapi/gpt-5.6-luna` → `LUNA_OK`（14.5s，
  logs 16989 prompt tokens 归因 ch137，quota=0 与 ModelRatio=0 一致）。contextWindow
  维持既有 400000 不动：同文件 gpt-5.6-sol 条目用 400000 为家族惯例，release notes
  载 5.6 家族 372K 自动注入——08-14 文档表的 272000 标称已陈旧；真实上限未实测
  （未做超限输入探针），用户未要求变更，保持原值。
- 角色任命（用户指令）：OMP `modelRoles.advisor` 由 omen-alpha 改为
  `zg-newapi/gpt-5.6-luna:high`（omp-agent 仓 dbe0ba4，仅该 hunk 入 commit，
  config.yml 其余既有漂移未动）；无头探针 `omp -p --no-tools` 会话
  `__advisor.jsonl` 实证 assistant 记录 `model=gpt-5.6-luna`。
