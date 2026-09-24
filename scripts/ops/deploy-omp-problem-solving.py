"""Deploy bounded OMP problem-solving extensions; no process restart or routing changes."""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import time
from pathlib import Path

common = importlib.import_module("deploy-stability-scripts")
digest = common.digest
replace_verified = common.replace_verified
REPO = Path(__file__).resolve().parents[2]
AGENT = Path.home() / ".omp/agent"
BASE = "53d81190"
FILES = {
    "review/omp-review-guard.js": "extensions/review/omp-review-guard.js",
    "omp-sota-escalation.js": "extensions/omp-sota-escalation.js",
    "omp-problem-solving.js": "extensions/omp-problem-solving.js",
    "sota-review-policy.json": "sota-review-policy.json",
}


def rollback(manifest_path: Path, agent: Path = AGENT) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(item["name"] for item in manifest["files"]) != set(FILES):
        raise RuntimeError("Unexpected rollback file set")
    for item in manifest["files"]:
        path = agent / FILES[item["name"]]
        current = digest(path) if path.exists() else None
        if current not in (item["before"], item["after"]):
            raise RuntimeError("Runtime drift; refusing rollback overwrite")
        if item["before"] is not None and digest(manifest_path.parent / item["name"]) != item["before"]:
            raise RuntimeError("Backup mismatch")
    for item in reversed(manifest["files"]):
        path = agent / FILES[item["name"]]
        if item["before"] is None:
            path.unlink(missing_ok=True)
        else:
            replace_verified(manifest_path.parent / item["name"], path, item["before"])


def deploy(agent: Path = AGENT, source: Path = REPO / "scripts/ops", base: bytes | None = None) -> Path:
    if base is None:
        base = subprocess.check_output(["git", "show", f"{BASE}:scripts/ops/omp-sota-escalation.js"], cwd=REPO)
    for name, relative in FILES.items():
        path = agent / relative
        if name == "omp-sota-escalation.js":
            if path.read_bytes().replace(b"\r\n", b"\n") != base.replace(b"\r\n", b"\n"):
                raise RuntimeError("Unreviewed SOTA extension drift")
        elif path.exists():
            raise RuntimeError(f"Unexpected existing destination: {name}")
    # Imported routing dependency must match the reviewed repository version.
    if (agent / "extensions/omp-model-routing-observability.js").read_bytes().replace(b"\r\n", b"\n") != (source / "omp-model-routing-observability.js").read_bytes().replace(b"\r\n", b"\n"):
        raise RuntimeError("Routing dependency drift")
    backup = agent / "extension-backups" / f"problem-solving-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
    backup.mkdir(parents=True)
    manifest = {"files": [], "restart_performed": False, "config_hashes": {
        name: digest(agent / name) for name in ("models.yml", "config.yml")}}
    for name, relative in FILES.items():
        path = agent / relative
        previous = backup / name
        before = digest(path) if path.exists() else None
        if before is not None:
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, previous)
            if digest(previous) != before or previous.stat().st_size != path.stat().st_size:
                raise RuntimeError("Backup verification failed")
        manifest["files"].append({"name": name, "before": before, "after": digest(source / name),
            "backup_size": previous.stat().st_size if before else 0,
            "backup_mtime": previous.stat().st_mtime if before else None})
    manifest_path = backup / "deployment.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name in ("deploy-omp-problem-solving.py", "deploy-stability-scripts.py"):
        shutil.copy2(source / name, backup / name)
    (backup / "rollback.cmd").write_text('@echo off\r\npython3 "%~dp0deploy-omp-problem-solving.py" --rollback "%~dp0deployment.json"\r\n', encoding="ascii")
    try:
        for item in manifest["files"]:
            destination = agent / FILES[item["name"]]
            if (digest(destination) if destination.exists() else None) != item["before"]:
                raise RuntimeError("Production changed after backup")
            destination.parent.mkdir(parents=True, exist_ok=True)
            replace_verified(source / item["name"], destination, item["after"])
        if any(digest(agent / name) != expected for name, expected in manifest["config_hashes"].items()):
            raise RuntimeError("Unrelated configuration changed")
    except Exception:
        rollback(manifest_path, agent)
        raise
    return manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        rollback(args.rollback)
        print("Rollback verified; reload OMP sessions.")
    else:
        print(json.dumps({"manifest": str(deploy()), "files": len(FILES), "restart_performed": False}))
