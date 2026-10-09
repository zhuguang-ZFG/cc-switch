# OMP LSP 三件套 + duckdb 工具装填（2026-09-04）

## 目标

补齐 OMP 会话内的代码智能（LSP）与日志/数据分析工具链。结论先行：**这是装二进制，
不是装插件**——OMP 原生 LSP 自动探测内置约 50 个 server，官方 marketplace 里的
`rust-analyzer-lsp`/`typescript-lsp`/`pyright-lsp` 等插件是给没有原生 LSP 的
Claude Code 补缺的，对 OMP 冗余，勿装。

## LSP 三件套

| 组件 | 安装方式 | 版本 | 二进制位置 |
|---|---|---|---|
| rust-analyzer | `scoop install rust-analyzer` | 2026-08-31 | `~/scoop/shims/rust-analyzer.exe` |
| typescript-language-server | `npm i -g typescript typescript-language-server` | latest | `~/AppData/Roaming/npm/` |
| pyright (+pyright-langserver) | `npm i -g pyright` | latest | 同上 |

机上无 rustup/cargo（`~/.cargo/bin` 仅 cargo-clippy/cargo-fmt/agent-inspect-mcp 残件），
rust-analyzer 走 scoop 单装。

### 关键配置：rootMarkers 扩展

OMP 的 LSP 自动探测是 **cwd-only**（不向上搜父目录）：默认 rust-analyzer 的
rootMarkers=`[Cargo.toml, rust-analyzer.toml]`，而 cc-switch 仓库根没有 `Cargo.toml`
（在 `src-tauri/` 下）→ 仓库根会话 rust-analyzer 永远不启动。新增用户级
`~/.omp/agent/lsp.json`：

```json
{
  "servers": {
    "rust-analyzer": {
      "rootMarkers": ["Cargo.toml", "rust-analyzer.toml", "rust-toolchain.toml"]
    },
    "pyright": {
      "rootMarkers": ["pyproject.toml", "pyrightconfig.json", "setup.py", "setup.cfg",
                       "requirements.txt", "Pipfile", "*.py"]
    }
  }
}
```

- `rust-toolchain.toml` 在 cc-switch 根目录存在，作为工作区标记命中。
- `*.py` 通配（一层、仅 cwd 顶层）：Guardian/ops 目录没有 pyproject， Guardian 目录
  顶层的 `guardian.py` 命中。
- 合并语义：浅合并到内置 defaults（`pi-coding-agent/src/lsp/defaults.json`，**不在
  dist/**）；override 只替换出现的顶层字段，未写的 fileTypes/command 继承默认。
- 配置查找顺序含插件 LSP 配置、`<cwd>/.omp/lsp.*` 等；用户级放 `~/.omp/agent/lsp.json`
  即 active native agent 目录。

## 验证

新 OMP 会话（关键：**运行中的会话持启动时快照**，二进制/配置变更必须开新会话才生效；
本会话 lsp device 报 "No language server configured" 即此因）：

```
src/main.tsx        → typescript-language-server ready, 1 条 hint（platform deprecated, 无害）
src-tauri/main.rs   → rust-analyzer ready, 0 诊断
```

## duckdb

| 项 | 值 |
|---|---|
| CLI | 1.5.5 (Variegata)，`scoop install duckdb`；scoop 建议 vcredist2022 可忽略（`SELECT 1` 实测通过） |
| 插件 | `duckdb-skills@claude-plugins-official` 0.2.4，user scope（`~/.omp/plugins/installed_plugins.json`） |

用途：NewAPI GIN 日志多文件直读（无需逐个 grep 管道）、`ATTACH '~/.new-api-local/new-api.db'
AS napi (READ_ONLY)` 挂 SQLite 做混合分析。Guardian/渠道排障全是这类工作流，现有
技能集不覆盖。

## marketplace 基础设施

- `omp plugin marketplace add anthropics/claude-plugins-official`（291 个插件，兼容
  Claude Code `.claude-plugin/marketplace.json` 目录格式）。
- 撤销：`omp plugin marketplace remove anthropics/claude-plugins-official`。
- 卸载本插件：`omp plugin uninstall duckdb-skills@claude-plugins-official`。
- npm 插件存量（本次未动）：omp-cache-optimizer 1.2.3、omp-model-profile 0.2.4、
  pi-hermes-memory 0.9.6。

## 官方目录筛选结论（291 → 0 个插件装）

| 不装 | 理由 |
|---|---|
| context7 / github | MCP 已挂载同源能力 |
| code-review / pr-review-toolkit | reviewer 代理 + babysit + trellis-check 覆盖 |
| skill-creator | skill-scout / skill_manage / skill-stocktake 覆盖 |
| frontend-design | impeccable 技能覆盖 |
| playwright / browser-use / chrome-devtools-mcp | OMP browser 工具覆盖 |
| desktop-commander | harness 原生 bash/read/write 覆盖（同为 Claude Code 补缺件） |
| commit-commands | OMP 内置 commit 模块 |
| mattpocock-skills | tdd-workflow 覆盖 |
| semgrep / claude-security / aikido | 安全流程走 GitHub Security Advisory，无常驻扫描需求 |
| anima-omp-plugin | 记忆已饱和（hermes + mnemopi + claude_mem 三套） |
| `*-lsp` 系列 | OMP 原生 LSP，装二进制即可 |
| 其余 ~260 | Airtable/Jira/Grafana/Sentry/AWS 等 SaaS 包装器，不在本机栈 |

值得观望（未装）：`omp-web`（长会话 web 监控，v0.4.1 发布次日、单维护者——等项目
长一长）；`@caichengle/omp-feishu-lark`（仅当启用飞书通知路径，现为 Telegram）。

## 回滚清单

```bash
scoop uninstall rust-analyzer duckdb
npm rm -g typescript typescript-language-server pyright
omp plugin uninstall duckdb-skills@claude-plugins-official
omp plugin marketplace remove anthropics/claude-plugins-official
rm ~/.omp/agent/lsp.json   # 本地 git 仓内，git rm 同效
```

本地仓 commit：`~/.omp/agent` `e30fbc5`（config/models/lsp 三件，含本次 lsp.json）。
