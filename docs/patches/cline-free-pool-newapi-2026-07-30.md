# Cline Free 账号池化(NewAPI ch35 pooled proxy)(2026-07-30)

把 VPS 上的单账号 cline free proxy(`docs/patches/cline-free-vps-newapi-channel-2026-07-30.md`)升级成**多账号池**:proxy 持有一个账号数组,请求按 round-robin 挑号,某账号某模型 429/额度/网络失败就打冷却并自动换下一个号重试。对 NewAPI 完全透明——ch35 的 `base_url`/端口/模型都不变。

## 0. 关键事实

- 旧文档 `cline-free-vps-newapi-channel-2026-07-30.md` §9 声称本机与 VPS 是「两个独立 cline 账户」。**实测为假**:本机与 VPS 原本是**同一个账户**(`barbarhonmamxi20@gmail.com`)。
- cline free 限额是**每模型每日**:实测 `429 INFERENCE_CAP_ERROR: "Daily free limit reached on model zai/glm-5.2. Try again in 16h 59m"`。同一账号 glm-5.2 打满时,`deepseek-v4-flash` / `poolside/laguna-s-2.1:free` / `stepfun/step-3.7-flash` 仍返回 200。
- **2026-07-30 已扩容到 4 个号**:用 `--data-dir` 隔离逐个登录 3 个新号(`17150303974@163.com` / `1171933076@qq.com` / `thaliahernandezyr7f0@alazinst.org`),`accounts.json` pool=4。实测某号 glm-5.2 当日打满时 proxy 自动 failover 到未打满的号,glm 恢复 200(见 §5)。继续加号见 §4。

## 1. 架构

```
NewAPI 容器 (bridge)  --ch35 base_url=http://10.88.0.1:3457-->  cline-pool-proxy (systemd, 宿主)
                                                                   |  accounts.json = [account, ...]
                                                                   |  round-robin + 按(账号,模型)冷却 + failover
                                                                   ▼
                                                        https://api.cline.bot/api/v1/chat/completions
```

- proxy 文件:`/opt/cline-proxy/cline-glm-proxy.mjs`(systemd unit `cline-proxy.service` 未变)。仓库工作副本:`tmp/cline-ref/win/cline-pool-proxy.mjs`(`tmp/` 已 gitignore)。
- 账号池:`/opt/cline-proxy/accounts.json`(chmod 600)= `[{ "email": "...", "refreshToken": "..." }, ...]`。
- accounts.json 缺失/为空时**回退**读 `providers.json` 单账号,零停机兼容。

## 2. 池化逻辑

| 事件 | 处理 |
|---|---|
| 正常请求 | `pickOrder(model)`:从 rr 游标起,只挑「该模型未冷却且未整号冷却」的号;命中即返回,rr 前进 |
| 429(带 `Try again in Xh Ym`) | 解析出精确时长,**只冷该(账号,模型)**;换下一个号 |
| 429(无 hint,瞬时限流) | 兜底冷 90s,只冷该(账号,模型) |
| 402 / 403(额度/权限) | 冷该(账号,模型)30min |
| 401 | 强制 refresh 同号重试 1 次;仍失败则整号冷 30min(`deadUntil`) |
| refresh / 网络失败 | refresh 挂=整号冷 30min;网络错=冷该模型 90s |
| 该模型全部号都冷 | 直接 502 快速失败(省额度),NewAPI 侧自行 failover |

- 冷却按 **(账号, 模型)** 维度(`acct.cd[model]`),因为限额是每模型每日——不能因 glm 打满就把整号的 deepseek 也拉黑。授权失效才整号冷(`acct.deadUntil`)。
- 每账号独立缓存 accessToken + 过期时间,各自 refresh。实测 cline `/auth/refresh` **不轮换** refreshToken,故 accounts.json 里静态 refreshToken 可长期用。
- `ponytail:` 冷却兜底时长是固定启发式;有 hint 时优先用 hint。上限:若限流频繁可改指数退避;若 cline 改成一次性 refreshToken,需在 refresh 后把新 token 持久化回 accounts.json。

## 3. 健康检查

```
curl -s http://10.88.0.1:3457/v1/health | jq
# { "pool": N, "accounts": [{ email, dead, cooldowns:{model:iso}, tokenExpires, ok, fail }], "models":[...] }
```

## 4. 加号(唯一需要人工的步骤)

每个新号要一个**新邮箱** + 浏览器 OAuth,agent 代做不了。VPS 无头,靠已就位的假 `/usr/local/bin/xdg-open`。**用 `--data-dir` 隔离登录**,不覆盖现有登录态:

```bash
# VPS, root:
# 1) 后台起 device flow(隔离到 /tmp/cline-acctN),抓打印出的 URL+code:
nohup cline auth cline --data-dir /tmp/cline-acctN >/tmp/cline-auth.log 2>&1 </dev/null & disown
sleep 8; cat /tmp/cline-auth.log     # -> https://authkit.cline.bot/device?user_code=XXXX-XXXX
# 2) 用【新邮箱】在任意有浏览器的机器打开该 URL,点「允许/Authorize」授权
#    (中途点 Deny/取消 => 日志 "user denied",code 作废,重起 device flow 换新 code)
# 3) 授权成功(日志 "You are now logged in")后收编:
bash /opt/cline-proxy/add-account.sh /tmp/cline-acctN
```

- `--data-dir X` 的登录态落在 **`X/settings/providers.json`**(注意不是 `X/data/settings/`;默认 `~/.cline` 才是 `data/settings/`)。`add-account.sh` 已能自动探测两种布局。
- `add-account.sh <dir>` 从该 providers.json 取 refreshToken,按 email 去重追加进 accounts.json,`chmod 600`,重启并打印新池大小。重复 N 次加 N 个号。

## 5. 验证(2026-07-30)

| 检查 | 结果 |
|---|---|
| proxy 直连 deepseek | `finish=stop content="POOLOK"` [OK] |
| proxy 直连 glm-5.2 | 502 `all 1 tried account(s) failed: 429 ... Daily free limit`,health 里 glm 冷到次日 08:12(精确解析 16h59m),deepseek 不受影响 [OK] 按模型冷却生效 |
| 跨账号 failover | 临时池 `[bogus, real]` 打 deepseek → bogus 整号冷(dead)、real 返回 200;之后恢复真实单账号池 [OK] |
| NewAPI ch35 test | deepseek `success=true 2.01s`、poolside `success=true 1.90s`、glm `success=false`(池 502 干净上抛)[OK] 全链路通 |
| 扩容到多号 | `--data-dir` 隔离逐个登录 3 个新号,pool=4(`barbarhon…`+`17150303974`+`1171933076`+`thalia…`);连打 glm-5.2 → 老号打满被冷却、其余号 round-robin 顶上,每个新号均 `ok≥1`、`content="POOL3OK"` [OK] |
| ch35 glm-5.2 | 单号时 `success=false`(无处 failover);多号后同一模型 **`success=true` 5.90s** [OK];ch35 对池大小透明,base_url/端口/模型不变,加号只加额度不改路径 |

## 6. 排障

| 现象 | 原因 | 修法 |
|---|---|---|
| `all N tried account(s) failed: 429 ... Daily free limit` | 该模型当日所有号都打满 | 加号(§4)或换未打满的模型 |
| health `pool:0` | accounts.json 空且 providers.json 也无 token | 跑 `cline auth cline` + add-account.sh |
| `all N account(s) cooled for <model>` | 该模型所有号在冷却窗口 | 等冷却(health 看 `cooldowns`),或加号 |
| 某号 `dead` 一直不恢复 | refreshToken 失效(被登出/改密) | 重新 `cline auth` 该号再 add-account.sh 覆盖 |

## 7. 回滚

```bash
cd /opt/cline-proxy
ls cline-glm-proxy.mjs.bak-*        # 部署前自动备份的单账号版
cp cline-glm-proxy.mjs.bak-YYYYMMDD-HHMMSS cline-glm-proxy.mjs && systemctl restart cline-proxy
```

> 安全:本文档不含 VPS 密码、cline refreshToken、NewAPI admin token(遵守 AGENTS.md「文档禁止出现完整凭据」)。SSH 用本机 `~/.ssh/lima_deploy_ed25519`(已写入 `~/.ssh/config` 的 `lima` host);此前文档误记「SSH 三台全死」,实为用错默认 key,`lima_deploy` key 密钥登录正常。
