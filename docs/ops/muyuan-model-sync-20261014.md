# muyuan ch173 模型目录同步（2026-10-14）

上游 muyuan.do 轮换目录：token 组可见 id 从 31 缩到 7（4 个聊天存活 +
`jev-latest`/`jev-preview` + key2 多 `glm-5.2`）。本 runbook 记录 ch173
裁剪与 OMP 注册同步全链。

## 上游证据（2026-10-14，浏览器 UA 过 Cloudflare 指纹门）

`/v1/models`：key1=7 id，key2=8 id（多 `glm-5.2`）。逐模型双 key 聊天探测：

| id | key1 | key2 | 处置 |
|---|---|---|---|
| deepseek-v4.1-flash | 200 | 200 | 保留 |
| glm-4.5-flash | 200 | 200 | 保留 |
| glm-5.3-flash | 200 | 200 | 保留 |
| grok-4.7 | 200 | 200 | 保留 |
| jev-latest / jev-preview | 400 `invalid_request`（全 payload 形态） | 同左 | 剔除——结构化决策模型，官方桥在 ch181 `jev-systemone` |
| glm-5.2 | 503 `model_not_found` | 500 | 从 ch173 剔除（活腿：ch220 lucky0625 p-20） |
| qwen3.8-27b | 504 ×5/1h（目录在册，聊天经 Cloudflare 恒 504；5 次 admin test 全 504） | 同左 | **剔除**——注册门（聊天 200）拒绝；若上游修复可重加 |
| 其余 21 id（mistral/ministral/codestral/voxtral 家族、qwen3.8-flash-next、qwen3-8-27b 旧形） | 503 `model_not_found ... under group default` | 同左 | 剔除 |

## NewAPI ch173 变更（scripts/ops/sync_muyuan_models_20261014.py --apply）

- 备份 `new-api-before-muyuan-sync-20261014-213645.db`（调查前）与
  `-224602.db`（apply 前，64 MB，integrity ok）。
- PUT models → **4** 存活 id；`model_mapping` 清掉旧
  `{"qwen3-8-27b":"qwen3.8-27b"}` 映射；`test_model`
  `ministral-3b-latest` → `glm-4.5-flash`（下线 id 会让周期探针误报）。
- POST fix 重建 abilities；PUT 怪癖未漂移（行全保持 `(default,1,20,1)`，
  改动前快照比对）；读回 models=4、mapping={}、`channel_info`
  `is_multi_key=true size=2` 全过。
- admin test **4/4 一次 200**（dsv41f 0.997s / glm-4.5f 13.6s /
  glm-5.3f 3.1s / grok-4.7 6.3s）。
- 网关归因：glm-4.5-flash relay 200 归因 **ch173**（sole source，裁剪后
  首笔实流）；glm-5.3-flash relay 200 归因 ch118（p25 池头，正常池路由）。

## OMP 变更（备份 `models.yml.bak-20261014-muyuan-sync` /
`config.yml.bak-20261014-muyuan-sync`）

- `models.yml` zg-newapi 86 → **68**：删 18 条 sole-source 死条目（17 条
  503 家族 + qwen3.8-27b；脚本块级删除，回证明：同 span 删备份+全部
  name 行编辑逐字节复现新文件；yaml parse + id 集合断言）。
  **注意：本文件现为 LF（备份即 LF，1146→1016 行恰为删除行数）；10-10
  CRLF 事故后已非 CRLF，勿再按 CRLF 假设编辑。**
- name 修正：`glm-5.2` 主来源改标 `lucky0625 ch220`（sensenova ch15 已死，
  abilities 仅剩 ch220 enabled）。
- `config.yml` 三处重指：
  - `modelRoles.vision` `voxtral-small-latest` → `glm-4.6v`（ch185 p30，
    活；探针 200 且正确描述测试图内容——推理模型，小 max_tokens 时
    content 为空是正常截断形态，非故障）；
  - `fallbackChains.vision` 原 `[voxtral-small-2507, agnes-2.5-flash]` →
    `[qwen3.8-max, step-3.7-flash]`（两 voxtral 死；agnes 探针 403
    「账号异常活动」且门禁禁链内重复主选）；
  - `mnemopi.embeddingModel` `mistral-embed-2312` → `jina-embeddings-v3`
    （ch187，1024 维对齐；实测 200 dim=1024 归因 ch187）。
- **会话期间 OMP 自行把 `modelRoles.default` 写成
  `zg-newapi/kimi-for-coding:max`（用户切模型，非本 runbook 操作）；
  门禁要求 kimi 主选必须带审批链，已补
  `zg-newapi/kimi-for-coding: [zg-newapi/deepseek-v4-flash]`（审批形状）。**

## 门禁（终态实测）

- `python -m unittest scripts.ops.test_omp_routes` → **40/40 OK**。
- `verify_omp_newapi_alignment.py --db C:/Users/zhugu/.new-api-local/new-api.db`
  → orphan 18 → **1**（余 `Atria-Dawn-Preview`，ch139 手动禁用，改动前
  已在册）；9 quarantined 全为已知死腿（ch178 桥 CLI、ch203 周期配额、
  ch175 手动停、ch222 key 过期），无 muyuan 相关新增。
- `newapi-local-smoke.py` → FAIL 集合与改动前基线（19:25 轮）逐项相同：
  automatic channel recovery / channels / ai.168661 posture / opus 双池 /
  opus 容量 / unregistered model ratios / pool answer rate。无新增失败类。
- guardian.log：ch173 相关仅 20:58 旧 test_model 503（裁剪前）与 21:27–
  21:39 qwen 504 尾部样本；无 auto_ban/禁用动作。

## 计费事实（未改动，仅记录）

7 日内 ch173 按隐含 ratio 37.5 计费最高的是 `qwen3.8-flash-next`
（3687 次 / 154 亿 quota——该 id 上游已 503，随裁剪自然止血）。
存活 4 id 里仅 `glm-5.3-flash` 有显式价（0.075/3.333），其余隐含
37.5 或 0（grok-4.7 CR=0）。ModelRatio 缺口是存量债务（workbuddy
runbook 在册 43 名），本轮未注册新价——上游成本结构不明，钉错价比
缺口更糟，留待用户裁决。

## 遗留与风险

1. **qwen3.8-flash-next 无替代**：22/40 近期 ch173 实流、最大 195k
   prompt 的长会话主力，随上游轮换消失；新目录的 qwen3.8-27b 亦因
   聊天恒 504 未能接棒。建议长会话用户迁 `qwen3-8-27b`（ch88/124/171，
   OMP 注册在册）或 `gemini-3.8-flash`。若 muyuan 修复 qwen3.8-27b
   （聊天恢复 200），按脚本重加即可。
2. ~~models.yml 待 OMP 重启生效~~ **已完成（2026-10-14 用户重启 OMP）**：
   重启后复核 `test_omp_routes` 40/40 OK；`omp models` 在册 glm-4.6v /
   jina-embeddings-v3 / glm-5.2，已删 18 死 id 全部不可见；glm-5.3-flash
   （advisor 新主选）relay 200 归因 ch183。config.yml 全部重指生效。
3. **agnes-2.5-flash 403**：ch68/69/220 载体之一账号受限；已移出
   vision 链，但 24h 仍有 210 笔计费流——若 403 持续，其渠道可能被
   auto_ban，留意。
4. **OMP 重启后复核**：重启后跑 `test_omp_routes` + 抽
   `omp models | grep -E "glm-4.6v|jina-embeddings-v3"`。

## 回滚

```bash
# NewAPI：整库还原（在线备份）
cp ~/.new-api-local/backups/new-api-before-muyuan-sync-20261014-224602.db ~/.new-api-local/new-api.db
# 并重启 new-api-local 服务
# OMP：
cp ~/.omp/agent/models.yml.bak-20261014-muyuan-sync ~/.omp/agent/models.yml
cp ~/.omp/agent/config.yml.bak-20261014-muyuan-sync ~/.omp/agent/config.yml
# 注意回滚 config 会丢失用户切的 default=kimi-for-coding 及其审批链
```

## 涉及文件

- `scripts/ops/sync_muyuan_models_20261014.py` — 裁剪+断言+门禁一体脚本
  （dry-run 默认，`--apply`；`--relay-only` 只跑网关归因；key 从 DB
  进程内读取，永不打印）
- 备份：DB ×2（21:34:51 调查前 + 22:46:02 终态 apply 前）、models.yml /
  config.yml ×1（21:39）

---

## 快照引用状态（2026-10-16 补记）

本页引用的整库快照已被轮转回收（`backups/` 为 KEEP=7 轮转缓存，非归档；
见 `backup-rotation-lexicographic-bug-2026-10-16.md`）：

- `new-api-before-muyuan-sync-20261014-213645.db` —— 本 bug（supervisor 轮转按字典序删除）+ KEEP=7 设计
- `new-api-before-muyuan-sync-20261014-224602.db` —— 本 bug（supervisor 轮转按字典序删除）+ KEEP=7 设计

回滚请以本页的逐字段步骤为准（快照只是加速器，不是前提）。
