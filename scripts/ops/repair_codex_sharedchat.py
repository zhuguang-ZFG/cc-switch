#!/usr/bin/env python3
"""Repair Codex Responses affinity and add SharedChat's Sol capability.

2026-09-12 policy (user directive, supersedes the same-day anti-brick pin):
the codex cli trace rule MUST allow cross-channel failover
(skip_retry_on_failure=false) so the same model keeps serving from the next
live channel when a pinned channel fails (e.g. SharedChat rolling spend
limit). Brick-risk of a cross-upstream switch is accepted; recovery paths:
affinity switch_on_success keeps failed retries unpinned, and
codex-resume-scrub.py can clear poisoned sessions."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import configure_codex_agent_any as pool
import quarantine_newapi_channels as channel_tools
from codex_window_pool import MODELS, SHAREDCHAT_CHANNEL_ID, SHAREDCHAT_CHANNEL_NAME


def api_client():
    smoke = channel_tools.load_smoke()
    token, user = smoke.admin_auth()
    headers = {"Authorization": "Bearer " + token, "New-Api-User": str(user)}

    def api(method: str, path: str, payload: dict | None = None):
        status, body = smoke.http_json(pool.BASE + path, method=method, body=payload, headers=headers, timeout=20)
        if status != 200 or not isinstance(body, dict) or body.get("success") is not True:
            raise RuntimeError(f"NewAPI {method} {path} failed (HTTP {status})")
        return body.get("data")

    return smoke, api


def read_affinity(api) -> tuple[list[dict], dict]:
    options = api("GET", "/api/option/")
    rules_raw = next((item.get("value") for item in options if item.get("key") == "channel_affinity_setting.rules"), None)
    if not isinstance(rules_raw, str):
        raise RuntimeError("Codex affinity rules are missing")
    rules = json.loads(rules_raw)
    if not isinstance(rules, list):
        raise RuntimeError("Codex affinity rules have invalid shape")
    target = next((rule for rule in rules if rule.get("name") == "codex cli trace"), None)
    if not isinstance(target, dict):
        raise RuntimeError("codex cli trace rule is missing")
    return rules, target


def repaired_rules(rules: list[dict]) -> list[dict]:
    result = []
    for rule in rules:
        if rule.get("name") == "codex cli trace":
            rule = {**rule, "skip_retry_on_failure": False}
        result.append(rule)
    return result


def verify(api) -> dict:
    channel = channel_tools.hydrate_key(api("GET", f"/api/channel/{SHAREDCHAT_CHANNEL_ID}"), pool.DB_PATH)
    if channel.get("name") != SHAREDCHAT_CHANNEL_NAME or channel.get("status") != 1:
        raise RuntimeError("SharedChat channel identity/status drifted")
    if channel.get("models") != ",".join(MODELS) or channel.get("test_model") != MODELS[1]:
        raise RuntimeError("SharedChat channel does not expose Astra and Sol")
    with pool.read_only(pool.DB_PATH) as db:
        abilities = [tuple(row) for row in db.execute("SELECT model,enabled,priority,weight FROM abilities WHERE channel_id=? ORDER BY model", (SHAREDCHAT_CHANNEL_ID,))]
    expected = [(model, 1, 60, 5) for model in sorted(MODELS)]
    if abilities != expected:
        raise RuntimeError(f"SharedChat abilities drifted: {abilities!r}")
    rules, target = read_affinity(api)
    if target.get("skip_retry_on_failure") is not False:
        raise RuntimeError("Codex affinity must allow cross-channel failover")
    return {"channel_id": SHAREDCHAT_CHANNEL_ID, "models": channel.get("models"), "test_model": channel.get("test_model"), "abilities": abilities, "codex_skip_retry_on_failure": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--snapshot-dir", type=Path)
    args = parser.parse_args()
    smoke, api = api_client()
    channel = channel_tools.hydrate_key(api("GET", f"/api/channel/{SHAREDCHAT_CHANNEL_ID}"), pool.DB_PATH)
    rules, target = read_affinity(api)
    proposed_rules = repaired_rules(rules)
    print(json.dumps({"channel": {"id": SHAREDCHAT_CHANNEL_ID, "name": channel.get("name"), "before_models": channel.get("models"), "after_models": ",".join(MODELS), "before_test_model": channel.get("test_model"), "after_test_model": MODELS[1]}, "affinity": {"before_skip_retry_on_failure": target.get("skip_retry_on_failure"), "after_skip_retry_on_failure": False}}, ensure_ascii=True))
    if not args.apply:
        return 0
    if args.snapshot_dir is None:
        parser.error("--apply requires --snapshot-dir")
    snapshot = args.snapshot_dir.resolve()
    with pool.read_only(snapshot / "new-api-before.db") as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("snapshot integrity check failed")
    backup = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "channel": {key: value for key, value in channel.items() if key != "key"},
        "affinity_rules": rules,
        "affinity_target": target,
    }
    (snapshot / "codex-sharedchat-repair-before.json").write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    changed_channel = False
    changed_rules = False
    try:
        updated = {key: value for key, value in channel.items() if key != "status"}
        updated["models"] = ",".join(MODELS)
        updated["test_model"] = MODELS[1]
        if not channel_tools.usable_key(updated.get("key")):
            raise RuntimeError("SharedChat key is unavailable in local SSOT")
        api("PUT", "/api/channel/", updated)
        changed_channel = True
        api("PUT", "/api/option/", {"key": "channel_affinity_setting.rules", "value": json.dumps(proposed_rules, ensure_ascii=False)})
        changed_rules = True
        api("DELETE", "/api/option/channel_affinity_cache?rule_name=codex%20cli%20trace")
        result = verify(api)
        (snapshot / "codex-sharedchat-repair-applied.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"applied": True, **result}, ensure_ascii=True))
        return 0
    except BaseException:
        rollback_errors = []
        try:
            if changed_rules:
                api("PUT", "/api/option/", {"key": "channel_affinity_setting.rules", "value": json.dumps(rules, ensure_ascii=False)})
        except Exception:
            rollback_errors.append("affinity_rules")
        try:
            if changed_channel:
                api("PUT", "/api/channel/", {key: value for key, value in channel.items() if key != "status"})
        except Exception:
            rollback_errors.append("sharedchat_channel")
        print(json.dumps({"rollback_errors": rollback_errors, "snapshot_dir": str(snapshot)}))
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR {type(error).__name__}: {error}")
        raise SystemExit(1)
