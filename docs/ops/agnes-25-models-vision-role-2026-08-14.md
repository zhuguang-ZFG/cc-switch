# Agnes 2.5 模型暴露 + vision 角色降本（2026-08-14）

**Scope:** 本机 NewAPI（127.0.0.1:3002，`~/.new-api-local`）ch68/69、OMP `~/.omp/agent/models.yml`、`~/.omp/agent/config.yml`（modelRoles / retry.fallbackChains）。

## 1. 背景

排查"agnes 渠道有无更强模型未使用"。直查上游 `/v1/models`（ch68 key → `apihub.agnes-ai.com`，ch69 key → `api.agnes-ai.cn`）：

- 上游在售文本模型：`agnes-2.0-flash`、`agnes-2.5-flash`、`agnes-2.5-pro-alpha`、`agnes-2.5-pro`（另有 image/video 生成模型，非 chat 路由）。
- NewAPI ch68/69 仅暴露 `agnes-2.0-flash` + `agnes-2.5-pro-alpha`（08-04 接入时 `agnes-2.5-pro`/`agnes-2.5-flash` 未配价故未暴露）。
- OMP models.yml 无 agnes 裸模型条目，无任何角色使用。

## 2. NewAPI 变更

| 项 | 变更 |
|----|------|
| 配价 | `ModelRatio`/`CompletionRatio` 新增 `agnes-2.5-pro`、`agnes-2.5-flash`（0.5/2，与 agnes 既有条目一致；`agnes-2.5-pro-alpha` 08-04 已有价） |
| ch68 `agnes-com-haiku` / ch69 `agnes-cn-haiku` | `models` 加入 `agnes-2.5-flash`、`agnes-2.5-pro`；model_mapping 不变（裸 id 直通）；key/base_url/priority/weight 未动 |
| abilities | `POST /api/channel/fix` 重建：34 success / 0 fails；3 个 agnes-2.5 模型 × 2 渠道 abilities 落表，pri 38/39、w20/10 继承 |

**fork API 约束复踩**：本机 fork 的 `PUT /api/channel`（body 含完整渠道对象含未脱敏 key）持续返回 `Invalid parameters`，`PUT /api/channel/{id}` 返回 404——与 VPS fork 的路由形态不同。改走 DB 直改 `channels.models` 列 + `/api/channel/fix` 重建 abilities。只动 models 列是安全的：路由按 abilities 选渠道，渠道本体字段（key/base_url/mapping）未变，无需缓存失效。

## 3. OMP 变更

- `models.yml` zg-newapi 新增 `agnes-2.5-pro`、`agnes-2.5-pro-alpha`（reasoning）、`agnes-2.5-flash`，均 `input: [text, image]`，contextWindow 128000（**保守估值**，agnes 未公布实际上下文）/ maxTokens 32768。
- `modelRoles.vision`：`zg-newapi-anthropic/claude-opus-5` → `zg-newapi/agnes-2.5-pro`（opus 识图浪费额度，用户决策降本）。
- `retry.fallbackChains.vision`：`[dots-3-note-prev, qwen3.8-max]` → `[dots-3-note-prev, agnes-2.5-flash]`（qwen 出链；agnes-pro 升主后链尾换 flash）。

## 4. 实测证据

- 识图能力（64×64 纯蓝 PNG + 1×1 红 PNG，经 3002 chat/completions `image_url`）：**agnes 全部 4 个文本模型正确识图**（`image_tokens: 64`，颜色判断正确）。
- `agnes-2.5-pro` 经 3002：200，1.2s，reasoning 正常（改前为 `model_not_found`）。
- `agnes-2.5-flash` 经 3002：200，0.5s。
- 反证记录：`claude-opus-5`/`claude-opus-4-8` 经 3003 `/v1/messages` 识图实测**正常**（64×64 蓝图判断正确）——vision 换角色是成本决策，非能力修复。
- **dots-3-note-prev（ch77）连续 429**：渠道 status=1、response_time 490ms、w5，上游配额见底，撑不起 vision 主角色——这是 vision 主位给 agnes-2.5-pro 而非 dots 的直接原因。agnes 免费池上量后若同样限流，链自动掉 dots → flash。

## 5. 备份与回滚

| 物 | 路径 |
|----|------|
| NewAPI DB 改前快照 | `~/.omp/guardian/task-backups/new-api.before-agnes25-2026-08-14T1234.db` |
| OMP models.yml 改前 | `~/.omp/agent/models.yml.20260814-agnes25.bak` |
| OMP config.yml 改前 ×2 | `config.yml.20260814-vision-agnes.bak`、`config.yml.20260814-vision-cheap.bak` |

回滚：DB 恢复快照 + `POST /api/channel/fix`；OMP 还原 `.bak`；或 `omp config set modelRoles '...'` / `retry.fallbackChains '...'` 写回旧值。

## 6. 备注

- agnes 定位不变：免费池 haiku 档/兜底，不进 Opus/Sonnet 链（`docs/patches/agnes-haiku-newapi.md` non-goals 继续有效）。
- ch68 依赖本机 agnes-relay（`100.83.32.95:9460`）在线且已登录；ch69 直连 `api.agnes-ai.cn` 为快渠。
- `omp models` 已识别 3 个新条目（images=yes）。

## 7. 同日 NewAPI 审计与配价补洞（追加）

全量审计（17 enabled / 15 manual-disabled / 2 auto-banned 渠道；7 天 24755 条消费、0 条 type=5 错误日志）：

- **配价缺口修复**：`opencode-go-pro`、`dots-3-note-prev` ModelRatio/CompletionRatio 全缺、`qwen3.8-max` 缺 CompletionRatio——前两个分别在 deepseek-v4-pro fallback 第一跳和 vision 兜底链上，触发即 `price not configured` 硬错误。已补 0.5/2（与 deepseek-v4-pro 等 sibling 一致），`opencode-go-pro` 实测 200（上游映射 deepseek-v4-pro）。
- **孤儿 abilities 已清理**：18 个无活渠道模型（gpt-5.5、glm-5.2、zg-glm-5.2、cline-free/glm-5.2、claude-opus-5-max/xhigh、zg-claude-opus-5、zg-agent-claude-opus-5/4-8、welfare-codex-gpt-5.6-sol、codex-auto-review、gpt-image-2、gpt-5.4、gpt-5.6、gpt-5.6-terra、deepseek/deepseek-v4-flash、poolside/laguna-s-2.1:free、stepfun/step-3.7-flash）共 25 行已删除，abilities 114→89，剩 47 个活模型、零孤儿。删除前行快照：`~/.omp/guardian/task-backups/orphan-abilities-before-cleanup-20260814.json`；渠道重启用后 `/api/channel/fix` 可重建。glm-5.2 已核实 7 天零流量（Claude Code sonnet 链路不再经过）。验证：`deepseek-v4-flash` 200 正常、`gpt-5.5` 返回干净 `model_not_found`。
- ch2/ch70 status=3 由 guardian 恢复队列管理，不干预。
