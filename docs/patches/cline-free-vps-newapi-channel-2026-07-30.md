# VPS Cline Free 新账户代理 + NewAPI 渠道（2026-07-30）

在阿里云 VPS（new-api 容器同机）装第二个 cline CLI、用**新账户**登录，把 cline free 4 模型代理成 OpenAI 端点，做成 NewAPI 渠道 ch35。与本机 cline 代理（`docs/patches/cline-free-multi-model-proxy-2026-07-30.md`）是**两个独立 cline 账户、两份独立免费额度**。

## 1. 架构

```
NewAPI 容器 (bridge, 10.88.0.125)
   │  渠道 ch35 base_url=http://10.88.0.1:3457
   ▼
cline-proxy (systemd, 宿主机, 绑 10.88.0.1:3457)  ← OpenAI 兼容
   │  带 cline 专属 headers + workos JWT
   ▼
https://api.cline.bot/api/v1/chat/completions  (新账户额度)
```

## 2. VPS 环境

- 阿里云 Linux 3，node v22.22.1（nvm），podman + docker，new-api 容器（bridge 网络，db 挂载 `/opt/new-api/data -> /data`）。
- cline 安装：`npm i -g cline`（约 1min，312 包）。装完 `cline --version` = 3.0.47。

## 3. 新账户登录（无头 VPS 的 device flow）

`cline auth cline` 是 workos OAuth device flow，但 VPS 无 `xdg-open`，cline 试图开浏览器失败后**直接退出**，device flow 不继续轮询。

**解法**：造假 `xdg-open` 骗过它：
```bash
printf '#!/bin/sh\necho "[fake-browser] $1"\nexit 0\n' > /usr/local/bin/xdg-open
chmod +x /usr/local/bin/xdg-open
cline auth cline   # 打印 user_code + https://authkit.cline.bot/device?user_code=XXXX
```
本机浏览器打开该 URL、用**新账户**邮箱授权后，VPS 的 cline 后台进程自动把 refreshToken 写进 `~/.cline/data/settings/providers.json`（`providers.cline.settings.auth.refreshToken`）。日志见 "You are now logged in to cline"。

## 4. proxy 部署（systemd 常驻）

脚本 `/opt/cline-proxy/cline-glm-proxy.mjs`（同本机版，两处改动）：
1. `tryFreePort` 的 powershell 改 Linux：`fuser -k ${port}/tcp 2>/dev/null || true`
2. `HOST` 从 `127.0.0.1` 改 `10.88.0.1`（见 §5 容器网络）

systemd unit `/etc/systemd/system/cline-proxy.service`：
```ini
[Unit]
Description=Cline Free Model Proxy (OpenAI-compatible -> api.cline.bot)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory=/opt/cline-proxy
ExecStart=/root/.nvm/versions/node/v22.22.1/bin/node /opt/cline-proxy/cline-glm-proxy.mjs
Restart=always
RestartSec=5
Environment=PATH=/root/.nvm/versions/node/v22.22.1/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/root
[Install]
WantedBy=multi-user.target
```
`systemctl enable --now cline-proxy`。验证 `curl http://10.88.0.1:3457/v1/health`。

## 5. 关键坑：容器网络（127.0.0.1 不通）

new-api 跑在 **podman bridge 容器**里，容器的 `127.0.0.1` 是容器自己，**连不到宿主机的 proxy**（实测 `podman exec new-api wget http://127.0.0.1:3457` 失败）。

- 容器 IP `10.88.0.125`，网关 `10.88.0.1` = 宿主机 podman 网桥接口（`cni-podman0`）。
- **proxy 必须绑 `10.88.0.1`**（podman 网桥接口），容器才能连；绑 `127.0.0.1` 容器连不上，绑 `0.0.0.0` 会暴露公网（VPS 有公网 IP，proxy 无鉴权，别人能白嫖 cline 额度）。绑 `10.88.0.1` 只有容器网段（10.88.0.0/16）能连，公网/eth0 内网都连不上，安全。
- 渠道 ch35 `base_url = http://10.88.0.1:3457`（不带 `/v1`，NewAPI OpenAI 类自动补）。

## 6. NewAPI 渠道 ch35（sqlite 直写）

`POST /api/channel` 在这个 NewAPI 版本触发 Go nil-pointer panic，用 sqlite 直写（`journal_mode=delete`，**必须先 `podman stop new-api`** 再写，避免锁）：

```sql
-- channel_info 必须是 BLOB（Go 端 value.([]byte) 断言），单 key 模式
INSERT INTO channels (id,type,key,status,name,weight,created_time,base_url,models,"group",priority,auto_ban,channel_info)
VALUES (35,1,'cline-local',1,'cline-free-proxy',10,<now>,'http://10.88.0.1:3457',
  'cline-free/glm-5.2,poolside/laguna-s-2.1:free,deepseek/deepseek-v4-flash,stepfun/step-3.7-flash',
  'default',50,1, <BLOB {"is_multi_key":false,"multi_key_size":0,"multi_key_status_list":{},"multi_key_polling_index":0,"multi_key_mode":"random"}>);
-- abilities（NewAPI 靠它路由，每模型一条）
INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight,tag) VALUES
  ('default','cline-free/glm-5.2',35,1,50,10,''),
  ('default','poolside/laguna-s-2.1:free',35,1,50,10,''),
  ('default','deepseek/deepseek-v4-flash',35,1,50,10,''),
  ('default','stepfun/step-3.7-flash',35,1,50,10,'');
```

注意：
- 这个版本 **type=1 是 OpenAI**（不是 3），type=14 是 Anthropic。
- `channel_info` typeof 必须是 `blob`（用 python `sqlite3.Binary(json.encode())` 写，sqlite3 CLI 直接 INSERT 字符串会存成 TEXT 导致 Go 端断言失败）。
- 改完 `podman start new-api`。改前先备份 `cp one-api.db backups/one-api.before-cline-<ts>.db`。

## 7. 模型价格（必须配，否则 503 "价格未配置"）

NewAPI 要求每个模型在 `options.ModelRatio`/`CompletionRatio` 里有配置（或开 `SelfUseModeEnabled`）。用 admin API 给 4 个 cline 模型加 `ModelRatio=0`/`CompletionRatio=0`（免费，精准不影响其他模型计费）：
```
PUT /api/option/ {key:"ModelRatio", value:<现值JSON + 4个cline模型:0>}
PUT /api/option/ {key:"CompletionRatio", value:<现值JSON + 4个cline模型:0>}
```
（admin API 改 option 立即生效，不用重启容器；需 `Authorization: Bearer <admin-token>` + `New-Api-User: 1`。）

## 8. 验证（NewAPI 公网入口，4 模型全 stop）

```
cline-free/glm-5.2            -> finish=stop 'OK'
poolside/laguna-s-2.1:free    -> finish=stop 'OK'
deepseek/deepseek-v4-flash    -> finish=stop 'OK'
stepfun/step-3.7-flash        -> finish=stop 'OK'
```

## 9. 两份 cline 额度对照

| | 本机 cline 代理 | VPS cline 代理（本篇） |
|---|---|---|
| 账户 | 本机 cline 账户 | **新账户**（独立额度） |
| proxy 位置 | 本机 127.0.0.1:3457 | VPS 10.88.0.1:3457（systemd） |
| 接入方式 | omp/kimi 直连本地 proxy | NewAPI 渠道 ch35（所有走 NewAPI 的客户端可用） |
| 模型 | 4 个 free | 同 4 个 free |

## 10. 排障

| 现象 | 原因 | 修法 |
|---|---|---|
| cline auth 报 xdg-open 退出 | VPS 无头 | 造假 xdg-open（§3） |
| NewAPI `do request failed` | 容器 127.0.0.1 连不到宿主 proxy | proxy 绑 10.88.0.1 + base_url 用 10.88.0.1（§5） |
| `价格未配置` 503 | 模型没在 ModelRatio | admin API 加 ModelRatio/CompletionRatio（§7） |
| `invalid header field value`/Go 断言失败 | channel_info 存成 TEXT | 用 sqlite3.Binary 写 BLOB（§6） |
| sqlite 写 db 锁 | new-api 容器占用 | 先 podman stop（journal_mode=delete） |

> 安全：本文档不含 VPS 密码、cline refreshToken、NewAPI admin token（遵守 AGENTS.md「文档禁止出现完整凭据」）。凭据见本机 `~/.kimi-code/AGENTS.md` 与密码管理器。
