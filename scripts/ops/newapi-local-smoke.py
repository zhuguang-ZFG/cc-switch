#!/usr/bin/env python3
"""Local NewAPI smoke check (replaces the retired VPS DX analyzer).

Checks, in order:
  1. NewAPI /api/status reachable (http://127.0.0.1:3002)
  2. Local gateway proxies listening on the Tailscale bind host (8787/8788/9457)
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
PROXY_PORTS = {"converter": 8787, "agentrouter": 8788, "atomcode": 9457}
SMOKE_MODELS = ["sensenova-6.7-flash-lite", "opencode-go"]

# Channels whose auto-disabled state is currently the CORRECT state (upstream
# confirmed broken), so their presence in status=3 must not fail the smoke.
# Remove an entry once the upstream recovers and the channel is re-enabled.
# (ch63 centos-fr-gpt recovered 2026-08-05 ~16:44 and was removed the same day.)
KNOWN_BROKEN_CHANNELS: set[int] = set()

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
    Reuse the cached token until the server rejects it, only then re-login.
    """
    try:
        cached = read_json(TOKEN_CACHE)
        token, user_id = cached["token"], str(cached.get("user_id") or "1")
        status, _ = http_json(
            f"{NEWAPI_BASE}/api/channel/?p=0&page_size=1",
            headers={"Authorization": f"Bearer {token}", "New-Api-User": user_id},
        )
        if status == 200:
            return token, user_id
    except (OSError, ValueError, KeyError):
        pass
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
    for name, port in PROXY_PORTS.items():
        try:
            with socket.create_connection((PROBE_HOST, port), timeout=3):
                check(f"proxy {name}:{port}", True)
        except OSError as e:
            check(f"proxy {name}:{port}", False, str(e))

    # 3. channel summary via admin API
    try:
        token, user_id = admin_auth()
        _, ch = http_json(
            f"{NEWAPI_BASE}/api/channel/?p=0&page_size=200",
            headers={"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)},
        )
        items = (ch.get("data") or {}).get("items") or ch.get("data") or []
        auto_disabled = [f"{c['id']}:{c['name']}" for c in items if c.get("status") == 3]
        unexpected = [c for c in auto_disabled if int(c.split(":")[0]) not in KNOWN_BROKEN_CHANNELS]
        known = [c for c in auto_disabled if int(c.split(":")[0]) in KNOWN_BROKEN_CHANNELS]
        enabled = sum(1 for c in items if c.get("status") == 1)
        check("channels", not unexpected,
              f"total={len(items)} enabled={enabled} auto_disabled={unexpected or 'none'}"
              + (f" known_broken={known}" if known else ""))
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
