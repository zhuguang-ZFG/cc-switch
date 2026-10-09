# intern-discovery ch140 接入（glm-5.3 / intern-s2 / deepseek-v4-flash-vision，2026-10-03）

## 结论

用户提供单 key + 端点 `https://discovery-api.intern-ai.org.cn/v1`（Intern/书生 discovery 网关），
要求接入本地 NewAPI。已建 **ch140 `intern-discovery`**；同日应要求追加 `deepseek-v4-flash-vision`，三模型 channel test 全绿。

## 上游直连证据（创建前）

| 探测 | 结果 |
|---|---|
| `GET /v1/models` | 200（350ms），目录 10 模型：Agents-A1, Atria-Dawn-Preview, deepseek-v4-flash-0731/vision, deepseek-v4-pro-0813, glm-5.3, intern-s2, kimi-k2.6, minimax-m3, qwen3.8-27b |
| `chat/completions intern-s2` 非流式 | 200（3.6s） |
| `chat/completions glm-5.3` 非流式 | 首次 60s 读超时；流式 200（TTFT 539ms，643ms 完成）；非流式复测 200（546ms）→ 首超时判为瞬态 |
| `chat/completions glm-5.3` 流式 | 200，TTFT 539ms |
| `chat/completions deepseek-v4-flash-vision` 非流式纯文本 | 200（843ms，usage 87/8） |
| `chat/completions deepseek-v4-flash-vision` 带图 | 200 多次：image 确被摄取（`multimodal_tokens.image=116`）；强 reasoning 型——max_tokens=16/128 时预算全耗于 reasoning 致 `content=null`；@1024 实测 `content='Red'`（识图正确，finish=stop） |
| 内容闭环复测 | glm-5.3@128 → `content='Pong'`；intern-s2@512 → `content='pong'`（reasoning_content 型，≤64 预算下 content 为空）；vision@1024 → `'Red'` |

## 冲突分析（建前渠道清单 + abilities 表）

- `intern-s2`：ch66/67 `internlm-s2-a/b`（prio50 w5）实际 abilities 是 **`intern-s2-preview`**，与 `intern-s2` 不同模型名 → 零冲突；ch140 是 `intern-s2` 唯一 abilities 行。
- `glm-5.3`：ch45/108/120/121/129 均 abilities `enabled=0`（禁用态），ch140 是唯一 enabled 提供者。
- `deepseek-v4-flash-vision`：全库 abilities 无此模型行（精确 + LIKE 均空）→ ch140 是唯一提供者，零冲突。
- 渠道 models 字段出现的名字 ≠ abilities 生效行，路由以 abilities 为准（本次实测印证）。

## 变更（可回滚）

`POST /api/channel/`（`{"mode":"single","channel":{...}}` wrapper）：

| 字段 | 值 |
|---|---|
| id / name | ch140 / intern-discovery |
| type / base_url | 1 / `https://discovery-api.intern-ai.org.cn`（不带 /v1） |
| models | `glm-5.3,intern-s2,deepseek-v4-flash-vision`（仅用户指定；目录其余 7 模型未挂） |
| group / priority / weight | default / 40 / 1（低于在役池，兜底姿态；实为三模型各自唯一 enabled 行） |
| auto_ban / test_model | 1 / intern-s2 |

abilities 自动派生 3 行（追加模型经 PUT 后同样自动派生）：`(default, glm-5.3)`、`(default, intern-s2)`、`(default, deepseek-v4-flash-vision)`，均 `enabled=1, prio40, w1`——确认继承渠道 prio/weight。

追加方式：列表端点读 ch140 → 去 `status`、`key` 置空（fork 实测空 key 保留原值，08-23 契约）→ 改 `models` → `PUT /api/channel/`；readback 三重无损（models 已更新、key 仍掩码、prio/w 不变）。

快照：`~/.new-api-local/backups/new-api-before-intern-discovery-20261003-153420.db`、`new-api-before-ch140-vision-20261003-153907.db`（均 integrity=ok）。

## 窄验证（NewAPI admin channel test，真实出站）

| 测试 | 结果 |
|---|---|
| `GET /api/channel/test/140?model=intern-s2` | success=True（1243ms） |
| `GET /api/channel/test/140?model=glm-5.3` | success=True（980ms） |
| `GET /api/channel/test/140?model=deepseek-v4-flash-vision` | success=True（710ms） |

## OMP 侧（models.yml，同日）

- `~/.omp/agent/models.yml` zg-newapi provider 新增 `intern-s2`（reasoning，131072/32768）与 `deepseek-v4-flash-vision`（reasoning，input [text,image]，131072/32768）；`glm-5.3` 条目本已存在，仅更新显示名为 intern-discovery ch140。
- 备份 `~/.omp/agent/models.yml.bak-20261003-intern-discovery`；YAML 校验通过（zg-newapi 60 模型、无重复 id）。
- **生效需重启 OMP**（运行中会话持启动配置）。OMP 回滚 = 用该 .bak 覆盖 models.yml。
- 使用注意：intern-s2 为 reasoning_content 型、vision 为强 reasoning 型，小 max_tokens 下 content 可能为空；OMP 默认大预算不受影响。

## 扩池（同日，+2 key → ch141/ch142）

- 用户补 2 个同端点 key。PUT 无法把单 key 渠道转多 key（fork 仅创建时算 is_multi_key），按 agentrouter 池惯例建并列单 key 渠道：**ch141 intern-discovery-k2、ch142 intern-discovery-k3**（均 type1 / prio40 / w1 / auto_ban1 / test_model=intern-s2，三模型同 ch140）。
- 直连测活：两 key `GET /models` 均 200（10 目录）+ `glm-5.3@32` 均 200。
- abilities：ch141/142 各 3 行 `enabled=1 prio40 w1`；channel test glm-5.3 双 success=True（662/782ms）。
- 池态：三模型各有 3 条 enabled abilities（ch140/141/142 同 prio 同权，加权轮询）；任一 key 失效由 auto_ban 单独禁用对应渠道，池不掉线。
- 快照：`new-api-before-intern-pool-20261003-155903.db`（integrity=ok）。回滚：`DELETE /api/channel/{141,142}` + 清对应 abilities。

### +k4（ch143，等权收尾）

- 第 4 个 key 直连测活：`GET /models` 200 + `glm-5.3@32` 200；建 **ch143 intern-discovery-k4**（同配置）。
- **额度事实（用户确认）**：每个 key 都是**独立账户**，50 RPM / 2M TPM 各自独立、互不合并 → 池总额度 = 4 × 50 RPM / 4 × 2M TPM；加 key 确实增吞吐。
- 权重等权 w1×4（配额相等即等权）；ch143 一度按"两账户"假设误设 w3，已 PUT 更正为 1，abilities 复核一致。
- 终态：4 渠道 × 3 模型 = 12 行 abilities 全 `enabled=1 prio40 w1`；channel test ch143 glm-5.3 success=True（747ms）。
- 快照：`new-api-before-intern-k4-20261003-160323.db`（integrity=ok）。

### 重启后路由 smoke（OMP 16:06 重启）

- OMP 进程启动时间 16:06 晚于 models.yml（15:44）/config.yml（15:56）mtime → 配置已加载。
- 经 OMP zg-newapi 凭据三选择器实测：glm-5.3 200 `content='Pong'`（91s）；intern-s2 200 但 512 预算被 reasoning_content 耗尽（content=null，同预算直连曾 236 token 出 'pong'——预算阈值有波动，建议 ≥1024）；vision 200 `content='Pink'`（3.5s）。
- 观察项：glm-5.3/intern-s2 首呼 91s/36s 偏高，单样本不区分冷启动/稳态；advisor 角色若嫌慢再议。

### 429 事件与超时修复（16:35–16:40）

- 症状：用户报"glm-5.3 总是报错"。机制双段式：① advisor 大上下文（36K–173K token）prefill 偶发超过 OMP `streamFirstEventTimeoutSeconds=120` → 中止（NewAPI 日志 `client_gone/context canceled`）；② `maxRetries=3` 重试=每次全新大 prefill → 单账户 2M TPM/min 打穿 → 上游 `429 quota exceeded`（ch140/141 16:37 实录），relay 终态报错。
- 关键认知：**四 key 防的是单 key 失效与总量，防不了"单账户分钟 TPM × 重试放大"**；429 为分钟级瞬态（四渠道即刻复绿），非余额耗尽。
- 修复：`providers.streamFirstEventTimeoutSeconds: 120 → 300`（备份 `config.yml.bak-20261003-timeout300`；**重启 OMP 生效**）。兜底链 `advisor → zg-newapi/k3` 已于本轮早些时候就位。
- 披露：16:37 429 爆发与调查中的 35K+170K 复测探针时间重叠，探针贡献了部分 TPM 压力；机制结论不受影响。
- 用户决策：池维持 4 key 不扩。若 429 再现，下一步取报错原文 + 考虑 advisor 降上下文/换模型。

### 429 再现（22:xx–23:xx）：第二种机制——申报包络预检（已修复）

- 症状：advisor 告警 `Fallback: glm-5.3:max -> k3:max / Advisor request failed: 429 quota
  exceeded`，**每次必发**（非 16:37 的分钟级瞬态）。
- 根因（受控实验钉死）：intern 上游对 glm-5.3 强制 `prompt_tokens + 申报 max_tokens
  ≤ ~131072`：小 prompt + `max_tokens=131072` → 429 `quota_exceeded`（与告警原文逐字节
  一致）；131071 → 200；20k prompt + 131071 → 429、+ 32768 → 200；76.8k + 8192 → 200。
  models.yml 对 glm-5.3 申报 `contextWindow: 1000000 / maxTokens: 131072` → advisor 大
  prompt + 顶格申报必越界。**错误消息伪装成额度耗尽，实为包络预检**。
- 修复（`~/.omp/agent` commit `1593471`，**pre-slim hash**，解析见 agent repo 根
  `commit-map-pre-slim-20261004.txt`）：zg-newapi glm-5.3 条目
  `contextWindow 1000000→131072`、`maxTokens 131072→32768`。E2E：`omp -p --model
  zg-newapi/glm-5.3:max` → `GLM_ADVISOR_OK`（19.8s，归因 ch146）。
- 生效范围：**models.yml 仅对新会话热加载**；已开着的终端持旧包络，会话重启前
  429 可能再现（预期内，非修复失败）。当时 timeout=300 修复已在役（OMP 进程
  22:43+ 重启，晚于 16:40 编辑）。
- 兄弟条目核查（同 intern 上游）：`deepseek-v4-flash-vision` 申报 128000 实测
  15484+128000=143k 仍 200——**不受同款预检**，未动；`intern-s2`（131072/32768）安全；
  `glm-5.3-flash`（bai ch121）与 agentrouter 直连 glm-5.3 为异上游，未测未动。

## 回滚

`DELETE /api/channel/140` + 清 abilities 行（`DELETE FROM abilities WHERE channel_id=140`，须 COMMIT）；
或恢复上述快照 + `POST /api/channel/fix`。

## 遗留

- key 为单点：auto_ban=1，上游 401 时渠道自动禁用，属预期 fail-closed。
- 若日后要给 glm-5.3 提优先（当前无竞争者，无必要）：prio>50 才会压过 intern-s2-preview 池语义无关；NewAPI 语义为**数值大者先**。
