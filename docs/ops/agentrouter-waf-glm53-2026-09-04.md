# agentrouter WAF 挑战导致 glm-5.3 全域故障 + 代理诚实化 + Guardian 错误侧观测（2026-09-04）

## 结论

- glm-5.3（ch45+ch120 同上游）不是"后端模型死"，而是 **agentrouter 两个域名（ps.air-outer.com / agentrouter.org，同一阿里云 WAF 栈）自 09/04 ~22:30 起对本机出口 IP 触发全量 JS 挑战**：所有 `/v1/*` 请求返回 `HTTP 200 + text/html`（`aliyun_waf_aa/bb` 挑战页），无指纹可绕（httpx/urllib/curl-schannel/curl_cffi chrome131 全被挑战）。
- 旧代理把 200-HTML 当成功：非流式 `r.json()` 抛未处理异常 → uvicorn 裸 500 `Internal Server Error`（NewAPI 记 `bad response status code 500`）；流式把挑战页当 SSE 透传 → NewAPI 记成功但 `completion_tokens=0`（**假成功污染**，ch45 今晚 gpt-5.6-sol 13 条 c=0，最后真实成功 22:28:11）。
- ch108 whyyin 上游 `v4.whyyin.cn:28327` **死亡**（RemoteDisconnected / curl HTTP 000 ×2），复援不可行。ch121 bai 的 glm-5.3-flash 不受影响。
- 8/29 起"0:189 ch45/ch120 分流失衡""聚合假冗余"等历史疑点，根因同此：200-垃圾被计成功 + 错误侧（DB type=5）自 8/1 停写 → 全链假绿。

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
3. **OMP models.yml 标注**（`~/.omp/agent` 本地仓 `820538e`）：`glm-5.3` name 加 `[DOWN 09/04 — agentrouter WAF 挑战 + whyyin 死; 代理已诚实 502]`；whyyin 系（deepseek-v4-pro-0813 / kimi-k2.6 / kimi-k2.7-code）name 加 `[whyyin ch108 DOWN 09/04]`。

## 回滚

- 代理：`cp agentrouter-proxy.py.bak-20260904-waf agentrouter-proxy.py` → `Stop-Process` 当前 PID → supervisor 自动拉起。
- Guardian：恢复 `state.json` 非必需（`file_tail` 为新增可选键）；代码回滚 = 还原 guardian.py + 重启任务。
- NewAPI 渠道 ch45/ch120/ch108 未做任何改动（保持启用/禁用原状）。

## 复判标准（何时恢复 glm-5.3）

1. 直探 `ps.air-outer.com` 或 `agentrouter.org` `/v1/chat/completions` 返回 `application/json` 且 choices 有真实 content（挑战页消失，WAF 通常随 IP 信誉/时间自行放松）；
2. NewAPI 对 ch45/ch120 的渠道测试通过（文件日志无新 `channel test bad response`）；
3. 观察一轮 proxy.log 无 `↻ 200 non-json`；
4. 更新 models.yml name 去掉 DOWN 标注。

## 未决选项

- **换出口**：WAF 按出口 IP 判定，走 Clash 代理节点出口可能立解（未测——需改 Clash 规则/选择器，涉及用户在用配置，未动）。
- **联系 agentrouter 放行**或更换同类上游。
- whyyin 上游死亡需联系服务商或确认其迁址。
