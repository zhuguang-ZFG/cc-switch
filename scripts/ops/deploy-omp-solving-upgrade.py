"""Upgrade the reviewed OMP solving/verification pair, preserving verified rollback."""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import time
from pathlib import Path

common = importlib.import_module("deploy-stability-scripts")
digest, replace_verified = common.digest, common.replace_verified
REPO = Path(__file__).resolve().parents[2]
AGENT = Path.home() / ".omp/agent"
FILES = {"omp-problem-solving.js": "extensions/omp-problem-solving.js",
         "omp-task-verification.js": "extensions/omp-task-verification.js",
         "task-verification-policy.json": "task-verification-policy.json"}


def rollback(manifest_path: Path, agent: Path = AGENT) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if Path(manifest["agent"]).resolve() != agent.resolve() or len(manifest["files"]) != len(FILES) or {x["name"] for x in manifest["files"]} != set(FILES):
        raise RuntimeError("Unexpected rollback scope")
    for item in manifest["files"]:
        if digest(agent / FILES[item["name"]]) not in (item["before"], item["after"]):
            raise RuntimeError("Runtime drift; refusing overwrite")
        backup = manifest_path.parent / item["name"]
        if digest(backup) != item["before"] or backup.stat().st_size != item["backup_size"]:
            raise RuntimeError("Backup mismatch")
    for item in reversed(manifest["files"]):
        replace_verified(manifest_path.parent / item["name"], agent / FILES[item["name"]], item["before"])


def deploy(previous_manifest: Path, agent: Path = AGENT, source: Path = REPO / "scripts/ops", hutuji_root: Path | None = None) -> Path:
    previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
    if Path(previous["agent"]).resolve() != agent.resolve():
        raise RuntimeError("Baseline agent mismatch")
    expected = {item["name"]: item["after"] for item in previous["files"]}
    if "omp-problem-solving.js" not in expected:
        extensions = {k.replace("\\", "/"): v for k, v in previous.get("existing_extensions", {}).items()}
        expected["omp-problem-solving.js"] = extensions["extensions/omp-problem-solving.js"]
    for name, relative in FILES.items():
        if digest(agent / relative) != expected[name]:
            raise RuntimeError("Unreviewed baseline drift")
    # Both extensions import the reviewed installed helper tree.
    for name in ("omp-model-routing-observability.js", "omp-sota-escalation.js"):
        if (agent / "extensions" / name).read_bytes().replace(b"\r\n", b"\n") != (source / name).read_bytes().replace(b"\r\n", b"\n"):
            raise RuntimeError("Dependency drift")
    old_policy = json.loads((agent / "task-verification-policy.json").read_text(encoding="utf-8"))
    policy = json.loads((source / "task-verification-policy.json").read_text(encoding="utf-8"))
    old_project = old_policy["projects"][0]
    commands = {"@node": next(x["command"] for x in old_project["checks"] if x["id"] == "omp-extensions"),
                "@python": next(x["command"] for x in old_project["checks"] if x["id"] == "omp-verification-deployment")}
    for project in policy["projects"]:
        if project["root"] != "@repository":
            raise RuntimeError("Unexpected project scope")
        project["root"] = old_project["root"]
        for check in project["checks"]:
            check["command"] = commands[check["command"]]
    # Preserve existing explicitly configured external projects on future upgrades.
    policy["projects"].extend(p for p in old_policy["projects"][1:])
    if hutuji_root is not None:
        root = hutuji_root.resolve(strict=True)
        if not (root / ".git").exists():
            raise RuntimeError("Additional project is not a Git checkout")
        project = json.loads((source / "task-verification-hutuji.json").read_text(encoding="utf-8"))
        project["root"] = str(root)
        for check in project["checks"]:
            check["command"] = commands[check["command"]]
            for name in check["args"]:
                if not name.startswith("-"):
                    path = (root / name).resolve(strict=True)
                    if not path.is_relative_to(root) or not path.is_file():
                        raise RuntimeError("Additional project test entry unavailable")
        policy["projects"] = [p for p in policy["projects"] if Path(p["root"]).resolve() != root]
        policy["projects"].append(project)
    for executable in commands.values():
        if not Path(executable).is_absolute() or not Path(executable).is_file():
            raise RuntimeError("Verification executable unavailable")
    protected = {str(p.relative_to(agent)): digest(p) for p in (agent / "extensions").rglob("*.js") if str(p.relative_to(agent)).replace("\\", "/") not in FILES.values()}
    protected.update({name: digest(agent / name) for name in ("config.yml", "models.yml", "mcp.json", "sota-review-policy.json")})
    backup = agent / "extension-backups" / f"solving-upgrade-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
    prepared = backup / "prepared"
    prepared.mkdir(parents=True)
    manifest = {"agent": str(agent.resolve()), "files": [], "protected": protected, "restart_performed": False}
    for name, relative in FILES.items():
        destination, old = agent / relative, backup / name
        if digest(destination) != expected[name]:
            raise RuntimeError("Production drift during preparation")
        shutil.copy2(destination, old)
        if digest(old) != expected[name] or old.stat().st_size != destination.stat().st_size:
            raise RuntimeError("Backup mismatch")
        if name.endswith(".json"):
            (prepared / name).write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        else:
            shutil.copy2(source / name, prepared / name)
        manifest["files"].append({"name": name, "before": expected[name], "after": digest(prepared / name),
                                  "backup_size": old.stat().st_size, "backup_mtime": old.stat().st_mtime})
    path = backup / "deployment.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for name in ("deploy-omp-solving-upgrade.py", "deploy-stability-scripts.py"):
        shutil.copy2(source / name, backup / name)
    (backup / "rollback.cmd").write_text('@echo off\r\npython3 "%~dp0deploy-omp-solving-upgrade.py" --rollback "%~dp0deployment.json"\r\n', encoding="ascii")
    try:
        for item in manifest["files"]:
            destination = agent / FILES[item["name"]]
            if digest(destination) != item["before"]:
                raise RuntimeError("Production drift after backup")
            replace_verified(prepared / item["name"], destination, item["after"])
        if any(digest(agent / name) != value for name, value in protected.items()):
            raise RuntimeError("Protected configuration drift")
    except Exception:
        rollback(path, agent)
        raise
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--hutuji-root", type=Path)
    args = parser.parse_args()
    if args.rollback:
        rollback(args.rollback)
        print("Rollback verified; start a new OMP session.")
    elif args.previous_manifest:
        print(json.dumps({"manifest": str(deploy(args.previous_manifest, hutuji_root=args.hutuji_root)), "restart_performed": False}))
    else:
        parser.error("Provide --previous-manifest or --rollback")
