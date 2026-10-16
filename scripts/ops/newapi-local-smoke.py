#!/usr/bin/env python3
"""Local NewAPI smoke check (replaces the retired VPS DX analyzer).

Checks, in order:
  1. NewAPI /api/status reachable (http://127.0.0.1:3002)
  2. Local gateway proxies listening on the Tailscale bind host (8788)
  3. Admin API contracts:
     - required options pinned (auto-enable/disable/retry status codes)
     - disable attribution: unexpected disables (status=3, or status=2 with
       auto_ban=0 not in KNOWN_BROKEN/DEGRADED_ACCEPTED_DISABLED/guardian queue)
     - model isolation, quarantine double-lock (status + weight=0),
       fallback posture, critical-model pool capacity
  3b. multi-key silent degradation via DB channel_info (upstream issue #3537)
  4. Eligible cheap real completions through the gateway (latency sample)

Logs one summary block to .tmp-newapi-dx-ops.log (repo root, same file the
old DX-Ops task used) and exits nonzero when any check fails, so Task
Scheduler surfaces the failure in LastTaskResult.

Run:  python scripts/ops/newapi-local-smoke.py
"""
from __future__ import annotations

import json
import re
import socket
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

NEWAPI_BASE = "http://127.0.0.1:3002"
DEPLOY_DIR = Path("C:/Users/zhugu/.new-api-local")
PROBE_HOST = "100.83.32.95"  # local proxies bind the Tailscale IP (secrets.json)
PROXY_PORTS: dict[str, tuple[str, int]] = {
    # converter:8787 (codebuddy/WorkBuddy) removed 2026-08-12 — upstream streaming
    # endpoint 11140 unavailable; do not re-add unless the service is restored.
    "agentrouter": (PROBE_HOST, 8788),
    # anyrouter binds loopback only (OMP slow chain + NewAPI ch72)
    "anyrouter": ("127.0.0.1", 8789),
    "codex-relay": ("127.0.0.1", 15999),
    "sharedchat-codex-relay": ("127.0.0.1", 16000),
    # mistral-conversations-relay: OpenAI -> Mistral /v1/conversations (glm-5-2)
    "mistral-relay": ("127.0.0.1", 16001),
}
SMOKE_PROBES: tuple[tuple[str, str], ...] = (
    # sensenova-6.7-flash-lite 2026-10-04 移出：商汤上游下架该模型（ch15 admin
    # 自测 404 model is not found），已从 ch15 models 摘除——无载体可探测。
    # 上游恢复时回捞本行 + ch15 models + models.yml 条目。
    # muse-spark-1.2-contributor 于 2026-08-24 移出：上游已收回+地区封锁
    # （403 not available in your country），ch48 属 KNOWN_BROKEN。
)

# Channels whose auto-disabled state is currently intentional. Channel 2 has
# no upstream model; channels 62-65 fail production-shaped pre-consumption.
# Channel 74 is held out until its shared upstream quota recovers and a real
# relay + aggregate smoke passes.
KNOWN_BROKEN_CHANNELS: set[int] = {2, 20, 39, 48, 62, 63, 64, 65, 70, 71, 73, 74, 78, 99, 110, 112}  # 78 deleted from DB 2026-09-13 but kept in quarantine set for Guardian exclusion parity（110/112 同理：yjs 上游账号封禁 2026-09-13 入 Guardian 排除集，渠道现已从 DB 删除，保留占位防 ID 复用并与 Guardian 对齐）  # 9/18 于 2026-08-24 移出：linxi 同账号余额恢复（管理探测双双 200），重新入池参与 opus-5 负载均衡；20: fengwind gpt-5.6-sol 故障路由，08-05 起禁用（sol 全局清除决策），08-10 补双锁；39/78: ai.168661 账号侧死 key 恢复点；48: opencode-go-muse RegionError 振荡（2026-08-21 复活即超时再禁用），付费 muse 上游已收回，无复活价值；57: gorouter 余额不足；70/71: 上游真死（2026-08-08 实测，71 已从 NewAPI 删除、保留占位防 ID 复用），与 Guardian 排除集一致；73/74: relay 渠道上游 405 禁用中；75: tabitoken 多 key 拆分后保留的禁用 tombstone（2026-08-20，单 key 欠费拖垮整渠道，拆为 ch97/98/99）；97/99: tabitoken key#1/key#3 余额耗尽（2026-08-20 16:25 实测 403 预扣费失败，$0.21/$0.61 < $0.8），充值前保持禁用；98: tabitoken-2 key#2 欠费 $0.22，充值前保持禁用（小探针假活，见 runbook docs/ops/tabitoken-split-single-key-2026-08-20.md）；57/75/97/98 同日移出：网关自带 /test 实测 claude-opus-5 通过，转 p50 备份次档

# opus-5 备份档（2026-08-24 起）：主池 3/9/18（p52/p52/p50）之下的一层，
# 只许低优先级存在——启用状态下 priority 越过 MAX_PRIORITY（进入主池档）即违规。
# 禁用被容忍：这些是历史波动渠道，上下由 Guardian/自动封禁驱动，抖动不算漂移。
BACKUP_CHANNEL_POSTURES: dict[int, dict[str, int]] = {
    57: {"max_priority": 50, "max_weight": 5},   # gorouter
    75: {"max_priority": 50, "max_weight": 5},   # tabitoken
    86: {"max_priority": 50, "max_weight": 13},  # agentrouter-claude（TTFT ~25s，垫底备份）
    94: {"max_priority": 50, "max_weight": 8},   # justwoker-opus-1
    95: {"max_priority": 50, "max_weight": 8},   # justwoker-opus-2
    97: {"max_priority": 50, "max_weight": 5},   # tabitoken-1
    98: {"max_priority": 50, "max_weight": 5},   # tabitoken-2
    123: {"max_priority": 0, "max_weight": 1},   # zzzcoding（zz_gate.py 门控；p0 兜底层，2026-08-30 自 p60 降级：空窗 p60 实测泄漏 500）
    # 2026-10-09 新增渠道契约（备份/免费层，priority 不得超过上限）
    173: {"max_priority": 20, "max_weight": 1},  # muyuan-gongyi（mistral/glm/qwen 系）
    174: {"max_priority": -10, "max_weight": 1}, # grok-heavy-indevs（gpt-5.6-luna 主档）
    175: {"max_priority": -20, "max_weight": 1}, # hubway（gpt-6-sol 等 p-20 备份）
    # 2026-10-16 用户指令「zen 做主力」：ch178 对 glm-5.3-flash 升为主腿（p30 > ch118 的
    # 25），契约同步放宽到 30（裁定与证据见 docs/ops/zen-free-glm53flash-2026-10-16.md）；
    # 免费层仍不得越过其他名路由的主池档，weight 上限 5 保留。
    178: {"max_priority": 30, "max_weight": 5},  # zen-free-bridge（免费层；glm-5.3-flash 主腿特批 2026-10-16）
    181: {"max_priority": 0, "max_weight": 5},   # jev-systemone-bridge
    182: {"max_priority": 20, "max_weight": 5},  # asvla-gpt-6-astra
    184: {"max_priority": 0, "max_weight": 2},   # opencode-go-step-5-preview（限时免费）
    185: {"max_priority": 30, "max_weight": 1},  # 0v0（glm-4.5-air/glm-4.6v）
    186: {"max_priority": -20, "max_weight": 1}, # tierflow-qwen38-flash（qwen3.8-flash 备份）
    # 2026-10-11 qgyz13（ch209）：供应商自述"纯 glm-5.3 包装映射、速度随机"，按操作者
    # 裁定只声明 glm-5.3，且钉死在 p20 兜底档——低于 ch203/ch208 的 p30。它升档即违规：
    # 未经证实的上游不能挤掉既有 glm-5.3 容量。
    209: {"max_priority": 20, "max_weight": 1},  # qgyz13（glm-5.3 兜底腿）
    # 2026-10-11 ciallo（ch211）：仅声明 /v1/models 真实返回的 deepseek-v4.1-flash 一个名。
    # 钉死 p20/w1 有两个理由：一是它必须低于 ch203/ch208（p30）与 ch15（p25）才算兜底；
    # 二是上游自述"小鸡不是很好、并发限 15"，且它自己的 NewAPI 带 CPU 甩负载
    # （首次管理探测即 503 system cpu overloaded 97.2%/threshold 90%）——weight 抬高就是在
    # 拿生产流量去撞别人的共享小机器。
    211: {"max_priority": 20, "max_weight": 1},  # ciallo（deepseek-v4.1-flash 兜底腿）
}

# Model isolation is channel-specific. AgentRouter (ch45) is GLM-only as of
# 2026-09 (Claude on AnyRouter ch72 since 2026-08-14; Sol selectors retired from
# ch45). CodeBuddy (ch44) keeps its Sol exclusion contract.
CHANNEL_MODEL_EXCLUSIONS: dict[int, set[str]] = {
    44: {"gpt-5.6-sol", "zg-wb-gpt-5.6-sol"},
}

# Live aggregate fallback contracts. These channels must stay enabled but below
# the primary pool; model eligibility remains governed separately above.
FALLBACK_CHANNEL_POSTURES: dict[int, dict[str, int]] = {
    # ch45 removed: deleted from live DB (was agentrouter fallback, gone as of 2026-10-09)
    72: {"priority": 40, "max_weight": 5},
}

# Keep AnyRouter Claude recovery independent from its overloaded Responses/Sol
# surface. NewAPI uses test_model (or the first model when unset) for channel
# recovery, so mixing Sol first previously kept healthy Claude capacity disabled.
ANYROUTER_CHANNEL_ID = 72
ANYROUTER_TEST_MODEL = "claude-opus-5"
ANYROUTER_CLAUDE_MODELS = (
    "claude-opus-5",
    "claude-opus-4-8",
    "zg-claude-opus-5",
    "zg-agent-claude-opus-5",
    "zg-agent-claude-opus-4-8",
)
ANYROUTER_CLAUDE_MAPPING = {
    "zg-claude-opus-5": "claude-opus-5",
    "zg-agent-claude-opus-5": "claude-opus-5",
    "zg-agent-claude-opus-4-8": "claude-opus-4-8",
}
JIANZHILE_SOL_CHANNEL_ID = 91
JIANZHILE_SOL_BACKUP_CONTRACT: dict[str, object] = {
    "name": "jianzhile-gpt-5.6-sol",
    "type": 1,
    "status": 1,
    "auto_ban": 1,
    "base_url": "https://jianzhile.vip",
    "priority": 55,
    "weight": 5,
    "test_model": "jianzhile-codex-gpt-5.6-sol",
}
ZZZCODING_SOL_CHANNEL_ID = 92
ZZZCODING_SOL_PRIMARY_CONTRACT: dict[str, object] = {
    "name": "zzzcoding-gpt-5.6-sol",
    "type": 1,
    "status": 1,
    "auto_ban": 1,
    "base_url": "https://api.zzzcoding.org",
    "priority": 60,
    "weight": 15,
    "test_model": "zzzcoding-codex-gpt-5.6-sol",
}
MUYUAN_SOL_CHANNEL_ID = 83
MUYUAN_SOL_FALLBACK_CONTRACT: dict[str, object] = {
    "name": "muyuan-sol",
    "type": 1,
    "status": 1,
    "auto_ban": 1,
    "base_url": "https://muyuan.do",
    "priority": 50,
    "weight": 5,
    "test_model": "gpt-5.6-sol",
    "models": (
        "gpt-5.6-sol",
        "zg-gpt-5.6-sol",
        "zg-agent-gpt-5.6-sol",
    ),
    "mapping": {
        "zg-gpt-5.6-sol": "gpt-5.6-sol",
        "zg-agent-gpt-5.6-sol": "gpt-5.6-sol",
    },
    "header_override": {"User-Agent": "codex_cli_rs/0.42.0"},
}
TEAMOROUTER_CHANNEL_ID = 84
# The free-tier credential exhausted its quota and the channel was retired on
# 2026-08-17. Preserve the shape contract for a future re-import, but do not
# require a dead credential row to remain in the live database.
TEAMOROUTER_RETIRED = True
TEAMOROUTER_FREE_CONTRACT: dict[str, object] = {
    "name": "teamorouter-deepseek-free",
    "type": 1,
    "status": 1,
    "auto_ban": 1,
    "base_url": "https://api.teamorouter.com",
    "priority": 40,
    "weight": 5,
    "test_model": "deepseek-v4-flash",
    "models": ("deepseek-v4-flash", "deepseek-v4-pro"),
    "mapping": {
        "deepseek-v4-flash": "deepseek-v4-flash-free",
        "deepseek-v4-pro": "deepseek-v4-pro-free",
    },
}



# ai.168661 uses one key per model family. Keep those credentials in separate
# channels so a revoked family key cannot silently poison unrelated models.
# NewAPI's OpenAI channel appends /v1 itself, so base_url must remain the host
# root even though direct upstream probes use https://ai.168661.xyz/v1.
# ch78 ai-168661-deepseek-0731 was deleted from the live DB (absent as of
# 2026-09-13 smoke); do not require a missing row. Re-add a contract only if
# the channel is re-imported. ch39 remains the quarantined Grok recovery point.
AI168661_CHANNEL_CONTRACTS: dict[int, dict[str, object]] = {
    39: {
        "name": "ai-168661-grok",
        "models": (
            "grok-4.20-0309-non-reasoning",
            "grok-4.20-0309-reasoning",
            "grok-4.20-multi-agent-0309",
            "grok-4.3",
            "grok-4.5",
            "grok-4.6",
            "grok-build-0.1",
            "grok-chat-fast",
            "grok-composer-2.5-fast",
            "zg-grok-4.5",
        ),
        "mapping": {"zg-grok-4.5": "grok-4.5"},
        "test_model": "grok-4.5",
        "priority": 55,
        "weight": 0,
        # The temporary 2026-08-16 revival regressed to INVALID_API_KEY.
        # Keep the recovery point quarantined and do not expose Grok in OMP.
        "status": 2,
    },
}

# Channels disabled by local automation due to upstream degradation (NOT config
# breakage). Distinct from KNOWN_BROKEN: these are expected to auto-recover via
# AutomaticEnableChannelEnabled once their scheduled channel test passes again,
# so neither their disable nor their future re-enable is a violation.
# Attribution: ch3 disabled 2026-08-10 12:04 (baibei upstream 502 for hours,
# Guardian had already degraded its weight 24→12 at 09:06); ch45 disabled 22:05
# (agentrouter upstream 429/503 flapping; channel carries auto_ban=1); ch72
# disabled 2026-08-09 00:10 by Guardian. After the Claude-only split, the
# claude-opus-5 recovery probe still returns upstream 429; Guardian keeps
# retrying with bounded backoff and re-enable remains automatic on recovery.
DEGRADED_ACCEPTED_DISABLED: dict[int, str] = {
    # ch45 removed: deleted from live DB (was agentrouter fallback, gone as of 2026-10-09)
    72: "anyrouter Claude upstream 429; disabled 2026-08-09 00:10 by Guardian",
    123: "zzzcoding claude pool window; auto-gated by zz_gate.py (status=2 while pool empty, auto re-enable on window)",
    # 2026-10-15 sweep attribution: manual parks WITHOUT an auto path; the
    # re-enable condition is named in each reason (no AutomaticEnableChannel*
    # wiring applies -- Guardian only imports status=3 && auto_ban=1).
    57: "gorouter balance exhausted; status=2 kept since 2026-08-20 with browser-UA header_override pre-staged for Cloudflare 1010 (ops README:229) -- re-enable when the upstream balance returns",
    68: "agnes-com-haiku upstream dead (admin test 2026-10-15: do request failed); its models are dark in OMP quarantine -- re-enable on upstream recovery",
    69: "agnes-cn-haiku account restricted (admin test 2026-10-15: 403 anomalous-activity); same quarantine as ch68 -- re-enable on account unblock",
}

# opus-5 主池正向姿态（2026-08-24，用户指示启用百倍+林夕负载均衡）：三渠道
# 必须保持启用且权重不低于下限；Guardian 按比例再平衡只能在其上漂移，跌破
# 下限即违规。ch3 于 2026-08-24 自 DEGRADED_ACCEPTED_DISABLED 移出——上游
# 502 已愈（当日 437K quota 成功计费），禁用-接受契约过期。
PRIMARY_CHANNEL_POSTURES: dict[int, dict[str, int]] = {
    3: {"priority": 52, "min_weight": 20},
    # 2026-10-10: linxi ch9 上游账户池耗尽（403 Insufficient account balance /
    # All available accounts exhausted），channels+abilities 双降至 49，把 opus
    # 头名让给 baibei ch3 与 justwoker ch95（402 池四腿另降 48，见当日 runbook
    # §10）。余额回血后恢复=两处回 52 并还原本常量。
    9: {"priority": 49, "min_weight": 16},
    18: {"priority": 50, "min_weight": 6},
}

# Critical models that must never lose their last enabled channel. Value is the
# minimum number of enabled (status=1) channels serving the model; 0 enabled =
# hard FAIL ("503 No available channel" state detected before traffic hits it).
# Current capacity is reported in the check detail either way.
# 2026-08-20：min=1 在塌缩到单渠道（ch86 裸奔）时仍是绿的， opus 双模型提到 2，
# 塌缩到单渠道即 FAIL（当前 justwoker ch94/95 + ch86 兜底，容量 3）。
# 2026-10-11 门禁口径改造：配置容量 + 实测容量双轴，两轴分别都不得少于 min。
# 只有配置行证明不了上游能回答——docs/ops/any-agent-claude-gpt-restore-2026-10-10.md:37
# 那轮记录 "ALL OK: pool capacity claude-opus-5 enabled=7 ids=[3,9,18,86,134,135,136]"，
# 7 条配置全绿，实际只有 0 条能回答。配置轴继续红（塌缩即失败），实测轴补抓幻影容量。
MIN_ENABLED_CRITICAL_MODELS: dict[str, int] = {
    "claude-opus-5": 2,
    # 2026-10-03 用户裁决：opus-4-8 池暂时去除（在营 0 渠道：agentrouter
    # 账户级 402 / anyrouter 关窗 / 网关池空）。
    # 2026-10-11 按该条记录的恢复方式回捞（"恢复=回本行 +"claude-opus-4-8": 2"）：
    # 实测 6h 窗口 ch94=143 / ch95=100 条真实 agent 成功行（prompt 6.5k~171k），
    # 且 OMP slow 角色已由用户裁决改指本模型，故按关键模型钉 min=2。
    "claude-opus-4-8": 2,
}

MEASURED_CAPACITY_WINDOW_SECONDS = 6 * 3600
# 探针不算容量（"小探针假活"，见 docs/ops/tabitoken-split-single-key-2026-08-20.md）：
# 管理侧 /api/channel/test 与运维短 prompt 探针同样写 type=2，但一条 2~14 token 的
# "Reply with exactly OK" 只证明上游肯接小请求，不证明它能扛 OMP 的真实上下文。
# 下界取 1000，与 guardian.py:134 的空轮口径一致。
MEASURED_CAPACITY_MIN_PROMPT_TOKENS = 1000

NEWAPI_DB = DEPLOY_DIR / "new-api.db"

REQUIRED_OPTIONS: dict[str, str] = {
    "AutomaticEnableChannelEnabled": "true",
    # Guardian owns disable/recovery locally; keep NewAPI's built-in scheduled
    # disable switch explicit instead of the ambiguous literal `<nil>`.
    "AutomaticDisableChannelEnabled": "false",
    "channel_affinity_setting.enabled": "true",
    "RetryTimes": "1",
    # 08-03 防放大策略固化（docs/ops/omp-model-config-review-2026-08-03.md）：
    # 403/401/402/502 触发自动禁用；403 余额错误必穿透。
    # 上游参考：QuantumNous/new-api#1457/#1609 —— auto-disable 依赖状态码/关键词
    # 匹配，社区多例失效报告，故本 fork 不得依赖 auto_ban，靠本契约钉死选项防漂移。
    # 08-11 去除 429：RPM 耗尽的同渠道重试零收益且放大拥塞（实测 173 请求×4
    # 重试全失败），429 透传交客户端退避+回退链；重试仅覆盖瞬时类状态码。
    "AutomaticDisableStatusCodes": "401,402,403,502",
    "AutomaticRetryStatusCodes": "408,500-503",
}

# Each sticky-routing rule must cover both the canonical model id and the
# zg-* aliases exposed to OMP/Cursor. Missing alias coverage silently disables
# affinity before model_mapping runs, reducing prompt-cache locality.
AFFINITY_REQUIRED_MODELS: dict[str, tuple[str, ...]] = {
    "codex cli trace": ("gpt-5.6-sol", "zg-gpt-5.6-sol"),
    "glm trace": ("glm-5.2", "zg-glm-5.2"),
    "grok trace": ("grok-4.5", "zg-grok-4.5"),
    "deepseek trace": ("deepseek-v4-pro", "zg-deepseek-v4-pro"),
    "longcat trace": ("LongCat-2.0", "zg-longcat-2.0"),
    "qwen trace": ("qwen3.8-max", "zg-qwen3.8-max"),
    "claude trace": ("claude-opus-5", "zg-claude-opus-5"),
}

# NewAPI channel PUT rebuilds abilities and can reset model-level routing
# posture. These rows are the deliberate pool/diagnostic selectors.
CRITICAL_ABILITY_POSTURES: dict[tuple[int, str], tuple[int, int]] = {
    # (48, "muse-spark-1.2-contributor") retired 2026-08-24.
    # ch45 agentrouter is GLM-only as of 2026-09 (sol abilities removed); do not
    # pin sol selectors there. ch92 zzzcoding now carries gpt-6-astra only and is
    # intentionally disabled — drop its sol ability pins (2026-09-13 false-red fix).
    (83, "gpt-5.6-sol"): (50, 5),
    (83, "zg-gpt-5.6-sol"): (50, 5),
    (83, "zg-agent-gpt-5.6-sol"): (50, 5),
    (91, "gpt-5.6-sol"): (55, 5),
    (91, "zg-gpt-5.6-sol"): (55, 5),
    (91, "zg-agent-gpt-5.6-sol"): (55, 5),
}


def _auto_ban_enabled(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true")


def disabled_critical_ability_ids(
    channel_rows: list[tuple[int, int, object]], guardian_ids: set[int]
) -> set[int]:
    """Return disabled critical channels with an automation owner."""
    return {
        channel_id
        for channel_id, status, auto_ban in channel_rows
        if status != 1
        and (_auto_ban_enabled(auto_ban) or channel_id in guardian_ids)
    }


def option_policy_violations(options: object) -> list[str]:
    """Return required NewAPI options that are missing or have drifted."""
    if not isinstance(options, list):
        return [f"{key}=missing" for key in REQUIRED_OPTIONS]
    by_key = {
        item.get("key"): item.get("value")
        for item in options
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    return [
        f"{key}={by_key.get(key, 'missing')}"
        for key, expected in REQUIRED_OPTIONS.items()
        if str(by_key.get(key, "missing")).strip().lower() != expected
    ]

# 用户指示钉死机制：此集合内的亲和规则必须保持 enabled=false。
# 2026-08-24 晚"林夕百倍不用渠道亲和"曾钉住 claude trace；深夜因长会话
# prompt cache 提速决策恢复（用户拍板）。再次翻转时同步清内存缓存
# （DELETE /api/option/channel_affinity_cache?all=true），否则存量钉扎续命。
AFFINITY_DISABLED_RULES: frozenset[str] = frozenset()


def affinity_rule_violations(options: object) -> list[str]:
    """Return sticky-routing rules that miss canonical/zg-* ids or user-disabled
    rules (AFFINITY_DISABLED_RULES) that were re-enabled."""
    if not isinstance(options, list):
        return ["channel_affinity_setting.rules=missing"]
    raw = next(
        (
            item.get("value")
            for item in options
            if isinstance(item, dict)
            and item.get("key") == "channel_affinity_setting.rules"
        ),
        None,
    )
    try:
        rules = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return ["channel_affinity_setting.rules=invalid-json"]
    if not isinstance(rules, list):
        return ["channel_affinity_setting.rules=invalid-shape"]

    by_name = {
        rule.get("name"): rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("name"), str)
    }
    violations: list[str] = []
    for name, required_models in AFFINITY_REQUIRED_MODELS.items():
        rule = by_name.get(name)
        if rule is None:
            violations.append(f"{name}=missing")
            continue
        patterns = rule.get("model_regex")
        if isinstance(patterns, str):
            patterns = [patterns]
        if not isinstance(patterns, list) or not all(
            isinstance(pattern, str) for pattern in patterns
        ):
            violations.append(f"{name}=invalid-model-regex")
            continue
        try:
            compiled = [re.compile(pattern) for pattern in patterns]
        except re.error:
            violations.append(f"{name}=invalid-model-regex")
            continue
        missing = [
            model
            for model in required_models
            if not any(pattern.search(model) for pattern in compiled)
        ]
        if missing:
            violations.append(f"{name}=missing:{','.join(missing)}")
    for name in AFFINITY_DISABLED_RULES:
        rule = by_name.get(name)
        if rule is None:
            continue  # 规则被整体删除也算达成指示
        if rule.get("enabled", True):
            violations.append(f"{name}=must-stay-disabled")
    return violations


def critical_ability_posture_violations(
    rows: object, disabled_channel_ids: set[int] | None = None
) -> list[str]:
    """Validate critical ability rows after channel model edits."""
    if not isinstance(rows, list):
        return ["abilities=invalid-shape"]
    actual: dict[tuple[int, str], list[tuple[int, int, int]]] = {}
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 5:
            return ["abilities=invalid-row"]
        channel_id, model, enabled, priority, weight = row
        if isinstance(channel_id, int) and isinstance(model, str):
            actual.setdefault((channel_id, model), []).append(
                (enabled, priority, weight)
            )

    disabled_channel_ids = disabled_channel_ids or set()
    violations: list[str] = []
    for key, expected in CRITICAL_ABILITY_POSTURES.items():
        channel_id, model = key
        values = actual.get(key)
        if not values:
            violations.append(f"{channel_id}:{model}=missing")
            continue
        expected_priority, expected_weight = expected
        expected_enabled = 0 if channel_id in disabled_channel_ids else 1
        drifted = [
            value
            for value in values
            if value != (expected_enabled, expected_priority, expected_weight)
        ]
        if drifted:
            violations.append(
                f"{channel_id}:{model}=expected:enabled={expected_enabled},priority="
                f"{expected_priority},weight={expected_weight};actual={drifted}"
            )
    return violations


def ability_posture_violations() -> list[str]:
    """Read critical abilities from NewAPI SQLite without exposing channels."""
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return [f"db-open-failed={e}"]
    try:
        channel_ids = sorted({channel_id for channel_id, _ in CRITICAL_ABILITY_POSTURES})
        placeholders = ",".join("?" for _ in channel_ids)
        rows = list(
            con.execute(
                "SELECT channel_id, model, enabled, priority, weight "
                f"FROM abilities WHERE channel_id IN ({placeholders})",
                channel_ids,
            )
        )
        channel_rows = list(
            con.execute(
                "SELECT id, status, auto_ban FROM channels "
                f"WHERE id IN ({placeholders})",
                channel_ids,
            )
        )
        disabled_channel_ids = disabled_critical_ability_ids(
            channel_rows, guardian_disabled_ids()
        )
        return critical_ability_posture_violations(rows, disabled_channel_ids)
    except sqlite3.Error as e:
        return [f"db-read-failed={e}"]
    finally:
        con.close()


def muyuan_sol_fallback_violations(channels: list[dict]) -> list[str]:
    """Keep muyuan.do at its bounded recovery tier for every Sol selector.

    An auto_ban disable (upstream outage) is accepted like other degraded
    postures; priority/weight are still enforced so the channel re-enters as
    its recovery tier after Guardian's recovery loop.
    """
    channel = next(
        (item for item in channels if item.get("id") == MUYUAN_SOL_CHANNEL_ID),
        None,
    )
    if channel is None:
        return [f"{MUYUAN_SOL_CHANNEL_ID}:missing"]

    expected = MUYUAN_SOL_FALLBACK_CONTRACT
    reasons: list[str] = []
    degraded = channel.get("status") != 1 and channel.get("auto_ban") in (1, True)
    for field in ("name", "type", "auto_ban", "base_url",
                  "priority", "weight", "test_model"):
        if channel.get(field) != expected[field]:
            reasons.append(f"{field}={channel.get(field)}")
    if channel.get("status") != expected["status"] and not degraded:
        reasons.append(f"status={channel.get('status')}")

    models = {
        model.strip()
        for model in str(channel.get("models") or "").split(",")
        if model.strip()
    }
    expected_models = set(expected["models"])
    if models != expected_models:
        reasons.append(
            "models="
            f"missing:{sorted(expected_models - models)},"
            f"extra:{sorted(models - expected_models)}"
        )

    for field, expected_value in (
        ("model_mapping", expected["mapping"]),
        ("header_override", expected["header_override"]),
    ):
        try:
            actual = json.loads(str(channel.get(field) or "{}"))
        except (TypeError, ValueError):
            actual = None
        if actual != expected_value:
            reasons.append(f"{field}=drifted")

    return (
        [f"{MUYUAN_SOL_CHANNEL_ID}:{channel.get('name', '')}=" + ",".join(reasons)]
        if reasons else []
    )


def sol_primary_posture_violations(channels: list[dict]) -> list[str]:
    """Keep zzzcoding primary and both direct/recovery channels below it."""
    by_id = {item.get("id"): item for item in channels}
    violations: list[str] = []
    for channel_id, expected in (
        (JIANZHILE_SOL_CHANNEL_ID, JIANZHILE_SOL_BACKUP_CONTRACT),
        (ZZZCODING_SOL_CHANNEL_ID, ZZZCODING_SOL_PRIMARY_CONTRACT),
    ):
        channel = by_id.get(channel_id)
        if channel is None:
            violations.append(f"{channel_id}:missing")
            continue
        reasons: list[str] = []
        degraded = (
            channel.get("status") != 1
            and channel.get("auto_ban") in (1, True)
        )
        for field in (
            "name", "type", "auto_ban", "base_url", "priority", "weight",
            "test_model",
        ):
            if channel.get(field) != expected[field]:
                reasons.append(f"{field}={channel.get(field)}")
        if channel.get("status") != expected["status"] and not degraded:
            reasons.append(f"status={channel.get('status')}")
        if reasons:
            violations.append(
                f"{channel_id}:{channel.get('name', '')}=" + ",".join(reasons)
            )
    return [*violations, *muyuan_sol_fallback_violations(channels)]

def teamorouter_free_violations(channels: list[dict]) -> list[str]:
    """Pin the teamorouter free tier as a bounded DeepSeek fallback channel.

    An auto_ban disable during upstream free-tier outages is accepted; the
    priority/weight tier is still enforced so the channel re-enters as a
    p40 fallback below the ch42/ch48 primaries.
    """
    channel = next(
        (item for item in channels if item.get("id") == TEAMOROUTER_CHANNEL_ID),
        None,
    )
    if channel is None:
        return [] if TEAMOROUTER_RETIRED else [f"{TEAMOROUTER_CHANNEL_ID}:missing"]

    expected = TEAMOROUTER_FREE_CONTRACT
    reasons: list[str] = []
    degraded = channel.get("status") != 1 and channel.get("auto_ban") in (1, True)
    for field in ("name", "type", "auto_ban", "base_url",
                  "priority", "weight", "test_model"):
        if channel.get(field) != expected[field]:
            reasons.append(f"{field}={channel.get(field)}")
    if channel.get("status") != expected["status"] and not degraded:
        reasons.append(f"status={channel.get('status')}")

    models = {
        model.strip()
        for model in str(channel.get("models") or "").split(",")
        if model.strip()
    }
    expected_models = set(expected["models"])
    if models != expected_models:
        reasons.append(
            "models="
            f"missing:{sorted(expected_models - models)},"
            f"extra:{sorted(models - expected_models)}"
        )
    try:
        mapping = json.loads(str(channel.get("model_mapping") or "{}"))
    except (TypeError, ValueError):
        mapping = None
    if mapping != expected["mapping"]:
        reasons.append("model_mapping=drifted")

    return (
        [f"{TEAMOROUTER_CHANNEL_ID}:{channel.get('name', '')}=" + ",".join(reasons)]
        if reasons else []
    )

def fallback_posture_violations(channels: list[dict]) -> list[str]:
    """Return fallback channels that are disabled or drifted into a primary tier.

    Channels in DEGRADED_ACCEPTED_DISABLED are exempt from the status==1
    requirement while disabled (local automation took them down for upstream
    degradation; AutomaticEnableChannelEnabled restores them once their
    scheduled test passes). Priority/weight drift is still enforced so they
    re-enter at the correct tier.
    """
    by_id = {channel.get("id"): channel for channel in channels}
    violations: list[str] = []
    for channel_id, expected in FALLBACK_CHANNEL_POSTURES.items():
        channel = by_id.get(channel_id)
        if channel is None:
            violations.append(f"{channel_id}:missing")
            continue
        reasons: list[str] = []
        degraded = (
            channel_id in DEGRADED_ACCEPTED_DISABLED
            and channel.get("status") != 1
        )
        if channel.get("status") != 1 and not degraded:
            reasons.append(f"status={channel.get('status')}")
        if channel.get("priority") != expected["priority"]:
            reasons.append(f"priority={channel.get('priority')}")
        weight = channel.get("weight")
        if not isinstance(weight, int) or weight > expected["max_weight"]:
            reasons.append(f"weight={weight}")
        if reasons:
            violations.append(
                f"{channel_id}:{channel.get('name', '')}=" + ",".join(reasons)
            )
    return violations

def backup_posture_violations(channels: list[dict]) -> list[str]:
    """Return opus-5 backup channels that drifted into the primary tier.

    Unlike fallback_posture_violations, disabled is NOT a violation here:
    these reserves flap on flaky upstreams (tabitoken 409s, agentrouter slow
    TTFT), so enable/disable is left to Guardian and auto-ban. What is gated:
    while enabled, a backup must never sit at/above the primary tier
    (priority 50+) or carry an inflated weight.
    """
    by_id = {channel.get("id"): channel for channel in channels}
    violations: list[str] = []
    for channel_id, expected in BACKUP_CHANNEL_POSTURES.items():
        channel = by_id.get(channel_id)
        if channel is None or channel.get("status") != 1:
            continue
        reasons: list[str] = []
        priority = channel.get("priority")
        if not isinstance(priority, int) or priority > expected["max_priority"]:
            reasons.append(f"priority={priority}")
        weight = channel.get("weight")
        if not isinstance(weight, int) or weight > expected["max_weight"]:
            reasons.append(f"weight={weight}")
        if reasons:
            violations.append(
                f"{channel_id}:{channel.get('name', '')}=" + ",".join(reasons)
            )
    return violations


def primary_posture_violations(channels: list[dict]) -> list[str]:
    """Primary opus-5 pool channels must stay enabled at their pinned tier.

    2026-08-24: linxi 双渠道曾因 Guardian 隔离被回弹禁用；姿态契约把
    status/priority/权重下限变成显式违规，杜绝"静默回零"。
    """
    by_id = {channel.get("id"): channel for channel in channels}
    violations: list[str] = []
    for channel_id, expected in PRIMARY_CHANNEL_POSTURES.items():
        channel = by_id.get(channel_id)
        if channel is None:
            violations.append(f"{channel_id}:missing")
            continue
        reasons: list[str] = []
        if channel.get("status") != 1:
            reasons.append(f"status={channel.get('status')}")
        if channel.get("priority") != expected["priority"]:
            reasons.append(f"priority={channel.get('priority')}")
        weight = channel.get("weight")
        if not isinstance(weight, int) or weight < expected["min_weight"]:
            reasons.append(f"weight={weight}")
        if reasons:
            violations.append(
                f"{channel_id}:{channel.get('name', '')}=" + ",".join(reasons)
            )
    return violations


def channel_policy_violations(channels: list[dict]) -> list[str]:
    """Return aggregate-pool model assignments that violate isolation policy."""
    violations: list[str] = []
    for channel in channels:
        forbidden = CHANNEL_MODEL_EXCLUSIONS.get(channel.get("id"))
        if forbidden:
            models = {
                model.strip()
                for model in str(channel.get("models") or "").split(",")
                if model.strip()
            }
            leaked = sorted(models & forbidden)
            if leaked:
                violations.append(f"{channel['id']}:{channel.get('name', '')}={','.join(leaked)}")
        # Every zg-* alias listed in a channel's models must resolve via
        # model_mapping; unmapped aliases silently 503 (proxies only know base
        # names) and waste a failover hop. Regression: ch45 zg-* 503 on 08-07.
        try:
            mapping = json.loads(str(channel.get("model_mapping") or "{}"))
        except (ValueError, TypeError):
            mapping = {}
        if not isinstance(mapping, dict):
            mapping = {}
        if channel.get("id") == ANYROUTER_CHANNEL_ID:
            models = {
                model.strip()
                for model in str(channel.get("models") or "").split(",")
                if model.strip()
            }
            expected_models = set(ANYROUTER_CLAUDE_MODELS)
            if models != expected_models:
                missing = sorted(expected_models - models)
                extra = sorted(models - expected_models)
                violations.append(
                    f"72:anyrouter=claude_only:missing={missing},extra={extra}"
                )
            if channel.get("test_model") != ANYROUTER_TEST_MODEL:
                violations.append(
                    f"72:anyrouter=test_model={channel.get('test_model')}"
                )
            if mapping != ANYROUTER_CLAUDE_MAPPING:
                violations.append("72:anyrouter=model_mapping=drifted")
        unmapped = sorted(
            m.strip()
            for m in str(channel.get("models") or "").split(",")
            if m.strip().startswith("zg-") and m.strip() not in mapping
        )
        if unmapped:
            violations.append(
                f"{channel['id']}:{channel.get('name', '')}=unmapped_aliases:{','.join(unmapped)}"
            )
    return violations


def ai168661_channel_violations(channels: list[dict]) -> list[str]:
    """Pin the remaining independently authenticated ai.168661 channel families."""
    by_id = {channel.get("id"): channel for channel in channels}
    violations: list[str] = []
    for channel_id, expected in AI168661_CHANNEL_CONTRACTS.items():
        channel = by_id.get(channel_id)
        if channel is None:
            violations.append(f"{channel_id}:missing")
            continue

        reasons: list[str] = []
        for field in ("name", "test_model", "priority", "weight"):
            if channel.get(field) != expected[field]:
                reasons.append(f"{field}={channel.get(field)}")
        if channel.get("type") != 1:
            reasons.append(f"type={channel.get('type')}")
        if channel.get("status") != expected["status"]:
            reasons.append(f"status={channel.get('status')}")
        if channel.get("auto_ban") not in (1, True):
            reasons.append(f"auto_ban={channel.get('auto_ban')}")
        if channel.get("base_url") != "https://ai.168661.xyz":
            reasons.append(f"base_url={channel.get('base_url')}")

        models = {
            model.strip()
            for model in str(channel.get("models") or "").split(",")
            if model.strip()
        }
        expected_models = set(expected["models"])
        if models != expected_models:
            reasons.append(
                "models="
                f"missing:{sorted(expected_models - models)},"
                f"extra:{sorted(models - expected_models)}"
            )
        try:
            mapping = json.loads(str(channel.get("model_mapping") or "{}"))
        except (TypeError, ValueError):
            mapping = None
        if mapping != expected["mapping"]:
            reasons.append("model_mapping=drifted")
        if reasons:
            violations.append(
                f"{channel_id}:{channel.get('name', '')}=" + ",".join(reasons)
            )
    return violations


def expected_disabled_violations(channels: list[dict]) -> list[str]:
    """Return intentional isolation channels that broke their quarantine contract.

    Two violation classes:
    - re-entered service: status flipped back to 1 (auto-enable or manual pull);
    - double-lock broken: status is down but weight != 0 — the 2026-08-10 linxi
      incident showed a status flip alone can resurrect traffic, so quarantined
      channels carry status=2 AND weight=0; either lock missing is a violation.
    """
    violations: list[str] = []
    for channel in channels:
        if channel.get("id") not in KNOWN_BROKEN_CHANNELS:
            continue
        label = f"{channel['id']}:{channel.get('name', '')}"
        if channel.get("status") == 1:
            violations.append(f"{label}=re-entered service")
        else:
            weight = channel.get("weight")
            if isinstance(weight, int) and weight != 0:
                violations.append(f"{label}=double-lock broken (weight={weight})")
    return violations


def multi_key_health_violations() -> list[str]:
    """Detect silent multi-key degradation (upstream QuantumNous/new-api#3537).

    In multi_to_single/polling channels, individually auto-disabled keys are
    NEVER auto-recovered upstream (ShouldEnableChannel only sees channel-level
    status; GetNextEnabledKey never re-tests disabled keys). The pool only
    shrinks, silently. Recovery is manual: DB channel_info fix + PUT refresh,
    documented in docs/ops/tabitoken-channel-2026-08-09.md.
    Reads the NewAPI SQLite DB read-only; the admin API does not expose
    multi_key_status_list on this fork.
    """
    violations: list[str] = []
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return [f"db-open-failed={e}"]
    try:
        for cid, name, status, info in con.execute(
            "SELECT id, name, status, channel_info FROM channels"
        ):
            try:
                ci = json.loads(info) if info else {}
            except (ValueError, TypeError):
                continue
            if not isinstance(ci, dict) or not ci.get("is_multi_key"):
                continue
            key_list = ci.get("multi_key_status_list") or {}
            if isinstance(key_list, dict):
                disabled = sorted(k for k, v in key_list.items() if v == 3)
            else:
                disabled = []
            if disabled and status == 1:
                violations.append(
                    f"{cid}:{name}=keys_disabled:{','.join(map(str, disabled))}"
                    f"/{ci.get('multi_key_size', '?')}"
                )
    except sqlite3.Error as e:
        violations.append(f"db-read-failed={e}")
    finally:
        con.close()
    return violations

ZERO_OUTPUT_WINDOW_SECONDS = 6 * 3600  # 回看窗口
ZERO_OUTPUT_MIN_USE_TIME = 120  # 挂死指纹：长时间流且零输出
ZERO_OUTPUT_MIN_EVENTS_PER_CHANNEL = 2  # 单渠道偶发取消不报，≥2 才算系统性挂死


def zero_output_billing_violations() -> list[str]:
    """Detect upstream hangs that still bill (2026-08-20 ch86: 67.9k prompt
    120s 零输出 client_gone，仍计费 ¥0.5)。

    指纹：consume 日志 completion_tokens=0 且 use_time>=120s 且 quota>0。
    用户手动取消可能产生单条同类记录，故单渠道窗口内 ≥2 条才报。
    直读 NewAPI SQLite logs 表（只读），admin API 无此聚合。
    """
    violations: list[str] = []
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return [f"db-open-failed={e}"]
    try:
        since = int(time.time()) - ZERO_OUTPUT_WINDOW_SECONDS
        rows = con.execute(
            "SELECT channel_id, COUNT(*), SUM(quota) FROM logs"
            " WHERE type=2 AND created_at>=? AND completion_tokens=0"
            " AND use_time>=? AND quota>0"
            " GROUP BY channel_id HAVING COUNT(*)>=?",
            (since, ZERO_OUTPUT_MIN_USE_TIME, ZERO_OUTPUT_MIN_EVENTS_PER_CHANNEL),
        ).fetchall()
        for channel_id, count, quota in rows:
            violations.append(
                f"channel={channel_id} zero-output-billed x{count} quota={quota}"
            )
    except sqlite3.Error as e:
        violations.append(f"db-read-failed={e}")
    finally:
        con.close()
    return violations

# 2026-10-11 未注册名按隐含 37.5 计费：gpt-6-astra 24h 615 条记 2,836,445,168 quota，
# 同族已注册名 gpt-5.6-sol（0.5）209 条只记 12,202,706。名字漏注册不是配置漂移，是
# 流量自己长出来的，所以按实测 consume 行报，不按 ModelRatio 键集报。
UNREGISTERED_RATIO_WINDOW_SECONDS = 24 * 3600
UNREGISTERED_RATIO_MIN_REQUESTS = 20  # 噪声名不报，≥20 条才算真在被使用


def unregistered_ratio_violations() -> list[str]:
    """Flag high-volume model names billed at the fork's implied ratio.

    A name absent from both ModelRatio and ModelPrice falls back to the implied
    ratio (37.5, read back from the row itself), which overstates local quota
    ~75x against the curated 0.5 family. Each row's own model_ratio/model_price is
    reported instead of inferring from the option key set: a fixed ModelPrice is a
    deliberate price and must not read as a gap.
    """
    violations: list[str] = []
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return [f"db-open-failed={e}"]
    try:
        priced: set[str] = set()
        for option in ("ModelRatio", "ModelPrice"):
            row = con.execute("SELECT value FROM options WHERE key=?", (option,)).fetchone()
            try:
                values = json.loads(row[0]) if row and row[0] else {}
            except ValueError:
                violations.append(f"option-{option}-unparsable")
                values = {}
            priced.update(str(k) for k in values)
        since = int(time.time()) - UNREGISTERED_RATIO_WINDOW_SECONDS
        busy = con.execute(
            "SELECT model_name, COUNT(*), SUM(quota) FROM logs"
            " WHERE type=2 AND created_at>=? AND quota>0 AND channel_id>0"
            " GROUP BY 1 HAVING COUNT(*)>=?",
            (since, UNREGISTERED_RATIO_MIN_REQUESTS),
        ).fetchall()
        latest = dict(con.execute(
            "SELECT x.model_name, l.other FROM logs l"
            " JOIN (SELECT model_name, MAX(id) mid FROM logs"
            " WHERE type=2 AND created_at>=? GROUP BY 1) x ON x.mid = l.id",
            (since,),
        ).fetchall())
        for model_name, count, quota in busy:
            name = str(model_name)
            if name in priced:
                continue
            try:
                info = json.loads(latest.get(name) or "{}")
            except ValueError:
                info = {}
            violations.append(
                f"{name} x{count} quota={quota} "
                f"model_ratio={info.get('model_ratio', '?')} "
                f"model_price={info.get('model_price', '?')}"
            )
    except sqlite3.Error as e:
        violations.append(f"db-read-failed={e}")
    finally:
        con.close()
    return violations

# 2026-10-11 intern-discovery 现场：ch140/141/142/143/146 五腿共享
# discovery-api.intern-ai.org.cn 一个账号，上游按请求体量预扣费。余额见底后 11 token
# 的探测仍然 200，25k token 的真实请求 429 "quota exceeded" —— 最大成功 prompt 逐日
# 塌缩（122158 → 77751 → 32946 → 87 → 17）就是这个形状。NewAPI auto_ban 不把 429 当
# 故障，五腿恒 status=1 并占着 glm-5.3 / intern-s2 / deepseek-v4-flash-vision 的最高
# 启用层，每个真实请求先白烧两次重试。任何"探测=健康"的门都看不见它，只能用生产报错
# × 生产真实成功交叉判断。
QUOTA_DRAIN_WINDOW_SECONDS = 6 * 3600
QUOTA_DRAIN_MIN_ERRORS = 10  # 抖动不报，窗口内 ≥N 次 quota 429 才算持续耗尽
QUOTA_DRAIN_REAL_PROMPT_TOKENS = 256  # 成功行最大 prompt 低于此 = 只接得住探测
QUOTA_DRAIN_LOG_TAIL_BYTES = 8 * 1024 * 1024
# 额度类报文词表：quota（intern-discovery）、limit（ch203 clinepass "You have reached
# your weekly Clinepass limit ... resets in 4d 22h"）、exhausted / insufficient。
# 纯 "too many requests" / rate limit 已由 _QUOTA_DRAIN_TRANSIENT_RE 先跳过。
QUOTA_DRAIN_CAP_MARKERS = ("quota", "limit", "exhausted", "insufficient", "预扣费")
_QUOTA_DRAIN_ERR_RE = re.compile(
    r"^\[(?:ERR|W)\] (\d{4}/\d{2}/\d{2}) - (\d{2}:\d{2}:\d{2})\b.*?"
    r"channel error \(channel #(\d+), status code: 429\): ([^|\n]{0,200})"
)
_QUOTA_DRAIN_TRANSIENT_RE = re.compile(r"rate.?limit|too many requests", re.IGNORECASE)


def drained_leg_violations() -> list[str]:
    """Flag enabled channels that only answer probes while 429ing on real traffic."""
    cutoff = time.time() - QUOTA_DRAIN_WINDOW_SECONDS
    log_path = DEPLOY_DIR / "stderr.log"
    counts: dict[int, int] = {}
    samples: dict[int, str] = {}
    try:
        size = log_path.stat().st_size
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            if size > QUOTA_DRAIN_LOG_TAIL_BYTES:
                handle.seek(size - QUOTA_DRAIN_LOG_TAIL_BYTES)
                handle.readline()  # 丢掉截断的首行
            for line in handle:
                match = _QUOTA_DRAIN_ERR_RE.match(line)
                if not match:
                    continue
                try:
                    when = time.mktime(
                        time.strptime(f"{match.group(1)} {match.group(2)}", "%Y/%m/%d %H:%M:%S")
                    )
                except ValueError:
                    continue
                if when < cutoff:
                    continue
                body = match.group(4).strip()
                if _QUOTA_DRAIN_TRANSIENT_RE.search(body):
                    continue  # 瞬态限流不是耗尽，交给池内重试
                if not any(marker in body.lower() for marker in QUOTA_DRAIN_CAP_MARKERS):
                    continue
                channel_id = int(match.group(3))
                counts[channel_id] = counts.get(channel_id, 0) + 1
                samples.setdefault(channel_id, body[:60])
    except OSError as error:
        return [f"stderr-read-failed={error}"]
    suspect = {cid for cid, n in counts.items() if n >= QUOTA_DRAIN_MIN_ERRORS}
    if not suspect:
        return []
    violations: list[str] = []
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return [f"db-open-failed={error}"]
    try:
        since = int(cutoff)
        for channel_id in sorted(suspect):
            row = con.execute(
                "SELECT status, name FROM channels WHERE id=?", (channel_id,)
            ).fetchone()
            if not row or row[0] != 1:
                continue  # 已禁用/已回收，不是裸奔
            largest = con.execute(
                "SELECT MAX(prompt_tokens) FROM logs"
                " WHERE type=2 AND channel_id=? AND created_at>=?",
                (channel_id, since),
            ).fetchone()[0]
            if largest is not None and largest >= QUOTA_DRAIN_REAL_PROMPT_TOKENS:
                continue  # 窗口内接过真实流量
            violations.append(
                f"ch{channel_id} {row[1]}: {counts[channel_id]}x 429 quota in 6h, "
                f"max success prompt={largest or 0} (probe-only) :: {samples[channel_id]}"
            )
    except sqlite3.Error as error:
        violations.append(f"db-read-failed={error}")
    finally:
        con.close()
    return violations


# 2026-10-11 glm-5.3 现场：logs 只在成功时写 type=2，所以"配置全绿 + 探测假活"能同时
# 骗过配置容量轴和实测容量轴；perf_metrics 是这份 DB 里唯一带失败尝试量的表（按
# model+group 每小时一行）。glm-5.3 6h req=215 ok=29（13.5%，且 ok 全是 17 token 的探测
# 尺寸），同窗口 claude-opus-4-8 98.4% / step-5-preview-free 99.6% / gpt-6-astra 91.7%
# ——阈值 50% 只报真出事的那个名，不制造噪声红灯。
ANSWER_RATE_WINDOW_SECONDS = 6 * 3600
ANSWER_RATE_MIN_ATTEMPTS = 30
ANSWER_RATE_MIN_PERCENT = 50.0


def pool_answer_rate_violations() -> tuple[list[str], str]:
    """Flag model names that carry real demand but barely get an answer."""
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        return [f"db-open-failed={error}"], ""
    try:
        rows = con.execute(
            """SELECT model_name, "group", SUM(request_count), SUM(success_count)
               FROM perf_metrics WHERE bucket_ts >= ?
               GROUP BY model_name, "group"
               HAVING SUM(request_count) >= ?
               ORDER BY SUM(success_count) * 1.0 / SUM(request_count)""",
            (int(time.time()) - ANSWER_RATE_WINDOW_SECONDS, ANSWER_RATE_MIN_ATTEMPTS),
        ).fetchall()
    except sqlite3.Error as error:
        return [f"perf-metrics-read-failed={error}"], ""
    finally:
        con.close()
    if not rows:
        # 无采样 ≠ 健康：fork 没写 perf_metrics 时显式说明，门禁不得静默绿。
        return [], "no-samples-in-window"
    violations: list[str] = []
    for model_name, group, attempts, answered in rows:
        rate = 100.0 * (answered or 0) / attempts
        if rate >= ANSWER_RATE_MIN_PERCENT:
            continue
        label = model_name if group in ("default", "", None) else f"{model_name}@{group}"
        violations.append(f"{label} req={attempts} ok={answered or 0} rate={rate:.1f}%")
    return violations, f"sampled={len(rows)}"


GUARDIAN_STATE_FILE = Path.home() / ".omp" / "guardian" / "state.json"


def guardian_disabled_ids() -> set[int]:
    """Channel ids in Guardian's bounded recovery queue (its own disables).

    Guardian disables via POST /api/channel/{id}/status with auto_ban=0 — the
    same signature as a human actor — so attribution needs this cross-reference.
    Unreadable state degrades to an empty set (the 4h health check separately
    asserts guardian heartbeat freshness).
    """
    try:
        state = json.loads(GUARDIAN_STATE_FILE.read_text(encoding="utf-8-sig"))
        return {
            int(record["id"])
            for record in state.get("disabled_channels", [])
            if isinstance(record, dict) and record.get("id") is not None
        }
    except (OSError, ValueError, KeyError, TypeError):
        return set()

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = REPO_ROOT / ".tmp-newapi-dx-ops.log"

failures: list[str] = []


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_json(path: Path) -> dict:
    # utf-8-sig: some tools write these files with a BOM
    return json.loads(path.read_text(encoding="utf-8-sig"))


def http_json(url: str, *, method: str = "GET", body: dict | None = None,
              headers: dict | None = None, timeout: float = 15) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def check(name: str, ok: bool, detail: str = "") -> None:
    log(f"{'OK  ' if ok else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


TOKEN_CACHE = DEPLOY_DIR / ".admin-token-cache.json"
GUARDIAN_SECRETS = Path.home() / ".omp" / "guardian" / "secrets.json"


def _drop_token_cache() -> None:
    """删除缓存的 admin token（401 过期 / 403 权限问题都不应让旧 token 残留毒化）。"""
    try:
        TOKEN_CACHE.unlink(missing_ok=True)
    except OSError:
        pass


class _AdminAuthUnavailable(Exception):
    """缓存 admin token 校验失败（403/429/5xx/网络异常）时抛出的降级信号。

    admin_auth 在抛出前已用 check() 记录 FAIL；main() 单独捕获本异常后跳过
    依赖 admin API 的检查，多 key / 冒烟检查照常执行——不让巡检整体中断。
    """


def _guardian_admin_token() -> tuple[str, str] | None:
    """Read Guardian's long-lived admin token without exposing its value."""
    try:
        secrets = read_json(GUARDIAN_SECRETS)
    except (OSError, ValueError, TypeError):
        return None
    token = secrets.get("newapi_token") if isinstance(secrets, dict) else None
    if not isinstance(token, str) or not token.strip():
        return None
    user_id = secrets.get("newapi_user", "1")
    return token.strip(), str(user_id or "1")


def admin_auth() -> tuple[str, str]:
    """Admin API auth with token reuse.

    Every /api/user/login creates a server-side session, and this fork caps
    concurrent sessions (HTTP 409 AUTH_SESSION_LIMIT) — the smoke runs on a
    schedule, so an uncached login per run exhausts the limit within a day.
    Reuse the cached token until the server rejects it with HTTP 401. Any
    other non-200 validation outcome (403/429/5xx/network error) degrades to
    an explicit FAIL check (recorded here) and raises _AdminAuthUnavailable
    instead of a RuntimeError, so main() skips the admin-API checks but keeps
    running multi-key and smoke checks. 401 drops the stale cache and refreshes
    via Guardian or, when no Guardian token is configured, a fresh login;
    403 drops the cache too; a configured Guardian token is tried before any
    password login. 429/5xx/network errors keep the
    cache (re-login on a transient blip would burn the AUTH_SESSION_LIMIT
    quota). A configured Guardian token that returns 401 is stale: it is
    dropped and one password login refreshes the cache, so the session cost is
    one login per cache expiry rather than one per scheduled run.
    """
    cached_permission_failure = False
    try:
        cached = read_json(TOKEN_CACHE)
        token, user_id = cached["token"], str(cached.get("user_id") or "1")
    except (OSError, ValueError, KeyError):
        token = ""
    if token:
        try:
            status, _ = http_json(
                f"{NEWAPI_BASE}/api/channel/?p=0&page_size=1",
                headers={"Authorization": f"Bearer {token}", "New-Api-User": user_id},
            )
        except urllib.error.URLError as e:
            # 网络异常：缓存 token 无法验证，保留缓存（抖动不烧 session），
            # 降级为 FAIL 而非中断——多 key / 冒烟检查照常执行。
            check("admin token auth", False,
                  f"cached token check network error: {e}")
            raise _AdminAuthUnavailable from None
        if status == 200:
            return token, user_id
        if status == 401:
            # 确定性鉴权失效(401)：丢弃缓存，走下方登录刷新（防旧 token 残留）。
            _drop_token_cache()
        elif status == 403:
            # 403 是权限问题（本轮重登无用，且烧 AUTH_SESSION_LIMIT）。删除
            # 缓存让下一轮重新登录——否则权限恢复后缓存 token 永久毒化，无人
            # 干预则永远 FAIL。若权限真被收回，下轮登录失败会给出可操作报错。
            _drop_token_cache()
            cached_permission_failure = True
        else:
            # 429/5xx/其他非 200：保留缓存（每次限流重登会打满
            # AUTH_SESSION_LIMIT），本轮降级 FAIL，不中断后续检查。
            check("admin token auth", False,
                  f"cached token check returned HTTP {status}; cache kept")
            raise _AdminAuthUnavailable from None
    guardian_token = _guardian_admin_token()
    if guardian_token is not None:
        token, user_id = guardian_token
        try:
            status, _ = http_json(
                f"{NEWAPI_BASE}/api/channel/?p=0&page_size=1",
                headers={"Authorization": f"Bearer {token}", "New-Api-User": user_id},
            )
        except urllib.error.URLError as e:
            check("admin token auth", False,
                  f"Guardian token check network error: {e}")
            raise _AdminAuthUnavailable from None
        if status == 200:
            return token, user_id
        if status != 401:
            check("admin token auth", False,
                  f"Guardian token check returned HTTP {status}; password login skipped")
            raise _AdminAuthUnavailable from None
        # Guardian token stale (401): drop it and fall through to password login.
        # Password login caches the session token, so this only costs one
        # /api/user/login per cache expiry — not per smoke run.
        _drop_token_cache()

    if cached_permission_failure:
        check("admin token auth", False,
              "cached token rejected with HTTP 403; no Guardian token available")
        raise _AdminAuthUnavailable from None

    creds = read_json(DEPLOY_DIR / "admin-credentials.json")
    _, login = http_json(
        f"{NEWAPI_BASE}/api/user/login", method="POST",
        body={"username": creds["username"], "password": creds["password"]},
    )
    if not (login.get("data") or {}).get("access_token"):
        err = (login.get("error") or login.get("message") or "unknown")
        raise RuntimeError(f"login failed: {str(err)[:160]} (响应体不打印，防凭据泄露)")
    token = login["data"]["access_token"]
    user_id = str((login.get("data") or {}).get("id") or "1")  # fork may omit id
    try:
        TOKEN_CACHE.write_text(json.dumps({"token": token, "user_id": user_id}))
    except OSError:
        pass
    return token, user_id


def serving_channel_ids(channels: list[dict], model: str) -> list[int]:
    """Return routable channel ids for a model.

    NewAPI treats ``weight=0`` as deliberately non-routable, even when the
    row remains ``status=1``. Excluding those rows prevents false capacity.
    """
    return [
        channel["id"]
        for channel in channels
        if channel.get("status") == 1
        and isinstance(channel.get("weight"), (int, float))
        and channel.get("weight", 0) > 0
        and model
        in {
            item.strip()
            for item in str(channel.get("models") or "").split(",")
        }
    ]


def measured_served_by_channel(
    window_seconds: int,
) -> tuple[dict[str, dict[int, int]], dict[str, int], str]:
    """Model -> channel -> successful real requests inside the look-back window.

    NewAPI writes a type=2 consume row only after a response succeeded, so this
    is the one capacity signal that proves the upstream answered; failed
    attempts leave no row at all.  Rows with ``completion_tokens=0`` are billed
    hangs (see ``zero_output_billing_violations``), not answers.

    Returns ``(served, demand, skipped_reason)``.  ``demand`` counts every
    consume row for the model including probe-sized ones, so the caller can tell
    "nobody asked" apart from "only pings asked".  A non-empty
    ``skipped_reason`` means the measured axis was not evaluated at all (DB
    unreadable), which must never be reported as a capacity FAIL.
    """
    served: dict[str, dict[int, int]] = {}
    demand: dict[str, int] = {}
    try:
        con = sqlite3.connect(f"file:{NEWAPI_DB.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return {}, {}, f"db-open-failed={e}"
    try:
        since = int(time.time()) - window_seconds
        placeholders = ",".join("?" * len(MIN_ENABLED_CRITICAL_MODELS))
        for model_name, channel_id, answered, asked in con.execute(
            "SELECT model_name, channel_id, SUM(CASE WHEN completion_tokens>0"
            f" AND prompt_tokens>={MEASURED_CAPACITY_MIN_PROMPT_TOKENS}"
            " THEN 1 ELSE 0 END), COUNT(*) FROM logs"
            " WHERE type=2 AND created_at>=? AND channel_id>0"
            f" AND model_name IN ({placeholders}) GROUP BY 1,2",
            (since, *sorted(MIN_ENABLED_CRITICAL_MODELS)),
        ):
            name = str(model_name)
            served.setdefault(name, {})[int(channel_id)] = int(answered or 0)
            demand[name] = demand.get(name, 0) + int(asked)
    except sqlite3.Error as e:
        return {}, {}, f"db-read-failed={e}"
    finally:
        con.close()
    return served, demand, ""


def smoke_probe_skip_reason(
    channels: list[dict], model: str, guardian_ids: set[int]
) -> str | None:
    """Skip only when every declared route is explicitly owned and disabled."""
    if serving_channel_ids(channels, model):
        return None
    declared = [
        channel
        for channel in channels
        if model
        in {
            item.strip()
            for item in str(channel.get("models") or "").split(",")
        }
    ]
    if not declared:
        return None
    policy_ids = KNOWN_BROKEN_CHANNELS | set(DEGRADED_ACCEPTED_DISABLED) | guardian_ids
    attributed = {
        channel.get("id")
        for channel in declared
        if channel.get("status") in (2, 3)
        and (
            channel.get("id") in policy_ids
            or _auto_ban_enabled(channel.get("auto_ban"))
        )
    }
    declared_ids = {channel.get("id") for channel in declared}
    if declared_ids and declared_ids == attributed:
        return f"all declared channels attributed disabled ids={sorted(attributed)}"
    return None


def main() -> int:
    channel_items: list[dict] | None = None
    # 1. NewAPI status
    status, _ = http_json(f"{NEWAPI_BASE}/api/status", timeout=8)
    check("newapi /api/status", status == 200, f"HTTP {status}")

    # 2. proxy ports
    for name, (host, port) in PROXY_PORTS.items():
        try:
            with socket.create_connection((host, port), timeout=3):
                check(f"proxy {name}:{port}", True)
        except OSError as e:
            check(f"proxy {name}:{port}", False, str(e))

    # 3. channel summary via admin API
    try:
        token, user_id = admin_auth()
        headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
        option_status, option_body = http_json(
            f"{NEWAPI_BASE}/api/option/",
            headers=headers,
        )
        option_violations = option_policy_violations(option_body.get("data"))
        check(
            "automatic channel recovery",
            option_status == 200 and not option_violations,
            f"HTTP {option_status} violations={option_violations or 'none'}",
        )
        affinity_violations = affinity_rule_violations(option_body.get("data"))
        check(
            "channel affinity aliases",
            option_status == 200 and not affinity_violations,
            f"HTTP {option_status} violations={affinity_violations or 'none'}",
        )
        # fork 渠道列表 API 每页硬顶 100（page_size>100 被钳制）且 p=0 是特例
        # （只回首页，与 p=1 同集合）：真翻页从 p=1 起。2026-10-08 起渠道数超 100
        # （104），单页抓取会静默丢渠道、enabled/unexpected_disabled 归因失真。
        # 按页拉全（total 对账），丢页即 FAIL。
        items: list = []
        status = 0
        ch = {}
        for page in range(1, 21):
            status, ch = http_json(
                f"{NEWAPI_BASE}/api/channel/?p={page}&page_size=100",
                headers=headers,
            )
            data = ch.get("data") if isinstance(ch, dict) else None
            page_items = data.get("items") if isinstance(data, dict) else data
            if status != 200 or not isinstance(page_items, list):
                break
            items.extend(page_items)
            total = data.get("total") if isinstance(data, dict) else None
            if not page_items or (isinstance(total, int) and len(items) >= total):
                break
        if isinstance(ch.get("data"), dict):
            total_field = ch["data"].get("total")
            if isinstance(total_field, int) and len(items) != total_field:
                # 翻页不完整：置 status 让下方统一校验路径报 FAIL（不静默绿）。
                status = 999
        # 渠道接口异常（500 + 空 body 等）不得误报健康：先校验状态码和 items 结构
        if status != 200 or not isinstance(items, list):
            check("channels", False, f"bad response: HTTP {status}, items={str(items)[:80]!r}")
        else:
            channel_items = items
            # 意外禁用归因（2026-08-10 事故类：多起渠道状态变更无法归因）：
            # - status=2 ∧ auto_ban=1 = fork 内部 auto-ban 自调（机器行为，预期）；
            # - status=2 ∧ auto_ban=0 = 人工/脚本禁用 → 必须能归因：白名单集合
            #   或 Guardian 恢复队列（state.json）在案，否则 FAIL；
            # - status=3（老式 auto-disable 落库）同理须可归因。
            def _auto_banned(c: dict) -> bool:
                return str(c.get("auto_ban", "")).strip().lower() in ("1", "true")

            accepted = (
                KNOWN_BROKEN_CHANNELS
                | set(DEGRADED_ACCEPTED_DISABLED)
                | guardian_disabled_ids()
            )
            unexpected = [
                f"{c['id']}:{c['name']}"
                for c in items
                if c.get("id") not in accepted and (
                    c.get("status") == 3
                    or (c.get("status") == 2 and not _auto_banned(c))
                )
            ]
            disabled_attributed = [
                f"{c['id']}:{c['name']}"
                for c in items
                if c.get("id") in accepted and c.get("status") in (2, 3)
            ]
            enabled = sum(1 for c in items if c.get("status") == 1)
            check("channels", not unexpected,
                  f"total={len(items)} enabled={enabled} unexpected_disabled={unexpected or 'none'}"
                  + (f" accepted_disabled={disabled_attributed}" if disabled_attributed else ""))
            policy_violations = channel_policy_violations(items)
            check(
                "channel model isolation",
                not policy_violations,
                f"violations={policy_violations or 'none'}",
            )
            ai168661_violations = ai168661_channel_violations(items)
            check(
                "ai.168661 channel posture",
                not ai168661_violations,
                f"violations={ai168661_violations or 'none'}",
            )
            sol_violations = sol_primary_posture_violations(items)
            check(
                "Sol primary posture",
                not sol_violations,
                f"violations={sol_violations or 'none'}",
            )
            teamo_violations = teamorouter_free_violations(items)
            check(
                "teamorouter free fallback posture",
                not teamo_violations,
                f"violations={teamo_violations or 'none'}",
            )
            disable_violations = expected_disabled_violations(items)
            check(
                "intentional channel disables",
                not disable_violations,
                f"violations={disable_violations or 'none'}",
            )
            posture_violations = fallback_posture_violations(items)
            check(
                "fallback channel posture",
                not posture_violations,
                f"violations={posture_violations or 'none'}",
            )
            primary_violations = primary_posture_violations(items)
            check(
                "primary opus pool posture",
                not primary_violations,
                f"violations={primary_violations or 'none'}",
            )
            backup_violations = backup_posture_violations(items)
            check(
                "backup opus pool posture",
                not backup_violations,
                f"violations={backup_violations or 'none'}",
            )
            # 池冗余：关键模型不得失去最后一个启用渠道（0 = "503 No available
            # channel" 前置检测；2026-08-10 凌晨 opus 池曾退化到单渠道 ch75）。
            # 双轴判定：配置轴防塌缩，实测轴防幻影（type=2 成功行是唯一能证明上游
            # 真的回答过的信号，失败请求不留行，所以实测只在窗口内有需求时评估）。
            measured, demand, measured_note = measured_served_by_channel(
                MEASURED_CAPACITY_WINDOW_SECONDS
            )
            for model, minimum in MIN_ENABLED_CRITICAL_MODELS.items():
                serving_ids = serving_channel_ids(items, model)
                served = measured.get(model, {})
                answered = [cid for cid in serving_ids if served.get(cid)]
                ok = len(serving_ids) >= minimum
                detail = (
                    f"enabled_channels={len(serving_ids)} min={minimum}"
                    f" ids={serving_ids}"
                    f" | measured_channels={len(answered)} ids={answered}"
                    f" measured_successes={sum(served.values())}"
                    f" demand={demand.get(model, 0)}"
                    f" window={MEASURED_CAPACITY_WINDOW_SECONDS}s"
                )
                if measured_note:
                    detail += f" | measured-skipped={measured_note}"
                elif demand.get(model, 0) == 0:
                    # 窗口内无人请求该模型：实测轴无信号可用，只能按配置容量判定。
                    detail += " | measured-skipped=no-demand"
                elif sum(served.values()) == 0:
                    # 只有探针尺寸的成功行，真实上下文从未验证过——红不起来，但必须显式写出。
                    detail += " | measured-skipped=probe-only-successes"
                else:
                    ok = ok and len(answered) >= minimum
                check(f"pool capacity {model}", ok, detail)
    except _AdminAuthUnavailable:
        # admin_auth 已记录 FAIL（缓存 token 校验失败降级），跳过依赖 admin
        # API 的检查；多 key / 冒烟检查在下方照常执行，本轮不中断。
        pass
    except Exception as e:  # noqa: BLE001
        check("channels", False, f"admin api error: {e}")

    # 3b. 多 key 渠道静默退化（上游 QuantumNous/new-api#3537：被自动禁用的单 key
    # 永不自动恢复，池只减不增且无告警）——直读 DB channel_info，API 不暴露该字段。
    mk_violations = multi_key_health_violations()
    check(
        "multi-key pool health",
        not mk_violations,
        f"violations={mk_violations or 'none'}",
    )
    zero_out_violations = zero_output_billing_violations()
    check(
        "zero-output billed streams",
        not zero_out_violations,
        f"violations={zero_out_violations or 'none'}",
    )
    ratio_violations = unregistered_ratio_violations()
    check(
        "unregistered model ratios",
        not ratio_violations,
        f"violations={ratio_violations or 'none'}",
    )
    drain_violations = drained_leg_violations()
    check(
        "probe-only drained legs",
        not drain_violations,
        f"violations={drain_violations or 'none'}",
    )
    rate_violations, rate_note = pool_answer_rate_violations()
    check(
        "pool answer rate",
        not rate_violations,
        f"violations={rate_violations or 'none'} {rate_note}".strip(),
    )
    ability_violations = ability_posture_violations()
    check(
        "critical ability posture",
        not ability_violations,
        f"violations={ability_violations or 'none'}",
    )

    # 4. real cheap completions
    if not SMOKE_PROBES:
        # 2026-10-04 sensenova 上游下架后探针集为空，本门禁一个端到端补全请求都不发。
        # "没有探针" ≠ "补全链路已验证"：显式声明缺口（与 pool answer rate 的
        # no-samples 语义一致），恢复探针需要一个可计费模型载体，见 SMOKE_PROBES 注释。
        check(
            "gateway completion probes",
            True,
            "no-probes-configured — completion path UNVERIFIED",
        )
    try:
        tok = read_json(DEPLOY_DIR / "client-token.json")
        key = tok.get("api_key") or tok.get("key")
        guardian_ids = guardian_disabled_ids()
        for model, api in SMOKE_PROBES:
            skip_reason = (
                smoke_probe_skip_reason(channel_items, model, guardian_ids)
                if channel_items is not None
                else None
            )
            if skip_reason:
                log(f"SKIP smoke {model} — {skip_reason}")
                continue
            t0 = time.time()
            if api == "responses":
                endpoint = f"{NEWAPI_BASE}/v1/responses"
                body = {
                    "model": model,
                    "max_output_tokens": 512,
                    "reasoning": {"effort": "low"},
                    "input": "Reply only: OK.",
                }
            else:
                endpoint = f"{NEWAPI_BASE}/v1/chat/completions"
                body = {
                    "model": model,
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": "Reply only: OK."}],
                }
            status, resp = http_json(
                endpoint,
                method="POST",
                timeout=60,
                headers={"Authorization": f"Bearer {key}"},
                body=body,
            )
            ms = int((time.time() - t0) * 1000)
            content = ""
            try:
                content = resp["choices"][0]["message"]["content"][:30]
            except Exception:
                content = str(resp)[:80]
            check(
                f"smoke {model}",
                status == 200,
                f"api={api} HTTP {status} {ms}ms {content!r}",
            )
    except Exception as e:  # noqa: BLE001
        check("smoke completions", False, f"{e}")

    log(f"summary: {'ALL OK' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
