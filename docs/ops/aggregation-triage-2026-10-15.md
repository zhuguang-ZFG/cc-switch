# 聚合渠道报错定位与修复（2026-10-15 晚）

用户报「好多模型聚合在一起会报错」。方法：对 16 条 enabled 多模型渠道做
**84 对 (渠道×模型) 探活矩阵**（每对 1 次 + 失败复测 1 次）→ **63 OK / 21 FAIL**；
按错因分类后裁目录、修链。

## 错误来源定性（先分清本地 vs 上游）

- `No available channel for model {{.Model}} under group {{.Group}} (distributor)`
  → **本地 fork 自己发的**（`new-api.exe` 内含该模板串）＝本地无渠道路由；
- `Not enough quota to pre-consume ...` → **上游透传**（二进制中无此串），
  即某个 NewAPI 类转售网关的账号余额不足以预扣。

## 21 个 FAIL 的分类与处置

| 类 | 配对 | 证据 | 处置 |
|---|---|---|---|
| **上游不提供该模型**（目录漂移） | ch15 `sensenova-u1.5-lite`；ch188/189/190/191 `gpt-5.5` | 404 `model is not found`；503 `auth_unavailable: ... gpt-5.5 is not supported when using Codex`（4 渠道同错） | **从目录裁剪**（6 渠道 PUT，写前快照 `new-api-before-catalog-trim-20261015-193212.db`，readback + abilities 归零核验） |
| **渠道活、该模型死** | ch118 `qwen3.8-max`(502)、`grok-4.5`(500)、`qwen3.7-max`(502→429 risk-control)、`claude-sonnet-5`(402 balance) | 同渠道 `glm-5.3-flash`/`deepseek-v4-flash` 均 200；前三者净窗复探第二次仍 500 | 同上裁剪（ch118 → 2 模型） |
| **整渠死（既知）** | ch3 ×4（100s 超时）、ch72 ×5（429 窗口）、ch204 ×3（本地 workbuddy 桥 down） | 与既有 runbook 一致 | 不裁（ch3/ch72 已有恢复机制；ch204 桥恢复即回） |

**修复效果**：这些名字在被路由时不再打到"不可能成功"的渠道（快速本地失败或
路由到其他腿），错误从"上游随机报错"变为可归因。

## OMP 侧联动

- **4 个新 orphan 入册**（alignment CLEAN 恢复）：`qwen3.8-max`、`qwen3.7-max`、
  `grok-4.5`、`gpt-5.5`（各自附再启用条件：seeseed 恢复 / jojatoken 支持 /
  midjok key 复活）。
- **死链修复**（`config.yml`）：
  - `vision`：`[qwen3.8-max, step-3.7-flash]`（两跳全死）→
    **`[zg-newapi-anthropic/intern-s2-preview, zg-newapi/dots-3-note-prev]`**；
  - `plan` 第二跳：`qwen3.7-max`（死）→ `zg-newapi/glm-5.3`；
  - `slow`：本晚早先挂的 `qwen3.8-max`（正好被裁）→
    `zg-newapi-anthropic/intern-s2-preview`；
- **compaction 候选**（extension）：`gpt-5.5` → `glm-5.3-flash`；删
  `glm-5.2`、`step-5-preview`（全暗）。

**如实记录一处自己的错误**：首次把 `intern-s2-preview` 写成
`zg-newapi/intern-s2-preview`，被静态门禁
`test_live_fallback_chains_have_no_hard_violations` 当场拦下（该 id 注册在
**`zg-newapi-anthropic`** provider）；改为 anthropic 选择器后 40/40。

## 单独归因：`Not enough quota to pre-consume`（上游余额类，**不在裁剪/链修复覆盖内**）

今天会话里最高频的一类报错（OMP 日志 11:54–13:27 ×5，model=`gpt-5.6-sol`）：

- 文案不在 `new-api.exe` 内 → **上游透传**（NewAPI 类转售网关的按请求预扣检查）；
- `gpt-5.6-sol` 的 enabled 腿**只有 ch188-191（同一站点 jojatoken.com）**；
  四把 key 的 sha256 指纹互不相同（=4 个独立 key），但无法读取各自余额；
  失败报文里的 "remaining" 历次为 `$0.0079/0.1882/0.1949/0.1952/0.3038`
  —— 有升有降，说明腿间/时点上余额各不相同（可能非同账号）；
- 直连逐 key 大 body 探针被 **Cloudflare 1010**（非浏览器 UA）拦下 → 无法逐
  key 读余额（也提示 jojatoken 渠道依赖既有的 header_override UA 策略）；
- 网关复现：同尺寸大 body（~200k 字符）经 127.0.0.1:3002 → **200（7.2s）**，
  当前不复现 → 该失败是"请求越大预扣越多 vs 上游余额近空"的**尺寸/余额型间歇**；
- 重试侧：`AutomaticRetryStatusCodes` 含 500-503、`RetryTimes=1` → 仅一跳重试，
  能否救取决于该腿是否有余量；本地裁剪与链修复**都碰不到这一类**。

**可选项**：给 jojatoken 账号充值 / 为这 7 个 gpt-* 名字找第二个货源 / 接受大
上下文的间歇 503（小请求实测可用）。

### 下游影响：compaction/memory 的大请求路径（本轮补测，~100k prompt）

compaction 是真发大请求的调用方（归档文档实弹：140,662-token 一次在 head 上
67.9s 完成，`docs/ops/archive/omp-global-compaction-model.md:159`）。同尺寸
（~100k prompt）网关实测：

| 候选 | 结果 |
|---|---|
| `deepseek-v4-flash`（候选**首位**） | **200 / 7.2s**，落 **ch180**，prompt=100,031 → **能扛大请求** |
| `gpt-5.6-terra`（jojatoken，候选 #3） | **200 / 7.5s**，ch191，prompt=100,303 |
| `gpt-5.6-sol`（jojatoken，候选 #4） | **503 pre-consume**：`remaining $0.003750 < required $0.080008` ← 该类**复现** |

结论：① 候选顺序已把能扛大请求的 dsv4f 放首位（大 session 首选不受影响）；
② jojatoken 四腿**并非同一账号**（terra 200 vs sol 503 分化）——是 **sol 这条
腿的钱包干了**，不是"jojatoken 必拒"；③ 只有 head 与 terra 都不可用时，大
session 才会落进 sol 并吃 503。

另：`global compaction model unavailable`×2 与 quota **无关**——发生在
17:08/17:09（重启前），原因是当时 reconciler 在 registry 里找不到任何可用候选
（分辨率失败，非大请求）；17:18 重启后每轮 `target=dsv4f / inspected=83` 正常，
无复发。

revision：候选改动后 **r12 → `2026.10.15-r13`**（双副本同步；扩展测试
21/21、`omp-extensions` 100/100）。

## 附带修（与聚合无直接因果，单独列出）

- **compaction 双副本漂移**（评审发现）：repo 源 `scripts/ops/omp-global-compaction-model.js`
  停在 r11（含 `gpt-5.5`/`glm-5.2`/`step-5-preview`，还带 live 早已删掉的
  `qwen3.8-flash-next`），而 `deploy-omp-global-compaction-model.ps1` 默认
  repo→live 覆盖（带备份）→ 再部署会回滚今晚的候选修复。**已把 repo 源同步为
  live r12 内容**（两副本 byte-identical → 部署变 no-op）。
- **扩展测试同步**：`test_omp_global_compaction_model.js` 也停在 r11
  （5 例失败：TARGET=longcat、注册表 mock 缺 dsv4f、glm-5.2 断言）→ 更新为
  r12 口径（TARGET=dsv4f、4 个 mock 注册表带 dsv4f、glm-5.2 断言改用
  glm-5.3-flash）→ **21/21 通过**。
- **门禁补口**：`omp-extensions` check 的 paths/args 增补
  `omp-global-compaction-model.js` + `test_omp_global_compaction_model.js`
  （此前完全未覆盖，双副本漂移无门禁）→ 该 check 全量跑 **100/100**。

## 附带发现

- `muse-spark-1.2-contributor-free` **未定论**：矩阵 1 次 200（9.1s）+ 追加 2 次
  间隔复探 200（共 3 连），**但上游 `opencode models` 目录仍未回列该 id**（只有
  1.3）。按"单次/少量 200 不等于健康"，**不宣布自愈、不取消 24h 宽限**：明日
  再多次复探 + 目录核对再定是否 `--allow-drop` 摘除。
- 全部改动的生效条件：`config.yml`/extension **下次 OMP 启动**；渠道目录裁剪
  即时生效。

## 门禁（终态实测）

- `test_omp_routes` **40/40 OK**；`verify_omp_newapi_alignment` **CLEAN**；
  冒烟仍 **5 类**（无新增）；`test_ops_gates` **11/11**；
  `omp-extensions`（含新增 compaction 覆盖）**100/100**。

## 回滚

```bash
# 目录裁剪：cp ~/.new-api-local/backups/new-api-before-catalog-trim-20261015-193212.db <db>（停机）
# 链/候选：git -C ~/.omp/agent revert <本 commit>
```
