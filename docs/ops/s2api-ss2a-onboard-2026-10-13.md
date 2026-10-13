# S2API (ss2a) 双 Key 接入与治理 Runbook（2026-10-13，ch183）

## 1. 来源与密钥解码

- 用户提供 Base58 格式两串 Key：
  - `key1`: `sk-9e56...83f3`（长度 67，Base58 编码传入）
  - `key2`: `sk-2893...485a`（长度 67，Base58 编码传入）
- 目标网关：`https://s2api.top`（即历史 `https://ss2a.top`，标题均为 *S2API - AI API Gateway*）。
- 模型范围：`deepseek-v4-flash`, `deepseek-v4.1-flash`, `glm-5.3-flash`。

---

## 2. 上游探测与模型筛选

直连上游 `https://s2api.top/v1/chat/completions`（标准 OpenAI 格式，测试运算 `17*23=391`）：
- `glm-5.3-flash`：**200 OK**（1.9s~8.4s，正确输出 391）。
- `deepseek-v4-flash`：**200 OK**（8.8s~12.8s，正确输出 391）。
- `deepseek-v4.1-flash`：持续返回 **502 `Upstream service temporarily unavailable`**。
  - **决策**：剔除 `deepseek-v4.1-flash`，不注册至 ch183，防止污染本地 DeepSeek 4.1 池。

---

## 3. 渠道配置（更新存量 ch183）

- 渠道名：`s2api-gateway`
- 协议类型：`type: 1`（OpenAI-compatible，NewAPI 自动拼 `/v1/chat/completions`，`base_url` 取 `https://s2api.top` 无尾部 `/v1`）
- 多 Key 治理：
  - 密钥换行存入两条 `sk-` Key。
  - 修正 `channel_info`：
    ```json
    {
      "is_multi_key": true,
      "multi_key_size": 2,
      "multi_key_status_list": {},
      "multi_key_polling_index": 0,
      "multi_key_mode": "polling"
    }
    ```
- 路由姿态：`priority: 25`, `weight: 5`, `group: default`, `auto_ban: 1`。
  - `deepseek-v4-flash` 池：与 ch118 同级 (p25)，排在 ch180 (p51)、ch127 (p40) 之后。
  - `glm-5.3-flash` 池：排在 ch208 (p30) 之后，高于 ch173 (p20)。

> 后续：`glm-5.3-flash` 经此成为 4 腿池（ch208 p30 / ch118+ch183 p25 / ch173 p20），已注册进 OMP models.yml 并置于 `tiny`/`smol` 兜底链首位（bench TTFT 2.2s，门禁 40/40 + CLEAN）。

---

## 4. 全链路实证

1. **管理测试**：
   - `GET /api/channel/test/183?model=glm-5.3-flash` -> 200 (5.525s)。
   - `GET /api/channel/test/183?model=deepseek-v4-flash` -> 200 (3.018s)。
2. **网关中转与归因核实**：
   - 提权测试通过本地网关 `127.0.0.1:3002/v1/chat/completions` 调用。
   - `logs` 表已确凿记录归因命中 `channel_id=183`，且 `admin_info.is_multi_key: true, multi_key_index: 0`。
   - 测试完毕后恢复 `priority=25` 并执行 `/api/channel/fix` 重建能力表。
3. **备份已落盘**：
   - `C:/Users/zhugu/.new-api-local/backups/newapi-before-s2api-onboard-20261013-122455.db`（`PRAGMA integrity_check = ok`）。
