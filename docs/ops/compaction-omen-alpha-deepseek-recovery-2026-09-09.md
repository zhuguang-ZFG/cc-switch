# 压缩模型切 Omen Alpha + DeepSeek 渠道复活与兜底重组（2026-09-09）

## 结论

两件事同日完成：

1. **OMP 全局压缩模型切 `zg-newapi/omen-alpha`**：扩展候选表首位换 omen-alpha 并把 zg-newapi/deepseek-v4-flash 移出候选；models.yml 88 处 `compactionModel:` 全量改 omen-alpha；config.yml 4 处 fallbackChains 的 deepseek 兜底位全换 omen-alpha。
2. **DeepSeek 渠道全线诊断 + 复活**：ch118 seeseed 429 配额（慢性）+ ch15 sensenova 复活接管主路 + `agentrouter/deepseek-v4-flash`（Tailscale 直连）进压缩候选表作第一兜底。relay 端到端 200/2s 验证。

## 一、压缩模型切换

### 候选表（r7 源码，repo 与 live 哈希一致 `b25228dd…CD8DC493B`）

```text
zg-newapi/omen-alpha              ← 主压缩模型（用户指令）
agentrouter/deepseek-v4-flash     ← 第一兜底（Tailscale 直连 100.83.32.95:8788，实测 3 探 2 成 1 抖）
zg-newapi/glm-5.2                 ← 预留（models.yml 未注册，reconciler 跳过）
zg-newapi/qwen3-8-27b             ← 尾部兜底
```

- `zg-newapi/deepseek-v4-flash` 移出候选表（注释留痕）：seeseed ch118 处于 720h 滚动配额硬拦 + 500 抖动，作为主压缩模型已不可靠。
- omen-alpha 的权重风险此前已由 commit/smol 角色切换评估过（见 `omp-smol-chain-cleanup-2026-09-04.md`）；本日压缩链复用同一判断。
- **生效时机**：扩展模块在 OMP 会话启动时加载——运行中会话持启动快照，`omp config set` 热重载只覆盖 config.yml，不重载扩展 JS。新会话起 omen-alpha 即压缩目标；可用 `/compaction-status` 核对。

### 改动与备份（全部可回滚）

| 文件 | 改动 | 备份 |
|---|---|---|
| `~/.omp/agent/extensions/omp-global-compaction-model.js` | 候选表 [omen, agentrouter/deepseek, glm-5.2(预留), qwen] | 同目录 `.bak-20260909` + `extension-backups/omp-global-compaction-model-20260909-221013-*`（部署脚本时间戳备份） |
| `~/.omp/agent/models.yml` | 88 处 `compactionModel:` deepseek-v4-flash → omen-alpha | `models.yml.bak-20260909` |
| `~/.omp/agent/config.yml` | fallbackChains 4 处（muse-spark-free / qwen3-8-27b / qwen3.8-max-free 单兜底位 + smol 链首位）deepseek → omen-alpha | `config.yml.bak-20260909-omen-chains` |
| 仓库 `scripts/ops/omp-global-compaction-model.js` | 源副本同块移植（部署脚本 SHA 双端校验） | git |

已知接受项（用户明示）：smol 链主备同为 omen-alpha（空心链，充当同渠道一次链内重试）；`scripts/ops/test_omp_routes.py` 的 primary-repeat 门禁为该角色加了显式豁免 `ACCEPTED_PRIMARY_REPEAT = {"smol"}`，其余角色保持硬门禁。

### 验证

- `node --test scripts/ops/test_omp_global_compaction_model.js` → 21/21（测试桩 TARGET 全量迁移 omen-alpha；优先序用例覆盖 [omen 头 / agentrouter/deepseek 次 / glm-5.2 预留 / qwen 尾]）。
- `node --test scripts/ops/test_omp_global_compaction_deploy.js` → 1/1。
- `python3 -m unittest scripts.ops.test_omp_routes` → 40/40（SOTA 隔离门禁的 compactionModel 钉 deepseek → omen-alpha）。
- `deploy-omp-global-compaction-model.ps1` → repo/live SHA-256 一致 `b25228dd…CD8DC493B`（r7 复跑：21/21 + deploy 1/1 + routes 40/40）。
- 扩展直测（node 内联 import）：全注册→omen-alpha；omen 缺席→agentrouter/deepseek-v4-flash；仅 agentrouter→解析成功。

## 二、DeepSeek 渠道全景与处置

### 渠道体检结论（双轮探针 + NewAPI 日志 + Guardian 三源交叉）

| 渠道 | 上游 | 状态 | 证据 |
|---|---|---|---|
| ch15 | sensenova-token | **复活启用**（此前禁用） | 2/2 渠道测试过（1.7-2.8s）；08-28 的 429 workspace-quota 已消退；relay 200/2s |
| ch118 | seeseed | 429 慢病 + 500 抖动 | 720h 窗口 18001/18000 请求；单日 268 条 ERR；Guardian 20:10 自动降权 5→2 |
| ch89 | seeseed hydrogel | grok-chat-fast 活；grok-4.6 抖（401↔503）；qwen 四模型当日新发持续 500 | NewAPI 日志 + 探针 |
| ch110 | yjs-free | 账号 403 封禁 | 探针 |
| ch107 | zzzcoding | token 池空 502 | 探针 |
| ch108 | whyyin | 死（勿复援） | `agentrouter-waf-glm53-2026-09-04.md` |
| 直连 | agentrouter 100.83.32.95:8788 | 活（3 探 2 成 1 抖，4-9s） | 绕 NewAPI，key 在 models.yml（本地仓） |

死端：`deepseek-v4-pro-0813` 唯一服务渠道 ch108 已死且 ch15 不覆盖该 id → NewAPI 侧不可复活，models.yml 摘除待定。

### ch15 复活（本日处置）

- 机制：`channels.status=1` + `abilities.enabled=1` 双置（guardian/zzgate 同款机制）。priority 50 > ch118 的 30 → **接管 deepseek-v4-flash 主路**，ch118 自动降为兜底。
- 副作用：sensenova-6.7-flash-lite / glm-5.2 / sensenova-u1-fast 同时恢复路由（同渠道 4 模型）。
- 验证：DB 前后 SELECT；NewAPI relay `POST /v1/chat/completions` model=deepseek-v4-flash → HTTP 200 / 2s（ch118 429 硬拦下仍 200 = 路由确认落 ch15）；GET 渠道对象完整性核验（key 未损）。
- NewAPI 内存同步（sync goroutine）约 1-2 分钟，DB 写后需等待再探。

### NewAPI 渠道 API 契约（guardian.py:745-850 实测定案）

- `PUT /api/channel/` 请求体**禁带 `status` 字段**（带即 `Invalid parameters`）；guardian 自己更新前会剥 status 并从本地 SSOT 回填未掩码 key。
- 启/停用走专用端点：`POST /api/channel/{id}/status` + `{"status": 1|2}`。
- 渠道 ops 自动 `UpdateAbilities` 同步 abilities 表；异常时 `POST /api/channel/fix` 全量修复。
- `GET /api/channel/{id}` 抹 key（返回空串）——**勿把 GET 响应原样 PUT 回去**（会抹 key）。
- 兜底路径：SQLite 直写双置（本机 `~/.new-api-local/new-api.db`）；python3（scoop shim）可用，`py -3` 无 runtime。

## 回滚

1. 压缩链：恢复三个 `.bak-20260909` 文件 + 用部署脚本回灌扩展（或 `extension-backups/…221013` 目录内 previous 文件）→ 新会话生效。
2. ch15：`POST /api/channel/15/status {"status":2}`（或 DB 双置回 2/0）→ 恢复 ch118 主路姿态。
3. 仓库：git revert 本次 docs/scripts 提交即可（源与测试在同一提交内）。

## 未决

- `deepseek-v4-pro-0813` 是否从 models.yml 摘除（死端 selector，无消费渠道）。
- ch118 止损（禁用止 retry 风暴 vs 等 720h 窗口自然滑出）——当前靠 Guardian 降权压制。
- ch89 qwen 系当日新发 500 待观察（上游抖动，不排除自愈）。
