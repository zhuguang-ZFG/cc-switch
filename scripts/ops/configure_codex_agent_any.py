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


def agent_payload(keys: list[str]) -> dict:
    if not keys or not all(channel_tools.usable_key(key) for key in keys):
        raise ValueError("AgentRouter credential is missing or masked")
    return {
        "name": CHANNEL_NAME, "type": 1, "status": 1,
        "base_url": "https://agentrouter.org", "key": "\n".join(keys),
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


def json_value(raw) -> dict:
    """Read a channels-table JSON column that this fork stores as TEXT on some rows, BLOB on others."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return json.loads(raw or "{}")


def status_note(channel: dict) -> str:
    """How this fork recorded the channel's last status change.

    other_info.status_reason is "manual operation" for every POST /api/channel/{id}/status, and the
    Guardian disables and recovers through that same endpoint -- so this names the route, not a
    person. Read it with the process log before concluding anyone clicked anything.
    """
    try:
        other = json_value(channel.get("other_info"))
    except ValueError:
        return "status record unparsable"
    stamp = other.get("status_time")
    when = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
            if isinstance(stamp, (int, float)) and stamp > 0 else str(stamp))
    return f"{other.get('status_reason', '?')} at {when}"


def verify_projection(db_path: Path, agent_id: int) -> dict:
    with read_only(db_path) as db:
        db.row_factory = sqlite3.Row
        agent = dict(db.execute("SELECT * FROM channels WHERE id=?", (agent_id,)).fetchone())
        any_channel = dict(db.execute("SELECT * FROM channels WHERE id=?", (ANY_CHANNEL_ID,)).fetchone())
        expected = agent_payload(["fixture-key-for-configuration-only"])
        # `models` and `group` are ADDITIVE in this deployment: ch127 also carries
        # deepseek-v4-flash, which took real traffic on 10-09/10-10/10-11 (11/13/18 requests), and
        # its consumers reach it through the `agent` group. Demanding exact equality here asked for
        # a capacity regression to make a gate green, so the contract checks presence of what the
        # pool needs, not absence of anything else.
        for key in ("name", "type", "status", "base_url", "priority", "weight", "auto_ban", "tag"):
            if agent.get(key) != expected[key]:
                raise RuntimeError(f"AgentRouter readback mismatch: {key}")
        owned_models = [m.strip() for m in str(agent.get("models") or "").split(",") if m.strip()]
        missing_models = [model for model in MODELS if model not in owned_models]
        if missing_models:
            raise RuntimeError(f"AgentRouter pool lost names {missing_models}; declares {owned_models}")
        agent_groups = {g.strip() for g in str(agent.get("group") or "").split(",") if g.strip()}
        if "default" not in agent_groups:
            raise RuntimeError(f"AgentRouter channel left the default group: {sorted(agent_groups)}")
        if str(agent.get("model_mapping") or "").strip() not in ("", "{}"):
            raise RuntimeError("AgentRouter model_mapping must stay empty; a mapping here rewrites "
                               "names for every consumer of this channel, not just the pool")
        test_model = str(agent.get("test_model") or "")
        if test_model not in MODELS:
            raise RuntimeError(
                f"AgentRouter test_model is {test_model!r}. This channel declares {owned_models}, so "
                "a blank or foreign test_model makes every scheduled and Guardian probe ask the "
                "fork's first name -- which answers even when both GPT names are dead. Repair it "
                f"with fix_channel_test_model.py --set {agent_id}={MODELS[0]}: test_model is "
                "channels-only metadata and goes through a direct UPDATE, because this fork's "
                "channel PUT round-trips key:\"\" and wipes the credential.")
        for row in (any_channel, agent):
            mapping = json_value(row["status_code_mapping"])
            if any(str(mapping.get(key)) != value for key, value in RETRY_MAPPING.items()):
                raise RuntimeError(f"ch{row['id']} channel-local retry mapping is incomplete")
        has_sharedchat = db.execute("SELECT COUNT(*) FROM channels WHERE name=?", (SHAREDCHAT_CHANNEL_NAME,)).fetchone()[0]
        any_priority = PRIORITY if has_sharedchat else 50
        if any_channel["priority"] != any_priority or any_channel["weight"] != 5:
            raise RuntimeError(
                f"AnyRouter tier drifted: ch{ANY_CHANNEL_ID} is p={any_channel['priority']} "
                f"w={any_channel['weight']}, contract expects p={any_priority} w=5")
        if any_channel["status"] != 1:
            raise RuntimeError(
                f"AnyRouter leg ch{ANY_CHANNEL_ID} is status={any_channel['status']}; last change "
                f"{status_note(any_channel)}")
        # This asserted User-Agent == "codex_cli_rs/0.154.0" until 2026-10-11. Direct calls to
        # agentrouter with that UA and with the claude-cli UA the channel carries now returned
        # byte-identical verdicts on both GPT names, so the header was never load-bearing here --
        # and restoring it would need the PUT that wipes the credential and rebuilds abilities.
        # A malformed header_override still breaks every request, so that is what is checked.
        try:
            headers_seen = json_value(agent.get("header_override"))
        except ValueError as error:
            raise RuntimeError(f"AgentRouter header_override is not valid JSON: {error}") from error
        if not isinstance(headers_seen, dict):
            raise RuntimeError("AgentRouter header_override must be a JSON object")
        stored_key = agent.get("key") or ""
        key_lines = [line for line in str(stored_key).split("\n") if line.strip()]
        storage = db.execute("SELECT typeof(channel_info) FROM channels WHERE id=?", (agent_id,)).fetchone()
        if storage is not None and storage[0] not in ("null",):
            if storage[0] != "blob":
                raise RuntimeError("channel_info must be stored as BLOB for the fork's []byte scan path")
            info = json.loads(bytes(agent["channel_info"]))
            if info.get("is_multi_key") and len(key_lines) != int(info.get("multi_key_size") or 0):
                raise RuntimeError("multi-key metadata does not match the stored key line count")
        elif len(key_lines) != 1:
            raise RuntimeError("single-key channel must store exactly one key line")
        if not all(channel_tools.usable_key(line) for line in key_lines):
            raise RuntimeError("stored AgentRouter credential is missing or masked")
        ability_rows = [dict(row) for row in db.execute(
            'SELECT channel_id,model,"group",enabled,priority,weight FROM abilities '
            'WHERE channel_id IN (?,?) ORDER BY channel_id,model,"group"',
            (ANY_CHANNEL_ID, agent_id),
        )]
        seen = {(row["channel_id"], row["model"], row["group"]): row for row in ability_rows}
        any_groups = {g.strip() for g in str(any_channel.get("group") or "").split(",") if g.strip()}

        def leg_missing(leg: tuple[int, str, str, int, int]) -> bool:
            channel_id, model, group, priority, weight = leg
            row = seen.get((channel_id, model, group))
            return (row is None or row["enabled"] != 1
                    or row["priority"] != priority or row["weight"] != weight)

        # A row's existence is not capacity: enabled follows the channel's status and the fork
        # answers the name or it does not. What this can prove read-only is the projection.
        required = [(agent_id, model, group, PRIORITY, WEIGHT)
                    for model in MODELS for group in sorted(agent_groups)]
        required += [(ANY_CHANNEL_ID, MODELS[0], group, any_priority, 5)
                     for group in sorted(any_groups)]
        gaps = [leg for leg in required if leg_missing(leg)]
        if gaps:
            raise RuntimeError(f"channel ability projection differs from the planned pool: {gaps}")
        extras = sorted((row["channel_id"], row["model"], row["group"]) for row in ability_rows
                        if row["model"] not in MODELS)
        options = dict(db.execute("SELECT key,value FROM options WHERE key IN ('RetryTimes','AutomaticRetryStatusCodes')"))
        if options != {"RetryTimes": "1", "AutomaticRetryStatusCodes": "408,500-503"}:
            raise RuntimeError("global retry policy drifted; refusing nested retries")
        return {"agent_id": agent_id, "abilities": ability_rows, "retry_options": options,
                "extra_declared_models": extras, "agent_groups": sorted(agent_groups),
                "header_user_agent": headers_seen.get("User-Agent")}


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
    print(json.dumps({"plan": channel_tools.safe_channel_summary(agent_payload(["fixture-key-for-plan-only"])), "any_primary": ANY_CHANNEL_ID, "channel_retry_mapping": RETRY_MAPPING, "codex_provider_label": POOL_LABEL}, ensure_ascii=False))
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
    pool_keys = [item if isinstance(item, str) else item.get("key", item.get("api_key")) for item in keys]
    desired_agent = agent_payload(pool_keys)
    candidate_config = CODEX_CONFIG.with_name("config.toml.codex-pool-new")
    if candidate_config.exists():
        raise RuntimeError("an unfinished Codex config candidate already exists")
    created_id = None
    any_changed = False
    config_changed = False
    try:
        api("POST", "/api/channel/", {"mode": "multi" if len(pool_keys) > 1 else "single", "channel": desired_agent})
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
