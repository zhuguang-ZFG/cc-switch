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
  4. Two cheap real completions through the gateway (latency sample)

Logs one summary block to .tmp-newapi-dx-ops.log (repo root, same file the
old DX-Ops task used) and exits nonzero when any check fails, so Task
Scheduler surfaces the failure in LastTaskResult.

Run:  python scripts/ops/newapi-local-smoke.py
"""
from __future__ import annotations

import json
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
}
SMOKE_MODELS = ["sensenova-6.7-flash-lite", "opencode-go"]

# Channels whose auto-disabled state is currently intentional. Channel 2 has
# no upstream model; channels 62-65 fail production-shaped pre-consumption.
# Channel 74 is held out until its shared upstream quota recovers and a real
# relay + aggregate smoke passes. Channel 45 remains a live fallback.
KNOWN_BROKEN_CHANNELS: set[int] = {2, 9, 18, 20, 57, 62, 63, 64, 65, 70, 71, 73, 74}  # 9/18: linxi 同账号余额耗尽（2026-08-10 403 insufficient balance），禁用+weight 0 双锁，被未知调用方回捞过一次；20: fengwind gpt-5.6-sol 故障路由，08-05 起禁用（sol 全局清除决策），08-10 补双锁；57: gorouter 余额不足；70/71: 上游真死（2026-08-08 实测，71 已从 NewAPI 删除、保留占位防 ID 复用），与 Guardian 排除集一致；73/74: relay 渠道上游 405 禁用中（73 于 08-10 15:06 被重新启用且未同步本契约，冒烟会持续 FAIL 直至 codex-relay 修复完成并更新本集合）

# Model isolation is channel-specific. AgentRouter (ch45) and AnyRouter (ch72)
# serve Sol AND Claude at their fallback tiers (Claude re-enabled 2026-08-07:
# anyrouter gate fingerprint fixed, upstream 429 = transient load). CodeBuddy
# (ch44) keeps its Sol exclusion contract.
CHANNEL_MODEL_EXCLUSIONS: dict[int, set[str]] = {
    44: {"gpt-5.6-sol", "zg-wb-gpt-5.6-sol"},
}

# Live aggregate fallback contracts. These channels must stay enabled but below
# the primary pool; model eligibility remains governed separately above.
FALLBACK_CHANNEL_POSTURES: dict[int, dict[str, int]] = {
    45: {"priority": 40, "max_weight": 5},
    72: {"priority": 40, "max_weight": 5},
}

# Channels disabled by local automation due to upstream degradation (NOT config
# breakage). Distinct from KNOWN_BROKEN: these are expected to auto-recover via
# AutomaticEnableChannelEnabled once their scheduled channel test passes again,
# so neither their disable nor their future re-enable is a violation.
# Attribution: ch3 disabled 2026-08-10 12:04 (baibei upstream 502 for hours,
# Guardian had already degraded its weight 24→12 at 09:06); ch45 disabled 22:05
# (agentrouter upstream 429/503 flapping; channel carries auto_ban=1); ch72
# disabled 2026-08-09 00:10 by Guardian (anyrouter upstream gpt-5.6-sol
# overload, 500 "负载已经达到上限"; 100+ recovery probes failed through
# 2026-08-13 — Guardian keeps retrying, re-enable is automatic on recovery).
DEGRADED_ACCEPTED_DISABLED: dict[int, str] = {
    3: "baibei upstream 502; disabled 2026-08-10 12:04 by local automation",
    45: "agentrouter upstream flapping; disabled 2026-08-10 22:05 by local automation",
    72: "anyrouter upstream sol overload; disabled 2026-08-09 00:10 by Guardian",
}

# Critical models that must never lose their last enabled channel. Value is the
# minimum number of enabled (status=1) channels serving the model; 0 enabled =
# hard FAIL ("503 No available channel" state detected before traffic hits it).
# Current capacity is reported in the check detail either way.
MIN_ENABLED_CRITICAL_MODELS: dict[str, int] = {
    "claude-opus-5": 1,
    "deepseek-v4-flash": 1,
}

NEWAPI_DB = DEPLOY_DIR / "new-api.db"

REQUIRED_OPTIONS: dict[str, str] = {
    "AutomaticEnableChannelEnabled": "true",
    # 08-03 防放大策略固化（docs/ops/omp-model-config-review-2026-08-03.md）：
    # 403/401/402/502 触发自动禁用；403 余额错误必穿透。
    # 上游参考：QuantumNous/new-api#1457/#1609 —— auto-disable 依赖状态码/关键词
    # 匹配，社区多例失效报告，故本 fork 不得依赖 auto_ban，靠本契约钉死选项防漂移。
    # 08-11 去除 429：RPM 耗尽的同渠道重试零收益且放大拥塞（实测 173 请求×4
    # 重试全失败），429 透传交客户端退避+回退链；重试仅覆盖瞬时类状态码。
    "AutomaticDisableStatusCodes": "401,402,403,502",
    "AutomaticRetryStatusCodes": "408,500-503",
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
    via a fresh login; 403 drops the cache too so a recovered permission
    re-logins next run instead of poisoning the cache forever; 429/5xx/network
    errors keep the cache (re-login on a transient blip would burn the
    AUTH_SESSION_LIMIT quota).
    """
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
            check("admin token auth", False,
                  "cached token rejected with HTTP 403; cache dropped, next run re-logins")
            raise _AdminAuthUnavailable from None
        else:
            # 429/5xx/其他非 200：保留缓存（每次限流重登会打满
            # AUTH_SESSION_LIMIT），本轮降级 FAIL，不中断后续检查。
            check("admin token auth", False,
                  f"cached token check returned HTTP {status}; cache kept")
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


def main() -> int:
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
        status, ch = http_json(
            f"{NEWAPI_BASE}/api/channel/?p=0&page_size=200",
            headers=headers,
        )
        items = (ch.get("data") or {}).get("items")
        if items is None:
            items = ch.get("data")
        # 渠道接口异常（500 + 空 body 等）不得误报健康：先校验状态码和 items 结构
        if status != 200 or not isinstance(items, list):
            check("channels", False, f"bad response: HTTP {status}, items={str(items)[:80]!r}")
        else:
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
            # 池冗余：关键模型不得失去最后一个启用渠道（0 = "503 No available
            # channel" 前置检测；2026-08-10 凌晨 opus 池曾退化到单渠道 ch75）。
            for model, minimum in MIN_ENABLED_CRITICAL_MODELS.items():
                serving = [
                    c for c in items
                    if c.get("status") == 1 and model in {
                        m.strip() for m in str(c.get("models") or "").split(",")
                    }
                ]
                check(
                    f"pool capacity {model}",
                    len(serving) >= minimum,
                    f"enabled_channels={len(serving)} min={minimum}"
                    f" ids={[c['id'] for c in serving]}",
                )
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

    # 4. real cheap completions
    try:
        tok = read_json(DEPLOY_DIR / "client-token.json")
        key = tok.get("api_key") or tok.get("key")
        for model in SMOKE_MODELS:
            t0 = time.time()
            status, resp = http_json(
                f"{NEWAPI_BASE}/v1/chat/completions", method="POST", timeout=60,
                headers={"Authorization": f"Bearer {key}"},
                body={"model": model, "max_tokens": 8,
                      "messages": [{"role": "user", "content": "Reply only: OK."}]},
            )
            ms = int((time.time() - t0) * 1000)
            content = ""
            try:
                content = resp["choices"][0]["message"]["content"][:30]
            except Exception:
                content = str(resp)[:80]
            check(f"smoke {model}", status == 200, f"HTTP {status} {ms}ms {content!r}")
    except Exception as e:  # noqa: BLE001
        check("smoke completions", False, f"{e}")

    log(f"summary: {'ALL OK' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
