# zen-free-bridge 静默截断修复 + cat-jam-free 注册（2026-10-15 晚）

评审发现（blocker）：`zen_free_bridge.mjs` 的消息处理是
`msg=(body.messages||[])[0]; prompt=String(msg.content).slice(0,32_000)` ——
**只转发 messages[0] 且截到 32k 字符**。于是所有走 ch178 的请求都只把第一条
消息的前 32k 字符喂给 CLI，多轮历史与长上下文**静默丢失**。prompt 走 stdin
（与 argv 长度无关），32k 只是人为上限。

## 修复（`scripts/ops/zen_free_bridge.mjs`）

- 新增 `promptFromMessages()`：单消息 → 原样（与旧行为逐字一致）；多消息 →
  `role: content` 逐行拼接；content 数组 → 文本分片连接，**image 分片 fail-loud
  400**（不再静默丢弃）；去掉 32k 截断。
- `Bun.serve` 包 `if (import.meta.main)`（Bun 入口 true、被 node --test 导入时
  不起监听）→ 可测。
- 新 hermetic 测试 `scripts/ops/test_zen_free_bridge.mjs`（7 例：单消息原样 /
  多轮序列化 / content 数组连接 / image 400 / 空 / **>32k 不截断** /
  缺 role 默认 user）。

## 决定性验证（重启后，修复态实测）

重启桥（kill PID 16276 → 24464/8044 拉起）后：

- **标记在 38k 字符之后**：模型回声 `ZEN-MARKER-7749` ✓（旧代码必截掉）；
- **多轮历史**：问"我之前让你记的词是什么" → `GIRAFFE` ✓；
- 修前行为留档：同请求下"记住的词"无法回答（只剩首条消息）。

## cat-jam-free 注册（修后实测，作废此前的桥伪影）

修前一轮探活（tools 拒答/vision 忽略/1M "接受"）全是桥伪影，作废。修后实测：

| 项 | 结果 |
|---|---|
| basic | 200 "OK" |
| tools | 模型/CLI 路径拒答（"No ping tool is available"）→ `supportsTools: false` |
| vision | 桥 fail-loud 400（`image parts not supported`）→ text-only |
| reasoning | 无 reasoning_content（17×23 直答 391）→ `reasoning: false` |
| maxTokens | 131072 接受 |
| context | **≥200k tokens 实测**（800k 字符 prompt 埋标记读回 ✓，86.2s）；~300k 被拒（502，上游上下文限或 CLI 超时未区分）→ 声明 200000 |

→ models.yml 条目：`zg-newapi/cat-jam-free`，ratio=0（ch178）……

## 门禁

- `test_zen_free_bridge.mjs` 7/7（node --test）；
- 回滚：git revert 本 commit（桥须重启才回退运行态）。

## 风险提示

- 桥的 CLI 是单飞（`zen bridge busy` 503）；转发全量消息后长 prompt 的真实
  上游速率/限流照常生效。
- 上游 `opencode.ai/inference/v1/models` 公开端点**只给 id 不给上下文元数据**；
  cat-jam 的 contextWindow 为实测下限，注释已写明。
