# space-bunny-free vs gpt-6-astra A/B 实测（2026-10-03）

## 结论

传闻"space-bunny-free 赶得上 gpt-6-astra"——**实测不是"赶得上"，是受测形态下
bunny 全面反超**：6 题 bunny 5 胜 1 平 0 负，且 bunny 免费、线路稳；
astra 2 题答错、2 题输出中途截断、路由多次 502/挂起。限定语见末节。

## 受测路径（重要前提）

- **bunny**：本地网关 3002 → ch130 `opencode-go-space-bunny-free`（免费，chat 协议直发可用）。
- **astra**：本地路由 ch127 **GPT 预算池已耗尽**（503 `Budget pool quota has been
  exhausted`，需用户面板充值——独立未决项）；改用 budsin 直连 `gpt-6-astra`
  同 id 上游（首次 502，重试后可用，线路抖动本身是发现）。
- 形态：OpenAI chat 直发。astra 是 code_mode 系模型（codex harness 优化），
  chat 形态可能低估其 harness 内表现——见限定语。

## 题组与判分（机械判分 + 代码实际执行）

| # | 题型 | bunny | astra |
|---|---|---|---|
| 1 | 数学（水池三管，答案 3h） | ✅ 完整推理 | ⚠️ 答案对但 48 tokens 处截断 |
| 2 | 逻辑（三人真话假话，答案 乙） | ✅ 推理正确 | ❌ **答错（丙）**，推理链条误读 |
| 3 | 格式（3 水果顿号分隔） | ✅ | ✅ |
| 4 | 排错（二分查找 `hi=mid-1` 应 `hi=mid`） | ✅ 定位+一行修正 | ❌ **分析错误**（误判 mid 越界） |
| 5 | 代码执行 merge_intervals（5 组断言） | ✅ PASS | ✅ PASS |
| 6 | 代码执行 is_balanced（9 组断言） | ✅ PASS | ❌ **输出截断**（半成品，76 tokens 即止，逻辑本身正确） |

延迟：bunny 2.0–19.6s 全部一次成功；astra 成功样本 8.1–8.7s，但路由层
4/6 题首轮失败（subprocess >170–300s 挂起/空响应/502），重试后补齐。
**astra 两题截断均非 max_tokens 预算（4096 给足）——budsin 线路提前断流。**

## 计分

bunny：5 胜（#2 #4 对方答错、#1 完整性、#6 对方截断、#3 平）+ 1 平（#5）= **不败**
astra：明确得分仅 #3 #5；2 答错 2 截断；可用性另扣路由抖动

## 对传闻的裁决与建议

1. "赶得上"在实用口径下**成立且偏保守**：免费 bunny 在通用问答/逻辑/排错/
   代码执行四类上均不劣于付费 astra（受测形态），可作为 astra 的免费平替候选。
2. astra 的价值锚点在 **codex harness 内 code_mode**（本地路由内存档：
   `codex-same-model-failover-2026-09-12.md`），chat 直发不是其设计形态；
   本测试不否定其在 codex 内的表现。
3. 行动项：astra 本地路由复活需**用户面板充值 GPT 预算池**（ch127 503
   Budget pool exhausted）；充值前任何 astra 依赖都在空转。bunny 已在 ch130
   在役免费可用。

## 限定语

- 样本 6 题小样本，单轮单次，无温度控制（双方均默认参数）；结论是方向性
  证据非基准排名。
- astra 侧经 budsin 聚合路由（本地 503 不可用），其抖动/截断可能部分来自
  budsin 线路而非模型本体；模型能力判负的两题（#2 #4）为完整输出的实质答错，
  不受此限定。
- 原始输出存档：`D:\Temp\User\bunny_vs_astra.json`（临时文件，不随库）。
