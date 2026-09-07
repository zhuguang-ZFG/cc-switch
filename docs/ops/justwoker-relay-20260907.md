# justwoker 上游换组接入修复（2026-09-07）

## 现象
- ch94/95（justwoker-opus-1/2）渠道测试与 relay 自 09-06 21:46 起全 403
- 上游 `api.justwoker.icu` 对家宽直连 IP Cloudflare 封禁（error 1010, "You are unable to access justwoker.icu"）
- 上游**换模型**：Claude 系（opus-5/4-8 及 thinking）全下架 → **gpt-5.6-luna / gpt-5.6-sol / gpt-5.6-terra**
- 上游**换面**：`/v1/chat/completions` 被 CF 拦截（GET 也 403）；**`/v1/messages`（Anthropic 面）可用**
- `/v1/models`（GET）例外放行

## 修复架构
```
NewAPI ch94/95 (type=14, base_url=http://127.0.0.1:8790)
  -> justwoker-relay（node, supervisor 看护, 端口 8790）
     - 注入 Chrome UA（CF 对非浏览器客户端 UA 敏感, 即使出口 IP 放行）
     - CONNECT 隧道经 Clash mixed-port 7897（悍刀行出口）
        -> api.justwoker.icu /v1/messages（Anthropic 面）
```

### 为什么不全局代理
NewAPI 进程无 per-channel 代理支持；全局 HTTP(S)_PROXY 会把 kimi（ch33,
国内直连健康）等全部拖进代理出口。本地中转是 8788/8789 桥同款先例。

## 变更清单
1. **`~/.kimi-code/proxies/justwoker-relay/justwoker-relay.cjs`**（新建）:
   CONNECT 隧道 + UA 注入 + 端口 8790
2. **`~/.omp/guardian/proxies-supervisor.py`**: PROXIES 增 `justwoker-relay` 条目
   （node, probe 127.0.0.1:8790）; supervisor 已重启（pid 1268）
3. **NewAPI DB**（ch94/95）:
   - `base_url`: `https://api.justwoker.icu` → `http://127.0.0.1:8790`
   - `type`: 1（openai）→ **14（anthropic）**
   - `models`: opus 四件套 → `gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra`
   - abilities 重建（default 组 × 三模型 × 双渠道, priority 50 weight 5）

## 验证
- 渠道测试矩阵 **4/4 PASS**（ch94/95 × luna/sol/terra, 1-2s each）
- 真请求验证: `gpt-5.6-sol` /v1/messages 返回 `pong`（Anthropic 响应格式, msg_ id）
- supervisor 日志: `justwoker-relay 端口 8790 不可达，重启` → 拉起成功

## 注意
- relay 依赖 **Clash 在线**（悍刀行出口）。Clash 停 → justwoker 渠道 502。
- gpt-5.6-* 目前**未挂 OMP models.yml**（zzzcoding 的 gpt-6-astra 教训:
  OpenAI 系模型 + anthropic-messages 线制的兼容性要先实测全量 tools）。
  需要时先 `omp -p --model zg-newapi-anthropic/gpt-5.6-sol` 类实测再入册。
- thinking 档: 上游 Anthropic 面对 thinking 参数的支持未验证。

## 后续同步（09-07 晚）
- models.yml: `zg-newapi/claude-opus-5-thinking` 条目标注 DEAD（上游 Claude 系全下架）。
  OMP config 门禁 40 tests OK（`650cd72`, ~/.omp/agent 本地 repo）。
- gpt-5.6-* 三模型 NewAPI 侧就绪（4/4 渠道测试 PASS）, OMP 入册待全量 tools 实测。
