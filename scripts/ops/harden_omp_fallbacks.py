"""Apply the reviewed September 25 OMP fallback/timeout changes with rollback.

Defaults to a redacted dry run. Never writes NewAPI or CC Switch databases.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

import yaml

from remove_omp_dead_fallback import atomic_write, remove_exact_candidate


REMOVALS = (
    ("slow", "zg-newapi-anthropic/claude-opus-4-8"),
    ("plan", "zg-newapi-anthropic/claude-opus-4-8"),
    ("zg-newapi/kimi-for-coding", "zg-newapi/k3"),
)
BOUNDS = (
    ("providers", "streamFirstEventTimeoutSeconds", None, 120),
    ("providers", "streamIdleTimeoutSeconds", None, 60),
    ("retry", "maxDelayMs", 90000, 60000),
    ("task", "maxRuntimeMs", 1800000, 900000),
)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError("duplicate YAML mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse(text: str) -> dict:
    try:
        data = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError:
        # PyYAML errors can include source lines containing credentials.
        raise ValueError("invalid OMP YAML; repair syntax before applying") from None
    if not isinstance(data, dict):
        raise ValueError("OMP config must be a mapping")
    return data


def set_number(text: str, section: str, key: str, value: int) -> str:
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == f"{section}:" and not line.startswith(" ")), None)
    if start is None:
        raise ValueError(f"unsupported section layout: {section}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i].startswith((" ", "#"))), len(lines))
    matches = [i for i in range(start + 1, end) if re.match(rf"^  {re.escape(key)}:", lines[i])]
    if len(matches) > 1:
        raise ValueError("duplicate numeric setting")
    if matches:
        i = matches[0]
        lines[i], count = re.subn(rf"^(  {re.escape(key)}:)\s*-?\d+", rf"\g<1> {value}", lines[i])
        if count != 1:
            raise ValueError("unsupported numeric setting layout")
    else:
        newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
        lines.insert(start + 1, f"  {key}: {value}{newline}")
    return "".join(lines)


def transform(text: str) -> tuple[str, dict]:
    before = parse(text)
    expected = copy.deepcopy(before)
    chains = expected["retry"]["fallbackChains"]
    if chains.get("zg-newapi/kimi-for-coding") not in (
        ["zg-newapi/k3", "zg-newapi/deepseek-v4-flash"], ["zg-newapi/deepseek-v4-flash"],
    ):
        raise ValueError("default fallback differs from reviewed configuration")
    report = {"chains": {}, "bounds": {}}
    for chain, candidate in REMOVALS:
        text, _ = remove_exact_candidate(text, chain, candidate)
        chains[chain] = [item for item in chains[chain] if item != candidate]
        report["chains"][chain] = chains[chain]
    for section, key, old, new in BOUNDS:
        current = expected.get(section, {}).get(key)
        accepted = (old, new, -1) if old is None else (old, new)
        if current not in accepted:
            raise ValueError(f"unreviewed numeric setting: {section}.{key}")
        text = set_number(text, section, key, new)
        expected[section][key] = new
        report["bounds"][f"{section}.{key}"] = {"before": current, "after": new}
    if parse(text) != expected:
        raise ValueError("configuration transformation changed unrelated fields")
    # Equality also protects smol, all model roles, concurrency and retry count.
    return text, report


def channel_posture(db_path: Path) -> dict[str, list[int]]:
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        return {model: [row[0] for row in db.execute(
            "SELECT DISTINCT a.channel_id FROM abilities a JOIN channels c "
            "ON c.id=a.channel_id WHERE a.model=? AND a.enabled=1 AND c.status=1 ORDER BY a.channel_id",
            (model,))] for model in ("kimi-for-coding", "k3", "deepseek-v4-flash", "intern-s2-preview", "claude-opus-4-8")}


def validate_posture(posture: dict) -> None:
    if posture["claude-opus-4-8"]:
        raise ValueError("Opus 4.8 capacity changed; review before removing candidate")
    if any(not posture[m] for m in ("kimi-for-coding", "k3", "deepseek-v4-flash", "intern-s2-preview")):
        raise ValueError("required remaining route has no enabled channel")
    if posture["kimi-for-coding"] != posture["k3"]:
        raise ValueError("Kimi/K3 channel overlap changed; review before removal")
    if set(posture["kimi-for-coding"]) & set(posture["deepseek-v4-flash"]):
        raise ValueError("default fallback shares primary channel")


def apply_config(config: Path, original: bytes, updated: bytes, report: dict) -> Path:
    if config.read_bytes() != original:
        raise ValueError("configuration drifted since planning")
    backup = config.parent / "backups" / f"fallback-stability-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir(parents=True, exist_ok=False)
    previous = backup / "config.yml"
    previous.write_bytes(original)
    if previous.read_bytes() != original or previous.stat().st_size != len(original):
        raise ValueError("backup verification failed")
    metadata = {"beforeSha256": hashlib.sha256(original).hexdigest(),
                "afterSha256": hashlib.sha256(updated).hexdigest(),
                "backupBytes": previous.stat().st_size, "backupMtime": previous.stat().st_mtime,
                "plan": report}
    (backup / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    try:
        if config.read_bytes() != original:
            raise ValueError("configuration drifted before replacement")
        atomic_write(config, updated)
        if config.read_bytes() != updated:
            raise ValueError("configuration readback failed")
    except Exception:
        # Do not overwrite a concurrent user edit when replacement never started.
        if config.read_bytes() == updated:
            atomic_write(config, original)
        raise
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", type=Path, help="backup directory")
    args = parser.parse_args()
    config = Path.home() / ".omp/agent/config.yml"
    if args.rollback:
        metadata = json.loads((args.rollback / "manifest.json").read_text(encoding="utf-8"))
        previous = (args.rollback / "config.yml").read_bytes()
        if hashlib.sha256(previous).hexdigest() != metadata["beforeSha256"]:
            raise ValueError("backup hash mismatch")
        if hashlib.sha256(config.read_bytes()).hexdigest() != metadata["afterSha256"]:
            raise ValueError("live configuration drift; refusing rollback overwrite")
        atomic_write(config, previous)
        if config.read_bytes() != previous:
            raise ValueError("rollback verification failed")
        print("Configuration rollback verified; reload OMP to activate.")
        return
    original = config.read_bytes()
    updated, report = transform(original.decode("utf-8"))
    posture = channel_posture(Path.home() / ".new-api-local/new-api.db")
    validate_posture(posture)
    report["channels"] = posture
    print(json.dumps(report, ensure_ascii=True))
    if args.apply:
        if updated.encode("utf-8") == original:
            print("Configuration already matches; no file written.")
            return
        backup = apply_config(config, original, updated.encode("utf-8"), report)
        print(json.dumps({"backup": str(backup), "activation": "reload or new OMP session"}))


if __name__ == "__main__":
    main()
