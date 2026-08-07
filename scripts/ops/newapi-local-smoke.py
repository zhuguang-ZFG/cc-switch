#!/usr/bin/env python3
"""Local NewAPI smoke check (replaces the retired VPS DX analyzer).

Checks, in order:
  1. NewAPI /api/status reachable (http://127.0.0.1:3002)
  2. Local gateway proxies listening on the Tailscale bind host (8787/8788)
  3. Admin API: channel health summary (auto-disabled channels are flagged)
  4. Two cheap real completions through the gateway (latency sample)

Logs one summary block to .tmp-newapi-dx-ops.log (repo root, same file the
old DX-Ops task used) and exits nonzero when any check fails, so Task
Scheduler surfaces the failure in LastTaskResult.

Run:  python scripts/ops/newapi-local-smoke.py
"""
from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

NEWAPI_BASE = "http://127.0.0.1:3002"
DEPLOY_DIR = Path("C:/Users/zhugu/.new-api-local")
PROBE_HOST = "100.83.32.95"  # local proxies bind the Tailscale IP (secrets.json)
PROXY_PORTS: dict[str, tuple[str, int]] = {
    "converter": (PROBE_HOST, 8787),
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
KNOWN_BROKEN_CHANNELS: set[int] = {2, 62, 63, 64, 65, 70, 71, 74}  # 70/71: 上游真死（2026-08-08 实测），与 Guardian 排除集一致

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

REQUIRED_OPTIONS: dict[str, str] = {
    "AutomaticEnableChannelEnabled": "true",
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
    """Return fallback channels that are disabled or drifted into a primary tier."""
    by_id = {channel.get("id"): channel for channel in channels}
    violations: list[str] = []
    for channel_id, expected in FALLBACK_CHANNEL_POSTURES.items():
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
    """Return intentional isolation channels that have re-entered service."""
    return [
        f"{channel['id']}:{channel.get('name', '')}"
        for channel in channels
        if channel.get("id") in KNOWN_BROKEN_CHANNELS and channel.get("status") == 1
    ]

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


def admin_auth() -> tuple[str, str]:
    """Admin API auth with token reuse.

    Every /api/user/login creates a server-side session, and this fork caps
    concurrent sessions (HTTP 409 AUTH_SESSION_LIMIT) — the smoke runs on a
    schedule, so an uncached login per run exhausts the limit within a day.
    Reuse the cached token until the server rejects it with HTTP 401. Any
    other non-200 response (403/429/5xx/network error) fails this run but keeps
    the cache, so a permission issue or transient blip doesn't burn a session.
    """
    try:
        cached = read_json(TOKEN_CACHE)
        token, user_id = cached["token"], str(cached.get("user_id") or "1")
    except (OSError, ValueError, KeyError):
        token = ""
    if token:
        status, _ = http_json(
            f"{NEWAPI_BASE}/api/channel/?p=0&page_size=1",
            headers={"Authorization": f"Bearer {token}", "New-Api-User": user_id},
        )
        if status == 200:
            return token, user_id
        if status != 401:
            # 只对确定性鉴权失效(401)重新登录。403 是权限问题（重登无用），
            # 429/5xx/抖动保留缓存——否则每次限流都会新建持久化 session，
            # 打满 AUTH_SESSION_LIMIT。实测本 fork token 过期返回 401。
            raise RuntimeError(f"cached token check returned HTTP {status}; cache kept")
    creds = read_json(DEPLOY_DIR / "admin-credentials.json")
    _, login = http_json(
        f"{NEWAPI_BASE}/api/user/login", method="POST",
        body={"username": creds["username"], "password": creds["password"]},
    )
    if not (login.get("data") or {}).get("access_token"):
        raise RuntimeError(f"login failed: {str(login)[:160]}")
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
            auto_disabled = [f"{c['id']}:{c['name']}" for c in items if c.get("status") == 3]
            unexpected = [c for c in auto_disabled if int(c.split(":")[0]) not in KNOWN_BROKEN_CHANNELS]
            known = [c for c in auto_disabled if int(c.split(":")[0]) in KNOWN_BROKEN_CHANNELS]
            enabled = sum(1 for c in items if c.get("status") == 1)
            check("channels", not unexpected,
                  f"total={len(items)} enabled={enabled} auto_disabled={unexpected or 'none'}"
                  + (f" known_broken={known}" if known else ""))
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
    except Exception as e:  # noqa: BLE001
        check("channels", False, f"admin api error: {e}")

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
