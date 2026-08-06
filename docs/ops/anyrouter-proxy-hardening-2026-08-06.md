# AnyRouter 指纹代理加固 - 2026-08-06(第二轮)

对象:`C:\Users\zhugu\.kimi-code\proxies\anyrouter-proxy\proxy.cjs`(127.0.0.1:8789,OMP slow 链 `anyrouter/*` 的指纹桥)。仓库外文件;本文件仅记录变更与回滚位置。

## 背景

exit 58 崩溃(约 10:02,无日志请求)+ 此前 09:45–09:46 429 限流风暴。第一轮已加 `uncaughtException`/`unhandledRejection` 保活。本轮补齐请求生命周期与监督层。

## 变更

| 项 | 内容 |
|---|---|
| 请求体上限 | `MAX_BODY_BYTES = 4 MiB`,超限 413 并断流(实测最大正常体 ~150KB) |
| 客户端中断 | `req.on('error')` + `clientError` 处理,上传中断不再悬挂缓冲 |
| 上游/响应流 | `upstreamRes`/`res` error 处理;`https.Agent keepAlive, maxSockets: 8` 复用 TLS |
| server error | 记录后 `exit(1)`,交给监督层有界退避,不做僵尸 |
| 监督 | hub `restart: on-failure`(有界退避)+ `persist`/`detached` |

回滚件:`proxy.cjs.bak-20260806`(加固前版本,8870B)。

## 验证(2026-08-06,本机实测)

- `node --check` 通过。
- `taskkill -F` 强杀 → hub 自动重启(restarts=1,新 PID 监听 8789)。
- 6MiB 请求体 → 413 `request body too large`,进程存活。
- 客户端中途断连(发送部分 body 后 destroy)→ 进程存活,端口仍监听。
- 真实链路:`omp -p --model anyrouter/claude-opus-5 "Reply exactly BRIDGE_OK"` → `BRIDGE_OK`(76.9s)。

## 遗留边界

- hub broker 退出后 detached 进程存活但无人重启;重启电脑后需手动 `hub start` 或注册 Windows 计划任务(未做,需用户授权系统级变更)。
- 上游 520/429 属 AnyRouter 公益站侧限流,代理不重试(避免重试风暴);OMP slow 链 cooldown-expiry 已兜底。
- 健康检查勿用最小 curl 体(门禁秒拒),用真实 OMP 请求。
