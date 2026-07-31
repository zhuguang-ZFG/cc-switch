# AtomCode CodingPlan Lite deepseek-v4-flash 接入 NewAPI 聚合池（ch43）（2026-07-31）

把 AtomCode（AtomGit 出品的终端 AI 编程助手）CodingPlan Lite 通道经 `atomcode-open-api` 代理接入 NewAPI（渠道 43），`deepseek-v4-flash` 聚合池增加一路免费额度。

后续 2026-07-31 补充：ch35（cline-free）加入、ch42（DeepSeek 官方直连）加入、ch44（codebuddy）加入。**当前 deepseek-v4-flash 聚合池共七源**（ch15 sensenova / ch35 cline-free / ch37-38 tokenrhythm / ch42 官方 / ch43 atomcode / ch44 codebuddy），完整快照见 `docs/patches/newapi-aggregation-pools-2026-08-01.md`。

## 1. 背景：agentrouter 挂后补量

- agentrouter（anyrouter 同源公益池）上游间歇 503/限流，`all keys failed: HTTP 503`（见 `docs/patches/agentrouter-local-proxy-2026-07-31.md`）。
- 用户要一个独立于现有渠道的 deepseek-v4-flash 补充源。选 AtomCode CodingPlan Lite：免费额度（800 次/5h 滚动窗口，0 成本），官方网关 `api-ai.gitcode.com` 稳定。

## 2. VPS 部署

- AtomCode 5.0.3 二进制：`https://raw.atomgit.com/atomgit_atomcode/atomcode/raw/main/scripts/install.sh | sh`（注意：`atomcode.atomgit.com/install.sh` 返回 HTML 不是安装脚本，正确源在 raw.atomgit.com）。
- Headless 登录：`atomcode login` 在无浏览器环境打印 OAuth URL（`https://acs.atomgit.com/s/<code>`），浏览器授权后 token 落盘 `~/.atomcode/auth.toml`。
  - **token 7 天过期**（expires_in=604799），需定期 `atomcode login` 续期；代理 `/health` 返回 `token_expires_in` 可监控。
  - CodingPlan Lite 激活：`deepseek-v4-flash`（默认）+ `Qwen3-VL-8B-Instruct`（vision）；GLM-5.2 需 Pro 不可用。
- 代理：`atomcode-open-api`（pip 安装，GitHub `baimocn/atomcode-open-api`），`systemd` 托管：

```ini
[Unit]
Description=AtomCode Open API proxy (CodingPlan Lite -> OpenAI-compat)
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/atomcode-open-api -H 0.0.0.0 -p 8899
Restart=on-failure
RestartSec=10
Environment=HOME=/root

[Install]
WantedBy=multi-user.target
```

- **监听 0.0.0.0 而非 127.0.0.1**：NewAPI 在 podman 容器里，经桥 IP `10.88.0.1:8899` 访问宿主代理；VPS 安全组已限制公网端口，8899 不对外。
- 代理自带限流：rate=10/s、capacity=10、queue_size=50（`/health` 可见），过载排队/拒绝由 NewAPI failover 兜底。
- 模型名：代理接受短名 `deepseek-v4-flash` 并映射到上游 `deepseek-ai/DeepSeek-V4-Flash`，NewAPI 侧无需 model_mapping。

## 3. NewAPI 渠道 ch43（sqlite 直写）

沿用既有做法（`POST /api/channel` 在本版本触发 Go nil-pointer panic；先 `podman stop new-api`，`channel_info` 必须 BLOB）：

| 字段 | 值 |
|---|---|
| id | 43 |
| type | 1（OpenAI） |
| name | atomcode-flash |
| key | `any`（代理不校验 key） |
| base_url | `http://10.88.0.1:8899`（podman 桥 IP，不带 /v1） |
| models | `deepseek-v4-flash` |
| group / priority / weight | default / 50 / 10 |
| channel_info | 单 key BLOB（`typeof=blob` 已验证） |

abilities：`('default','deepseek-v4-flash',43, enabled=1, priority=50, weight=10)`。

改前备份：`/opt/new-api/data/backups/one-api.before-atomcode-20260731-<TS>.db`。

> 注意：channels 表列名是 `used_quota`/`test_time`（不是 `used`/`tested_time`）；首次写 SQL 因列名错误失败过一次，abilities 已在首次半执行时写入，二次执行只补 channels 即可（UNIQUE 约束防重）。

## 4. 验证

- 隔离验证（临时禁用 ch15/37/38 的 flash ability，只留 ch43）：
  - `curl http://10.88.0.1:3000/v1/chat/completions -H "Authorization: Bearer <token>" -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"reply ATOMCODE-OK only"}],"max_tokens":256}'` → `finish=stop`、`content='ATOMCODE-OK'`。
  - **坑：max_tokens 必须 ≥256**——deepseek-v4-flash 是推理模型，16 token 全被 reasoning_content 吃掉，content 为空（finish_reason=length 不是失败）。
- 验证后已恢复全部 flash ability，聚合池七源全 enabled（详见总览文档）。

## 5. 使用与注意

- 任何走 NewAPI 的客户端 `model` 填 `deepseek-v4-flash` 即可，权重自动分流；无新客户端配置。
- CodingPlan Lite 为免费额度（800 次/5h），适合补量；不限量时上游 429，NewAPI failover 到 ch15/37/38，不会整体挂。
- 代理进程崩溃自愈（systemd Restart=on-failure）。

### Token 自动续期（已内置）

代理已内置 OAuth2 token 自动刷新，用的是 AtomCode 官方同款端点（`POST https://acs.atomgit.com/oauth/refresh`，client_secret 在 broker 端）:

- 后台每 1 小时检查 `auth.remaining_seconds`，启动时立即检查一次，< 1 天时自动通过 refresh_token 换取新 token
- 刷新后同时更新内存（`gateway.access_token`、`auth` 对象）和磁盘（`~/.atomcode/auth.toml`）
- **无需手动续期**。首次 `atomcode login` 授权后，代理长期运行即可
- 验证：`python3 -c "from atomcode_open_api.auth import load_auth, refresh_access_token; auth=load_auth(); print(refresh_access_token(auth).remaining_display)"` 应返回 `7d 0h`

安全：

- `auth.toml` 权限已收紧为 `0600`（`save_auth` 原子写后 `os.chmod`），含凭据文件不可被其他系统用户读取
- 刷新断点使用 `urllib` HTTPS 直连 `acs.atomgit.com`，不经过代理（避免凭据泄露）

运维：

- 监控：`/health` 的 `token_expires_in` 字段正常值应为 `7d 0h`（或 `6d 23h`）；若接近 `0s` 说明刷新失败，需检查 VPS 到 `acs.atomgit.com` 网络
- 刷新失败时代理继续使用旧 token（7 天内有效），不会立即中断服务
- 强制手动刷新：`systemctl restart atomcode-open-api`（重启后加载新 token）

> 安全：本文档不含 AtomCode access_token、NewAPI admin token、relay token、VPS 密码。
