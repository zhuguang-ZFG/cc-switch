# 基元律动（tokenrhythm）key 池接入：21 单 key 渠道 + WoTrus 桥（2026-10-04 凌晨）

## 结论

用户提供 37 个 `sk_tr_` key（文件"基元律动"）。**21/37 通过余额实测**，
按 agentrouter-keypool 先例落成 **21 个单 key 渠道 ch150–170
（tokenrhythm-k01..k21）**，全部经 8792 本地桥（WoTrus CA 被 Mozilla 系
distrust）接入，p-10/w1 备份位，7 模型缺口集。4 项抽测全过、147 ability
行读回一致、双门禁干净。**qwen3.7-max 随 ch89 死亡的空缺就此复活**。

## 关键事实（全部实证）

- 端点 `https://tokenrhythm.studio`（api 子域无服务）；23 模型目录。
- **WoTrus CA**（证书 CN=WoTrus RSA DV SSL CA 2，2026-07 签发）：Mozilla
  自 2017 WoSign 事件后 distrust，fork 内嵌根包同 → 与 nimbridge 同款
  "do_request failed"。复用桥脚本第二目录 `~/.kimi-code/proxies/tokenrhythm-bridge/`
  （supervisor 条目 `PROXIES["tokenrhythm"]` port 8792，
  `env BRIDGE_UPSTREAM=https://tokenrhythm.studio`；桥参数化 =
  `BRIDGE_UPSTREAM` env，缺省 nimbridge IP）。
- **多 key 渠道被证伪**：两次创建探针（`\n` / `\r\n` 分隔）
  `channel_info.is_multi_key` 均 = false——blob 被当**一把非法 key**，
  每请求 Go 头装配即败（time=0、无上游日志、桥零到达的典型特征）。
  本 fork 多 key 唯一可行形 = N 单 key 渠道。
- **余额扫描**：37 key 全量抽测（4 并发小调用）→ 21 OK / 16 空响应
  （余额尽或挂死）。好 key 行号记录在脚本 `GOOD_KEY_INDICES`（值永不打印）。
  16 把死 key 未入库（文件原样留在 D:/Downloads）。
- 402 细节：fork 渠道自测 payload 不带 max_tokens，推理模型按成本预估
  可超小余额 key → 单测偶发 402（INSUFFICIENT_BALANCE），不影响生产
  （生产调用按实际计费 + 多渠道轮换）。

## 渠道与模型

| 项 | 值 |
|---|---|
| 渠道 | ch150–170 `tokenrhythm-k01..k21`（type=1，base `http://127.0.0.1:8792`，p-10/w1，auto_ban=1） |
| 模型 | `k3`(→kimi-k3)、`qwen3.7-max`、`qwen3.8-max`、`glm-5.3`、`glm-5.3-flash`、`deepseek-v4-flash`(→deepseek-flash)、`longcat-2.0` |
| 备份 | `new-api-before-tokenrhythm-20261004-020905.db`（integrity=ok） |
| 定价 | 全部已在库（k3=2、qwen×2=0.5、glm-5.3=0.7、flash=0.075、deepseek=0.5）；`longcat-2.0` unset（官方价未查，open item） |
| 验证 | abilities 21×7=147 行；抽测 glm-5.3(0.865s)/qwen3.7-max(3.348s)/longcat-2.0(1.636s)/k3-mapping(1.751s) 全 success；k3 链健康归因 ch33（备份未抢流量） |

## 密钥纪律（评审 blocker 执行）

- key 一律运行时从 `D:/Downloads/基元律动.txt` 读取（脚本 `load_keys` /
  shell `head|sed` 提取），**绝不内联**进脚本/命令/runbook/commit。
- 报告只出现 `sk_tr_` 前缀与数量。该文件本身仍躺在 Downloads——建议用户
  移至受控位置（如 `~/.omp/guardian/` 旁加 ACL），未代劳。

## 运营要点

- fork 渠道/key 缓存：sqlite 直改 key 后须重启 fork 生效（本次重启
  new-api.exe 验证过；`stop.ps1` + detached `start.ps1`，3002 秒回）。
- 同类故障鉴别：base_url 指向回环桥时"do_request failed + time=0 +
  桥零到达"= 请求根本没出发（key blob 非法/TLS 校验），不是上游问题。
- longcat-2.0 现三腿：ch68/69 agnes（38/39，429 前科）+ 21 渠道备份
  ——haiku 路径 agnes 429 的缓解就此获得冗余。

## 评审补遗（2026-10-04 02:1x）

- **16 把"empty"key 直连复核**（绕桥、retryAfter 节奏、25s 超时）：仍全空——
  桥吃掉/瞬时限流两种假设均被否，确认上游侧死亡。21 好 key 名单维持。
- **备份档序**：评审指出 budsin/nimbridge/tokenrhythm 同档 -10 会三等分备份
  流量、稀释指定备份位 → 已压实为 **budsin(ch148) -10 → nimbridge(ch149)
  -20 → tokenrhythm(ch150-170) -30**（渠道级 + fix，abilities 镜像读回一致；
  备份 `new-api-before-tier-order-20261004-021112.db`）。
- **ch15 glm-5.2 遗留**：ch15 复活连带启用 glm-5.2（sole carrier）——07-29
  runbook 曾按 5M TPM 共享陷阱摘除该模型（大 prompt 429 放大）。本次**保留
  启用**（小请求可用；摘除会让 glm-5.2 全池零在营），口径：大上下文 GLM
  请求勿走 glm-5.2，用 glm-5.3。
- supervisor 仓库镜像 `scripts/ops/proxies-supervisor.py` 已同步双桥条目
  （与 live 文件 diff 验证 IDENTICAL；10/10 服务 healthy）。

## 回滚

删 ch150–170（或禁双表）+ `POST /api/channel/fix`；还原
`new-api-before-tokenrhythm-20261004-020905.db`；supervisor 移除
tokenrhythm 条目 + 分离式重启 + 删桥目录。
