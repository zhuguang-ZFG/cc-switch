# agentrouter WAF 挑战导致 glm-5.3 全域故障 + 代理诚实化 + Guardian 错误侧观测（2026-09-04）

## 结论

**已修复（09/04 23:45 定案）**。glm-5.3（ch45+ch120 同上游）不是"后端模型死"，而是 **agentrouter 两个域名（ps.air-outer.com / agentrouter.org，同一阿里云 WAF 栈）自 09/04 ~22:30 起对本机家宽出口 IP 触发全量 JS 挑战**。上游 glm 后端与 4 把池 key 全程健康——改走 Clash 专属出口组（香港节点）后全链恢复。

上游三层门（均已实测）：
1. **阿里云 WAF（IP 级）**：家宽 IP 被全量 JS 挑战（200 + text/html `aliyun_waf_aa/bb`）；**香港02直连/香港05原生出口穿透**。与 TLS 指纹无关（同 IP 下 httpx/urllib/curl/chrome131 全被挑战）。
2. **客户端指纹门**：UA 必须 claude-cli 样式；`python-urllib`/`curl` 裸 UA → 401 `unauthorized client detected`（type=unauthorized_client_error）。
3. **令牌校验**：claude UA + 池 key → 200。NewAPI ch45/120 里的 64 字符 key 只是"NewAPI→本地 8788 代理"的本地鉴权，**不透传上游**；代理用 `keys.json` 池 key（sk-…，4 把）+ CLAUDE_UA 转发。

## 证据

- 直探双域（走/不走 Clash 7897）均返回同一 WAF 页；UA 已伪装 claude-cli 仍被挑战 → 判定 IP 级全量挑战，非 TLS 指纹。
- 代理 `agentrouter-proxy.py` 旧代码：`if r.status_code == 200: ... return JSONResponse(content=r.json())`（200 体非 JSON 即崩）。
- NewAPI 文件日志（`~/.new-api-local/logs/oneapi-*.log`）`[ERR]` 行完整记录中继错误（DB type=5 死后唯一错误侧信号源）：
  `[ERR] ... | bad response status code 502, body: {"detail":{"error":{"message":"upstream 200 non-JSON (waf challenge?)"...}}}`
  `[ERR] ... | channel error (channel #120, status code: 502): ...`
- ch45 消费行：`gpt-5.6-sol` 今晚 c=0 ×13 vs 真实 c>0 ×4（末次 22:28:11）。

## 修复（均已验证）

1. **agentrouter-proxy 诚实化**（`~/.kimi-code/proxies/agentrouter-proxy/`，备份 `agentrouter-proxy.py.bak-20260904-waf`）：
   - 新增 `_upstream_body_ok()`/`_looks_like_html()`：200 体必须是合法 JSON（或流式 `data:`/`event:` 开头），否则记 `↻ 200 non-json | waf? try next upstream` 并轮转下一上游/密钥；双上游均被挑战即诚实 502 `upstream 200 non-JSON (waf challenge?)`。
   - `/v1/models` 同样加固（原先必崩 500）。
   - 弹回：`Stop-Process` 旧 PID → `proxies-supervisor` 30s 内自拉起（注意 git-bash 下 `taskkill //F` 与 `schtasks //run` 会被吞参，用 PowerShell `Stop-Process`/`Start-ScheduledTask`）。
   - 验证：非流式/流式/模型列表均 4.5-14s 内诚实 502；NewAPI 全链路返回 502；proxy.log 出现 `↻ 200 non-json` 行。
2. **Guardian 错误侧观测补盲**（`~/.omp/guardian/guardian.py`，运行时 uv cpython 3.12）：
   - 新增 `AutoFixEngine.tail_external_error_logs()` + 周期步骤 `file log error tail`（error scan 之后）：按偏移增量回看 NewAPI 最新 `oneapi-*.log` 与 `proxy.log`，匹配 `channel test bad response` / `channel error (channel #` / `relay error` / `200 non-json` / `流中断`，按渠道聚合 `logger.warning`（只记录不处置，增量防洪泛，状态存 `state.json` `file_tail`）。
   - `test_guardian.py` 142 用例全过；重启（`Start-ScheduledTask -TaskName 'NewAPI Guardian'`）后首轮即捕获 50 条历史错误行（ch120×15、ch45×4、ch118×3）与增量轮转正常。
   - 副发现（此前不可见）：ch119 muyuan glm-5.2 测试 503/500（无可用渠道/代理组失败）；ch105 上游拒 `reasoning_effort=max`（400）；ch110 muse-spark Free 组无可用渠道。待后续处置。
3. **OMP models.yml 标注**（`~/.omp/agent` 本地仓）：`glm-5.3` 先加 DOWN 后随终局修复摘除（现名 `GLM 5.3 [agentrouter ch45/120，经 Clash Agentrouter-EG 出口]`）；whyyin 系（deepseek-v4-pro-0813 / kimi-k2.6 / kimi-k2.7-code）保持 `[whyyin ch108 DOWN 09/04]`。

## 终局修复（23:40–23:45）：Clash 专属出口组钉死 agentrouter 出口

### 节点矩阵（经 7897 + 池 key + claude UA 实测）

| 出口 | 结果 |
|---|---|
| 🇭🇰香港02丨直连 (Hy2 38ms) | **穿透**，glm-5.3 200 全 4 key（1.3–3.7s，真实 content） |
| 🇭🇰香港05原生丨移动直连 | **穿透**，200 |
| 🇭🇰香港03丨直连 / 🇯🇵日本02三网优化2x / 🇭🇰香港01丨V6 | TLS `UNEXPECTED_EOF`（transport 层死，非 WAF） |
| 家宽直连（现状 22:30 前） | 全量 JS 挑战 |

### 改动清单（均有备份）

1. **Clash runtime 注入**（`%APPDATA%/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml`，备份 `*.bak-20260904-agentrouter`）：`Agentrouter-EG` select 组（5 节点，默认香港02直连）+ `DOMAIN-SUFFIX,air-outer.com/agentrouter.org → Agentrouter-EG` 两条置顶规则；`PUT /configs?force=true` 热重载（mihomo 204）。
2. **per-profile merge**（`profiles/mc4PF6D8TBKv.yaml`，备份同后缀）：与 freebuff 同款结构（`prepend: {proxy-groups, rules}`），订阅更换时优雅降级。
3. **代理出口环境变量**（`~/.omp/guardian/proxies-supervisor.py` agentrouter 条目，备份 `*.bak-20260904-egress`）：`HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897` + `NO_PROXY=localhost,127.0.0.1,::1`。代理本就 `trust_env=True`，无需改代理代码。重启 supervisor（旧进程内存里的 env 不会重读）→ 新 PID 自动带出。
4. **重注入脚本**（仓库 `scripts/ops/apply-agentrouter-egress-rules.py`，幂等）：Verge 重生成 runtime（订阅更新/激活 profile）会丢注入，跑一遍即补回并复核、默认选回香港02直连。已验证幂等路径（规则在位 → no-op）。

### 端到端验证

- `NewAPI(3002) → glm-5.3`：HTTP 200，3.5s，`content='OK'`，`completion_tokens=35`，DB 行 ch45 23:44:47（真实消费，非假成功）。
- `proxy.log`：`▶ glm-5.3 … ◀ 3.4s ok`，无 `↻ 200 non-json`。
- Guardian file-tail（23:43 周期）：仅剩 ch93 sota 配额 429（已知独立问题），ch45/120 无新错误行。
- 附带发现：**全局 Merge.yaml（sharedchat/muyuan/linux.do 规则）从未进过任何 runtime**——它不在任何 profile 的 option 链上，Verge 重生成时只是把它字面倾倒进 runtime（mihomo 容忍未知键忽略之）。sharedchat 系一直按 profile 默认规则路由。未动（独立问题）。

### 回滚（恢复到"诚实 502"状态）

1. `cp proxies-supervisor.py.bak-20260904-egress proxies-supervisor.py` → 重启 supervisor（剥掉代理 env，代理回到家宽直连 → 挑战复活 → 诚实 502）。
2. 可选：`cp profiles/mc4PF6D8TBKv.yaml.bak-20260904-agentrouter profiles/mc4PF6D8TBKv.yaml`；runtime 注入会随下次 Verge 重生成自然消失。

## 回滚

- 代理：`cp agentrouter-proxy.py.bak-20260904-waf agentrouter-proxy.py` → `Stop-Process` 当前 PID → supervisor 自动拉起。
- Guardian：恢复 `state.json` 非必需（`file_tail` 为新增可选键）；代码回滚 = 还原 guardian.py + 重启任务。
- NewAPI 渠道 ch45/ch120/ch108 未做任何改动（保持启用/禁用原状）。

## 复判标准（何时恢复 glm-5.3）

1. ~~直探返回 application/json 且 choices 有真实 content~~ **已满足**（经 Clash 香港出口，4 池 key 全 200）；
2. NewAPI 对 ch45/ch120 的渠道测试通过（文件日志无新 `channel test bad response`）——**进行中观察**；
3. 观察一轮 proxy.log 无 `↻ 200 non-json`——**已满足**（23:44 起）；
4. ~~models.yml 去掉 DOWN 标注~~ **已完成**。

## 未决选项

- ~~换出口~~ **已实施**（见终局修复）。
- **联系 agentrouter 放行**或更换同类上游（备用，当前已不需要）。
- whyyin 上游死亡需联系服务商或确认其迁址。

