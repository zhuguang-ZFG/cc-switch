# OMP 角色配置审查与修正（2026-09-07）

## 审查结论
核心结构健康：default=glm-5.3（ch45+ch120 双渠道）、plan/designer=k3（1344 次/24h 零错）、
slow=opus-5:xhigh、providers 分级并发、retry/fallbackRevertPolicy、17 条链无死引用。

## 变更（config.yml, 提交 9bc9659）

### task 角色: muse-spark-1.2-contributor-free -> zg-newapi/k3
- 根因: ch110 yjs-free 09/07 13:57 实锤 403 "User has been banned"（free 通道掉组）
- 候选 intern-s2-preview 被否决: OMP `_grep` 工具 schema 含
  `"type": ["number", "null"]` union 写法, intern-s2 上游（semantic TTFT gateway, Go）
  unmarshal 拒绝 -> 400 invalid_request_error。Claude 系模型无此问题。
  **教训: 给非 Claude 模型挂 task 角色前, 先带全量 tools 实测一次。**

### advisor 角色: 保持 sota 不变（用户约束, 门禁强制）
- 09/07 ch93 omp-sota-sotamodel 日额度 429 耗尽（03:13 起）, 12:19 自动禁用
- advisor 连续 503 "No available channel"（12:44-14:00 多次）
- 曾改指 zg-newapi-anthropic/claude-opus-5（付费）-> **门禁
  test_advisor_role_is_pinned_to_sota 挡下**: 用户 2026-08-20 约束
  "advisor 只走 sota 免费模型, 日额度耗尽停机是可接受取舍, 不得切付费路由"
  （历史: 曾切 TTFT 付费路径烧 justwoker 每 3 分钟 ¥0.2+ 被叫停）
- 处置: 回滚, 等 sota 日额度次日恢复

## 发现未修（外部依赖, 观察中）
- **ch125 opencode-go-omen-alpha 实际已死**: 09/07 中午起上游 opencode.ai Console Go
  强制 `x-opencode-session` 头（每请求唯一会话 ID）, NewAPI 渠道测试与 relay 全 400
  MissingSessionID。静态 header_override 无法伪造动态会话 ID, 该渠道不可救
  （等 NewAPI 适配或换接入方式）。omen-alpha 在 OMP smol 角色靠 fallback 链撑着。
- **ch86 禁用后 anthropic opus 池仅剩 ch93（sotamodel）+ ch116（kktoken）**:
  ch93 白天跑 opus 计费通道（627 次/24h）, ch116 空闲。opus 可用性 = ch93 单点。

## 验证
- test_omp_routes.py: 40 tests OK（含 sota pin / no-deepseek-in-reasoning / 链可解析等门禁）
- `omp -p zg-newapi/k3` pong ✓

## 相关提交
- 9bc9659 (omp config): task 角色切换
- ccbd503b (docs): ch45 glm-5.3 冗余
- 7de7f862 (docs): opus 聚合根因
