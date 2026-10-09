# ss2a.top kimi 组探测与备份腿接入（2026-10-09）

## 背景

用户提供 ss2a.top 新 key（与 ch183 glm-5.3 组同 host、不同分组）。当日
k3 刚发生单腿断供（`k3-outage-misattributed-glm53-2026-10-09.md` 遗留
风险：ch33 kimi 官方周额度再耗尽即复发）。本任务探明新 key 能力并准备
kimi 家族第二腿。

## 探测结论（全部实测）

- `/v1/models`：4 模型 `k3,k3-256k,kimi-for-coding,kimi-for-coding-highspeed`。
  ⚠️ 首探曾返回 claude 全家目录（fable/opus 系）——**瞬态假象**：随后
  3 连探稳定 kimi 目录，且 `claude-opus-5-5` 实弹 404
  `not supported by any configured account in this group`。勿据单次
  /v1/models 判型。
- Anthropic `/v1/messages`（Bearer + anthropic-version）：**4 模型全 200
  真实补全**（1.4–8.2s；k3 系带 thinking 块，小 max_tokens 会被思考吃掉
  属正常）。
- OpenAI `/v1/chat/completions`：**502 `Upstream access forbidden`**（组
  策略只放行 Anthropic 协议）→ NewAPI 渠道必须 **type=14**。
- `glm-5.3`：404（同上）→ 与 ch183 是不同分组，**新开渠道**而非换 key。
- x-api-key 头风格未证实（被 503 窗口掩盖）；apply 时管理探针即检验
  NewAPI type=14 适配器实际发出的头。

## 稳定性观测（重要）

- 首分钟 6 连发全 200 后，kimi 组进入长冷却：**503/502 连续 8/8 失败
  （150s 间隔，~20 分钟监视窗）**，叠加首尾至今 ~35 分钟无成功。
- 观察项（非结论）：ss2a kimi 池与 ch33 周额度可能共享上游官号账号；
  若相关性成立，本备份腿的独立性有限（今日数据混合：ch33 早晨恢复、
  ss2a 池死——两者不同步，暂不支持强关联）。
- 同 host glm 组（ch183）同期完全正常（网关实弹 200 归因 ch183；昨晚
  生产 13+ 次零失败）→ host 可靠，**kimi 组上游共享官号池为 burst 型
  容量**：来一波能打数发，打完冷却。与 ch33 周额度同类病、粒度更碎。
- 定位：**彩票备份腿**——ch33 活着时它闲着（p20 不抢流量）；ch33 再死
  时热则救场、冷则维持现状，不会更糟。

## 渠道设计（`scripts/ops/add_ss2a_kimi_channel.py`）

- `ss2a-kimi`，type=14，base `https://ss2a.top`（裸 base，适配器自动
  追 `/v1/messages`），5 模型：4 个真实 id exact-id + **`zg-k3` 别名腿**
  （mapping `zg-k3→k3`，镜像 ch33——zg-k3 是 Cursor BYOK 现役流量
  （`cursor-newapi-byok-2026-08-04.md`），此前 ch33 单源），group=default。
- **p20/w1/auto_ban=1**，`test_model=kimi-for-coding-highspeed`（最快
  ~4.6s）。ch33（p50/w10）保持主力；本腿只在 ch33 不可用时接盘。
- 流程：在线备份 → POST 建渠（已存在则 PUT 续跑，漂移即拒）→ 模型
  读回对账 → `/api/channel/fix` abilities 重建并等形 `(default,1,20,1)`
  → 定价只读报告 → **管理探针直连必须 success**（上游冷却期会挡住
  apply，按设计）→ 网关归因断言：`kimi-for-coding-highspeed` 必须归因
  ch33（主力赢）或本渠（failover 路径），其余为路由缺陷。
- key 经 `SS2A_KIMI_KEY` env 传入，不落盘不回显。

## 落地状态（本文档写作时）

- ⏳ **待上游恢复后 apply**：
  `SS2A_KIMI_KEY=... python3 scripts/ops/add_ss2a_kimi_channel.py --apply`
  （`py` launcher 会因 shebang 失败，用 `python3`；dry-run 已过）。
- 渠道落地后依次：newapi-local-smoke（零新增 FAIL）→ OMP 条目复活
  （见下）→ OMP `zg-newapi/k3:max` E2E 回归。

## 落地后待补验证（apply 时顺带）

- **SSE 流式**：`stream:true` 帧序列 + `[DONE]` + usage（Claude Code
  恒流式；直连路径必需）。
- **text 块非空复核**：`kimi-for-coding` 的 thinking 块曾含字面
  `PROBE_OK`（疑似中继伪造 thinking），需确认真实 text 块非空、
  非仅 thinking 承载内容。

## OMP models.yml 复活（条件已满足）

网关 `/v1/models` 已含 4 个 kimi id（ch33 今日启用）；10-08 B 类摘除的
3 条目复活条件达成，原始形状已从 git（522a6d5 / 24853ce）回捞：

```yaml
    - id: k3-256k
      compactionModel: zg-newapi/deepseek-v4-flash
      name: Kimi K3 256K (kimi-official ch33)
      reasoning: true
      thinking:
        mode: effort
        efforts: [minimal, low, medium, high, xhigh, max]
      contextWindow: 262144
      maxTokens: 32768
    - id: kimi-for-coding
      compactionModel: zg-newapi/deepseek-v4-flash
      name: Kimi K2.7 Coding
      reasoning: true
      contextWindow: 262144
      maxTokens: 32768
      contextPromotionTarget: zg-newapi/k3
    - id: kimi-for-coding-highspeed
      compactionModel: zg-newapi/deepseek-v4-flash
      name: Kimi for Coding Highspeed (kimi-official ch33)
      reasoning: true
      contextWindow: 262144
      maxTokens: 32768
```

（插回 zg-newapi 块 `k3` 条目之后；名称里 ch33 注记可留可去。）

## 直连 Claude Code 用法（用户原始 env 块）

```bash
export ANTHROPIC_BASE_URL="https://ss2a.top"
export ANTHROPIC_AUTH_TOKEN="<key>"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

本 key 组仅放行 `/v1/messages`；nonessential 流量端点未验证，保持
DISABLE 开关。模型名用 `k3` / `kimi-for-coding(-highspeed)`。
