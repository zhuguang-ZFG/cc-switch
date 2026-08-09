# claude-opus-5 上游真实上限探测 + 会话卡死根因修复（2026-08-09 下午）

**Status:** 已生效（models.yml 改动需 OMP 重启加载；运行中的 OMP 仍按旧窗口 170k 计数）
**Scope:** `~/.omp/agent/models.yml`、`scripts/ops/test_omp_routes.py`

## 1. 问题

OMP 主会话（hutuji，session 019fe461）反复出现 agent turn 直接死于 400：

```text
agent turn ended with provider error  provider=zg-newapi-anthropic model=claude-opus-5
400 {"error":{"type":"bad_response_status_code","message":"bad response status code 400 ..."}}
```

NewAPI 侧（stdout.log）：请求 `重试：3->9->45` —— claude 三条渠道全部 400，20s 后放弃。
`~/.omp/logs/http-400-requests/` 当天 8 个抓包（00:28–15:49，543KB–830KB），含一个
gpt-5.6-sol 736KB 的 400 —— 同一失败族。

## 2. 根因链（逐层实证）

1. **上游拒绝**：失败请求 602KB / 69 消息。Kiro 反代上游对 claude-opus-5 的
   真实接受上限约 **130k tokens**（名义 200k，实测拒绝区远低于此；社区佐证
   kiro-gateway 对 api.kiro.com 有硬载荷限制、隐藏系统提示占 ~2k tokens）。
2. **OMP 窗口虚高**：models.yml `claude-opus-5 contextWindow: 170000` → 压缩阈值
   85% = 144.5k，已在上游拒绝区之上；且阈值只在 provider 调用边界检查
   （mid-turn shake），单轮内暴涨（本次 70k→173k est.）或死端停泊
   （`#midTurnCompactionDeadEnds`）都会漏检。
3. **错误无法归类（放大器）**：NewAPI 把上游 400 包装成不透明的
   `bad_response_status_code`（上游错误体丢失）。OMP `pi-ai/src/error/flags.ts`
   的 OVERFLOW_PATTERNS 全部匹配不上 → errorId=裸 400 → 非 retriable →
   **不压缩、不提升、不 fallback，turn 硬死**。此后该会话每次续跑都 400（确定性）。
4. **重试浪费**：NewAPI 对包装后的 400 做了渠道级 failover（3→9→45），
   3 次全量 600KB 上游投递全部白费（社区已知问题 QuantumNous/new-api #1961）。

排除项（均有反证）：`thinking: adaptive/summarized` 字段、`max_tokens: 64000`
（同形状小请求 200；B 原样改 max_tokens=1 仍 400）；字节数上限
（604KB 低 token 填充请求被接受）。

### 探针证据矩阵（2026-08-09，经 NewAPI 3002，zg-newapi key）

| 探针 | 字节 | 内容 | 结果 |
|------|------|------|------|
| 小请求同形状（thinking+64k max_tokens） | ~1KB | — | 200 ✓ |
| T2 system+1 消息 | 147KB | 真实 | 200 ✓（cache_creation 33.4k） |
| T3 = T1+重复 CJK 填充 | 326KB | 半真实 | 接受（报 input 80.5k*） |
| T5 = T1+JSON 填充 | 424KB | 半真实 | 接受（报 78k*） |
| T7 = B 全结构截断 | 489KB | 真实 | 接受 ✓ |
| T8 = B 全结构轻截断 | 557KB | 真实 | 接受 ✓ |
| V-bytes 590KB `aaaa` | 604KB | 低 token | 接受 ✓ |
| T4 = B 原样 max_tokens=1 | 602KB | 真实 | **400 ✗** |
| B 原始请求 | 602KB | 真实 | **400 ✗（3 渠道全灭）** |

\* 上游 relay 的 input_tokens 报告不可靠（12.6k–80k 对同量级字节），仅以
接受/拒绝为准。接受区 ≤557KB（≈125k tokens est.），拒绝点 602KB（≈140k+ est.）。

## 3. 修复

`~/.omp/agent/models.yml`：`zg-newapi-anthropic/claude-opus-5`
`contextWindow: 170000 → 110000`。

- 压缩阈值 0.85×110k = **93.5k**，请求稳态 ≤93.5k，单轮边界跳变 +40k 后 ~133k
  OMP 计数（≈530KB）仍处今日实测接受区。
- 只动 zg-newapi-anthropic 条目（卡死热路径：slow/plan/vision + 用户手选主模型）。
  agentrouter/anyrouter 的 opus-5（200k）不在 fallback 链上，未动；
  ch45 同样拒了 B（602KB），属同类潜在风险，选用时再评。
- `compaction.thresholdTokens` 是全局设置（会误伤 deepseek 380k 窗口），弃用该方案。
- 回归防护：`test_omp_routes.py` 新增
  `test_opus5_gateway_window_stays_under_upstream_rejection_zone`（≤110000 硬断言）。

### 验证

- `py -m unittest scripts.ops.test_omp_routes`：**33/33 OK**（含新断言）。
- `system-health-check.py`：20/20 ALL GREEN。
- 3002 链路 smoke：探针全程多个 200（T2 完整生成 200）。

### 回滚

```text
~/.omp/agent/models.yml.bak-20260809-opus5-window
```

## 4. 遗留与边界

- **生效需重启 OMP**；重启前运行中的会话仍按 170k 窗口计数，长会话仍有卡死风险。
- **已卡死会话（如 019fe461）无法自愈**：手动 /compact 的摘要请求本身也超上限会
  400；恢复路径 = 开新会话（resume 不做压缩，续跑一次 400 一次的旧结论不变）。
- **gpt-5.6-sol（bigctx，400k 窗口）有同类风险**：00:30 有一个 736KB 的 400 抓包，
  真实上限未探测；依赖 400k 前先探针验证。
- **OMP 上游修复建议（未实施，不改全局安装包）**：oh-my-pi 的溢出分类器不认
  NewAPI 包装错误。建议向其 GitHub 报 issue：`bad_response_status_code` + 大请求
  应归类 context-overflow（走压缩/提升恢复），或 NewAPI 保留上游错误体。
- Kiro 渠道中流不稳定（25 分钟爬行流、"stream ended before message_stop"）：
  社区已知（kiro-gateway #197/#217，高负载截流），本地无干净修复，TTFT 网关
  只管首 token，不管中段 stall。
