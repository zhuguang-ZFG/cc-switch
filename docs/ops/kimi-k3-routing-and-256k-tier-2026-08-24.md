# Kimi K3 报错定位、256K 档注册与路由重排回滚(2026-08-24)

用户报告"kimi k3 怎么总报错"并指出存在 256K 档模型。排查结论、一次按指示执行
后又被实证推翻的路由重排、以及两条工件纪律教训。

## "总报错"根因:Moonshot 官方上游间歇 429 过载(候选成因)

NewAPI 计费日志侧 k3 完全健康(24h 内 432/432 成功计费、零失败行),但日志只有
type=2(消费)/3(系统)——**计费前的上游拒绝不落盘**,这是结构性盲点。管理探测
直击三渠道得到真值:

| 渠道 | k3 小请求 | ≈68 万 token 大请求 | 定位 |
|---|---|---|---|
| ch33 kimi-official | ⚠️ 单点探针实测 `429 engine overloaded` | ✅ 已验证承载 63.6 万(hutuji 会话实测) | 唯一大上下文来源 |
| ch108 whyyin | ❌ `503 分销商无 Kimi-K3 容量` | 未测(无意义) | 当前对 k3 零贡献 |
| ch110 yjs-free | ✅ | ❌ 504 网关超时 | 免费小请求池 |

因果口径:ch33 上游存在间歇 429 过载(单点管理探针实测),是用户可见报错的
**候选成因之一**;但服务端无失败留痕(当日 432/432 全成功计费、auto_ban 始终
未触发),确切触发频率与是否为主因未实证。OMP 侧另有 maxRetries=3 缓冲,
过载窗口连耗重试后才暴露为用户可见错误。

## 附带发现:hutuji 会话 63.6 万 token 巨型上下文

`--D--Users-hutuji--` 当日会话(带 4 个子代理)以 promptTokens=636,289、
TTFT 79.8s 的形态持续打 k3(22:00-01:28 共 394 次)。这证明 ch33 上游真实
上下文 ≥63.6 万——models.yml 声明的 1M 站得住;同时意味着任何路由变更都要先
回答"新首选渠道能否吃下这个规模"。

## k3-256k 档注册(先探测后注册)

ch33 abilities 里本就启用了独立的 `k3-256k`(ModelRatio=2),但 OMP 从未注册。
relay 探测:`high` 与 `max` 均 ACCEPT,且 **reasoning_tokens 272(max) vs
17(high)**——档位分离清晰,参数真实生效。已注册:

```yaml
- id: k3-256k
  thinking: {mode: effort, efforts: [minimal..max]}
  contextWindow: 262144 / maxTokens: 32768   # 镜像同渠道同上下文档的 kimi-for-coding 形态
```

agent 本地仓 `5f3021a`;39/39 路由门禁通过。**注意:纯可用面扩充,
config.yml 零引用,不参与任何角色/链路。**

## 路由重排尝试与回滚(教训记录)

按用户指示"k3 聚合的先用其他的,再用官方的",将 ch33 priority 50→5
(共享矩阵证明仅影响 k3,最小爆炸半径判断正确)。行为验证随即揭示:

1. ch108 对 k3 无容量(503 分销商空),流量级联跳到 ch110;
2. ch110 在 68 万 token 探针上 504——**无法承载生产会话的 63.6 万上下文**;
3. 若长会话持续撞 400/504,auto_ban 会把免费渠道逐个拉黑,三渠道冗余退化为
   串行雪崩路径。

结论:**NewAPI 按渠道选路、无法按请求大小分流,"非官方优先"在存在大上下文
生产会话时不能在 priority 层安全表达**。已回滚 ch33→p50(readback + relay
归因 ch33 双证),整库备份 `new-api-after-k3-reorder-revert-20260824-020147.db`
(integrity ok)作为回滚后基线留档。

### 工件纪律教训(两条,均已付出代价)

1. **list-endpoint 往返会静默丢字段**(test_model/key 不回传),JSON 快照
   不能作为字段级对照依据——权威来源是 DB 直读 + 时间戳整库备份对比。
   本次 ch33 的 `test_model=''` 经 08-22 整库基线证实为原始状态而非 PUT
   损失,但这个结论只能用 DB 真值得出。
2. **被系统跳过的工具调用不计为已完成**——一轮"备份+integrity+revert"组合
   调用被 pending advisory 跳过后,只有 revert 部分被补跑,备份部分遗漏,
   而交付陈述一度声称备份已存在。由独立核验(目录全貌列表)抓出并当场更正;
   补建的合规工件为 `new-api-after-k3-reorder-revert-20260824-020147.db`
   (110MB,integrity ok)。

## 可落地的分轨替代方案(后续选项)

本轮回滚证明了 priority 层无法按请求大小分流,但模型层可以:利用 k3-256k
有界档与 k3 大上下文档的同脑分轨——**小上下文常规流量指向免费源上的
k3-256k 档,大上下文会话保留官方 ch33 的 k3**。这需要在 OMP 角色/链路层
显式配置(当前 k3-256k 零引用),作为后续省配额改造的候选方案记录在案。

## 待批事项

ch33 是 `kimi-for-coding`/`kimi-for-coding-highspeed`/`k3-256k`/`zg-k3`
四个模型的唯一渠道,`test_model=''`(既有盲区,08-22 基线证实独立于本次变更)
意味着这些独占能力的故障无法被自动探测。建议经 admin API 设
`test_model='k3'`(一条 PUT,模式同 ch61 先例),待批。


## 附:本地 NewAPI CPU 过载保护调整(2026-08-24)

同日早些时候,8 个并发 relay 探针全部被本地 NewAPI 以
`503 system cpu overloaded (threshold: 98%)` 拒绝——保护阈值盯的是**整机**
CPU,而本机是开发机,bun 编译/多代理会话/TGitCache 扫描等突发负载动辄顶满,
NewAPI 自身反而很轻(累计 CPU 秒远低于 webview2/explorer)。单用户本机中继
上,"桌面忙就拒绝请求"比排队更伤。

处置:经 admin API(`PUT /api/option/`)将
`performance_setting.monitor_cpu_threshold` 与 `monitor_memory_threshold`
98→100(guard 等效关闭)。回滚快照
`options-performance-before-20260824-021124.json`(原值 98/98);整库基线见
同日 `new-api-after-k3-reorder-revert-*.db`。

行为验证(同命令闭环):启动 16 个忙循环→采样整机 CPU=100%→relay 探针
`HTTP 200 ACCEPT`→按记录 PID 清杀→复采样 CPU=15%(无孤儿残留)。作为对照,
阈值调整前的实测为:8 个并发 relay 探针在同类突发负载下全部被该保护以 503
拒绝。电源计划已是卓越性能,无需调整。

注意:上游 Moonshot 的 `429 engine overloaded`(ch33)是**服务端**负载,
与本机阈值无关,不可通过本机制消除。

## 附:ch110 摘除 k3 死映射(2026-09-04)

日志排障发现 ch110 `yjs-free` 的 `k3→kimi-k3` 映射上游已死:yjs
distributor 自身返回 `503 No available channel for model kimi-k3 under
group Free`(其池已下架该模型)。ch110 p6 是 k3 首选渠道,导致每个 k3
请求先吃一次必败 503 再重试 ch115/ch33;当 ch115 TPM 429 抖动叠加时,
三路同时失败,`relay error` 透传到 OMP(当日 20:03 ×3;另有 yjs 传输层
EOF ×20 + HTTP/2 PROTOCOL_ERROR ×4)。

处置:API PUT 摘除 ch110 models 中的 `k3`(18→17 个模型)及
model_mapping 的 `"k3":"kimi-k3"` 键;status=1/p6/w5/其余映射
(hy3-free、muse-spark)不动。快照
`new-api-before-ch110-k3-removal-20260904-201304.db`
(155,103,232 B,integrity=ok)。回读:k3 双清零,17 模型;abilities
即时重生成(k3 只剩 ch33=1、ch115=1、ch108=0 预禁)。75s 缓存同步后
验证:ch33 k3 管理探针 200、relay k3 探针 200(路由直达 ch33/ch115,
死跳消除)。

回加条件:yjs 上游恢复 kimi-k3 分销 + 新一轮直连 200 探针。