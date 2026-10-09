# Jev 官方密钥池：NewAPI → OMP 判定工具（2026-10-08）

## 当前状态

- 用户附件中的 **200 枚唯一官方 key** 已存入 NewAPI **ch181**
  `jev-systemone-bridge`，逐字读回与附件一致；`multi_key_mode=polling`、
  `multi_key_size=200`、无禁用 key。
- 渠道保持 `type=1`、`status=1`、`base_url=http://127.0.0.1:8413`、
  `models=jev-latest`、`priority=0`、`weight=5`、`ModelRatio=0`、`auto_ban=0`。
- 官方上游为 `https://api.typesafe.ai/v1/systemone`。桥不再读取旧网关凭据，
  每个请求转发 NewAPI 选中的 Bearer key；不跟随重定向，不记录凭据/请求正文。
- OMP 的 `jev_judge` 使用现有 `newapi_probe_key` 调 NewAPI，不再绕过密钥池。
  工具签名与 `{model, answers, usage}` 响应不变；未注册聊天角色或 fallback。

## 链路和接口

```mermaid
flowchart LR
  OMP[OMP jev_judge] --> N[NewAPI 3002 / ch181 / 200-key polling]
  N --> B[8413 SystemOne 协议桥]
  B --> T[api.typesafe.ai /v1/systemone]
```

[官方 API 契约](https://docs.typesafe.ai/api)：`state` 为 string/object/array，
`questions` 为自选问题名到 `noul`、`choice`、`score` 的映射，`model=jev-latest`。
响应的真实版本号本次为 `jev-1.13.0`。概率/分数是模型判断，不是真值或测试证据。

NewAPI 仅作传输和选钥。MCP 将判定封装进最后一条 user message 的 JSON：

```json
{
  "type": "jev.systemone",
  "model": "jev-latest",
  "state": {"ticket": "Deployment blocked"},
  "questions": {
    "deployment_urgent": {"type": "noul", "instructions": "Is deployment blocked?"}
  }
}
```

- `POST /v1/chat/completions`：精确的 `type=jev.systemone` 标记启用自定义判定；
  无标记的文本/JSON 保持既有固定模板 `is_blocking/urgency/route`。
- `POST /v1/systemone`：原生 state/questions 透传，支持对象和数组。
- 两个 POST 均须 Bearer key；缺少鉴权为 401，坏封装为 400，上游 401/429
  保留状态，错误正文不回显凭据。桥不自行重试。
- `GET /healthz` 无鉴权；只证明本地服务存活，不证明上游健康。
- `JEV_UPSTREAM_BASE` 只控制桥上游，默认官方；`JEV_NEWAPI_BASE` 控制 MCP，
  默认 `http://127.0.0.1:3002`。MCP 从 guardian secrets 读取 `newapi_probe_key`。

## 本次实测

- 从原 user attachment 读取 200 枚 key，没有手工重抄；并发 2、无重试，
  **200/200 官方 SystemOne 请求 HTTP 200**。这不证明独立额度或长期有效期。
- 首/中/尾样本均回 `jev-1.13.0`、urgency `0.98`；只测试最小判断请求，
  未做长上下文、并发上限或判断准确率基准。
- NewAPI 管理探针推进 polling index **2→3→4→5**，三次均 ok。
  入池脚本管理探针、启用、普通 relay 实弹和 key/abilities 读回均通过。
- 新桥真实官方 canary 保留自选 `deployment_urgent`/`owner`，200；无鉴权 401。
- MCP 独立 stdio：自选 noul/choice/score、默认模板均成功；坏模型返回
  `isError=true`，进程不退出。回传 usage 保持 input/output token 字段。
- 实际 `omp --print` 调用 `jev_judge` 成功并输出 `JEV_NEWAPI_OK`；
  NewAPI log **245196** 归属 ch181（281 input / 23 output）。当前会话重连后
  再次成功，log **245199**（298 input / 22 output）。旧会话进程加载旧代码，
  需重连而非关闭 TLS 校验；已按 PID+创建时间核实后仅停止旧 Jev 子进程。
- NewAPI SSE：HTTP 200、自定义两问题结果可拼回 JSON、收到 `[DONE]`。
  这是一次性判定结果的 SSE 包装，不是官方逐 token 生成流。
- 独立 OMP smoke 首次在 stdin 未关闭时超时；下一次显式 `stdin=DEVNULL`
  后 exit 0。未排除其他瞬时因素。同次 GitHub MCP 鉴权警告与 Jev 无关，未改其配置。

## 回归与验证边界

`scripts/ops/test_jev_systemone.mjs` 使用隔离 HOME、本地上游和真实 Bun 子进程。
最初五项 RED，协议修复后五项 GREEN；另新增 MCP 坏模型会杀进程的 RED，
将 `handleToolCall` 改为 async 后最终 **6/6 PASS**：

1. 自选问题和对象 state 不被固定模板替换。
2. 原生数组 state 保留自选问题。
3. 无鉴权/坏封装在出站前拒绝。
4. 上游 401/429 状态保持，错误不泄露 key。
5. 无标记 JSON 仍是普通输入，不误判为协议封装。
6. 无效模型不会终止 MCP 会话，后续 ping 仍响应。

执行：`BUN_EXECUTABLE=<bun路径> node --test scripts/ops/test_jev_systemone.mjs`。
新增回归属于本轮验证输入变更；`task_verify` 返回
`unverified / verification-inputs-changed-review-required`，`checks=[]`。
需要用户评审这份回归，由新一轮建立基线；未编辑固定策略或弱化既有测试。
真实 smoke 和手动回归通过，**不等于 operator-configured gate 已通过**。

## 运维

- 入池：`python scripts/ops/add_jev_systemone_channel.py --keys-file <私密文件>`
  默认只计划；加 `--apply` 才备份并写入。也支持 `--keys-stdin`，避免在命令行
  明文传 key。已有池健康验证：不传 key、加 `--apply`。
- 此 fork 单渠道改多 key 不能只 PUT 多行 key。脚本保留 ch181 身份，写
  `key` 与完整 `channel_info` **BLOB**，等待缓存同步；禁止随后 PUT 覆盖元数据。
- 桥服务 `jev-official-live-8413`（PID 25580）为 OMP 管理的 `persist=true`
  进程，退出当前会话不终止。未添加 Windows 登录/重启自启；重启机器后需启动
  `bun scripts/ops/jev_systemone_bridge.mjs 8413`，启动前确认没有重复监听者。
- 日志：`omp ps logs jev-official-live-8413 --lines 20`。不要直接输出
  `omp ps info --json` 的完整结果，里面含继承的环境凭据；仅投影安全状态字段。
- 保留既有 MAX_INFLIGHT=4、NewAPI retry/auto_ban 策略，不增加自动重试/切模。
  密钥失效或配额问题显式报错；不能以 200 枚 key 推导独立额度。
- 旧 `jev_shared_key` 未删除，仅保留用户回滚资料；运行代码不再引用它。

## OMP 内置用途与本次范围

本机 OMP **18.8.4** 的 `--help` 将 `TYPESAFE_API_KEY` 列为 auto-thinking、
unexpected-stop、AI staging、Eval `judge()` 的凭据。Eval 设备文档也说明：
有 TypeSafe 凭据时可用 TypeSafe，否则可用 tiny/smol chat model。

本次仅接通 MCP，经 NewAPI 按需判断；**没有宣称这些内置功能已改走本池**。
没有自动派工、发布审批或安全授权策略变更。判断能辅助筛选/分诊/复核，但
不能替代测试、权限检查或确定性配置规则。

## 回滚

- 先禁用 ch181：`POST /api/channel/181/status {"status":2}`。
- 切换前完整备份目录：
  `~/.new-api-local/backups/jev-official-pool-20261008-214338/`。
  含桥/MCP/入池脚本、mcp.json、secrets.json 字节校验副本与 SQLite 快照
  （36,384,768 bytes，integrity ok）。
- 脚本另备份：`new-api-before-jev-systemone-20261008-220942.db`
  （36,524,032 bytes，integrity ok；该时刻 ch181 已禁用）。
- 恢复旧链需同时恢复旧桥/MCP脚本与 ch181 的旧 key/channel_info；不能把
  旧网关 key 送到官方端点。核对并只恢复 Jev 行/abilities，勿整库覆盖其他工作。
  核实进程身份后重启桥、重连 MCP、做旧链真实探针，最后再启用渠道。

## 增强落地（2026-10-09）

用户决定双开两条增强路径（原约束不变：仅建议信号，不 gate 自动决策，
不进聊天角色链）：

1. **技能层第二意见**：全局技能 `global:jev-second-opinion`
   （`~/.pi/agent/pi-hermes-memory/skills/jev-second-opinion/SKILL.md`）。
   触发：交付高风险改动前 / 2+ 实质方案摇摆 / 不确定断言量化。流程：
   先定自身结论 → 构造客观 state + typed questions（noul/choice/score，
   问题先于答案定稿）→ 调 `xd://mcp__jev_judge` → 并列上报，分歧显式
   标注，Jev 失败静默跳过不阻塞交付。
2. **Ops 周报判定**：`scripts/ops/jev_ops_triage.py`（report-only）。
   输入：channels 表 status≠1 + Guardian state.json disabled/degraded 池
   （错误证据来自 Guardian 扫描 reason——**NewAPI 错误日志(type=5)自
   2026-08-01 起停写**，上游缺陷，见 `scripts/ops/guardian.py:1721`，
   Guardian 改为 tail `~/.new-api-local/logs/oneapi-*.log`；logs 表仅存
   type=2 消费/type=3 管理/少量 type=7，k3 503 亦仅存 stderr）；
   每个信号渠道发一次 jev 判定
   （persistence: transient/persistent/unknown + severity low/med/high），
   输出 markdown 排序表。实测 2026-10-09：76 信号 76 行 ch181 精确
   1:1 归因，余额耗尽类全判 persistent；fail-open（网关不可达）输出
   原始表。**契约坑**：信封必须带 `"type": "jev.systemone"` 字段，否则
   桥走固定模板（is_blocking/urgency/route），答案键对不上；model 保持
   `jev-latest` 即可。

两者均不改渠道状态、不自动决策；Jev 概率仅作独立参考信号（准确率未验证）。
