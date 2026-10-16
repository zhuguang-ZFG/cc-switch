# chengapi.icu 灰测 gemini 接入（2026-10-13，ch222）

## 来源与解谜

用户转 L站公益回馈帖：~3亿 token 池，两个灰测号可测 gemini-4，key 直给，URL 是谜题：
「小明、小正、小橙为了诶皮爱在努力，只有小橙成功够到了诶皮爱，可是他被石头绊倒进入了重症监护室。」

解谜链：小明/小正为幌子；**小橙够到诶皮爱** = cheng + api → `chengapi.icu`（ICU=**.icu** TLD，石头/ICU 为 TLD 提示）。搜索路径全灭：`chenggong.icu`（橙功谐音）真实存在但域名过期停放（NS=expired.hichina，DoH+crt.sh+外部 fetch 三面证死）；`anorange.icu` 是博主「小橙子」的 SpringBoot 博客（401 非 NewAPI）——身份佐证后弃。`chengapi.icu` DoH 命中 CF（104.21.84.61），直连 200。

## 实例画像（直连实测）

- NewAPI 实例（`new_api_error` 报文、`/v1/responses` 原生支持，reasoning carrier 标 `cpa-gemini-responses-carrier-v1`）。
- key 作用域仅 1 个 id：`gemini-3.8-flash-high` → 上游自报 **`gemini-3.8-flash-exp-a`**（灰度变体，重思考：111 完成 tokens 里 104 是 reasoning）。
- **`gemini-4` 存在但本 key 无权限**（"token has no access"）——帖主的第二个灰测号才有；换 key 可扩。
- chat 正确性 2/2（17×23=391）。

## 入库 ch222

| 项 | 值 |
|---|---|
| name/base | chengapi-gemini / https://chengapi.icu |
| models | gemini-3.8-flash-high（裸 id，**不并入裸 gemini-3.8-flash 池**——exp-a 思考档独立，且裸池仅 ch220/RPM5） |
| group | default,agent,**any**（any=cc-switch Any 兜底专用组，见下） |
| 姿态 | p10/w1 auto_ban=1；ModelRatio/CompletionRatio 0/0（公益 0 费先例） |

## 关键发现：cc-switch Any 的组语义

15721 首探 503 `under group auto→any (distributor)`。链路真相：
- cc-switch Any provider 用 NewAPI root key（tokens 表无此行，admin 权限）→ NewAPI distributor 按 **`any` 组**找渠道；
- `any` 组是兜底白名单（此前仅 6 行：ch72 claude-opus 系 + ch126 gpt-6-astra）；
- **新模型要经 cc-switch 兜底可达 = 渠道必须进 any 组**（NewAPI 侧一行 group 即改，cc-switch 库零改动，守 ops 约束）。

## 验证

- 管理 test/222 3.6s 200；直连 3002（Any key）200/5.9s；**15721 经 cc-switch 200/3.5s** 全链 content=391。
- 归因 logs 264568/264577/264599/264600 → ch222。
- `verify_omp_newapi_alignment` CLEAN、`test_omp_routes` 40/40 OK。
- 备份 `newapi-before-chengapi-20261013-105853.db`。

## 待办

- OMP **已接**（同日晚，用户令）：models.yml 目录条目 `gemini-3.8-flash-high`（200K/16K，紧跟裸 3.8-flash 后；备份 `.bak-20261013-gemini-high`）。`omp models` 冷载两 gemini 行、`omp bench` 1/1 TTFT 9.4s。**不挂角色**（灰度腿仅显式选择；重思考 out 1.4K 慢腿勿上高频）。mistral-medium/small 有意**不进** OMP 目录（独苗抖动，防路由门禁 flaky）。
- 第二灰测号若到手：gemini-4 权限开 → 追加 id 再验。
- 公益站寿命风险与状态更新：同日 11:34 后上游该灰测 Key 失效返回 `401 Invalid token`，因 `auto_ban=1` 已自动禁用（ch222 `status=2`），并于 `verify_omp_newapi_alignment.py` 加入 `QUARANTINE` 观察，待补新 Key 后恢复。

---

## 快照引用状态（2026-10-16 补记）

本页引用的整库快照已被轮转回收（`backups/` 为 KEEP=7 轮转缓存，非归档；
见 `backup-rotation-lexicographic-bug-2026-10-16.md`）：

- `newapi-before-chengapi-20261013-105853.db` —— **不在** supervisor 轮转 glob（`newapi-*` 无连字符族）内；消失另有原因（手工轮转或未落盘）

回滚请以本页的逐字段步骤为准（快照只是加速器，不是前提）。
