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

## 3. GPT 加入 Claude 故障路由（两次迭代，含重要机制发现）

- **机制发现（实测）**：本 fork 按 **`channels.priority` DESC** 选渠，`abilities.priority` 不是主排序键
- ❌ v1（已撤回）：给 `#21/#124`（ch_pri 60/55）直接挂 claude ability（abilities pri0）→ **`#21` 立即抢走全部 Claude 流量**（`use_channel:["21"]` 直选，CC 会话 16 连击落在 GPT 上），opus/sonnet 全中招
- ✅ v2（现网）：克隆 `#21` → **`#129 gpt-terra-claude-fallback`，ch_pri=-30 全局最低**（低于 `#118`=0、`#63`=-20），挂 `claude-opus-5`/`[1M]`/`claude-sonnet-4-6`/`[1m]`/`[1M]` ability；`model_mapping`：Opus→`gpt-5.6-terra`，Sonnet→`gpt-5.5`
- 验证：opus→`["10"]/["20"]`，sonnet→`["125","63"]`，terra→`["21"]`；`#129` 只有 Claude 渠全灭才会被选中
- 推论：Sonnet 的「主备对调」其实由 ch_pri 决定（`#125`=35 > `#63`=-20），vyceai 恢复后**自动**回主渠，无需手动改 abilities priority

## 4. 备注

- `#21 gpt-8317` = DC 公益渠道（`216.195.211.206:8317`），同源还有 `gpt-5.6-luna`/`grok-4.5` 等
- 存量噪音（不影响功能）：`channel_cache.go` `channel_info` JSON scan 报错（7-26 起，~400 条/6h，85 渠道 `channel_info` 全合法，未深查）
- 临时脚本：仓库根 `.tmp-*.py`（probe/fix 类，勿提交）

## Related

- Ops posture: `docs/ops/newapi-dx-cursor-ops.md`
- Sonnet 故障复盘: 同上文档「Sonnet 单点故障 + `[1m]` 别名坑（2026-07-27）」
