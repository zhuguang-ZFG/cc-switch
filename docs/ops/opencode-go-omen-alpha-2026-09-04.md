# opencode-go omen-alpha 专用渠道（ch125，2026-09-04）

用户报告"opencode zen 新增 omen-alpha"。扫码后走 add_* 标准合约入网
（备份 → 建渠道 → ModelRatio → abilities → 回读验证 → 3002 relay 逐模型探测）。

## 上游扫码

key 沿用 Go 套餐（ch101 donor），浏览器 UA 必需（裸客户端 CF 1010），key 不打印不入仓：

- Zen 免费端点 `https://opencode.ai/zen/v1`（ch96）：`/v1/models` 66 模型
  **不含** omen-alpha，直连 chat 401 `ModelError "Model omen-alpha is not
  supported"` → 免费档没有，ch96 不挂（文档滞后/灰度发布核实动作）。
- Go 端点 `https://opencode.ai/zen/go`（ch101）：`/v1/models` 列出
  omen-alpha，直连 chat 200 finish=stop → 可用。

## 决策：专用单模型渠道（同 ch112/ch113/ch117 模式）

`abilities` 表是渠道行派生物，per-model 权重只能靠渠道级字段控制；ch101
挂 mimo-v2.5，扩 ch101 会缠死权重向量 → 单独建渠道：

- ch125 `opencode-go-omen-alpha`：type=1，base_url `https://opencode.ai/zen/go`
  （**不带 /v1**，NewAPI type=1 自动补），key 复用 ch101（donor 不动），
  models `omen-alpha`（无 mapping：对外 id == 上游 id），group default，
  p0/w5，auto_ban=1，test_model `omen-alpha`，header_override 浏览器 UA。
- 坑1（同 qwen3-8-27b 池教训）：POST 创建必须 `{"mode":"single",
  "channel":{...}}` 包裹，body 不带 `status` 字段。
- ModelRatio `omen-alpha -> 0`：Go 是 $60/月包月套餐，边际成本 0
  （同 ch101 mimo-v2.5 定价逻辑）。

## 验证

- API 回读 + abilities 行 `(default,1,0,5)` + ModelRatio=0 三重一致；
  ch101 未动。
- 功能测试：3002 网关 12 请求、按 `logs` 表归因渠道，11/12 落 ch125。
- relay 语义冒烟：`POST 127.0.0.1:3002/v1/chat/completions` model=omen-alpha
  → 200。注意 omen-alpha 是推理模型：小 max_tokens（64）会被推理
  token 吃满（finish=length、content 空、`reasoning_content` 有货），
  不是故障；实测给足 max_tokens 即出文。

## OMP 侧注册（同日）

`~/.omp/agent/models.yml` 的 `zg-newapi` provider 块末尾
（`dots-3-note-preview` 之后、`zg-newapi-anthropic` 之前）：

```yaml
- id: omen-alpha
  compactionModel: zg-newapi/deepseek-v4-flash
  name: Omen Alpha (opencode-go ch125)
  reasoning: true
  contextWindow: 200000
  maxTokens: 32000
```

- `contextWindow`/`maxTokens` 对齐同套餐兄弟 mimo-v2.5 的保守估值，
  **未实测**，遇截断/超限再收紧。
- 备份：`~/.omp/agent/models.yml.bak-20260904-omen-alpha`。
- **不进 fallbackChains**：链是手工策划的，omen-alpha 走 Go 套餐
  付费订阅入口，不混免费兜底链（避免链路语义混乱）。
- OMP 配置按进程启动时加载；已打开的交互会话认不出新模型需重启 OMP。

## OMP 升级备注（同日）

- registry 最新 `@oh-my-pi/pi-coding-agent@18.1.10`；`bun add -g` 已落地
  18.1.10 包。
- 运行中二进制 `~/.bun/bin/omp.exe` 仍是 18.0.11（8-30 拷贝）——exe 被
  存活 OMP 进程锁住无法覆盖（本会话即其中之一）。**不要杀活进程解锁**
  （OMP→proxy→NewAPI 生产链），下次重启 OMP 自动落到 18.1.10。

## 脚本

`scripts/ops/add_opencode_go_omen_alpha.py`（幂等：重跑只探测+回读验证；
失败自动 DELETE 渠道 + abilities 行清理 + 全量快照兜底）。
