# OMP 模型 effort/上下文审计与引用门禁（2026-08-23）

对 OMP 生效的 81 个模型做两层审计：静态交叉核对 config.yml 全部
`(selector:effort)` 引用 vs `omp models` 生效白名单；对被引用对逐一 relay 实测
上游接受度。发现并修复 1 处真实漂移，并把该核验固化为常驻路由门禁。

## k3 白名单漂移（已修复）

现象：`modelRoles.plan` 与 `designer` 均引用 `zg-newapi/k3:max`，而生效表只有
`minimal,low,medium,high`。k3 条目无显式 `thinking` 块——白名单是 OMP 按 kimi
家族默认推断的，与上游真实能力脱节。

三态鉴别（advisory 提醒 200 ≠ 生效）：同题对比 high/max 的 reasoning tokens
（160 vs 193、completion 265 vs 481）方向一致但单样本；最终由渠道维护者确认
K3 原生支持 max。修复为显式声明全范围：

```yaml
thinking:
  mode: effort
  efforts: [minimal, low, medium, high, xhigh, max]
```

落点：`~/.omp/agent/models.yml`（agent 本地仓 `424497b`）；`omp models` 复验生效。
角色引用本身无需改动。

## 其余引用核清（5/5）

| 引用 | 判定 |
|---|---|
| `zg-newapi/k3:max` | ✅ 修复后白名单内；max/xhigh/high relay 实测 200 |
| `zg-newapi-anthropic/claude-opus-5:max` | ✅ 白名单含 max |
| `zg-newapi/gpt-5.6-sol:max` | ✅ |
| `zg-newapi/muse-spark-1.2-contributor-free:max` | ✅ relay ACCEPT |
| `zg-newapi/omp-sota-claude-opus-5:high`（advisor） | ✅ 当日实弹评审即此强度 |

PI_* 环境变量无 effort 引用；fallbackChains 成员均不带强度后缀。上下文长度
81 项逐个核对无内部矛盾（max-out ≤ ctx），唯一下述观察项。

## 常驻门禁：`:effort` 引用 ↔ 生效白名单

`test_omp_routes.py` 新增
`test_role_effort_references_within_effective_whitelist`（repo `d110a172`）：

- `_parse_effective_efforts(output)`：解析 `omp models` 表格为
  `{(provider, model): 强度集合}`——**用生效表而非 models.yml 显式声明做基准**，
  因为漂移恰恰发生在"无显式块、家族推断"的区域；
- `_collect_effort_references(config_text)`：全文收集 `provider/model:effort`
  引用（已知强度名过滤 + 去重）；
- 任一引用越界即失败并列出合法集合；selector 注册/可解析性仍由既有门禁覆盖。

合成回归验证：把 k3 生效集退回修复前状态，门禁精确报出
`zg-newapi/k3:max not in ['high','low','medium','minimal']`。当前 39/39 通过。

README 中 canary 行原引用 `test_omp_routes.py:487` 行号因插入漂移，改为函数名
`test_default_role_has_no_model_fallback_chain` 稳定引用。

## LSP servers 结论：不安装

SOTA 子进程 `--tools read,grep,glob,lsp` 中的 lsp 在本机始终优雅失败：
bundle 内服务器表是纯声明式命令spec（`typescript-language-server`/
`pyright-langserver`/`clangd`/`gopls`/`rust-analyzer` 等，含 fileTypes/
rootMarkers），按 PATH 解析 lazy start（`lsp.lazy=true`）；**无任何下载逻辑**
（唯一 npm-install 流程属于自更新器）。无服务器时工具报
`no language server found for this file`，单次调用损失不挂起。全部历史成功
评审均在此状态下完成。触发再评估的条件：评审 verdict 开始抱怨缺符号信息，
或耗时显著变长；届时首选 `npm i -g typescript-language-server typescript`
（本仓 TS 为主），无需任何 OMP 配置改动。

## 观察项（不动作）

- fengwind `step-3.7-flash`：声明 max-out 256K > ctx 131K 内部矛盾（外部
  provider，低使用）。
- `gpt-5.6-luna` ctx=400K 为镜像 gpt-5.6-sol 家族的假设值，无廉价验证手段。
- ch93 当日再现一次刷新窗口瞬态 503（管理探测失败→禁用→重试通过→启用的间隙），
  与 2026-08-21 记录的自愈行为同类，通道当前 status=1 且有成功调用日志。
