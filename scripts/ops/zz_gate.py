#!/usr/bin/env python3
"""zzzcoding claude-opus-5 pool auto-gate for NewAPI ch123 (primary tier).

zzzcoding's claude group is a Claude-subscription account pool: accounts have
usage windows, so the pool oscillates between 200 (has accounts) and 503
"No available accounts" (exhausted). This probe tracks the real pool state and
gates ch123 accordingly:

  pool UP   (200)                  -> enable ch123 (status=1), it is p60/w10
                                      = primary above the p50 relay mains.
  pool DOWN (503 no-accounts)      -> disable ch123 (status=2, manual) so real
                                      traffic never wastes a retry on it.
  other errors (DNS/TLS/timeout)   -> inconclusive, keep current gate state.

Anti-flap: switch only after 2 consecutive probes in the new state, with a
minimum dwell of MIN_DWELL_S between flips. Manual status=2 is not revived by
the guardian (guardian only touches status=3 auto-bans).

Key source: ZZ_KEY env var, else ~/.claude/zzzcoding-settings.json (not in repo).
Admin token: ~/.omp/guardian/secrets.json. DB: ~/.new-api-local/new-api.db.
State: ~/.omp/zz_gate_state.json. Log: ~/.omp/zz_gate.log + stdout.

Usage:
  python3 zz_gate.py           # run the gate loop (single instance via lockfile)
  python3 zz_gate.py --once    # one probe + decision print, no state change (dry)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

CHANNEL_ID = 123
PROBE_URL = "https://api.zzzcoding.org/v1/messages"
NEWAPI = "http://127.0.0.1:3002"
INTERVAL_S = 60           # probe every 60s — availability windows are sub-minute
UP_AFTER = 1              # enable on first up-probe to catch transient windows
DOWN_AFTER = 2            # disable only after 2 consecutive downs (anti-flap)
LOCK = os.path.expanduser("~/.omp/zz_gate.lock")
STATE = os.path.expanduser("~/.omp/zz_gate_state.json")
LOG = os.path.expanduser("~/.omp/zz_gate.log")
MIN_DWELL_S = 60          # min seconds between flips (one cadence tick)
SETTINGS = os.path.expanduser("~/.claude/zzzcoding-settings.json")
SECRETS = os.path.expanduser("~/.omp/guardian/secrets.json")
DB = os.path.expanduser("~/.new-api-local/new-api.db")


def pid_alive(pid: int) -> bool:
    """Non-destructive liveness check. On Windows os.kill(pid, 0) maps to
    TerminateProcess — never use it as a probe. tasklist output is GBK on
    zh-CN locales; decode binary with replacement."""
    if os.name == "nt":
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True)
        out = (r.stdout or b"").decode("gbk", "replace")
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except (ValueError, OSError):
        return False


def load_key() -> str:
    if os.environ.get("ZZ_KEY"):
        return os.environ["ZZ_KEY"].strip()
    with open(SETTINGS, encoding="utf-8") as f:
        return json.load(f)["env"]["ANTHROPIC_API_KEY"]


def load_token() -> str:
    d = json.load(open(SECRETS, encoding="utf-8"))
    return d.get("newapi_token") or d.get("newapi_user")


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def toast(title: str, msg: str) -> None:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip(6000, '{title}', '{msg}', [System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Seconds 7;$n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log(f"toast failed: {e}")
def probe(key: str):
    """-> 'up' | 'down' | 'unknown'. Full claude-code request profile so the
    probe sees exactly what a real claude client would."""
    body = json.dumps({
        "model": "claude-opus-5", "max_tokens": 16, "stream": True,
        "system": [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for software engineering."}],
        "metadata": {"user_id": "user_probe_account__session_zz-gate"},
        "messages": [{"role": "user", "content": "pong"}],
    }).encode()
    req = urllib.request.Request(PROBE_URL, data=body, method="POST")
    for k, v in {
        "x-api-key": key,
        "authorization": f"Bearer {key}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "claude-cli/2.1.251 (external, cli)",
        "x-app": "cli",
        "anthropic-beta": "claude-code-20250219,interleaved-thinking-2025-05-14,fine-grained-tool-streaming-2025-05-14,context-1m-2025-08-07",
        "x-stainless-arch": "x64", "x-stainless-lang": "js",
        "x-stainless-os": "Windows", "x-stainless-package-version": "0.68.0",
        "x-stainless-runtime": "node", "x-stainless-runtime-version": "v22.14.0",
    }.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            r.read()
            return "up", f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "ignore")
        if e.code == 503 and "No available accounts" in text:
            return "down", f"HTTP {e.code} no-accounts"
        return "unknown", f"HTTP {e.code} {text[:80]}"
    except Exception as e:
        return "unknown", f"{type(e).__name__} {e}"


def db_status() -> int:
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT status FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()
    con.close()
    return row[0] if row else -1


def db_remark() -> str:
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT COALESCE(remark,'') FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()
    con.close()
    return row[0] if row else ""


def api_noop(token: str) -> bool:
    """Fire a channel API event so the sync goroutine re-derives abilities."""
    payload = {"id": CHANNEL_ID, "remark": db_remark()}
    req = urllib.request.Request(
        f"{NEWAPI}/api/channel/", data=json.dumps(payload).encode(), method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()).get("success", False)
    except Exception:
        return False


def set_enabled(enabled: bool, token: str) -> bool:
    """status=1 (enable) / status=2 (manual disable) + re-derive abilities."""
    import sqlite3
    target = 1 if enabled else 2
    con = sqlite3.connect(DB)
    con.execute("UPDATE channels SET status=? WHERE id=?", (target, CHANNEL_ID))
    con.commit()
    con.close()
    api_noop(token)
    time.sleep(6)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ch = con.execute("SELECT status FROM channels WHERE id=?", (CHANNEL_ID,)).fetchone()
    ab = con.execute(
        "SELECT enabled FROM abilities WHERE channel_id=? AND model='claude-opus-5'",
        (CHANNEL_ID,)).fetchone()
    con.close()
    ok = ch and ch[0] == target and ab and ab[0] == (1 if enabled else 0)
    log(f"set_enabled({enabled}) -> status={ch and ch[0]} ability={ab and ab[0]} ok={ok}")
    return bool(ok)


def main() -> int:
    dry = "--once" in sys.argv
    if not dry:
        if os.path.exists(LOCK):
            try:
                pid = int(open(LOCK).read().strip())
            except (ValueError, OSError):
                pid = None
            if pid is not None and pid_alive(pid):
                print(f"already running pid={pid}", file=sys.stderr)
                return 1
        with open(LOCK, "w") as f:
            f.write(str(os.getpid()))

    key = load_key()
    token = load_token()
    log(f"gate start (dry={dry}) ch{CHANNEL_ID} primary-p60w10")

    streak_kind = None
    streak = 0
    last_flip = 0.0
    try:
        while True:
            try:
                state, detail = probe(key)
                if state == streak_kind:
                    streak += 1
                else:
                    streak_kind, streak = state, 1
                currently = db_status() == 1
                decision = "hold"
                now = time.time()
                if not dry and now - last_flip >= MIN_DWELL_S:
                    ready = (state == "up" and streak >= UP_AFTER) or \
                            (state == "down" and streak >= DOWN_AFTER)
                else:
                    ready = False
                if ready:
                    if state == "up" and not currently:
                        if set_enabled(True, token):
                            decision = "ENABLED"
                            last_flip = now
                            toast("zzzcoding Opus ONLINE",
                                  "Full-capability pool recovered; ch123 primary enabled.")
                    elif state == "down" and currently:
                        if set_enabled(False, token):
                            decision = "DISABLED"
                            last_flip = now
                            toast("zzzcoding pool EMPTY",
                                  "ch123 disabled; relay mains (p50) serving.")
                record = {
                    "t": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "probe": state, "detail": detail,
                    "streak": streak, "ch123_enabled": db_status() == 1,
                    "decision": decision,
                }
                with open(STATE, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False)
                log(f"probe={state} streak={streak} enabled={record['ch123_enabled']} {decision} {detail}")
                if dry:
                    return 0
            except Exception as e:
                log(f"loop error (continuing): {type(e).__name__} {e}")
            time.sleep(INTERVAL_S)
    finally:
        if not dry and os.path.exists(LOCK):
            os.remove(LOCK)


if __name__ == "__main__":
    raise SystemExit(main())
