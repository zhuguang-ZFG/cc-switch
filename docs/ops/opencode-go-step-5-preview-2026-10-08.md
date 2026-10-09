# OpenCode Go Step-5-Preview 限时免费入 OMP（ch184）（2026-10-08）

## 结论

OpenCode 官方宣告：**Step-5-Preview** 在 OpenCode 与 OpenCode Go
**限时免费开放（1 周，约至 2026-10-15）**；1M Context · Multi-modal · ZDR；
**不消耗 Go 套餐额度**。用户指令：“这个要加入 omp 中”。

接入完成：
- **NewAPI ch184 `opencode-go-step-5-preview`**（type=1，
  `https://opencode.ai/zen/go`；key/header_override 克隆自 ch130 donor，
  donor 未被修改；p0/w2；auto_ban=1；ModelRatio=0）。
- 双 id 暴露：`step-5-preview-free`（上游 exact id，主 OMP 条目）+
  `step-5-preview`（alias→upstream，与 arcdent 侧 id 一致，便于日后改指）。
- **OMP models.yml** zg-newapi 下新增 `step-5-preview-free`：
  input=text+image（多模态）、contextWindow=1048576（**官方口径，未做 1M
  实测填充**）、maxTokens=32768（保守；`max_tokens=131072` 参数实测被接受）。

## 证据（2026-10-08，Go key 从 DB 只读，不落盘）

| 探测 | 结果 |
|---|---|
| `GET /v1/models`（donor ch130 key） | 200，**38 个模型**，含 `step-5-preview-free`（唯一 step 家族 id） |
| chat/completions 非流 | 200 `GO_STEP_OK`，`reasoning`+`reasoning_content` 自带（usage 含 cache 明细） |
| SSE | 200，113 frames + `[DONE]` + usage |
| vision（1x1 png `image_url`） | 200 判色正确（"Red"）→ 多模态实证 |
| `reasoning_effort=max` + `max_tokens=131072` | 200（reasoning_tokens=42 计入） |
| 强制 tool_calls | 200，`report_canary` 参数 JSON 解码 `{"value":"CANARY_TOOL_OK"}` |
| 40K 字符（~9K tok）prompt | 200 无损（needle 命中）→ 1M context 仅官方口径，未做 1M 填充实测 |

## 变更

执行脚本：`scripts/ops/add_opencode_go_step5preview_channel.py`
（dry-run 默认，`--apply` 执行；key/header 克隆自 ch130；创建 disabled →
disabled 下 management probe → enable → abilities/ModelRatio 读回 →
网关双 id 实弹 + 日志归因断言；重跑=verify-only 不动状态）。

| 项 | 值 |
|---|---|
| 渠道 | **ch184 `opencode-go-step-5-preview`**（type=1，base `https://opencode.ai/zen/go`） |
| 模型 | `step-5-preview-free,step-5-preview`（mapping `{"step-5-preview":"step-5-preview-free"}`） |
| 姿态 | p0/w2，auto_ban=1（Go 家族惯例；campaign 免费，ModelRatio=0） |
| abilities | 2 行读回 `(default,1,0,2)` |
| DB 快照 | `new-api-before-opencode-go-step5preview-20261008-235123.db`（37068800B，integrity=ok） |
| OMP | models.yml `zg-newapi/step-5-preview-free`；config.yml 角色链未改（plan/task fallback 仍走 `arcdent/step-5-preview`，直连 arcdent 不动） |

## 验证

- 脚本 `--apply` exit 0：管理 probe 200（3.226s）→ enable → abilities x2
  读回 → ModelRatio 双 0 读回 → 网关 `step-5-preview-free` 200
  （17/77 tokens，attr=ch184）→ `step-5-preview` 200（17/34，attr=ch184）。
- 真实 OMP 端到端：`omp --model zg-newapi/step-5-preview-free --print
  "Reply with exactly: STEP_OK"` → 输出 `STEP_OK`（93.7s）；NewAPI logs
  id 245615-245617 全部 attr=ch184（prompt_tokens 10K+，`step-5-preview-free`）。
- `models.yml` YAML 校验 OK（providers 12）。

## 风险与后续

- **1 周限时**：活动结束（约 2026-10-15）上游大概率移除 `-free` id →
  摘除 models.yml 条目 + ch184（或改指 arcdent 侧的 `step-5-preview`）。
  models.yml 内嵌注释已写明。
- p0/w2 意味着与其他 p0 渠道同权竞争；本模型仅 ch184 承载（其余 p0 渠道
  不承载该 id），无抢占问题。
- 未做 1M 上下文实弹（成本/时间权衡，官方口径声明），大上下文使用如有
  异常请回查本条目。
- config.yml 未改指 `zg-newapi/step-5-preview`：角色链保持 arcdent 原路
  径（arcdent=付费中继，链稳定）；如需把步骤任务切到免费档，
  一行改 `arcdent/step-5-preview` → `zg-newapi/step-5-preview` 即可
  （模型 id 已同）。未做，待用户决定。

## 回滚

- 摘除：`DELETE /api/channel/184`；ModelRatio 两个 key 原位恢复/删除；
  models.yml 移除 `step-5-preview-free` 条目（备份见上；或改指 arcdent）。
- 禁用：`POST /api/channel/184/status` status=2（保留配置）。