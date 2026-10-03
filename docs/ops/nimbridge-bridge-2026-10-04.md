# nimbridge 中继接入：TLS 桥 + ch149 + flash 档复活（2026-10-04 凌晨）

## 结论

用户提供 nimbridge 连接（IP:3333 + base64 key）。上游 **Let's Encrypt YE2
新型 IP 证书**（SAN=IP、6 天短有效期）本地 NewAPI fork 内嵌根包不认 →
按现有 supervisor 桥模式建 **8791 回环桥**（`~/.kimi-code/proxies/nimbridge-bridge/`），
**ch149 `nimbridge-relay`**（p-10/w1）经桥在册：`glm-5.3-flash`（本地零在营复活）
+ `deepseek-v4-flash`（映射→`deepseek-flash`，budsin 后第二备份腿）。
`kimi-k3` key 无权（403 relay 自报 nimbridge）未接。E2E `FLASH_OK`。

## 桥（生产面新组件）

- 形状：FastAPI+httpx 哑巴转发，回环 127.0.0.1:8791 → `https://207.57.126.219:3333`
  （skip-verify）。**桥本体不持 key**，Authorization 由 ch149 端到端透传；
  明文仅限回环段。上游 MITM 面=IP 锁定单上游 + LE IP 证书 6 天轮换，已记录。
- 排障实录：①httpx 预解压 vs 转发 `content-encoding: gzip` 头 → 客户端解压
  混乱（头过滤）；②上游响应头含 U+2026 非 latin-1 值 → Starlette
  UnicodeEncodeError 500（头消毒，TestClient 抓栈定位）。
- **supervisor 注册**：`~/.omp/guardian/proxies-supervisor.py` 加
  `PROXIES["nimbridge"]`（备份 `.bak-20261004-nimbridge`；**条目必须含
  `"env": {}`，缺了 restart 报 KeyError: 'env'**——本次踩中）。
- 运营事故实录：首启 supervisor 用前台 bat 被 90s 超时杀 → 全桥看护空窗
  （8788/8789/15999-16001/15721）→ `Start-Process -WindowStyle Hidden` 分离
  拉起恢复。**教训：supervisor 重启必须分离式。**

## ch149 与池终态

| 项 | 值 |
|---|---|
| 渠道 | ch149 `nimbridge-relay`（type=1，base `http://127.0.0.1:8791`，备份 `new-api-before-nimbridge-20261004-013752.db`） |
| 模型 | `glm-5.3-flash`（exact）、`deepseek-v4-flash`（mapping→`deepseek-flash`） |
| 姿态 | p-10/w1，与 ch148 同层沉底 |
| 验证 | 双模型 admin 自测 success（2.3s/12.5s 经桥）；flash 归因 ch149 ✓；申报 131072 **无包络预检**（与 intern 上游不同） |
| 定价 | glm-5.3-flash 官方 $0.15/$0.50/缓存$0.03 → 0.075/3.333/0.2（备份 `015207`）；**glm-5.3 CacheRatio 修正 0.25→0.186**（官方缓存价 $0.26 实证，推翻早前"75% off"推断值） |

## 自动化复活观察（与 Guardian/auto_ban 讨论相关）

- **ch15 sensenova-token 被自动复活机制重新启用**（00:34 还 en=0）：其
  deepseek-v4-flash 真实服役（归因实测 88/10）。deepseek 池现为
  ch15(50) 主 + ch148/149(-10) 双备份。
- 连带：复活的 ch15 携带**上游已下架的** `sensenova-6.7-flash-lite` →
  冒烟 404。已从 ch15 models 摘除该死模型 + models.yml 条目 + SMOKE_PROBES
  退役（**pinned-check 编辑，见评审清单**；恢复=三处回捞）。
  注意 SKIP→FAIL 行为差异：ability 行删除后该探针变 503 硬失败而非跳过，
  死模型必须退役探针，不能只摘 ability。

## models.yml（agent repo）

- +`glm-5.3-flash`（nimbridge ch149 via 8791，131072/32768 保守标称，
  申报 131072 实测无预检）。
- −`sensenova-6.7-flash-lite`（商汤下架，死选择子）。

## 待办衔接

- 基元律动（tokenrhythm）36 key 池：端点已验（tokenrhythm.studio，23 模型），
  接入范围待用户裁决（见会话）。
- 门禁：smoke 仅存量 opus-posture FAIL；route gate 40/40。

## 回滚

- ch149：禁双表 / 还原 `new-api-before-nimbridge-20261004-013752.db`。
- 桥：supervisor 移除 nimbridge 条目 + 分离式重启；桥目录删除。
- 定价：还原 `new-api-before-flash-pricing-20261004-015207.db`。
- 死模型退役：smoke/ch15/models.yml 三处按注释回捞。
