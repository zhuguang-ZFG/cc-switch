# omp-cache-optimizer 模型家族匹配修复（2026-08-07）

**状态：** 已本地修复并验证（node_modules 补丁，插件升级会覆盖，需重打）
**对象：** `omp-cache-optimizer@1.2.3`（npm 插件，EF-FlowCode/omp-cache-optimizer）

## 症状

OMP footer 显示 `Kimi Cache | 缓存命中率：- | 缓存请求命中次数：0/0 次 | 缓存token/总输入：0/0`——当前模型 Kimi K3 已跑几十轮（$7+），统计恒为 0/0。`~/.omp/agent/omp-cache-optimizer-stats.json` 中只有 `zg-newapi/gpt-5.6-sol` 桶（16 次），无任何 `zg-newapi/k3` 桶。

## 根因

`index.ts` 的 `modelFromAssistantMessage` 把消息模型构造成 `{ id, name: id, provider, ... }`——**`name` 被强制覆盖为 wire id**，丢弃 `ctx.model`（注册模型）的显示名。家族适配器（Kimi/Hunyuan/LongCat 等）靠 `id+name` 里的家族关键词匹配：

- `zg-newapi/k3`：注册名 "Kimi K3" 含 "kimi"，但覆盖后 name="k3" → tokens 全为 "k3" → `selectAdapterForAssistantMessage` 返回 undefined → `message_end` 提前返回，永不记录。
- `zg-newapi/gpt-5.6-sol`：wire id 恰好含 "gpt" → OpenAI 适配器命中 → 正常统计（唯一有数据的桶由此而来）。

**受害模型**（wire id 不含家族关键词即不统计）：`k3`、`hy3-preview-agent`、`LongCat-2.0`、`mercury-2`、`opencode-go` 等。

用插件 `__internals_for_tests` 实测复现：`selectAdapterForAssistantMessage(k3消息, {id:'k3', name:'Kimi K3'})` → `NO_ADAPTER`；sol 消息 → `OpenAI cache`。

## 修复

`index.ts`（生产副本 `~/.omp/plugins/node_modules/omp-cache-optimizer/index.ts`）：消息与当前模型同身份（provider/id 相同）时保留注册显示名；路由到不同模型（虚拟路由场景）维持 `name: id` 原行为：

```ts
const sameIdentity =
  !!fallback && lower(fallback.id) === lower(id) && lower(fallback.provider) === lower(provider);
return {
  ...(fallback ?? {}),
  id,
  name: sameIdentity && fallback?.name ? fallback.name : id,
  ...
```

回滚原件：`index.ts.orig-1.2.3`（npm pack 提取的 1.2.3 原版，MD5 `ad092c440d32db57fb32bcc1e9fb0f46`）。回滚 = 原件覆盖回去，或重装插件。插件 npm 升级会覆盖补丁，升级后需重打或等上游修复。

## 验证（`__internals_for_tests` 直调）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| k3 消息 + k3 ctx | NO_ADAPTER | Kimi cache |
| hy3 消息 + hy3 ctx | NO_ADAPTER | Hunyuan cache |
| sol 消息 + sol ctx | OpenAI cache | OpenAI cache（不变） |
| sol 消息经 k3 ctx（虚拟路由） | OpenAI cache | OpenAI cache（路由行为不变） |
| k3 usage 归一化 | — | `{cacheRead:0, cacheWrite:0, totalInput:100}` 正常 |

## 重启后实证（2026-08-07 深夜）

OMP 重启加载补丁后，本会话首个 k3 回合即被记录：`zg-newapi/k3` 桶 `totalRequests=1, hitRequests=1, cachedInputTokens=31488 / totalInputTokens=288037`（命中率 10.9%）。**附带纠正一个先前判断**：NewAPI k3 链路会真实返回缓存 usage（非「恒 0%」），footer 可显示真实命中率。

## 全量模型适配器审计（24 个注册模型，`__internals_for_tests` 逐个实测）

| 分类 | 模型 | 结果 |
|---|---|---|
| 本 bug 受害、修复后恢复 | `zg-newapi/k3`、`codebuddy/hy3-preview-agent`、`zg-newapi/opencode-go` | Kimi / Hunyuan / DS cache |
| 一直正常（21 个） | gpt 全系（zg-newapi×8、codebuddy、agentrouter）、claude 全系（zg-newapi-anthropic×2、anyrouter）、deepseek×2、qwen3.8-max、grok-4.5、kimi-for-coding、sensenova-6.7-flash-lite 等 | 各自家族适配器 |
| 仍无适配器（家族覆盖缺口，非本 bug） | `mercury-2`（Inception Labs）、`intern-s2-preview`（插件只认 internlm/intern-lm）、`LongCat-2.0`（无 LongCat 家族） | footer 不显示缓存行，无误导 0/0；需上游新增家族定义 |

**独立维度**：适配器匹配只决定「能否统计」；命中率真实性取决于各上游渠道是否回缓存 usage 字段（k3 已实测回 cached_tokens），与插件无关。

**生效条件**：运行中的 OMP 需 `/reload` 或重启才加载补丁；k3 统计从 reload 后开始累积（已实证，见上节）。上游不回缓存字段的渠道命中数仍为 0，但请求数与总输入会正确累计，命中率显示 0% 而非 `-`。

## 后续

- 上游修复建议：`modelFromAssistantMessage` 保留 fallback 显示名（同身份时），或适配器匹配时合并注册模型 name。可向 EF-FlowCode/omp-cache-optimizer 提 issue。
