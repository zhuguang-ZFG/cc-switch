# NewAPI deepseek-v4-flash 503 救火 + 单点根治（2026-07-31）

deepseek-v4-flash 经网关调用报 `503 No available channel`，定位为 ch15 sensenova 两 key 被 429 永久 ban + 该模型单点。救火中又踩 NewAPI rc.21 多 key 模式的换行 header 坑。最终修复：两渠道改单 key + ch40 扩挂 deepseek-v4-flash 补源。

## 1. 现象

```text
503 No available channel for model deepseek-v4-flash under group default
```

裸名 `deepseek-v4-flash` 此前只有 ch15（sensenova）一个源，单点。

## 2. 根因链

### 2.1 ch15 两 key 被 429 永久 ban

ch15 `channel_info` BLOB 记录多 key 状态：

```json
{"is_multi_key":true,"multi_key_size":2,
 "multi_key_status_list":{"0":3,"1":3},   // 两 key 都 status=3（禁用）
 "multi_key_disabled_reason":{"0":"status_code=429, You exceeded your current quota","1":"..."}}
```

两个 sensenova key 都因 429 配额超限被 NewAPI auto_ban **永久禁用**（status=3），触发渠道级 `status=3, All keys are disabled`，连带 abilities 全部 enabled→0。但直连上游测两 key 都返回 200——429 是针对特定模型/时段的限流，事后已恢复，NewAPI 却不会自动恢复。

### 2.2 NewAPI rc.21 多 key 换行 header 坑

救火第一步重置 `multi_key_status_list` 重新启用两 key，结果出现 `net/http: invalid header field value for "Authorization"`：key 字段是两 key 换行分隔，NewAPI 取到**带换行的字符串**做 `Authorization: Bearer sk-xxx\nsk-yyy`，换行符使 header 非法。ch40（0v0 两 key 换行但 `channel_info=NULL`）同样踩此坑。

**结论**：rc.21 多 key 模式不可靠——`channel_info` 不正确时换行会泄漏到 header。所有渠道应改单 key。

## 3. 修复

### 3.1 ch15 + ch40 改单 key

- ch15：key 只留 k0（`...yqbclw`），`channel_info` 设 `is_multi_key=false`。k1 备用。
- ch40：key 只留 k0（`...8f229f`），第二个 0v0 key 备用。

### 3.2 ch15 恢复

- `channels.status` 3→1，`other_info` 清 ban 标记。
- abilities 恢复：`deepseek-v4-flash`、`sensenova-6.7-flash-lite` enabled 0→1（`sensenova-u1-fast` 保持禁用，见 [u1-fast 摘除](newapi-sensenova-u1-fast-disable-2026-07-31.md)）。

### 3.3 ch40 扩挂 deepseek-v4-flash（单点根治）

0v0.club 直连测 `deepseek-v4-flash` 返回 200。ch40 models 从 `glm-5.2` 扩到 `glm-5.2,deepseek-v4-flash`，abilities 同步启用。

当前 deepseek-v4-flash 两源：

| 渠道 | 权重 | 上游 | key |
|---|---|---|---|
| ch15 sensenova-token | 10 | token.sensenova.cn | 单 key |
| ch40 0v0-glm | 5 | 0v0.club | 单 key |

改前备份 `one-api.before-dsv4-rescue-<ts>.db`。sqlite 直写 + `podman restart new-api`。

## 4. 验证

```text
deepseek-v4-flash 连打 8 次 -> 8/8 http200（content 偶空为 reasoning 占满 max_tokens=64，非渠道问题）
glm-5.2 -> 200 OK
sensenova-6.7-flash-lite -> 200（content 空，同 reasoning 现象）
```

## 5. 注意与教训

- **auto_ban 是永久禁用**：NewAPI 对 429 等错误自动 ban key 后不会自动恢复，即使上游已恢复。需手动重置 `channel_info.multi_key_status_list` + `channels.status` + `abilities.enabled`（三处都要，auto_ban 会连禁 abilities）。
- **多 key 模式慎用**：rc.21 下 `key` 字段换行分隔多 key，若 `channel_info` 不正确会泄漏换行到 Authorization header。建议所有渠道单 key；要轮换就建独立渠道（如 0v0 第二 key 可建 ch41）。
- **单点模型必补源**：deepseek-v4-flash 此前只 sensenova 一个源，被 ban 即全挂。现在补 0v0 双源。
- 第二个 0v0 key、sensenova k1 备用；需轮换时建独立渠道，不再用多 key 模式。

> 安全：本文档不含完整 API key、VPS 密码。
