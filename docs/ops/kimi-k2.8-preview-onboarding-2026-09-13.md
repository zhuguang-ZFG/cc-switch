# K2.8 Preview 接入核查与 OMP 升级 — 2026-09-13

## 结论

K2.8 Preview **无需在 NewAPI 新建**：官方将其部署在 `kimi-for-coding`
槽位（`~/.kimi-code/config.toml` 权威映射：`kimi-for-coding` → "K2.8
Preview"，1M context，support_efforts [low, high, max]）。本地 NewAPI
ch33（kimi-official-k3，base_url `https://api.kimi.com/coding`，type 1，
key 前缀 `sk-kim`）的 models 已含 `kimi-for-coding`，一直在服务。

## 验证证据

- 管理探针 `test/33?model=kimi-for-coding`：ok。
- Relay 端到端（3002 `/v1/chat/completions`，OMP token 路径）：200，
  max_tokens=512 时 content "pong"、finish_reason=stop、56 completion tokens。
- **注意**：max_tokens 给 16 这类小值会空响应——K2.8 是 always_thinking
  模型，reasoning 先吃预算（与 glm-5.3 同模式，见
  hashneuron-channel-2026-09-13.md 的 glm-5.3 注记）。

## OMP 侧变更（`~/.omp/agent/`，非 git 仓库）

1. `models.yml` 条目 `kimi-for-coding` 从 K2.7 规格升级：
   contextWindow 262144 → 1048576，maxTokens 32768 → 131072，新增
   `thinking: {mode: effort, efforts: [low, high, max]}`，name 改为
   "Kimi K2.8 Preview (kimi-for-coding, official ch33)"。
   YAML 校验通过（88 模型），热重载生效。
2. `config.yml` `retry.fallbackChains` 新增
   `zg-newapi/kimi-for-coding → [zg-newapi/k3, zg-newapi/deepseek-v4-flash]`
   （对齐 k3 chain 的 08-16 事故教训：防随机掉到 haiku）。
   **chain 启动时缓存，需重启 OMP 会话生效**。

## 运营注记

- models.yml 热重载对运行中会话有 ~90s fallback 窗口
  （omp-models-yml-hotreload-fallback-2026-08-16.md 规则 1）；本次编辑时
  cc-switch 项目的 OMP 会话无进行中的 turn，未触发。
- `kimi-for-coding-highspeed`（= K2.7 Code Highspeed）条目未动。
