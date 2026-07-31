# NewAPI 接入 zzzcoding.org gpt 备份渠道（2026-07-31）

将 zzzcoding.org 作为 gpt 系列备份渠道接入 NewAPI ch41，重点补 `gpt-5.6-luna` 单点。

## 1. 上游探测

`https://api.zzzcoding.org/v1/models` 列出 9 个模型。逐一测 `/v1/chat/completions`：

```text
gpt-5.5        -> 200 OK
gpt-5.6-sol    -> 200 OK
gpt-5.6-luna   -> 200 OK
gpt-5.6-terra  -> 200 OK
gpt-5.4        -> 200 OK
gpt-5.4-mini   -> 200 OK
gpt-5.3-codex  -> 502 Upstream request failed（不可用，不接）
gpt-5.2        -> 502（不接）
gpt-image-2    -> 400 This model is not supported on the Chat Completions endpoint
                 （图像模型，同 sensenova-u1-fast 坑，不接）
```

接入前发现 NewAPI 的 `gpt-5.6-luna` 仅 ch16 单源（SPOF），`gpt-image-2` 仅 ch30 单源但属图像模型不补。zzzcoding 正好补 luna 单点 + 主力备份。

## 2. 接入配置

新增渠道 ch41：

| 字段 | 值 |
|---|---|
| id | 41 |
| name | zzz-gpt |
| type | 1（OpenAI） |
| base_url | `https://api.zzzcoding.org` |
| models | `gpt-5.5,gpt-5.6-sol,gpt-5.6-luna,gpt-5.6-terra,gpt-5.4,gpt-5.4-mini` |
| status | 1 |
| weight | 5 |
| group | default |
| auto_ban | 1 |
| key | 单 key（rc.21 多 key 不可靠，见 [503 救火](newapi-deepseek-v4-flash-503-rescue-2026-07-31.md)） |
| channel_info | `is_multi_key=false` 单 key 模式 |

abilities 表同步启用 6 个模型。

## 3. 验证

- **单独验证**：临时禁用其他源只留 ch41，gpt-5.5/gpt-5.6-luna 各 2/2 http200。
- **混跑验证**：恢复后连打 6 次（两模型各 3）全 http200。

接入后 gpt 源分布变化（重点）：

| 模型 | 接入前 | 接入后 |
|---|---|---|
| gpt-5.6-luna | ch16（**单点**） | ch16, ch41（双源） |
| gpt-5.5 | ch16,30,34 | ch16,30,34,41 |
| gpt-5.6-sol | ch16,30,34 | ch16,30,34,41 |
| gpt-5.6-terra | （单源） | +ch41 |
| gpt-5.4 | （单源） | +ch41 |
| gpt-5.4-mini | （单源） | +ch41 |

## 4. 注意

- 不接 gpt-image-2（图像模型不可 chat）和 gpt-5.3-codex/gpt-5.2（502），避免重蹈 sensenova-u1-fast 错挂覆辙。
- 单 key 模式，rc.21 多 key 不可靠。
- 接入验证时临时禁用其他源做 solo 测试，恢复时曾误启用原本 disabled 的 ch2/21/25 ability，已手动修正回原状。

> 安全：本文档不含完整 API key、VPS 密码。
