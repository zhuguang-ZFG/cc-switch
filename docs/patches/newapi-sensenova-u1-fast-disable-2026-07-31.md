# NewAPI ch15 sensenova-u1-fast 错挂修复（2026-07-31）

巡检发现 NewAPI 近期有约 190 条 `模型不存在` 错误，集中在 ch15（sensenova-token）。核对后定位为 `sensenova-u1-fast` 错挂。

## 1. 现象与定位

ch15（sensenova，`https://token.sensenova.cn`）abilities 表挂了三个模型：
- `sensenova-6.7-flash-lite`
- `deepseek-v4-flash`
- `sensenova-u1-fast`

直连上游 `/v1/models` 确认三个都在列表里。但逐一测 `/v1/chat/completions`：

```text
sensenova-6.7-flash-lite -> 200 OK
deepseek-v4-flash        -> 200 OK
sensenova-u1-fast        -> 404 {"error":{"message":"model is not found","code":"5"}}
```

`sensenova-u1-fast` 是**图像生成模型**（output modality = image，非 text），走 chat completions 永远 404。上游 `/v1/models` 把它列出来是误导——它不能用于文本对话。NewAPI 把它当 chat 模型挂在 ch15 上，请求一旦路由过去就 404。

历史 190 条"模型不存在"日志里它占大头。客户端（kimi/omp）早在 7-21 那次会话已摘除该模型，但 NewAPI ch15 的 ability 仍 `enabled=1`，是纯遗留。

## 2. 修复

禁用 ch15 的 `sensenova-u1-fast` ability，并从 `channels.models` 字段移除该模型名，保持一致。改前备份 `one-api.before-u1fast-disable-<ts>.db`。

```text
abilities: channel_id=15, model=sensenova-u1-fast, enabled 1 -> 0
channels.models (ch15): sensenova-6.7-flash-lite,deepseek-v4-flash,sensenova-u1-fast
                       -> sensenova-6.7-flash-lite,deepseek-v4-flash
```

sqlite 直写（rc.21 的 `POST /api/channel` 会 panic），改完 `podman restart new-api`。仅禁用 ability，不删渠道，渠道本身及另两个模型不受影响。

## 3. 验证

经网关逐一调三个模型名：

```text
sensenova-6.7-flash-lite -> 200 OK
deepseek-v4-flash        -> 200 OK
sensenova-u1-fast        -> 503 No available channel for model sensenova-u1-fast
```

`sensenova-u1-fast` 不再路由到 ch15 产生上游 404，改为 NewAPI 自身 503（无可用渠道），符合预期——客户端本就不该请求它，且客户端配置已无引用。

## 4. 注意

- 上游 `/v1/models` 列出的模型不一定都能走 chat completions；接入新上游时应逐个实测 chat 调用，不能只看列表。
- ch15 仍是 sensenova 单源（6.7-flash-lite / deepseek-v4-flash），单点风险如旧，但这两个实测稳定。

> 安全：本文档不含 API key、VPS 密码。
