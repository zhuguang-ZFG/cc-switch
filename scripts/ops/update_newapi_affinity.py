"""Apply and verify the repository's canonical NewAPI affinity aliases.

The script is intentionally opt-in: without ``--apply`` it only reports the
current drift. On apply it stores the previous rules/enabled values in the
NewAPI backup directory before issuing single-option PUT requests.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import importlib.util


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RULE_UPDATES = {
    "codex cli trace": ["^(?:gpt-.*|zg-gpt-.*|welfare-codex-gpt-.*)$"],
    "glm trace": ["^(?:glm-.*|zg-glm-.*)$"],
    "grok trace": ["^(?:grok-.*|zg-grok-.*)$"],
    "deepseek trace": ["^(?:deepseek-.*|zg-deepseek-.*)$"],
    "longcat trace": ["^(?:LongCat-.*|zg-[Ll]ong[Cc]at-.*)$"],
    "qwen trace": ["^(?:qwen.*|zg-qwen.*)$"],
    "claude trace": ["^(?:claude-.*|zg-claude-.*)$"],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    status, body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
    )
    options = body.get("data") if isinstance(body, dict) else None
    if status != 200 or not isinstance(options, list):
        print(f"option read failed: HTTP {status}")
        return 1

    by_key = {
        item.get("key"): item.get("value")
        for item in options
        if isinstance(item, dict)
    }
    raw_rules = by_key.get("channel_affinity_setting.rules")
    try:
        rules = json.loads(raw_rules) if isinstance(raw_rules, str) else raw_rules
    except (TypeError, ValueError):
        print("channel_affinity_setting.rules is not valid JSON")
        return 1
    if not isinstance(rules, list):
        print("channel_affinity_setting.rules has invalid shape")
        return 1

    updated = []
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            print("channel_affinity_setting.rules contains invalid entry")
            return 1
        name = rule.get("name")
        if name in RULE_UPDATES:
            rule = {**rule, "model_regex": RULE_UPDATES[name]}
            seen.add(name)
        updated.append(rule)
    missing = sorted(set(RULE_UPDATES) - seen)
    if missing:
        print(f"missing affinity rules: {missing}")
        return 1

    next_options = [
        {"key": "channel_affinity_setting.rules", "value": json.dumps(updated, ensure_ascii=False)},
        {"key": "channel_affinity_setting.enabled", "value": "true"},
    ]
    proposed = list(options)
    for change in next_options:
        for index, item in enumerate(proposed):
            if isinstance(item, dict) and item.get("key") == change["key"]:
                proposed[index] = {**item, "value": change["value"]}
                break
        else:
            proposed.append(change)
    current_violations = smoke.affinity_rule_violations(options)
    proposed_violations = smoke.affinity_rule_violations(proposed)
    print(f"current violations: {current_violations or 'none'}")
    print(f"proposed violations: {proposed_violations or 'none'}")
    if not args.apply:
        print("dry-run: no changes made")
        return 1 if proposed_violations else 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"channel-affinity-{time.strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(
        json.dumps(
            {
                "channel_affinity_setting.enabled": by_key.get(
                    "channel_affinity_setting.enabled"
                ),
                "channel_affinity_setting.rules": raw_rules,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"backup: {backup.name}")
    for change in next_options:
        put_status, put_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/",
            method="PUT",
            body=change,
            headers=headers,
        )
        if put_status != 200 or not isinstance(put_body, dict) or not put_body.get("success"):
            print(f"option update failed: {change['key']} HTTP {put_status}")
            return 1

    verify_status, verify_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
    )
    verified = (
        verify_body.get("data") if isinstance(verify_body, dict) else None
    )
    final_violations = smoke.affinity_rule_violations(verified)
    print(f"verified: HTTP {verify_status} violations={final_violations or 'none'}")
    return 1 if verify_status != 200 or final_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
