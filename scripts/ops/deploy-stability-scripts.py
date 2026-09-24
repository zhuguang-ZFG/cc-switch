#!/usr/bin/env python3
"""Deploy the reviewed OMP/Guardian stability scripts, with verified rollback.

Does not restart processes. Use the recorded supervisor PID and canonical
startup shortcut for activation; OMP extensions activate in new sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
RUNTIME = Path.home() / ".omp"
FILES = {
    "proxies-supervisor.py": "guardian/proxies-supervisor.py",
    "anyrouter-window-canary.py": "guardian/anyrouter-window-canary.py",
    "omp-model-routing-observability.js": "agent/extensions/omp-model-routing-observability.js",
}
BASE = "0ebb556a^"


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


def rollback(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        destination = RUNTIME / FILES[item["name"]]
        backup = manifest_path.parent / item["name"]
        if digest(backup) != item["before"]:
            raise RuntimeError("rollback backup hash mismatch")
        if digest(destination) not in {item["before"], item["after"]}:
            raise RuntimeError("runtime drift; refusing rollback overwrite")
    for item in manifest["files"]:
        replace_verified(manifest_path.parent / item["name"],
                         RUNTIME / FILES[item["name"]], item["before"])
    print("Rollback files verified. Restart only the supervisor; reload OMP sessions as needed.")


def deploy() -> None:
    # Compare against the inspected production base before writing any file.
    for name, relative in FILES.items():
        original = subprocess.check_output(
            ["git", "show", f"{BASE}:scripts/ops/{name}"], cwd=REPO)
        if (RUNTIME / relative).read_bytes().replace(b"\r\n", b"\n") != original.replace(b"\r\n", b"\n"):
            raise RuntimeError(f"unreviewed production drift: {name}")
    backup = RUNTIME / "guardian/backups" / f"stability-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    backup.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": time.time(), "files": [], "restart_performed": False}
    for name, relative in FILES.items():
        destination = RUNTIME / relative
        previous = backup / name
        shutil.copy2(destination, previous)
        before = digest(destination)
        if digest(previous) != before or previous.stat().st_size != destination.stat().st_size:
            raise RuntimeError("backup verification failed")
        manifest["files"].append({"name": name, "before": before,
                                  "after": digest(REPO / "scripts/ops" / name),
                                  "backup_size": previous.stat().st_size,
                                  "backup_mtime": previous.stat().st_mtime})
    # Configuration is never copied or printed; hashes detect incidental drift.
    manifest["configuration_hashes"] = {
        relative: digest(RUNTIME / relative) for relative in
        ("agent/config.yml", "agent/models.yml", "guardian/secrets.json")}
    manifest["runtime_status"] = {}
    for filename in ("supervisor-status.json", "heartbeat.json"):
        status = json.loads((RUNTIME / "guardian" / filename).read_text(encoding="utf-8"))
        manifest["runtime_status"][filename] = {
            key: status.get(key) for key in
            ("pid", "ts", "services", "restarts_today", "last_backup")}
    manifest["log_metadata"] = {
        name: {"size": (RUNTIME / "guardian" / name).stat().st_size,
               "mtime": (RUNTIME / "guardian" / name).stat().st_mtime}
        for name in ("guardian.log", "proxies-supervisor.log", "watchdog.log")}
    manifest_path = backup / "deployment.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # A self-contained rollback entry point remains available with the backup.
    shutil.copy2(Path(__file__), backup / "deploy-stability-scripts.py")
    (backup / "rollback.cmd").write_text(
        '@echo off\r\npython "%~dp0deploy-stability-scripts.py" --rollback "%~dp0deployment.json"\r\n',
        encoding="ascii")
    try:
        for item in manifest["files"]:
            replace_verified(REPO / "scripts/ops" / item["name"],
                             RUNTIME / FILES[item["name"]], item["after"])
        for relative, expected in manifest["configuration_hashes"].items():
            if digest(RUNTIME / relative) != expected:
                raise RuntimeError("configuration changed during deployment")
    except Exception:
        rollback(manifest_path)
        raise
    print(json.dumps({"manifest": str(manifest_path), "verified_files": len(FILES),
                      "restart_performed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        rollback(args.rollback)
    else:
        deploy()
