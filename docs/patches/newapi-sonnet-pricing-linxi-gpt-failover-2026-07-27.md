# Sonnet 定价补齐 + 林夕复活 + GPT 兜底 Claude（2026-07-27）

**VPS DB:** `/opt/new-api/data/one-api.db`（改完均 `podman restart new-api`）

## 1. `claude-sonnet-4-6[1m]/[1M]` 定价补齐

- 症状：ability 已加但网关 e2e 仍 **400「价格未配置」**，auto-mode 分类器仍被挡
- 修复：`ModelRatio` +0.5、`CompletionRatio` +2（对齐 haiku/opus 既有别名）
- **教训：新增别名 ability 必须同步补定价，否则 ability 有了照样不可用**

## 2. 林夕（k40，`:8400`）复活入 Opus 池

- 探活：`#11`/`#60` 上游 `claude-opus-5` 直连 200（额度已恢复）；`[1M]` 上游无实体，走既有 `model_mapping` 回基础模型
- 变更：渠道 status=1；仅开 `claude-opus-5`/`[1M]` ability，pri45 **w5/w3**（`#10 w50`/`#20 w40` 仍主导；`#60` 权重由 DX 管理，`#11` 手动）
- 验证：e2e 6/6 200，日志见 `use_channel:["60"]` 真实出量

## 3. GPT 加入 Claude 故障路由（pri0 兜底）

- `#21`/`#124` 新增 ability：`claude-opus-5`/`[1M]`/`claude-sonnet-4-6`/`[1m]`/`[1M]`，**pri0 w0** 最后兜底
- `model_mapping`：Opus 家族 → `gpt-5.6-terra`（372k ctx）；Sonnet 家族 → `gpt-5.5`
- 故障层次：Opus `pri45 主池 → #118 AR + GPT`；Sonnet `#63(35) → #125(25) → GPT(0)`
- 兜底路径未实弹演练（需打死主池）；Anthropic→OpenAI 转换同 `#63` Kimi 路径，有大量成功先例

## 4. 备注

- `#21 gpt-8317` = DC 公益渠道（`216.195.211.206:8317`），同源还有 `gpt-5.6-luna`/`grok-4.5` 等
- 存量噪音（不影响功能）：`channel_cache.go` `channel_info` JSON scan 报错（7-26 起，~400 条/6h，85 渠道 `channel_info` 全合法，未深查）
- 临时脚本：仓库根 `.tmp-*.py`（probe/fix 类，勿提交）

## Related

- Ops posture: `docs/ops/newapi-dx-cursor-ops.md`
- Sonnet 故障复盘: 同上文档「Sonnet 单点故障 + `[1m]` 别名坑（2026-07-27）」
