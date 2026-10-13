# deepseek-v4.1-flash 池扩容：muyuan ch173 + cpa2 ch221（2026-10-13）

## 触发

用户令：muyuan 公益站上新 DeepSeek → 聚合；随后补一路线 cpa2.803099.xyz（cline-pass 账号池）。

## 上游实测（直连，浏览器 UA，key 不落盘）

- muyuan.do：31 id（较昨日 +10：glm-5.3-flash、mistral-large-4 系、labs-leanstral、voxtral-mini 等），deepseek id 与池名**同名** `deepseek-v4.1-flash`；chat 2/2 200（1.1–1.5s，17×23=391 对）。key 双行取首行。
- cpa2.803099.xyz：1 id `cline-pass/deepseek-v4.1-flash`（上游自报 `deepseek/deepseek-v4.1-flash`）；chat 2/2 200（3.2–4.0s，答对）。与已禁死的 ch203 clinepass 同族新 key。

## 变更

| 渠道 | 动作 | 姿态 |
|---|---|---|
| ch173 muyuan-gongyi | PUT models += `deepseek-v4.1-flash`（同名聚合，无映射；21→22 模型） | abilities 新行 **p26/w5**（SQL UPDATE，其余 21 行 p20 未重置——本次 PUT 无 fork 重置 quirk）；mgmt 1.7s 200 |
| ch221 cpa2-clinepass（新建） | models=`cline-pass/deepseek-v4.1-flash,deepseek-v4.1-flash`；mapping `deepseek-v4.1-flash→cline-pass/deepseek-v4.1-flash` | p22/w2 auto_ban=1；mgmt 3.25s 200 |

池终态（default 组，活腿）：ch208 p30 主 / **ch173 p26** / ch15 p25 / **ch221 p22** / ch218 p0。happycoding 主位未动——两新腿为公益/账号池，不当池头；打爆 auto_ban 自动落下一级。

## 验证

- ch173 readback 22 模型、22 abilities 行、新行 (default,1,26,5)；mgmt test 173?model=deepseek-v4.1-flash 200。
- ch221 abilities 2 行 (default,1,22,2)（池名+raw 名各一行，与 longcat unify 先例同型）；mgmt 即「池名→映射→上游」全链路证明 200。
- 对齐门禁 CLEAN。
- 备份：`newapi-before-{muyuan-dsv41f,cpa2}-20261013-*.db`（integrity ok）。

## 待办

- muyuan 上游其余新 id（glm-5.3-flash 若权限正常可解 ch173 glm 隔离旧账、mistral-large-4、voxtral-mini 系）本次未接——用户令仅 DeepSeek。
- ch203 clinepass（旧 key 欠费）保留禁用；cpa2 若与 clinepass 同账户配额，注意薅爆联动。

---

## §5 muyuan 新一波全量 diff（同日晚，用户令"还有哪些新模型没接入"）

重探 muyuan.do 上游现 31 id，与 ch173.models（22）精确 diff = **10 候选**。逐个直连活体测：

| 结果 | 处置 |
|---|---|
| `glm-5.3-flash` 200/3.6s =391（推理型，572字 rc） | **入池**：glm-5.3-flash 池补第 3 活腿（原仅 208 p30/118 p25），muyuan p20 兜底，已定价 0.075/3.333 |
| `mistral-medium-latest` 200/5.8s =391，但间歇 `500 do_request_failed`（mgmt 1成18s/1败） | **入池（抖动标注）**：新池独苗 p20，补 0/0 价 |
| `mistral-small-latest` 200/5.5s =391，间歇 500（直连成、mgmt 双败） | **入池（抖动标注）**：同上 |
| `labs-leanstral-1-5-1` 200 但推理不收敛（600 token 全烧 reasoning=2464字，content 空） | **排除**：静默空答腿，任何池里比缺更糟 |
| `glm-5.2-200k`/`glm-5.3-200k`/`mistral-large-4`/`mistral-large-4-0` 503 `under group auto (distributor)` | **排除**：token 组无权限（历史同症） |
| `voxtral-mini-2602`/`voxtral-mini-latest` 400 `Invalid model` | **排除**：列而不售 |

## §5 关键运维事实

- **PUT 重置 quirk 现形**：本次给 ch173 加 3 模型（22→25）后，deepseek-v4.1-flash 行 **p26→p20 被重置**（早先"未触发"只是巧合，全行本就 p20）。已 SQL 恢复 p26/w5；新 3 行留 p20。**结论：ch173 每次 PUT 后必复核并恢复非默认行。**
- **auto_ban 不会因 500 连坐**：本 fork `AutomaticDisableStatusCodes=401,402,403,502`（smoke 契约钉死），mistral 的 `500 do_request_failed` **不触发渠道禁用** → co-locate 抖动 mistral 不危及 ch173 其余 22 solid 模型。
- **multi-key 未被打平**：ch173 `channel_info.is_multi_key=true size=2 polling`，两次 PUT 传完整多行 key 后仍 size=2（仅 polling_index 复位，无害）。`.scratch/merge_*.txt` 那份带 glm-5.2/200k/voxtral-mini 的 ch173 快照是**历史未授权取证试验**，非现网，未接续。
- **定价**：mistral-medium/small 补 0/0（公益独苗）；glm-5.3-flash 沿用 0.075；避开 merge_dry 警告的"未注册名按 37.5 隐含倍率计费"坑。
- 门禁：`verify_omp_newapi_alignment` CLEAN、`test_omp_routes` 40/40 OK。备份 `newapi-before-muyuan-new3-20261013-112040.db`。
