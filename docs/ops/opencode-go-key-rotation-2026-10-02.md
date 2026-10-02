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

- **ch96 opencode-zen-free 仍用旧 key**（/zen 非 /zen/go，status=2）。若旧 key 被上游吊销，
  zen-free 恢复时需先换 key——届时向用户索取 zen 侧 key。
- ~~muse-1.3 放行后验证~~（已闭环：responses-only，models.yml 声明 openai-responses，
  网关 /v1/responses 200 归因 ch131，reasoning_tokens=181）。
