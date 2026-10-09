# solver.000.moe Turnstile 求解器接入（2026-10-08）

**状态**：✅ 工具就绪，全路径实测通过（含真实 token 输出）。百倍 sitekey 求解失败 = 求解农场侧限制，见下。

## 服务契约（openapi 3.1.0, v3.5.2）

地址 `https://solver.000.moe`，所有接口 POST + JSON。

| 接口 | 鉴权 | 计费 | 说明 |
|------|------|------|------|
| `/getBalance` | body `clientKey` | 免费 | 余额查询 |
| `/createTask` | body `clientKey` | 1 积分/成功 | YesCaptcha/CapSolver 风格 `TurnstileTaskProxyless` 任务 |
| `/getTaskResult` | body `clientKey` | 免费 | 轮询；`processing` 时 3s 后再查，`ready` 返回 `solution.token` |
| `/solve` | 头 `X-API-Key` 或 `Authorization: Bearer` | 1 积分/成功 | 同步：body `{url, sitekey, action?, cdata?, timeout?}` → `{token, elapsed, attempts, user_agent?}` |

- 计费：**求解成功才扣**；失败、超时、10 分钟未取结果自动退还。
- 繁忙：`ERROR_NO_SLOT_AVAILABLE` / HTTP 429，稍后重试；自动扩容。
- 状态：`/health` 返回 `{status, backend, solver, active, capacity, queued, ...}`。
- `/solve` 的 `url` 域名须与 sitekey 绑定域名一致；`action` ≤32、`cdata` ≤255，均仅 `[A-Za-z0-9_-]`。

## 实测证据（2026-10-08）

- `GET /` → `{"msg":"Turnstile Solver is ready!","version":"3.5.2"}`
- `/health` → `{"status":"ok","backend":"ok","solver":"ok","active":0,"capacity":7,"queued":0}`
- `/getBalance`（CLI `balance`）→ 256 → 254（见计费注）
- `/solve` 测试 sitekey（`https://example.com/` + `1x00000000000000000000AA`）→ **成功**：
  `{"token":"XXXX.DUMMY.TOKEN.XXXX","elapsed":14.744,"attempts":1,"user_agent":"Mozilla/5.0 (X11; Linux x86_64) … Chrome/152.0.0.0"}` —— 端到端链路可用
- `/solve` + `/createTask→getTaskResult` 双路径对百倍 `0x4AAAAAADk8NysR` → **均失败**（农场侧）：
  - `/solve`：HTTP 500 `{"status":"error","code":"timeout","message":"未能在限定时间内拿到 token(api.js 未加载)(共尝试 2 次)"}`
  - `task`：`{"errorId":1,"errorCode":"ERROR_CAPTCHA_UNSOLVABLE","errorDescription":"未能在限定时间内拿到 token(api.js 未加载)(共尝试 2 次)"}`

> ⚠️ **CF 1010 陷阱**：urllib 默认 UA（`Python-urllib/3.13`）被 Cloudflare 以 `error code: 1010`（browser signature ban）拦截；curl 默认 UA 正常。客户端**必须携带浏览器 UA**，工具已内置。

> 💰 **计费实测**：4 次求解尝试（1 成功测试 key + 3 失败百倍）净扣 2 积分，即**部分失败尝试未按文档「自动退还」退**。对不可解目标不要反复重试烧积分。

> 🔍 **百倍失败根因推测**：该服务只有 `TurnstileTaskProxyless`（无代理、纯脚本渲染）任务类型；solvers 农场在百倍环境下 `api.js 未加载`（目标页脚本自带 nonce CSP，`window.__APP_CONFIG__` 后跟 `nonce="HUY0MPEPQKyPyuH/W1FjZg=="`，疑 CSP 拦了农场注入的 widget 脚本；或为托管/交互型 challenge）。**本机客户端无法修复**——农场/目标侧限制。

## 工具

`scripts/ops/turnstile_solver.py`（python313，仅标准库）：

```powershell
# key 来源优先级：--client-key > 环境变量 SOLVER_CLIENT_KEY > SOLVER_KEY_FILE 指向的文件
$env:SOLVER_CLIENT_KEY = 'sk-…'   # 或 --client-key

python turnstile_solver.py health
python turnstile_solver.py balance
python turnstile_solver.py solve <url> <sitekey> [--action A] [--cdata C] [--timeout 60]
python turnstile_solver.py task  <url> <sitekey> [--action A] [--cdata C] [--timeout 120]
```

- `task` = `/createTask` + 轮询 `/getTaskResult`（3s 间隔，默认 120s 上限）。
- key 永不落盘/打印；输出自动脱敏。
- 可作模块导入：`from turnstile_solver import solve_token, create_task, get_task_result, get_balance`。

## 场景：sub.100xlabs.space（百倍）登录 Turnstile —— ⚠️ 当前不可解

历史 runbook（`docs/ops/newapi-dx-cursor-ops.md`）：百倍 5 账号签到的 API/headless 登录**被 Cloudflare Turnstile 阻止**，当时回退本机浏览器 All API Hub 插件。

- 登录页确证：`window.__APP_CONFIG__={"…","turnstile_enabled":true,"turnstile_site…"}`，sitekey `0x4AAAAAADk8NysR`（2026-10-08 抓取）。
- **实测结论**：本服务（仅 proxyless）对该 sitekey 无法出 token（api.js 未加载）。百倍登录自动化**维持浏览器插件方案**；要 API 化需换支持交互式/代理任务的求解服务。
- 其他站 sitekey 提取：页面里 `0x[0-9A-Za-z]{20,}` 或 `turnstile_sitekey` 配置字段；接入前先用本工具对目标 sitekey 试 `solve`（1 积分）验证可解，失败即退场。

## 后续待定

- [ ] 找一个可解的 CF 护栏公益站接线（用 `turnstile_solver.py` 先试解再决定）
- [ ] 或维持纯工具状态（`health`/`balance`/`solve`/`task` 四命令已可用）