"""Back up and converge NewAPI's gateway retry budget to one retry."""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path


TARGET_RETRY_TIMES = "1"
TARGET_RETRY_STATUS_CODES = "408,500-503"
TARGET_AUTOMATIC_DISABLE = "false"


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
    targets = {
        "RetryTimes": TARGET_RETRY_TIMES,
        "AutomaticRetryStatusCodes": TARGET_RETRY_STATUS_CODES,
        "AutomaticDisableChannelEnabled": TARGET_AUTOMATIC_DISABLE,
    }
    originals = {key: option_value(options, key) for key in targets}
    print(f"current={originals} proposed={targets}")
    if any(value is None for value in originals.values()):
        print("required retry option missing; refusing incomplete rollback")
        return 1
    if originals == targets:
        print("already configured")
        return 0
    if not args.apply:
        print("dry-run: no changes made")
        return 0

    backup_dir = Path(smoke.DEPLOY_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"newapi-retry-budget-{time.strftime('%Y%m%d-%H%M%S')}.json"
    backup.write_text(
        json.dumps(originals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for key, value in targets.items():
        put_status, put_body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/",
            method="PUT",
            body={"key": key, "value": value},
            headers=headers,
        )
        if (
            put_status != 200
            or not isinstance(put_body, dict)
            or not put_body.get("success")
        ):
            print(f"update {key} failed: HTTP {put_status}; backup={backup.name}")
            restore_options(smoke, headers, originals)
            return 1

    verify_status, verify_body = smoke.http_json(
        f"{smoke.NEWAPI_BASE}/api/option/", headers=headers
    )
    verified_options = verify_body.get("data") if isinstance(verify_body, dict) else None
    verified = {key: option_value(verified_options, key) for key in targets}
    ok = verify_status == 200 and verified == targets
    print(f"backup={backup.name} verified={verified} ok={ok}")
    if ok:
        return 0

    rollback_ok = restore_options(smoke, headers, originals)
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
            and all(
                option_value(rollback_options, key) == value
                for key, value in originals.items()
            )
        )
    print(f"verification failed; rollback_ok={rollback_ok}")
    return 1


def restore_options(smoke, headers: dict[str, str], originals: dict[str, object]) -> bool:
    """Best-effort restore of every retry option changed by this tool."""
    ok = True
    for key, value in originals.items():
        status, body = smoke.http_json(
            f"{smoke.NEWAPI_BASE}/api/option/",
            method="PUT",
            body={"key": key, "value": value},
            headers=headers,
        )
        ok = ok and status == 200 and isinstance(body, dict) and bool(body.get("success"))
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
