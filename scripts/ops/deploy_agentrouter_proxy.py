#!/usr/bin/env python3
"""Deploy the reviewed `agentrouter-proxy.py` mirror to the live bridge, with verified rollback.

Why a deploy script (2026-10-08): the bridge is the single live path behind NewAPI ch180
(agentrouter `deepseek-v4-flash` main leg — the only channel pointed at 8788). It lived
only outside the repo, so incident reviews could not distinguish reviewed code from running
code, and the 2026-10-08 fixes (payload sanitizer, key×gateway retry layering, exhaustion
503, keyside attribution logging) had no version history or rollback path.

Contract:
  scripts/ops/agentrouter-proxy.py                                  reviewable source of truth
  ~/.kimi-code/proxies/agentrouter-proxy/agentrouter-proxy.py       live copy, byte identical
  scripts/ops/test_mirror_sync.py                                   byte-identity gate
  scripts/ops/test_agentrouter_exhaustion_status.py                 behaviour gate (503)

Modes:
  --check                  compare repo vs live SHA256; exit 1 on drift (no writes)
  --apply [--expect-before SHA]
                           refuse unreviewed live drift when --expect-before is given, back up,
                           install atomically with hash verification, restart (kill the 8788
                           listener; Guardian respawns within HEALTH_CHECK_INTERVAL=15s, with a
                           direct-spawn fallback), then verify /health and one production-shaped
                           chat request through the bridge
  --rollback MANIFEST      restore the recorded pre-image after hash verification
  --restart                restart the bridge and verify /health + chat probe (no file writes;
                           use after a failed/interrupted --apply, or to force a reload)

Secrets: `keys.json` and the proxy auth key are never printed or copied into the repo; only
hashes of `keys.json` are recorded in the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
SOURCE = REPO / "agentrouter-proxy.py"
LIVE_DIR = Path.home() / ".kimi-code" / "proxies" / "agentrouter-proxy"
LIVE = LIVE_DIR / "agentrouter-proxy.py"
KEYS = LIVE_DIR / "keys.json"
BACKUP_DIR = LIVE_DIR / "backups"
SECRETS = Path.home() / ".omp" / "guardian" / "secrets.json"
PORT = 8788
HEALTH_TIMEOUT_S = 90
PYTHON = "C:/Users/zhugu/scoop/apps/python313/current/python.exe"
PROBE_MODEL = "deepseek-v4-flash"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_verified(source: Path, destination: Path, expected: str) -> None:
    temporary = destination.with_name(destination.name + f".deploy-{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != expected:
            raise RuntimeError("staging hash mismatch")
        os.replace(temporary, destination)
        if digest(destination) != expected:
            raise RuntimeError("installed hash mismatch")
    finally:
        temporary.unlink(missing_ok=True)


def listener_pids(port: int) -> list[str]:
    # netstat 走 Windows OEM 代码页（非 UTF-8），必须容错解码，否则 None/异常。
    out = subprocess.run(["netstat", "-ano"], capture_output=True).stdout or b""
    pids = []
    for line in out.decode("utf-8", "replace").splitlines():
        if f":{port} " in line and "LISTENING" in line:
            pid = line.split()[-1]
            if pid not in pids:
                pids.append(pid)
    return pids


def secret(name: str, default: str = "") -> str:
    try:
        return str(json.loads(SECRETS.read_text(encoding="utf-8")).get(name, default))
    except Exception:
        return default


def pooled_keys() -> list[str]:
    try:
        data = json.loads(KEYS.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [k.strip() for k in (data.get("keys") or []) if isinstance(k, str) and k.strip()]


def wait_health(timeout: float = HEALTH_TIMEOUT_S) -> tuple[bool, str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                break
        time.sleep(1.0)
    else:
        return False, f"port {PORT} not listening after {timeout:.0f}s"
    auth = secret("agentrouter_proxy_key")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/health",
                                 headers={"Authorization": "Bearer " + auth} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
        return True, json.dumps(body, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return False, f"health probe failed: {e}"


def spawn_direct() -> None:
    """Guardian-down fallback: same command line Guardian uses for the bridge."""
    env = {**os.environ, "AGENTROUTER_PROXY_KEY": secret("agentrouter_proxy_key")}
    subprocess.Popen(
        [PYTHON, str(LIVE), "--host", secret("local_proxy_bind_host", "0.0.0.0"),
         "--port", str(PORT), "--log", "proxy.log"],
        cwd=str(LIVE_DIR), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def restart_bridge() -> tuple[bool, str]:
    pids = listener_pids(PORT)
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    started = time.time()
    ok, detail = wait_health()
    if not ok:
        spawn_direct()
        ok, detail = wait_health()
        detail = f"guardian respawn timed out ({time.time() - started:.0f}s); direct spawn -> {detail}"
    return ok, detail


def probe_chat() -> tuple[bool, str]:
    # 桥的入站鉴权是 secrets.json 的 agentrouter_proxy_key（env AGENTROUTER_PROXY_KEY），
    # 不是 keys.json 里的上游 key。
    auth = secret("agentrouter_proxy_key") or (pooled_keys() or [""])[0]
    if not auth:
        return False, "no proxy auth key available for the post-deploy probe"
    payload = json.dumps({"model": PROBE_MODEL, "max_tokens": 64, "stream": False,
                          "messages": [{"role": "user", "content": "Reply with exactly: DEPLOY_OK"}]}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + auth})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read())
        text = (body["choices"][0]["message"].get("content") or "").strip()
        return True, f"HTTP 200, content={text[:40]!r}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read()[:160].decode('utf-8', 'replace')}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def check() -> int:
    if not SOURCE.is_file() or not LIVE.is_file():
        print(f"FAIL: missing source={SOURCE.is_file()} live={LIVE.is_file()}")
        return 1
    repo_hash, live_hash = digest(SOURCE), digest(LIVE)
    if repo_hash != live_hash:
        print(f"DRIFT: repo {repo_hash[:12]} != live {live_hash[:12]}\n"
              f"      run `{Path(__file__).name} --apply` after review")
        return 1
    print(f"in sync: {repo_hash[:12]} (repo == live); keys.json hash "
          f"{digest(KEYS)[:12] if KEYS.is_file() else 'n/a'} (never copied)")
    return 0


def apply(expect_before: str | None) -> int:
    if not SOURCE.is_file():
        print(f"FAIL: repo source missing: {SOURCE}")
        return 1
    if not LIVE.is_file():
        print(f"FAIL: live bridge missing: {LIVE}")
        return 1
    before = digest(LIVE)
    if expect_before and before != expect_before:
        print(f"REFUSED: live hash {before[:12]} != --expect-before {expect_before[:12]} "
              f"(unreviewed production drift)")
        return 1
    after = digest(SOURCE)
    if before == after:
        print(f"already deployed: {after[:12]}")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = LIVE_DIR / f"agentrouter-proxy.py.bak-{stamp}"
    shutil.copy2(LIVE, backup)
    if digest(backup) != before or backup.stat().st_size != LIVE.stat().st_size:
        print("FAIL: backup verification failed")
        return 1

    manifest = {
        "created_at": time.time(),
        "source": str(SOURCE),
        "live": str(LIVE),
        "before": before,
        "after": after,
        "backup": str(backup),
        "keys_json_hash": digest(KEYS) if KEYS.is_file() else None,
        "restart_performed": False,
        "post_health": None,
        "post_chat_probe": None,
        "note": "manifest written BEFORE install so --rollback is entrypoint-even if --apply crashes mid-restart",
    }
    manifest_path = BACKUP_DIR / f"agentrouter-proxy-deploy-{stamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    replace_verified(SOURCE, LIVE, after)
    print(f"installed {after[:12]} (backup {backup.name}, pre-image {before[:12]})")

    ok, detail = restart_bridge()
    manifest["restart_performed"] = True
    manifest["post_health"] = detail
    print(f"restart: {'OK' if ok else 'FAIL'} — {detail}")
    if ok:
        chat_ok, chat_detail = probe_chat()
        manifest["post_chat_probe"] = chat_detail
        print(f"chat probe: {'OK' if chat_ok else 'FAIL'} — {chat_detail}")
        ok = ok and chat_ok

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"manifest: {manifest_path}")
    if not ok:
        print(f"rollback: python3 {Path(__file__).name} --rollback {manifest_path}")
        return 1
    print(f"rollback entry point: --rollback {manifest_path}")
    return 0


def rollback(manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup = Path(manifest["backup"])
    if digest(backup) != manifest["before"]:
        print("FAIL: backup hash mismatch; refusing rollback")
        return 1
    if digest(LIVE) not in {manifest["before"], manifest["after"]}:
        print("FAIL: live drift since deploy; refusing rollback overwrite")
        return 1
    replace_verified(backup, LIVE, manifest["before"])
    print(f"restored {manifest['before'][:12]}")
    ok, detail = restart_bridge()
    print(f"restart: {'OK' if ok else 'FAIL'} — {detail}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="compare repo vs live (no writes)")
    group.add_argument("--apply", action="store_true", help="deploy repo source to the live bridge")
    group.add_argument("--rollback", type=Path, metavar="MANIFEST", help="restore a pre-image")
    group.add_argument("--restart", action="store_true",
                       help="restart the bridge (kill the 8788 listener; Guardian respawns) "
                            "and verify /health + the chat probe, without touching files")
    parser.add_argument("--expect-before", default=None,
                        help="with --apply: refuse unless the live hash matches (drift guard)")
    args = parser.parse_args()
    if args.check:
        return check()
    if args.apply:
        return apply(args.expect_before)
    if args.restart:
        ok, detail = restart_bridge()
        print(f"restart: {'OK' if ok else 'FAIL'} — {detail}")
        if ok:
            chat_ok, chat_detail = probe_chat()
            print(f"chat probe: {'OK' if chat_ok else 'FAIL'} — {chat_detail}")
            ok = chat_ok
        return 0 if ok else 1
    return rollback(args.rollback)


if __name__ == "__main__":
    raise SystemExit(main())
