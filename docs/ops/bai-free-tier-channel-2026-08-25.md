# B.AI 免费档接入 + guardian-watchdog 恢复（2026-08-25）

本日两项：guardian 看护进程恢复、B.AI（api.b.ai）免费档渠道接入与 3 个新能力
模型注册。全部先探测后落地，均有回滚物。

## guardian-watchdog 退出 58 恢复

症状：hub 报 `guardian-watchdog failed with exit code 58`；`hub ps` 显示无守护
进程。诊断：watchdog.ps1（pid 12756，昨日 20:04 启动）已死，但 guardian 本体
（pid 22452）仍在跑——**即 guardian 处于无人值守状态**，心跳僵化后无人重启。
watchdog.log 末行停在 `2026-08-24 20:04:14`，未记录退出原因（PowerShell 宿主
被外部终止时不落日志）。

处置：经 hub 以 `persist:true` + `restart:on-failure` 重启（AGENTS.md 要求服务
类进程走 hub 托管）。验证：pid 1552 running，日志新增
`2026-08-25 16:20:23 Guardian watchdog started`，heartbeat.json mtime 距当前
9 秒（存活证据）。

## B.AI 免费档：广告核实与真实边界

用户转来的推广消息附带一个 `sk-` key 与邀请码。**先做凭据归属确认**（未确认前
不外泄探测），用户确认自有后才继续。

广告与实测的差距（这是本次最重要的结论）：

| 广告声称 | 实测 |
|---|---|
| 基址 `chat.b.ai` | **假**：真实 API 基址是 `https://api.b.ai/v1`（chat.b.ai/v1/models 返回的是另一套目录，completions 404） |
| MiMo V2.5 完全免费 | **真**：`cost:"0"`，mimo-v2.5 与 mimo-v2.5-pro 均可用 |
| DeepSeek V4 Flash / Hy3 / Vision Exp 免费 | **真**：三者均 200 |
| （隐含）目录内模型可用 | **假**：`claude-opus-5`、`deepseek-v4-pro`、`kimi-k3`、`glm-5.3`、`qwen3.8-max` 全部 403 `Access restricted. Deposit required to unlock premium models` |

即结构是「免费小模型引流 + premium 需充值」。目录 42 个模型，可用仅 5 个。

注：urllib 直连该站被 Cloudflare 拦为 `403 error code 1010`（与今日 gorouter/
tabitoken 同一堵墙），curl 正常——探测第三方站点须用真实 HTTP 栈，否则会把
指纹封锁误判为鉴权失败。

## ch111 bai-free 渠道

```
ch111 bai-free  type=1  base_url=https://api.b.ai  p30/w5  auto_ban=1
  models: mimo-v2.5, mimo-v2.5-pro, hy3, deepseek-v4-flash,
          deepseek-v4-flash-vision-exp
  test_model: deepseek-v4-flash
```

按既有纪律执行（同 `add_justwoker_opus_channel.py` / `split_tabitoken_channel.py`）：

1. 整库备份 `new-api-before-bai-channel-20260825-162537.db`（119.4MB，integrity ok）；
2. `POST /api/channel/` 必须双层包 `{"mode":"single","channel":payload}`——
   裸 payload 返回 `channel cannot be empty`；
3. **先建为禁用**（status=2）→ 逐模型管理探测 5/5 通过（1.3s–10.2s）→ 才启用；
4. 启用后核验：channel status=1/p30/w5，abilities 5 行全部 `enabled=1,p30,w5`。

p30 定档理由：全部是免费小模型，不与主力池（p50+）争流量；auto_ban=1 保留
（第三方免费额度可能随时收回，让自动封禁正常接管）。

## OMP 注册 3 个新能力

mimo-v2.5 与 deepseek-v4-flash 本机已有渠道（ch101/109、ch15/110），B.AI 仅作
冗余不新增模型条目。真正新增的 3 个按**实测**能力填写，不按家族猜测：

| 模型 | contextWindow/maxTokens | reasoning | 实测依据 |
|---|---|---|---|
| mimo-v2.5-pro | 262144 / 32768 | **不标** | `reasoning_tokens=0`、`cost=0`，10–35s 出词 |
| hy3 | 262144 / 32768 | true | `reasoning_tokens=30` |
| deepseek-v4-flash-vision-exp | 1000000 / 131072 | true + `input:[text,image]` | `reasoning_tokens=12` |

备份 `~/.omp/agent/models.yml.before-bai-free.bak`；`omp models` exit 0 且三项
可见。relay 端到端：hy3 200/1.8s、vision-exp 200/1.1s、mimo-v2.5-pro 首发 503
（渠道刚启用的能力缓存瞬态，与今日备份档启用时同一现象）重试即 200。

## 遗留：config.yml default 角色越界（需用户定调）

`test_omp_routes` 1 项失败：
`test_untrusted_sotamodel_is_not_an_automatic_role_or_fallback`。根因是
`~/.omp/agent/config.yml` 的 `default: sotamodel-canary/claude-opus-5-max`——
未受信 sotamodel 被绑为自动角色，正是该门禁设计要拦的情形。

**非本轮改动**（本轮只在 models.yml 追加 3 个模型条目），属外部变更。未擅自
回改生产路由配置，待用户确认是否有意为之；若非有意，改回受信模型即恢复绿。

## 林夕现状（承接昨日）

ch9 + ch18 双双 `503 No available accounts`（账号池整体耗尽，供应商侧）。
claude-opus-5 实际容量仍为 baibei ch3 + p50 备份档（94/95 实测健康）。
B.AI 无法补此缺口——其 claude-opus-5 属 premium，需充值。
