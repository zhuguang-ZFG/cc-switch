#!/usr/bin/env python3
"""new-api 渠道健康检查 v5.1 (dx-hardened)

Scope (2026-07-26 / v5.1):
- ONLY probe OPUS_POOL (#9/#10/#20/#60)
- Probe model prefers claude-opus-5[1M] (matches live traffic)
- No HTTP→HTTPS sing-box fallback (was false 400 noise)
- Community transient (no available accounts / bare 503) → log only, do NOT fail-count toward disable
- Hard fails → count + disable at threshold (Opus only); no auto-reactivate
- NO DISABLE-QUOTA shortcut
- Slow probe → Telegram alert only (priority/weight left to analyze_newapi_dx)
- set_channel_status does NOT blanket-toggle abilities
- PINNED_EXCLUDE never touched

状态: /opt/new-api/health_state.json
cron: */30 flock health_check.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

import sys as _sys

_sys.path.insert(0, "/opt/new-api")
try:
    from tg_notify import send_tg
except Exception:

    def send_tg(t, buttons=None, markdown=True, chat_id=None):
        return False


DB = "/opt/new-api/data/one-api.db"
STATE = "/opt/new-api/health_state.json"
API = "http://127.0.0.1:3000"


def _secret(k, default=""):
    try:
        with open("/opt/new-api/secrets.json") as f:
            return json.load(f).get(k, default)
    except Exception:
        return default


ADMIN_USER = _secret("admin_user", "admin")
ADMIN_PASS = _secret("admin_pass")
# Higher bar: community flaps are normal; only hard consecutive fails disable.
FAIL_THRESHOLD = 12
# Never probe / mutate these (pinned or parked). Name kept for older STATUS notes.
PINNED_EXCLUDE = {11, 75, 77, 78, 79, 80, 81, 118, 119, 120}
AUTO_REACTIVATE_EXCLUDE = PINNED_EXCLUDE  # alias
TIMEOUT = 45
SLOW_MS = 20000
SLOW_HITS_NEED = 2
# Opus hot pool only (channel_id -> label priority for messages)
OPUS_POOL = {9: 45, 10: 45, 20: 45, 60: 45}
PREFERRED_PROBE_MODELS = (
    "claude-opus-5[1M]",
    "claude-opus-5",
    "claude-opus-4-8[1M]",
    "claude-opus-4-8",
)


def sh(cmd, timeout=TIMEOUT + 5):
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=isinstance(cmd, str),
    )
    return r.stdout


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"fails": {}, "disabled_by_script": [], "slow_hits": {}}


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, STATE)


def _http_request(
    method: str,
    url: str,
    headers: Optional[dict] = None,
    body: Optional[bytes] = None,
    timeout: int = 15,
    proxy: Optional[str] = None,
) -> Tuple[int, str]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = (
        urllib.request.build_opener(*handlers)
        if handlers
        else urllib.request.build_opener()
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def http_json(method, path, body=None, cookie_header=None):
    headers = {"Content-Type": "application/json", "New-Api-User": "1"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    code, text = _http_request(method, f"{API}{path}", headers=headers, body=data, timeout=15)
    try:
        return json.loads(text or "{}")
    except Exception:
        return {"_http": code, "_raw": (text or "")[:200]}


def login_cookie() -> str:
    payload = json.dumps(
        {"username": ADMIN_USER, "password": ADMIN_PASS}, ensure_ascii=False
    ).encode()
    req = urllib.request.Request(
        f"{API}/api/user/login",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.headers.get_all("Set-Cookie") or []
            return "; ".join(c.split(";", 1)[0] for c in raw)
    except Exception as e:
        print("login error:", type(e).__name__)
        return ""


def set_channel_status(cid, status, cookie_header):
    """Flip channel status via API only — do NOT blanket-toggle abilities."""
    r = http_json("POST", f"/api/channel/{cid}/status", {"status": status}, cookie_header)
    return bool(r.get("success") or r.get("data") is True)


def purge_affinity_for_channel(cid):
    try:
        keys = sh(
            ["redis-cli", "--scan", "--pattern", "new-api:channel_affinity:v1:*"]
        ).splitlines()
        n = 0
        for k in keys:
            if sh(["redis-cli", "get", k]).strip() == str(cid):
                sh(["redis-cli", "del", k])
                n += 1
        if n:
            print(f"AFFINITY-PURGE channel #{cid}: {n} keys")
    except Exception as e:
        print("affinity purge error:", e)


def parse_headers(hdr_override):
    headers = {"Content-Type": "application/json", "User-Agent": "new-api-health/1.0"}
    if hdr_override:
        try:
            for k, v in json.loads(hdr_override).items():
                headers[k] = v
        except Exception:
            pass
    return headers


def probe_once(base, key, model, ctype, hdr_override, proxy=None, timeout=TIMEOUT):
    headers = parse_headers(hdr_override)
    # multi-key channels store keys one per line; probe with the first
    key = (key or "").splitlines()[0].strip()
    headers["Authorization"] = f"Bearer {key}"
    base = base.rstrip("/")
    t0 = time.time()
    if ctype == 14:
        url = base + "/v1/messages"
        body = json.dumps(
            {
                "model": model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode()
        headers.setdefault("anthropic-version", "2023-06-01")
    else:
        url = base + "/v1/chat/completions"
        body = json.dumps(
            {
                "model": model,
                "max_tokens": 16,
                "stream": False,
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode()
    try:
        code, out = _http_request(
            "POST", url, headers=headers, body=body, timeout=timeout, proxy=proxy
        )
        elapsed_ms = (time.time() - t0) * 1000
        if ctype == 14:
            ok = '"stop_reason"' in out or ('"type":"message"' in out and '"content"' in out)
        else:
            ok = '"choices"' in out and code == 200
        return ok, elapsed_ms, code, out[:2048]
    except Exception as e:
        return False, (time.time() - t0) * 1000, 0, str(e)[:200]


def channel_alive(base, key, model, ctype, hdr_override, setting_proxy):
    """Return (ok, lat_ms, last_http_code, last_snippet).

    Proxy order: channel setting proxy (if any), then direct. No global sing-box
    fallback — HTTPS upstreams via :7890 previously produced false HTTP 400s.
    """
    last_ms = 0.0
    last_code = 0
    last_snippet = ""
    proxies = []
    if setting_proxy:
        proxies.append(setting_proxy)
    proxies.append(None)
    for px in proxies:
        ok, ms, code, snippet = probe_once(base, key, model, ctype, hdr_override, px)
        last_ms, last_code, last_snippet = ms, code, snippet
        if ok:
            return True, ms, code, snippet
        print(f"  probe fail proxy={px!r} http={code} ms={ms:.0f} {snippet[:120]!r}")
    return False, last_ms, last_code, last_snippet


def is_community_transient(code: int, snippet: str) -> bool:
    """公益站账号池空/过载：预期噪声，不计入禁用阈值。"""
    s = (snippet or "").lower()
    if "no available accounts" in s:
        return True
    if "auth_unavailable" in s or "no auth available" in s:
        return True
    if code == 503 and (
        "no available" in s or "overloaded" in s or "capacity" in s or not s.strip()
    ):
        return True
    return False


def pick_probe_model(models: str) -> str:
    """Prefer live Opus 5 ids; fall back to any opus; else first model."""
    parts = [m.strip() for m in (models or "").split(",") if m.strip()]
    if not parts:
        return ""
    lower_map = {m.lower(): m for m in parts}
    for pref in PREFERRED_PROBE_MODELS:
        if pref.lower() in lower_map:
            return lower_map[pref.lower()]
    for m in parts:
        ml = m.lower()
        if "opus-5" in ml or "opus_5" in ml:
            return m
    for m in parts:
        if "opus" in m.lower():
            return m
    return parts[0]


def main():
    state = load_state()
    fails = state.setdefault("fails", {})
    slow_hits = state.setdefault("slow_hits", {})
    alerted = state.setdefault("alerted", {})
    disabled_by_script = set(state.get("disabled_by_script") or [])

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, name, base_url, key, models, status, header_override, type, setting, priority "
        "FROM channels WHERE id IN (%s)"
        % ",".join(str(i) for i in sorted(OPUS_POOL))
    ).fetchall()
    conn.close()

    cookie = login_cookie()
    changed = False

    for cid, name, base, key, models, status, hdr, ctype, setting, priority in rows:
        if cid in PINNED_EXCLUDE:
            fails[str(cid)] = 0
            continue

        model = pick_probe_model(models)
        if not model:
            print(f"SKIP #{cid} {name}: no models")
            continue

        setting_proxy = None
        try:
            setting_proxy = (json.loads(setting) or {}).get("proxy") if setting else None
        except Exception:
            pass

        print(f"CHECK #{cid} {name} model={model} status={status}")
        ok, lat_ms, code, snippet = channel_alive(
            base, key, model, ctype, hdr, setting_proxy
        )

        if ok:
            fails[str(cid)] = 0
            if status == 1:
                # alive again: drop any stale script-disable marker
                disabled_by_script.discard(cid)
            alerted.pop(str(cid), None)
            if lat_ms > SLOW_MS:
                slow_hits[str(cid)] = slow_hits.get(str(cid), 0) + 1
                if slow_hits[str(cid)] >= SLOW_HITS_NEED:
                    print(f"SLOW-ALERT #{cid} {name} lat={lat_ms:.0f}ms hits={slow_hits[str(cid)]}")
                    send_tg(
                        f"🐌 Opus慢探针 #{cid} {name}（{lat_ms/1000:.0f}s×{slow_hits[str(cid)]}；"
                        f"不改 priority，交给 DX analyze）"
                    )
                    # do not reset hits so we don't spam every cycle — reset after alert
                    slow_hits[str(cid)] = 0
            else:
                slow_hits[str(cid)] = 0

            # Intentionally NO auto-reactivate (manual / TG button only).
            if status != 1 and cid in disabled_by_script:
                print(f"STILL-DISABLED #{cid} {name} (probe ok; no auto-reactivate)")
            continue

        # fail path
        if status != 1:
            print(f"FAIL-IGNORE #{cid} {name} (already status={status})")
            continue

        if is_community_transient(code, snippet):
            # 公益站账号池空/过载：不累计禁用计数（仍打印，便于对照）
            print(
                f"FAIL-TRANSIENT #{cid} {name} http={code} "
                f"(no fail-count; community flap) last_ms={lat_ms:.0f}"
            )
            continue

        fails[str(cid)] = fails.get(str(cid), 0) + 1
        print(f"FAIL #{cid} {name} fails={fails[str(cid)]} last_ms={lat_ms:.0f}")
        if fails[str(cid)] >= FAIL_THRESHOLD:
            if cookie and set_channel_status(cid, 2, cookie):
                disabled_by_script.add(cid)
                alerted.pop(str(cid), None)
                changed = True
                print(f"DISABLE #{cid} {name} (fails={fails[str(cid)]})")
                purge_affinity_for_channel(cid)
                send_tg(
                    f"⛔ Opus渠道禁用 #{cid} {name}（连续失败 {fails[str(cid)]} 次）",
                    buttons=[
                        [{"text": f"复活 #{cid}", "callback_data": f"enable:{cid}"}],
                        [{"text": "📊 渠道状态", "callback_data": "status"}],
                    ],
                )
            elif not alerted.get(str(cid)):
                if send_tg(
                    f"⚠️ Opus渠道连续失败 #{cid} {name}×{fails[str(cid)]}（禁用 API 未成功）"
                ) is not False:
                    alerted[str(cid)] = True

    # Drop stale fail counters for channels we no longer manage
    managed = {str(i) for i in OPUS_POOL}
    for k in list(fails.keys()):
        if k not in managed:
            fails.pop(k, None)

    state["fails"] = fails
    state["slow_hits"] = slow_hits
    state["alerted"] = alerted
    state["disabled_by_script"] = sorted(disabled_by_script)
    # clear legacy degrade keys noise
    state.pop("degraded", None)
    state.pop("degrade_fails", None)
    save_state(state)
    print("changed:", changed, "| watching:", sorted(OPUS_POOL))


if __name__ == "__main__":
    main()
