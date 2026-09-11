#!/usr/bin/env python3
"""Configure a bounded AnyRouter -> AgentRouter Codex pool through NewAPI.

Default: inspect and print a credential-free plan. Apply requires the verified
pre-change snapshot directory and the deployed Guardian resource-window policy.
No CC Switch database, executable, global retry option, or other channel is
modified. Existing keys are read only into memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import tomllib
from contextlib import closing
from pathlib import Path

import quarantine_newapi_channels as channel_tools
from codex_window_pool import (
    ANY_CHANNEL_ID,
    CHANNEL_NAME,
    MODELS,
    PRIORITY,
    RETRY_MAPPING,
    WEIGHT,
    WINDOW_POOL_TAG,
    SHAREDCHAT_CHANNEL_NAME,
)


HOME_DIR = Path.home()
DB_PATH = HOME_DIR / ".new-api-local/new-api.db"
CODEX_CONFIG = Path(os.environ.get("CODEX_HOME") or HOME_DIR / ".codex") / "config.toml"
BASE = "http://127.0.0.1:3002"
POOL_LABEL = "Any / Agent GPT"
PROTECTED_CHANNELS = (45, 86, 92, 120)
CONFIG_COLUMNS = (
    "id,name,type,status,base_url,models,model_mapping,status_code_mapping,"
    "priority,weight,auto_ban,test_model,header_override,param_override,tag"
)


def read_only(path: Path):
    return closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15))


def merge_retry_mapping(raw: str | None) -> str:
    result = json.loads(raw or "{}")
    if not isinstance(result, dict):
        raise ValueError("channel status mapping must be an object")
    for key, value in RETRY_MAPPING.items():
        if key in result and str(result[key]) != value:
            raise ValueError(f"existing status mapping for {key} conflicts with bounded failover")
        result[key] = value
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def agent_payload(key: str) -> dict:
    if not channel_tools.usable_key(key):
        raise ValueError("AgentRouter credential is missing or masked")
    return {
        "name": CHANNEL_NAME, "type": 1, "status": 1,
        "base_url": "https://agentrouter.org", "key": key.strip(),
        "models": ",".join(MODELS), "group": "default",
        "priority": PRIORITY, "weight": WEIGHT, "auto_ban": 0,
        "test_model": MODELS[0], "model_mapping": "",
        "tag": WINDOW_POOL_TAG,
        "header_override": json.dumps({"User-Agent": "codex_cli_rs/0.154.0"}),
        "status_code_mapping": merge_retry_mapping(None),
        "remark": "GPT resources refill 00:00/08:00/16:00 Asia/Shanghai; one cross-channel retry; no cross-model fallback.",
    }


def config_with_pool_label(raw: bytes, label: str = POOL_LABEL) -> bytes:
    source = raw.decode("utf-8-sig")
    config = tomllib.loads(source)
    provider = config.get("model_providers", {}).get("any", {})
    if (
        config.get("model_provider") != "any"
        or config.get("model") != MODELS[0]
        or provider.get("base_url", "").rstrip("/") != BASE + "/v1"
        or provider.get("wire_api") != "responses"
    ):
        raise ValueError("Codex default no longer matches the inspected NewAPI Responses route")
    pattern = r'(?ms)(^\[model_providers\.any\][^\r\n]*\r?\n)(.*?)(?=^\[|\Z)'
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError("expected exactly one model_providers.any section")
    match = matches[0]
    block = match[2]
    updated, count = re.subn(r'(?m)^name\s*=\s*[^\r\n]*', f'name = {json.dumps(label)}', block)
    if count != 1:
        raise ValueError("expected one display name in model_providers.any")
    result = source[:match.start(2)] + updated + source[match.end(2):]
    parsed = tomllib.loads(result)
    parsed["model_providers"]["any"]["name"] = provider["name"]
    if parsed != config:
        raise ValueError("Codex edit would modify fields outside the provider display name")
    return (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + result.encode("utf-8")


def configuration_rows(path: Path, ids: tuple[int, ...]) -> list[tuple]:
    with read_only(path) as db:
        placeholders = ",".join("?" for _ in ids)
        return db.execute(f"SELECT {CONFIG_COLUMNS} FROM channels WHERE id IN ({placeholders}) ORDER BY id", ids).fetchall()


def verify_projection(db_path: Path, agent_id: int) -> dict:
    with read_only(db_path) as db:
        db.row_factory = sqlite3.Row
        agent = dict(db.execute("SELECT * FROM channels WHERE id=?", (agent_id,)).fetchone())
        any_channel = dict(db.execute("SELECT * FROM channels WHERE id=?", (ANY_CHANNEL_ID,)).fetchone())
        expected = agent_payload("fixture-key-for-configuration-only")
        for key in ("name", "type", "status", "base_url", "models", "group", "priority", "weight", "auto_ban", "test_model", "tag"):
            if agent.get(key) != expected[key]:
                raise RuntimeError(f"AgentRouter readback mismatch: {key}")
        for row in (any_channel, agent):
            mapping = json.loads(row["status_code_mapping"] or "{}")
            if any(str(mapping.get(key)) != value for key, value in RETRY_MAPPING.items()):
                raise RuntimeError(f"ch{row['id']} channel-local retry mapping is incomplete")
        has_sharedchat = db.execute("SELECT COUNT(*) FROM channels WHERE name=?", (SHAREDCHAT_CHANNEL_NAME,)).fetchone()[0]
        any_priority = PRIORITY if has_sharedchat else 50
        if any_channel["status"] != 1 or any_channel["priority"] != any_priority or any_channel["weight"] != 5:
            raise RuntimeError("AnyRouter tier drifted")
        if json.loads(agent.get("header_override") or "{}").get("User-Agent") != "codex_cli_rs/0.154.0":
            raise RuntimeError("AgentRouter Codex header missing")
        abilities = [dict(row) for row in db.execute(
            "SELECT channel_id,model,enabled,priority,weight FROM abilities WHERE channel_id IN (?,?) ORDER BY channel_id,model",
            (ANY_CHANNEL_ID, agent_id),
        )]
        expected_abilities = {
            (ANY_CHANNEL_ID, MODELS[0], 1, any_priority, 5),
            *{(agent_id, model, 1, PRIORITY, WEIGHT) for model in MODELS},
        }
        if {tuple(row.values()) for row in abilities} != expected_abilities:
            raise RuntimeError("channel ability projection differs from the planned pool")
        options = dict(db.execute("SELECT key,value FROM options WHERE key IN ('RetryTimes','AutomaticRetryStatusCodes')"))
        if options != {"RetryTimes": "1", "AutomaticRetryStatusCodes": "408,500-503"}:
            raise RuntimeError("global retry policy drifted; refusing nested retries")
        return {"agent_id": agent_id, "abilities": abilities, "retry_options": options}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--snapshot-dir", type=Path)
    args = parser.parse_args()
    with read_only(DB_PATH) as db:
        found = db.execute("SELECT id FROM channels WHERE name=?", (CHANNEL_NAME,)).fetchall()
    if len(found) > 1:
        raise RuntimeError("duplicate AgentRouter Codex channels found")
    if args.verify or found:
        if not found:
            raise RuntimeError("AgentRouter Codex channel is not configured")
        result = verify_projection(DB_PATH, found[0][0])
        print(json.dumps({"configured": True, **result}, ensure_ascii=False))
        return 0

    original_config = CODEX_CONFIG.read_bytes()
    updated_config = config_with_pool_label(original_config)
    print(json.dumps({"plan": channel_tools.safe_channel_summary(agent_payload("fixture-key-for-plan-only")), "any_primary": ANY_CHANNEL_ID, "channel_retry_mapping": RETRY_MAPPING, "codex_provider_label": POOL_LABEL}, ensure_ascii=False))
    if not args.apply:
        return 0
    if args.snapshot_dir is None:
        parser.error("--apply requires --snapshot-dir from the verified preflight backup")
    snapshot_dir = args.snapshot_dir.resolve()
    snapshot_db = snapshot_dir / "new-api-before.db"
    config_backup = snapshot_dir / "codex-config-before.toml"
    with read_only(snapshot_db) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("rollback database integrity check failed")
    guarded = (*PROTECTED_CHANNELS, ANY_CHANNEL_ID)
    if configuration_rows(DB_PATH, guarded) != configuration_rows(snapshot_db, guarded):
        raise RuntimeError("a guarded channel changed since the snapshot; refresh preflight before applying")
    if config_backup.read_bytes() != original_config:
        raise RuntimeError("Codex configuration changed since the snapshot")
    live_guardian = HOME_DIR / ".omp/guardian/guardian.py"
    source = live_guardian.read_text(encoding="utf-8-sig")
    live_policy = live_guardian.with_name("codex_window_pool.py")
    if source.count("if is_window_budget_exhausted(channel, test_msg)") != 2 or not live_policy.exists():
        raise RuntimeError("deploy and restart the Guardian resource-window policy before enabling the channel")
    if live_policy.read_bytes() != Path(__file__).with_name("codex_window_pool.py").read_bytes():
        raise RuntimeError("live Guardian resource-window policy hash differs from the reviewed source")

    smoke = channel_tools.load_smoke()
    token, user = smoke.admin_auth()
    headers = {"Authorization": "Bearer " + token, "New-Api-User": str(user)}

    def api(method: str, path: str, payload: dict | None = None):
        status, body = smoke.http_json(BASE + path, method=method, body=payload, headers=headers, timeout=20)
        if status != 200 or not isinstance(body, dict) or body.get("success") is not True:
            raise RuntimeError(f"NewAPI {method} {path} failed (HTTP {status})")
        return body.get("data")

    original_any = channel_tools.hydrate_key(api("GET", f"/api/channel/{ANY_CHANNEL_ID}"), DB_PATH)
    original_mapping = original_any.get("status_code_mapping")
    keys_file = HOME_DIR / ".kimi-code/proxies/agentrouter-proxy/keys.json"
    keys = json.loads(keys_file.read_text(encoding="utf-8-sig"))["keys"]
    key = keys[0] if isinstance(keys[0], str) else keys[0].get("key", keys[0].get("api_key"))
    desired_agent = agent_payload(key)
    candidate_config = CODEX_CONFIG.with_name("config.toml.codex-pool-new")
    if candidate_config.exists():
        raise RuntimeError("an unfinished Codex config candidate already exists")
    created_id = None
    any_changed = False
    config_changed = False
    try:
        api("POST", "/api/channel/", {"mode": "single", "channel": desired_agent})
        with read_only(DB_PATH) as db:
            rows = db.execute("SELECT id FROM channels WHERE name=?", (CHANNEL_NAME,)).fetchall()
        if len(rows) != 1:
            raise RuntimeError("created channel could not be identified uniquely")
        created_id = int(rows[0][0])
        updated_any = {k: v for k, v in original_any.items() if k != "status"}
        updated_any["status_code_mapping"] = merge_retry_mapping(original_mapping)
        any_changed = True
        api("PUT", "/api/channel/", updated_any)
        result = verify_projection(DB_PATH, created_id)
        if configuration_rows(DB_PATH, PROTECTED_CHANNELS) != configuration_rows(snapshot_db, PROTECTED_CHANNELS):
            raise RuntimeError("an unrelated protected channel changed during apply")
        if CODEX_CONFIG.read_bytes() != original_config:
            raise RuntimeError("Codex config changed concurrently; refusing overwrite")
        candidate_config.write_bytes(updated_config)
        os.replace(candidate_config, CODEX_CONFIG)
        config_changed = True
        if CODEX_CONFIG.read_bytes() != updated_config:
            raise RuntimeError("Codex display-name readback failed")
        result.update({"created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "config_sha256": hashlib.sha256(updated_config).hexdigest(), "snapshot_dir": str(snapshot_dir)})
        (snapshot_dir / "applied.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"applied": True, **result}, ensure_ascii=False))
        return 0
    except BaseException:
        rollback_errors = []
        if config_changed:
            try:
                if CODEX_CONFIG.read_bytes() != updated_config:
                    raise RuntimeError("concurrent Codex edit prevents rollback")
                candidate_config.write_bytes(original_config)
                os.replace(candidate_config, CODEX_CONFIG)
            except Exception:
                rollback_errors.append("codex_config")
        if any_changed:
            try:
                current = channel_tools.hydrate_key(api("GET", f"/api/channel/{ANY_CHANNEL_ID}"), DB_PATH)
                current = {k: v for k, v in current.items() if k != "status"}
                current["status_code_mapping"] = original_mapping
                api("PUT", "/api/channel/", current)
            except Exception:
                rollback_errors.append("any_mapping")
        if created_id is not None:
            try:
                api("DELETE", f"/api/channel/{created_id}")
            except Exception:
                rollback_errors.append("created_agent_channel")
        print(json.dumps({"rollback_errors": rollback_errors, "snapshot_dir": str(snapshot_dir)}))
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Error messages are generated locally; never serialize API bodies or keys.
        print(f"ERROR {type(error).__name__}: {error}")
        raise SystemExit(1)
