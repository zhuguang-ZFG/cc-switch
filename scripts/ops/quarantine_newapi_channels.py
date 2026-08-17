"""Back up and double-lock explicitly selected NewAPI channels."""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("channel_ids", nargs="+", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if any(channel_id <= 0 for channel_id in args.channel_ids):
        parser.error("channel ids must be positive")

    smoke = load_smoke()
    token, user_id = smoke.admin_auth()
    headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}
    channels: list[dict] = []
    for channel_id in args.channel_ids:
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
        )
        channel = body.get("data") if isinstance(body, dict) else None
        if status != 200 or not isinstance(channel, dict):
            print(f"channel {channel_id}: read failed HTTP {status}")
            return 1
        channels.append(channel)
        print(
            f"channel {channel_id}: name={channel.get('name')} "
            f"status={channel.get('status')} weight={channel.get('weight')}"
        )

    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    joined = "-".join(str(channel_id) for channel_id in args.channel_ids)
    backup = backup_dir / f"channels-{joined}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(
        json.dumps({"channels": channels}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"backup: {backup.name}")

    failed = False
    for channel in channels:
        channel_id = int(channel["id"])
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}/status",
            method="POST",
            body={"status": 2},
            headers=headers,
        )
        if status != 200 or not isinstance(body, dict) or not body.get("success"):
            print(f"channel {channel_id}: disable failed HTTP {status}")
            failed = True
            continue

        # Avoid a full channel PUT when the weight lock already exists. Admin
        # GET responses may redact keys; replaying a masked key can corrupt an
        # otherwise valid channel merely to persist the same weight value.
        if channel.get("weight") != 0:
            updated = {key: value for key, value in channel.items() if key != "status"}
            updated["weight"] = 0
            status, body = smoke.http_json(
                f"{smoke.NEWAPI_BASE}/api/channel/",
                method="PUT",
                body=updated,
                headers=headers,
            )
            if status != 200 or not isinstance(body, dict) or not body.get("success"):
                print(f"channel {channel_id}: weight lock failed HTTP {status}")
                failed = True
                continue

        verify_status, verify_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/channel/{channel_id}", headers=headers
        )
        verified = verify_body.get("data") if isinstance(verify_body, dict) else None
        ok = (
            verify_status == 200
            and isinstance(verified, dict)
            and verified.get("status") == 2
            and verified.get("weight") == 0
        )
        print(
            f"channel {channel_id}: verified={ok} "
            f"status={verified.get('status') if isinstance(verified, dict) else None} "
            f"weight={verified.get('weight') if isinstance(verified, dict) else None}"
        )
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
