# furry.vg 免费源入网（ch105 furry-vg-muse，2026-08-21）

来源：用户提供 `https://freeapi2.furry.vg/v1`，声称长期可用、240 RPM、
每天约 20B tokens 免费额度，模型 ox-alpha + muse-spark-1.2-contributor。

## 实测结论（2026-08-21 直连探测）

- **必须带浏览器 UA**，否则 Cloudflare 1010（403）——与 opencode Go 同款，
  走 `header_override` 注入。
- key 权限仅放行 `ox-alpha` 和 `muse-spark-1.2-contributor` 两个模型 id；
  `oc/*` 前缀变体一律 403 permission_error。
- **muse 可用但有怪癖**：推理模型，reasoning 不回显且计入 max_tokens——
  max_tokens≤32 会得到空 content（200）；给 1024 正常出文
  （330 completion tokens）。`finish_reason` 恒为 null。非流式/流式均可。
- **ox-alpha 当前上游侧坏掉**：网关把它重写到自己拼错的
  `x-preivew-f-free`（注意拼写），真实上游 401 "Model x-preivew-f-free
  is not supported"。已从渠道排除，源头修复前不要加回。

## 渠道形态

- ch105 `furry-vg-muse`，p9/w5，ModelRatio=0，
  `models=muse-spark-1.2-contributor-free`，
  model_mapping `muse-spark-1.2-contributor-free → muse-spark-1.2-contributor`。
- muse 免费池从单一 ch96 Zen（p10）变为双渠道：Zen 抖 503 时 NewAPI 池内
  切 furry，不再惊动 OMP 模型级 fallback。
- 入网脚本：`scripts/ops/add_furry_muse_channel.py`（add_* 标准合约：
  备份 → 建禁用 → 探活 → 启用 → 回读验证 → 3002 relay 探测）。
  备份 `~/.new-api-local/backups/new-api-before-furry-muse-20260821-222629.db`。

## 运维要点

- 看到 muse 回复空 content 先检查 max_tokens 是否被推理吃光，不要误判
  渠道故障。
- ox-alpha 若源头修复（自测 `{"model":"ox-alpha"}` 不再 401），可复跑
  脚本扩展 models/model_mapping 把 x-preview-f-free 也挂上。
- Guardian 自动纳入扫描/恢复队列，无需额外配置。
