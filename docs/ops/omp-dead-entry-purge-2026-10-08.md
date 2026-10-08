# OMP 死条目批量摘除（B 类）— 2026-10-08

## 背景

mistral-large-4-0 事件确立了一条规则：**models.yml 里没有"禁用"机制，死条目被选中即 503**，
正确处置是摘除条目 + 注释留复活模板（见 `agentrouter-bridge-400-fix-2026-10-08.md`）。
OMP 全量体检（A–F 分级）列出的 B 类：17 个已注册但无可用承载的模型条目。

角色表（`~/.omp/agent/config.yml` modelRoles）只有 `advisor` 挂在死集上
（`zg-newapi/space-bunny`），因此本轮摘 16 个，**space-bunny 保留**，待 A3 换指后再摘。

## 摘除清单（zg-newapi，82 → 66 条目）

```
diffusiongemma-26b-a4b-it  dots-3-note-preview  hy3  hy3-free  k3-256k
kimi-for-coding            kimi-for-coding-highspeed  muse-spark-1.3-contributor
nemotron-3-ultra-550b-a55b  omen-alpha  qwen3.8-flash  qwen3.8-max-free
sensenova-6.8-flash-lite   step-3.7-flash  step-router-v1  zai-glm-5-2
```

判定依据：DB `abilities` 无 enabled 承载，或唯一承载渠道为 fail-closed / 站侧确定性错误
（k3 → ch170 402、agnes → ch68/69 403 预扣费、omen-alpha → ch125 429 auto_ban）。

## 操作与回滚

- 备份：`models.yml.bak-20261008-205216-drop16`（摘除前）、
  `models.yml.bak-20261008-205657-restore-fengwind-step37`（误摘修复前）。
- 删除脚本按 `- id:` 起、缩进 >4 的整块扫描，**未限定 provider 作用域**，
  因此把 `fengwind/step-3.7-flash`（同名、活条目）一并摘掉，已从备份核对后恢复
  （fengwind 11 → 12 条目）。教训：跨 provider 同名 id 必须按 provider 段过滤。
- models.yml 末尾（`zg-newapi-anthropic` 之前）留了聚合注释指向本文件。

## config.yml fallback 链联动修复

摘除后 `test_live_fallback_chains_have_no_hard_violations` 变红——链内仍引用死选择器：

- 删除以已摘 id 为**链键**的条目：`kimi-for-coding`、`zai-glm-5-2`、`qwen3.8-max-free`、`omen-alpha`。
- 链值 `zg-newapi/omen-alpha`、`zg-newapi/omen-alpha:high` → `zg-newapi/deepseek-v4-flash`
  （muse-spark-1.2-contributor-free、qwen3-8-27b、tiny、commit、smol 五处）。
- vision 链的 `zg-newapi/step-3.7-flash` 直接删除（保留 deepseek-v4-flash-vision、agnes-2.5-flash；
  链首角色模型即 dots-3-note-prev）。
- 备份：`config.yml.bak-20261008-205725-fallback-deadchain`。

## 验证

- `python3 -m unittest scripts.ops.test_omp_routes` → **Ran 40 tests, OK**。
- `models.yml` / `config.yml` YAML 解析通过；zg-newapi 66、fengwind 12、mistral-official 4，无重复 id。
- 选择器对账：全部 fallback 链键/值 + 11 个 modelRoles 指向均能在注册模型中解析（unresolved = []）。
- 实弹（3002 网关 + local-windows-clients token，串行）：
  `dots-3-note-prev` 200、`deepseek-v4-flash` 200、`qwen3-8-27b` 200、`deepseek-v4-flash-vision` 200。

## 复活方式

上游恢复后，把注释块（或本文件清单）中的 id 按 zg-newapi 现有条目格式回填，
并同步补 `compactionModel: zg-newapi/deepseek-v4-flash`（A1 全局换轨后的统一值）。

## 仍挂账

- A2：`tiny` 角色 `agnes-2.5-pro-alpha` 上游余额尽（403），需充值或换指。
- A3：`advisor` 角色 `space-bunny` 无承载（ch130 429 auto_ban；ch178 无正主）；换指后才能摘该条目。
- vision 链首位角色模型 `dots-3-note-prev` 实弹 200，但 `agnes-2.5-flash` 尾项仍依赖 A2 同族余额。
- D（ch170 停泊）、E（zen 桥高延迟）、F（3003 五死条目 + claude-opus-5 流式缺陷）未动。
