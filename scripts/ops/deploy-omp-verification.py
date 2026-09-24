"""Add OMP task verification and GitNexus MCP with verified, drift-safe rollback."""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
import time
from pathlib import Path

common = importlib.import_module("deploy-stability-scripts")
digest, replace_verified = common.digest, common.replace_verified
REPO = Path(__file__).resolve().parents[2]
AGENT = Path.home() / ".omp/agent"
FILES = {
    "omp-task-verification.js": "extensions/omp-task-verification.js",
    "task-verification-policy.json": "task-verification-policy.json",
    "mcp.json": "mcp.json",
}


def rollback(manifest_path: Path, agent: Path = AGENT) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if Path(manifest["agent"]).resolve() != agent.resolve():
        raise RuntimeError("Rollback agent mismatch")
    if len(manifest["files"]) != len(FILES) or {x["name"] for x in manifest["files"]} != set(FILES):
        raise RuntimeError("Unexpected rollback file set")
    for item in manifest["files"]:
        path = agent / FILES[item["name"]]
        if (digest(path) if path.exists() else None) not in (item["before"], item["after"]):
            raise RuntimeError("Runtime drift; refusing rollback overwrite")
        if item["before"] is not None:
            old = manifest_path.parent / item["name"]
            if digest(old) != item["before"] or old.stat().st_size != item["backup_size"]:
                raise RuntimeError("Backup mismatch")
    for item in reversed(manifest["files"]):
        path = agent / FILES[item["name"]]
        if item["before"] is None:
            path.unlink(missing_ok=True)
        else:
            replace_verified(manifest_path.parent / item["name"], path, item["before"])


def deploy(baseline: Path, node: Path, python: Path, gitnexus: Path,
           agent: Path = AGENT, repository: Path = REPO) -> Path:
    source = REPO / "scripts/ops"
    inspected = json.loads((baseline / "baseline.json").read_text(encoding="utf-8"))
    # Only metadata is logged; MCP backups contain credentials and stay local.
    for name in ("mcp.json", "config.yml", "models.yml"):
        if digest(agent / name) != inspected[name]["sha256"]:
            raise RuntimeError("Unreviewed baseline drift")
    if digest(baseline / "mcp.json") != inspected["mcp.json"]["sha256"]:
        raise RuntimeError("Inspected backup mismatch")
    for name in FILES:
        if name != "mcp.json" and (agent / FILES[name]).exists():
            raise RuntimeError("Destination already exists; review before redeploy")
    for executable in (node, python, gitnexus):
        if not executable.is_absolute() or not executable.is_file():
            raise RuntimeError("Absolute executable or GitNexus CLI unavailable")
    dependency = "omp-model-routing-observability.js"
    if (agent / "extensions" / dependency).read_bytes().replace(b"\r\n", b"\n") != (source / dependency).read_bytes().replace(b"\r\n", b"\n"):
        raise RuntimeError("Routing dependency drift")
    existing = json.loads((agent / "mcp.json").read_text(encoding="utf-8"))
    if not isinstance(existing.get("mcpServers"), dict) or "gitnexus" in existing["mcpServers"]:
        raise RuntimeError("Unexpected MCP server configuration")
    if "gitnexus" in existing.get("disabledServers", []):
        raise RuntimeError("GitNexus was deliberately disabled")
    existing["mcpServers"]["gitnexus"] = {"command": str(node), "args": [str(gitnexus), "mcp"]}
    policy = json.loads((source / "task-verification-policy.json").read_text(encoding="utf-8"))
    commands = {"@node": str(node), "@python": str(python)}
    for project in policy["projects"]:
        if project["root"] != "@repository":
            raise RuntimeError("Unexpected policy root")
        project["root"] = str(repository.resolve())
        for check in project["checks"]:
            check["command"] = commands[check["command"]]
    backup = agent / "extension-backups" / f"verification-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
    prepared = backup / "prepared"
    prepared.mkdir(parents=True)
    shutil.copy2(source / "omp-task-verification.js", prepared / "omp-task-verification.js")
    for name, data in (("mcp.json", existing), ("task-verification-policy.json", policy)):
        (prepared / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    manifest = {"agent": str(agent.resolve()), "files": [], "restart_performed": False,
                "config_hashes": {name: inspected[name]["sha256"] for name in ("config.yml", "models.yml")},
                "existing_extensions": {str(p.relative_to(agent)): digest(p) for p in (agent / "extensions").rglob("*.js")}}
    for name, relative in FILES.items():
        path, old = agent / relative, backup / name
        before = digest(path) if path.exists() else None
        if name == "mcp.json" and before != inspected[name]["sha256"]:
            raise RuntimeError("MCP drift during preparation")
        if name != "mcp.json" and before is not None:
            raise RuntimeError("Destination appeared during preparation")
        if before is not None:
            shutil.copy2(path, old)
            if digest(old) != before or old.stat().st_size != path.stat().st_size:
                raise RuntimeError("Backup verification failed")
        manifest["files"].append({"name": name, "before": before, "after": digest(prepared / name),
                                  "backup_size": old.stat().st_size if before else 0,
                                  "backup_mtime": old.stat().st_mtime if before else None})
    manifest_path = backup / "deployment.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name in ("deploy-omp-verification.py", "deploy-stability-scripts.py"):
        shutil.copy2(source / name, backup / name)
    (backup / "rollback.cmd").write_text(
        '@echo off\r\npython3 "%~dp0deploy-omp-verification.py" --rollback "%~dp0deployment.json"\r\n', encoding="ascii")
    try:
        for item in manifest["files"]:
            destination = agent / FILES[item["name"]]
            if (digest(destination) if destination.exists() else None) != item["before"]:
                raise RuntimeError("Production drift after backup")
            replace_verified(prepared / item["name"], destination, item["after"])
        for name, expected in {**manifest["config_hashes"], **manifest["existing_extensions"]}.items():
            if digest(agent / name) != expected:
                raise RuntimeError("Unrelated configuration or extension drift")
    except Exception:
        rollback(manifest_path, agent)
        raise
    return manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if args.rollback:
        rollback(args.rollback)
        print("Rollback verified; reload OMP. Repository indexes were preserved.")
    else:
        if not args.baseline:
            parser.error("--baseline is required")
        node = shutil.which("node")
        if not node:
            parser.error("node unavailable")
        cli = Path.home() / "AppData/Roaming/npm/node_modules/gitnexus/dist/cli/index.js"
        print(json.dumps({"manifest": str(deploy(args.baseline, Path(node), Path(sys.executable), cli)),
                          "files": len(FILES), "restart_performed": False}))
