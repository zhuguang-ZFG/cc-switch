# 福利腿三连：jhsy gpt-6-luna + lucky0625 全量 + 3.25.96.16 并发实测（2026-10-12/13 深夜）

## 0. 前置结论：opencode-go 没有下架 longcat

用户问"opencode go 把 free 模型 longcat 下了吗"。实测证伪：
ch133 `opencode-go-longcat-2.5-preview-free` 当日 20:34–23:06 **type=2 消费日志连续成功**（logs 3 天窗口零条 longcat 非成功记录），10-02 rotation runbook 的四模型清单含 longcat 未变。若上层看到 longcat 失败，来源是 `LongCat-2.5-Preview` 钉选（ch145 官方腿 status=2 禁用中）或 OMP 侧条目，不是 go 下架。

## 1. jhsy gpt-6-luna 福利腿（ch219，已上线）

- 上游：`https://ai.jhsy0721.xyz`，新 key（base64 传入，未落盘仓库）仅暴露 **1 个模型 gpt-6-luna**；直连 3/3 200（2.3–3.4s）。
- 渠道：ch219 `jhsy-gpt-6-luna`，type=1，group=default，**p30/w5**（池头，先薅免费额度；原 p20 头部 dddai ch200 变兜底，jojatoken ×4 p-20 不变），auto_ban=1，test_model=gpt-6-luna。
- 验证：management probe 3.8s 200；abilities `(default,1,30,5)`；OMP token 网关实弹 200；logs 归因最近两次 gpt-6-luna 消费落 ch219（此前全 ch200）→ 池头切换生效。
- ch201 `jhsy-glm`（同域旧 key，status=2）**未动**——新 key 无 glm-5.3-flash 权限，两 key 不通用。
- 脚本：`scripts/ops/add_jhsy_gpt6luna_channel.py`（幂等 verify-only 重入）。
- 踩坑：本 fork management probe 是 `GET /api/channel/test/{id}?model=`，写成 `/api/channel/{id}/test` 直接 404 `Invalid URL`。

## 2. lucky0625 聚合福利腿（ch220，已上线）

- 上游：`https://new.lucky0625.qzz.io`，30 id；chat 实测 **23 活**（含 chat-claude-opus-5 12.8s / chat-claude-fable-5.1 15.8s / chat-gpt-6astra 16.7s / gemini-3.8-flash 10.3s）。
- 剔除 2 死：`gemini-3.5-flash-lite`（200 但内容 "Gemini 3.5 Flash is no longer available" 上游已停）、`gemini-3.6-flash`（500 upstream service request failed）；`agnes-image-*`×3 / `agnes-video-*`×2 走 /v1/images|/v1/videos，chat 渠道不收。
- **key 级硬限流 5 req/min（失败也计数）** → 姿态取**兜底腿 p-20/w1**，绝不放池头（打满 RPM 会 429 连环 + auto_ban 抖动）。
- 定价：15 个 id ModelRatio/CompletionRatio 缺失，按免费套餐先例补 **0/0**（否则首用即 `price not configured`）；已有 10 个 id 沿用原值。
- 验证：management probe 1.26s 200；abilities 23/23 enabled；零腿 id `chat-claude-opus-5`、`gemini-3.8-flash` 网关实弹 200 且 logs 归因 ch220。
- 脚本：`scripts/ops/add_lucky0625_channel.py`（--models 参数化 + 定价自动合并 + 回滚=禁用）。
- OMP 已接（用户令「干小活」）：models.yml 新增 `gemini-3.8-flash`（200K/16K，无 reasoning 标记——未见 reasoning_content，宁缺勿错标），`modelRoles.tiny` 由 agnes-2.5-flash 切至 `zg-newapi/gemini-3.8-flash`，模型链 gemini→agnes→deepseek（RPM 打爆自动降级，role 链兜底不变）。3.7 身份实测自称 3.8 → 同上游换名，只挂 tiny 低频位。`omp bench` 1/1 TTFT 5.8s / 66.9 tok/s；路由门禁 40/40 + 对齐 CLEAN。备份 models.yml/config.yml `.bak-20261013-tiny-gemini`。

## 3. 3.25.96.16:8080 深水 dsv4.1f 并发实测（按用户令：不注册、不测活）

base64 解出 `http://3.25.96.16:8080/v1` + key（51 位）。model=`deepseek-v4.1-flash`，max_tokens=64：

| 波次 | 并发 | 结果 | 吞吐 | 延迟 |
|------|------|------|------|------|
| 1 | 10×20 | 20/20 200 | 2.5 req/s | p50 3.0s / p95 5.5s |
| 2 | 30×30 | 30/30 200 | 6.0 req/s | p50 4.1s / p95 4.9s |

30 并发零 429 零退化，HTTP 明文裸 IP。**未注册 NewAPI**（用户明令）；要接入时直接照 add_lucky0625 脚本换 base/key/models。key 已在福利群裸贴过一轮（帖子自己都承认被爬），当作公开 key 对待——即便后续注册也别放敏感模型池。

## 4. OMP 装 REA MCP（morluto/rea）

- rea 官方支持 `--client omp`（npm 6.4.0 起）。全局 `npm i -g rea-agents` 后 `rea setup --client omp --yes` 写入 `~/.omp/agent/mcp.json`，7 个既有 server 保留，备份 `mcp.json.rea.backup`。
- 首跑 npx 版注册钉在 npx 缓存路径（缓存清即坏），全局安装后二次 setup 已改钉全局路径 `npm/node_modules/rea-agents/scripts/rea.mjs`；`rea doctor --client omp` scope healthy=true（node 24.14 + registration ok；hopper/ghidra/ida 未装属 informational，需要逆向 native 时再 setup --install-hopper）。
- **本会话不会热挂载**——重启 OMP 后 xd://mcp__rea_* 工具才出现。

## 4.5 门禁既存漂移处置（tiny 接线时发现）

- 基线 `.bak-20261013-tiny-gemini` 对照跑门禁：40 项 2 失败**均为编辑前既存**（OMP_REAL_HOME 指 temp home 复现）。
- ①`slow` 链裸 `claude-opus-4-8` 重复主模型 → 已去重（安全规则，无歧义）。
- ②`fallbackChains.default`（deepseek/step 两跳）违反 08-10「default 不得有降级链」契约。曾按「去留待用户决策」先例原样恢复挂账；**用户裁决（本会话）：删链守 08-10 约**——已删，门禁回 40/40。default 失败即硬报错、不跨模型降级；重启 OMP 后运行态生效（旧会话内存链不随磁盘撤销，需新会话/切模型清态，muyuan-0814 契约注记同样适用）。

## 备份
`~/.new-api-local/backups/newapi-before-{jhsy-gpt-6-luna,lucky0625}-2026101*.db`（integrity ok）。

## 待办

- lucky0625 若上游补 gemini-3.6/3.5-lite 或放开源 RPM → 复测再议提权。
- 3.25.96.16 注册与否待用户令。
- ch219 若 jhsy 上游限流收紧（薅爆）：p30→p20 退与 dddai 同权。
