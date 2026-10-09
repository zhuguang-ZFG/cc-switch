# OMP/Clash 修复：节点病态 + autoupdate 永久饥饿（2026-09-04）

## 一句话

当晚 NewAPI 错误风暴（ch125 500×13、ch93 EOF+no-channel 刷屏、github 下载失败）的公共根因是
Clash「宝可梦」组选中节点（日本01三网优化丨Vless 2x）病态；另修复 `omp-autoupdate.ps1`
的成熟度选择缺陷（只看 atom[0]，上游日更节奏下永远 age<2d => 永久饥饿），并借脚本自带
管道把 omp.exe 从 18.0.11 接到 18.1.2。

## 证据链

1. **故障实时复现**（21:20 前后）：
   - `curl https://opencode.ai/zen/go/v1/models` → HTTP 000，TCP 0.018s 连上但 TLS 挂死 17s 超时
   - `https://www.sotamodel.net/v1/models` → HTTP 000
   - powershell `curl.exe` 到 github → `schannel: failed to receive handshake`（exit 35）
2. **定位**：mihomo(v1.19.29) controller `127.0.0.1:9097`（secret 见
   `~/AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev/config.yaml`）：
   - 规则 517 条，无 opencode/sotamodel 专属规则 → 兜底 `MATCH -> 宝可梦`
   - `宝可梦` selector 当前选中「日本01三网优化丨Vless 2x」，节点延迟测试垫底/超时
   - 「故障转移」组 now 指向订阅塞的伪信息节点（“剩余流量：59.98 GB”）——机场把信息节点排最前，fallback 组实际不可用
3. **拓扑事实**：系统代理 ProxyEnable=0、TUN 关闭，但系统 DNS 走 mihomo（fake-IP 198.18.x），
   应用连接 fake-IP 被本地路由交给 mihomo → **实际流量仍按 mihomo 规则出站**。
   这解释了“直连也挂”：所谓直连 github/zen 其实进了 mihomo 的病态节点。
4. **切换后（PUT /proxies/宝可梦 {"name":"🇭🇰【亚洲】香港03丨直连"}）**：
   - zen-go：200 / 0.70s（TLS 0.45s）
   - sotamodel：401（可达）
   - github：200 / TLS 0.11s
   - NewAPI 渠道测试 ch125(omen-alpha) 200/1.1s、ch93(omp-sota-claude-opus-5) 200/1.7s
   - 21:25 后 zen/sota/github 相关 ERR = 0（仅剩 ch115 sensova 429）

## autoupdate.ps1 成熟度修复

文件：`~/.omp/omp-autoupdate/omp-autoupdate.ps1`（不在仓库内）
备份：`~/.omp/omp-autoupdate/omp-autoupdate.ps1.bak-20260904-maturityfix`

- 旧逻辑缺陷：`$entries[0]` 只看最新 release；上游日更节奏下 age 恒 <2d → 永久 held back。
  叠加今天 9:40 检查点整机睡眠 → exe 自 Aug 30 一直停在 18.0.11。
- 新逻辑：从新到旧遍历 entries，取第一个发布满 2 天的版本；全部未成熟才 held back。
- 首次手动运行即选出 **18.1.2 (age=2.7d)** 并完成下载+SHA 校验+rename-aside+版本复核：
  `2026-09-04 21:43:26 updated to 18.1.2 OK`
- 收尾的备份轮转报 `无法删除 omp.exe.running-.hold`——旧 exe 镜像被 3 个存活 OMP 进程占用，
  预期行为，进程退出后可删。计划任务次日 9:40 起恢复正常自更新。

## 残留/观察项

- `~/.bun/bin` 现存：omp.exe(18.1.2)、omp.exe.pre-update-.bak(18.0.11 回滚点)、
  omp.exe.running-.hold(被占用，进程退出后删)。已手工清掉 18.0.7/18.0.8 陈旧备份与 bun 更新器残片(约 440MB)。
- bun 全局包 `@oh-my-pi/pi-coding-agent@18.1.10` 仍只是 node_modules 里的 JS 入口，
  与 exe 路径无关；版本以 exe 为准（下次 autoupdate 会追到 ≥2d 的最新）。
- **ch115 sensova**：429 文案变为 `token plan entitlement exhausted`——套餐额度耗尽
  （比 RPM 限速更实），需充值或弃用；当前仍会吃 fallback 往返。
- Clash 配置文件未改动（纯运行时切节点）；Verge 的 cache.db 会记住选择。

## 回滚

- 节点：controller `PUT /proxies/宝可梦 {"name":"🇯🇵【亚洲】日本01三网优化丨Vless 2x"}`
- autoupdate 脚本：恢复 `.bak-20260904-maturityfix`
- omp.exe：`copy omp.exe.pre-update-.bak omp.exe`（回 18.0.11）
