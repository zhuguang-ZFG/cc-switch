"""Back up and converge NewAPI's gateway retry budget to one retry."""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path


TARGET_RETRY_TIMES = "1"


def option_value(options: object, key: str) -> object | None:
    if not isinstance(options, list):
        return None
    return next(
        (
            item.get("value")
            for item in options
            if isinstance(item, dict) and item.get("key") == key
        ),
        None,
    )


def load_smoke():
    path = Path(__file__).with_name("newapi-local-smoke.py")
    spec = importlib.util.spec_from_file_location("newapi_local_smoke_retry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load newapi-local-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    current = option_value(options, "RetryTimes")
    print(f"current={current} proposed={TARGET_RETRY_TIMES}")
    if current is None:
        print("RetryTimes is missing; refusing to create a rollback without an original value")
        return 1
    if current == TARGET_RETRY_TIMES:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"newapi-retry-budget-{time.strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(
        json.dumps({"RetryTimes": current}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    put_status, put_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": "RetryTimes", "value": TARGET_RETRY_TIMES},
        headers=headers,
    )
    if put_status != 200 or not isinstance(put_body, dict) or not put_body.get("success"):
        print(f"update failed: HTTP {put_status}; backup={backup.name}")
        return 1

    verify_status, verify_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
    )
    verified_options = verify_body.get("data") if isinstance(verify_body, dict) else None
    verified = option_value(verified_options, "RetryTimes")
    ok = verify_status == 200 and verified == TARGET_RETRY_TIMES
    print(f"backup={backup.name} verified={verified} ok={ok}")
    if ok:
        return 0

    rollback_status, rollback_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/",
        method="PUT",
        body={"key": "RetryTimes", "value": current},
        headers=headers,
    )
    rollback_ok = (
        rollback_status == 200
        and isinstance(rollback_body, dict)
        and bool(rollback_body.get("success"))
    )
    if rollback_ok:
        rollback_verify_status, rollback_verify_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
        )
        rollback_options = (
            rollback_verify_body.get("data")
            if isinstance(rollback_verify_body, dict)
            else None
        )
        rollback_ok = (
            rollback_verify_status == 200
            and option_value(rollback_options, "RetryTimes") == current
        )
    print(f"verification failed; rollback_ok={rollback_ok}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
