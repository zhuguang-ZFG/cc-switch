#!/usr/bin/env python3
"""Add SharedChat first, with Any/Agent in one reachable fallback tier.

All live writes go through NewAPI's API except the Codex display name. Apply
requires a verified snapshot and the deployed narrow Guardian policy. Secrets
are read in memory from the existing private store, never supplied in argv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import tomllib
from pathlib import Path

import configure_codex_agent_any as pool
import quarantine_newapi_channels as channel_tools
from codex_window_pool import (
    ANY_CHANNEL_ID,
    CHANNEL_NAME,
    MODELS,
    PRIORITY,
    SHAREDCHAT_BASE_URL,
    SHAREDCHAT_CHANNEL_ID,
    SHAREDCHAT_CHANNEL_NAME,
    SHAREDCHAT_POOL_TAG,
    SHAREDCHAT_PRIORITY,
    SHAREDCHAT_RETRY_MAPPING,
    WEIGHT,
)


POOL_LABEL = "SharedChat / Any / Agent GPT"
CODEX_HEADERS = {
    "User-Agent": "codex_cli_rs/0.154.0",
    "Originator": "codex_cli_rs",
    "Version": "0.154.0",
}


def sharedchat_payload(key: str) -> dict:
    if not channel_tools.usable_key(key):
        raise ValueError("SharedChat credential is missing or masked")
    return {
        "name": SHAREDCHAT_CHANNEL_NAME, "type": 1, "status": 1,
        "base_url": SHAREDCHAT_BASE_URL, "key": key.strip(),
        "models": ",".join(MODELS), "group": "default", "model_mapping": "",
        "priority": SHAREDCHAT_PRIORITY, "weight": WEIGHT, "auto_ban": 0,
        "test_model": MODELS[1], "tag": SHAREDCHAT_POOL_TAG,
        "header_override": json.dumps(CODEX_HEADERS, sort_keys=True),
        "status_code_mapping": json.dumps(SHAREDCHAT_RETRY_MAPPING, sort_keys=True),
        "remark": "Native Codex Responses; prefer site-wide quota. Any/Agent share the single fallback tier. No cross-model substitution or AgentRouter refill schedule.",
    }


def verify_projection(db_path: Path, sharedchat_id: int, agent_id: int) -> dict:
    existing = pool.verify_projection(db_path, agent_id)
    with pool.read_only(db_path) as db:
        import sqlite3
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM channels WHERE id=?", (sharedchat_id,)).fetchone()
        if row is None:
            raise RuntimeError("SharedChat channel missing")
        current = dict(row)
        expected = sharedchat_payload("fixture-sharedchat-key")
        for key in ("name", "type", "status", "base_url", "models", "group", "priority", "weight", "auto_ban", "test_model", "tag", "model_mapping"):
            if current.get(key) != expected[key]:
                raise RuntimeError(f"SharedChat readback mismatch: {key}")
        if current["id"] != SHAREDCHAT_CHANNEL_ID:
            raise RuntimeError("SharedChat id does not match the Guardian native probe contract")
        for key in ("header_override", "status_code_mapping"):
            if json.loads(current.get(key) or "{}") != json.loads(expected[key]):
                raise RuntimeError(f"SharedChat readback mismatch: {key}")
        ability = db.execute("SELECT model,enabled,priority,weight FROM abilities WHERE channel_id=? ORDER BY model", (sharedchat_id,)).fetchall()
        if [tuple(row) for row in ability] != [(model, 1, SHAREDCHAT_PRIORITY, WEIGHT) for model in sorted(MODELS)]:
            raise RuntimeError("SharedChat native model ability differs from the plan")
        astra = [dict(row) for row in db.execute(
            'SELECT channel_id,priority,weight FROM abilities WHERE model=? AND "group"=? AND enabled=1 ORDER BY channel_id',
            (MODELS[0], "default"),
        )]
        if {row["channel_id"] for row in astra} != {ANY_CHANNEL_ID, agent_id, sharedchat_id}:
            raise RuntimeError("unexpected enabled Astra route; refusing an unreviewed primary or orphan")
        if sorted({row["priority"] for row in astra}, reverse=True) != [SHAREDCHAT_PRIORITY, PRIORITY]:
            raise RuntimeError("Astra must have exactly two priority tiers with RetryTimes=1")
    return {**existing, "sharedchat_id": sharedchat_id, "astra_routes": astra, "max_attempts_per_gateway_request": 2}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--snapshot-dir", type=Path)
    args = parser.parse_args()
    with pool.read_only(pool.DB_PATH) as db:
        agents = db.execute("SELECT id FROM channels WHERE name=?", (CHANNEL_NAME,)).fetchall()
        shared = db.execute("SELECT id FROM channels WHERE name=?", (SHAREDCHAT_CHANNEL_NAME,)).fetchall()
        max_id = db.execute("SELECT MAX(id) FROM channels").fetchone()[0]
    if len(agents) != 1 or len(shared) > 1:
        raise RuntimeError("expected one AgentRouter channel and at most one SharedChat channel")
    agent_id = agents[0][0]
    if shared:
        result = verify_projection(pool.DB_PATH, shared[0][0], agent_id)
        config = tomllib.loads(pool.CODEX_CONFIG.read_text(encoding="utf-8-sig"))
        if config["model_providers"]["any"]["name"] != POOL_LABEL:
            raise RuntimeError("Codex provider label differs from the deployed pool")
        print(json.dumps({"configured": True, **result}))
        return 0
    if args.verify:
        raise RuntimeError("SharedChat channel is not configured")
    pool.verify_projection(pool.DB_PATH, agent_id)
    original_config = pool.CODEX_CONFIG.read_bytes()
    updated_config = pool.config_with_pool_label(original_config, POOL_LABEL)
    print(json.dumps({"plan": channel_tools.safe_channel_summary(sharedchat_payload("fixture-plan-key")), "fallback_priority": PRIORITY, "fallback_channel_ids": [ANY_CHANNEL_ID, agent_id], "global_retry_changes": False, "codex_model": MODELS[0]}))
    if not args.apply:
        return 0
    if args.snapshot_dir is None:
        parser.error("--apply requires --snapshot-dir")
    snapshot = args.snapshot_dir.resolve()
    backup_db = snapshot / "new-api-before.db"
    with pool.read_only(backup_db) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("rollback snapshot integrity check failed")
    guarded = (*pool.PROTECTED_CHANNELS, ANY_CHANNEL_ID, agent_id)
    if pool.configuration_rows(backup_db, guarded) != pool.configuration_rows(pool.DB_PATH, guarded):
        raise RuntimeError("guarded channel changed since snapshot")
    if (snapshot / "codex-config-before.toml").read_bytes() != original_config:
        raise RuntimeError("Codex config changed since snapshot")
    if max_id + 1 != SHAREDCHAT_CHANNEL_ID:
        raise RuntimeError("next channel id differs from the reviewed native probe contract")
    live_guardian = pool.HOME_DIR / ".omp/guardian/guardian.py"
    source = live_guardian.read_text(encoding="utf-8-sig")
    if source.count("or is_codex_probe_incompatible(channel, test_msg)") != 2 or "/api/channel/test/128?model=gpt-5.6-sol" not in source:
        raise RuntimeError("deploy the Guardian native Codex probe policy before enabling SharedChat")
    policy = Path(__file__).with_name("codex_window_pool.py").read_bytes()
    if live_guardian.with_name("codex_window_pool.py").read_bytes() != policy:
        raise RuntimeError("live Guardian pool policy differs from reviewed source")
    key = json.loads((pool.HOME_DIR / ".omp/guardian/secrets.json").read_text(encoding="utf-8-sig"))["sharedchat_codex_key"]
    desired = sharedchat_payload(key)
    smoke = channel_tools.load_smoke()
    token, user = smoke.admin_auth()
    headers = {"Authorization": "Bearer " + token, "New-Api-User": str(user)}

    def api(method, path, payload=None):
        status, body = smoke.http_json(pool.BASE + path, method=method, body=payload, headers=headers, timeout=20)
        if status != 200 or not isinstance(body, dict) or body.get("success") is not True:
            raise RuntimeError(f"NewAPI {method} {path} failed (HTTP {status})")
        return body.get("data")

    original_any = channel_tools.hydrate_key(api("GET", f"/api/channel/{ANY_CHANNEL_ID}"), pool.DB_PATH)
    candidate = pool.CODEX_CONFIG.with_name("config.toml.sharedchat-new")
    if candidate.exists():
        raise RuntimeError("unfinished Codex config candidate exists")
    created_id = None
    any_changed = config_changed = False
    try:
        api("POST", "/api/channel/", {"mode": "single", "channel": {**desired, "status": 2}})
        with pool.read_only(pool.DB_PATH) as db:
            created = db.execute("SELECT id FROM channels WHERE name=?", (SHAREDCHAT_CHANNEL_NAME,)).fetchall()
        if len(created) != 1:
            raise RuntimeError("cannot identify newly created SharedChat channel")
        created_id = created[0][0]
        if created_id != SHAREDCHAT_CHANNEL_ID:
            raise RuntimeError("new channel id differs from the reviewed Guardian probe")
        updated_any = {k: v for k, v in original_any.items() if k != "status"}
        updated_any["priority"] = PRIORITY
        any_changed = True
        api("PUT", "/api/channel/", updated_any)
        api("POST", f"/api/channel/{created_id}/status", {"status": 1})
        result = verify_projection(pool.DB_PATH, created_id, agent_id)
        untouched = (*pool.PROTECTED_CHANNELS, agent_id)
        if pool.configuration_rows(backup_db, untouched) != pool.configuration_rows(pool.DB_PATH, untouched):
            raise RuntimeError("a protected channel changed during deployment")
        if pool.CODEX_CONFIG.read_bytes() != original_config:
            raise RuntimeError("concurrent Codex edit prevents deployment")
        candidate.write_bytes(updated_config)
        os.replace(candidate, pool.CODEX_CONFIG)
        config_changed = True
        if pool.CODEX_CONFIG.read_bytes() != updated_config:
            raise RuntimeError("Codex label readback failed")
        result.update({"created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "snapshot_dir": str(snapshot), "config_sha256": hashlib.sha256(updated_config).hexdigest()})
        (snapshot / "sharedchat-applied.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"applied": True, **result}))
        return 0
    except BaseException:
        errors = []
        if config_changed:
            try:
                if pool.CODEX_CONFIG.read_bytes() != updated_config:
                    raise RuntimeError("concurrent edit prevents Codex rollback")
                candidate.write_bytes(original_config)
                os.replace(candidate, pool.CODEX_CONFIG)
            except Exception:
                errors.append("codex_config")
        # POST may have committed even when its response was lost. Resolve by
        # the unique, preflight-absent name AND the in-memory credential.
        if created_id is None:
            with pool.read_only(pool.DB_PATH) as db:
                found = db.execute("SELECT id FROM channels WHERE name=? AND key=? AND id>?", (SHAREDCHAT_CHANNEL_NAME, desired["key"], max_id)).fetchall()
            if len(found) == 1:
                created_id = found[0][0]
            elif found:
                errors.append("ambiguous_created_channel")
        if created_id is not None:
            try:
                api("DELETE", f"/api/channel/{created_id}")
            except Exception:
                errors.append("created_sharedchat_channel")
        if any_changed:
            try:
                current = channel_tools.hydrate_key(api("GET", f"/api/channel/{ANY_CHANNEL_ID}"), pool.DB_PATH)
                if current["priority"] != PRIORITY:
                    raise RuntimeError("concurrent channel edit prevents rollback")
                current = {k: v for k, v in current.items() if k != "status"}
                current["priority"] = original_any["priority"]
                api("PUT", "/api/channel/", current)
            except Exception:
                errors.append("any_priority")
        print(json.dumps({"rollback_errors": errors, "snapshot_dir": str(snapshot)}))
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR {type(error).__name__}: {error}")
        raise SystemExit(1)
